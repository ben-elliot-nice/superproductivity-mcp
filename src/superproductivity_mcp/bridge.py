import asyncio
import json
import logging
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

PORT_RANGE_START = 27833
PORT_RANGE_END   = 27841  # exclusive — 8 candidates: 27833–27840


def _find_free_port(start: int, end: int) -> int:
    """Return the first bindable port in [start, end). Raises OSError if none free."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("localhost", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port found in range {start}–{end - 1}")


def _resolve_future(future: asyncio.Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)


class _BridgeHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, bridge: "PluginBridge", **kwargs):
        # Must be set before super().__init__() — BaseHTTPRequestHandler calls handle() during init
        self._bridge = bridge
        super().__init__(*args, **kwargs)

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            self._send_json(200, {"status": "ok", "port": self._bridge.port})
        elif self.path == "/commands":
            with self._bridge._lock:
                cmds = list(self._bridge._queue)
                self._bridge._queue.clear()
            self._send_json(200, cmds)
        elif self.path == "/config":
            with self._bridge._lock:
                cfg = dict(self._bridge._config)
            self._send_json(200, cfg)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/response/"):
            cmd_id = self.path[len("/response/"):]
            try:
                body = json.loads(self._read_body())
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json"})
                return
            with self._bridge._lock:
                future = self._bridge._pending.pop(cmd_id, None)
            if future is not None:
                self._bridge._loop.call_soon_threadsafe(_resolve_future, future, body)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": f"unknown command id: {cmd_id}"})
        elif self.path == "/config":
            try:
                data = json.loads(self._read_body())
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json"})
                return
            with self._bridge._lock:
                self._bridge._config.update(data)
            self._send_json(200, {"ok": True})
        elif self.path == "/events":
            self._read_body()  # consume body
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # silence default access log


class PluginBridge:
    """HTTP bridge between the asyncio MCP server and the SP plugin renderer.

    Pass port=None (default) to auto-select the first free port in
    PORT_RANGE_START–PORT_RANGE_END. Pass an explicit int for tests.
    The actual bound port is available as bridge.port after start().
    """

    def __init__(self, port: Optional[int] = None):
        self._requested_port = port
        self.port: Optional[int] = port  # set to actual bound port in start()
        self._lock = threading.Lock()
        self._queue: list = []
        self._pending: Dict[str, asyncio.Future] = {}
        self._config: Dict[str, Any] = {
            "commandCheckIntervalMs": 2000,
            "logLevel": "info",
        }
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

        if self._requested_port is not None:
            self.port = self._requested_port
        else:
            self.port = _find_free_port(PORT_RANGE_START, PORT_RANGE_END)

        def make_handler(*args, **kwargs):
            return _BridgeHandler(*args, bridge=self, **kwargs)

        self._server = HTTPServer(("localhost", self.port), make_handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logging.info("PluginBridge HTTP server started on port %d", self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=2.0)

    async def send_command(self, action: str, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        if self._loop is None:
            raise RuntimeError("Bridge not started — call start() first")
        cmd_id = f"{action}_{uuid.uuid4().hex}"
        cmd = {"id": cmd_id, "action": action, **kwargs}
        future: asyncio.Future = self._loop.create_future()
        with self._lock:
            self._pending[cmd_id] = future
            self._queue.append(cmd)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._lock:
                self._pending.pop(cmd_id, None)
            return {"success": False, "error": f"Timeout waiting for response to {action}"}
        except asyncio.CancelledError:
            with self._lock:
                self._pending.pop(cmd_id, None)
            raise
