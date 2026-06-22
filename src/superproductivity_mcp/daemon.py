import json
import logging
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

DAEMON_PORT = 27833
_PID_DIR = Path(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
) / "super-productivity-mcp"
_DEFAULT_COMMAND_TIMEOUT = 30.0


class _DaemonHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, daemon: "BridgeDaemon", **kwargs):
        # Must be set before super().__init__() — BaseHTTPRequestHandler calls handle() during init
        self._d = daemon
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            with self._d._lock:
                sessions = len(self._d._sessions)
                queued = len(self._d._plugin_queue)
            self._send_json(200, {
                "status": "ok",
                "port": self._d.port,
                "active_sessions": sessions,
                "queued_commands": queued,
            })
        elif self.path == "/commands":
            with self._d._lock:
                cmds = list(self._d._plugin_queue)
                self._d._plugin_queue.clear()
            self._send_json(200, cmds)
        elif self.path == "/config":
            with self._d._lock:
                cfg = dict(self._d._config)
            self._send_json(200, cfg)
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/session/register":
            session_id = str(uuid.uuid4())
            with self._d._lock:
                self._d._sessions.add(session_id)
            self._send_json(200, {"session_id": session_id})

        elif self.path.startswith("/session/") and self.path.endswith("/command"):
            parts = self.path.split("/")
            # /session/{id}/command → ['', 'session', id, 'command']
            if len(parts) != 4:
                self._send_json(400, {"error": "invalid path"})
                return
            session_id = parts[2]
            with self._d._lock:
                if session_id not in self._d._sessions:
                    self._send_json(404, {"error": "session not found"})
                    return
            try:
                body = json.loads(self._read_body())
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json"})
                return

            action = body.get("action", "unknown")
            # Allow tests to override timeout via _timeout field (stripped before queuing)
            cmd_timeout = float(body.pop("_timeout", _DEFAULT_COMMAND_TIMEOUT))
            cmd_id = f"{action}_{uuid.uuid4().hex}"
            event = threading.Event()
            # entry: [session_id, event, response_or_None]
            entry: list = [session_id, event, None]
            cmd = {"id": cmd_id, **body}

            with self._d._lock:
                self._d._routing[cmd_id] = entry
                self._d._plugin_queue.append(cmd)

            responded = event.wait(timeout=cmd_timeout)

            with self._d._lock:
                self._d._routing.pop(cmd_id, None)
                # Remove from queue if not yet consumed — prevents stale commands
                # being executed by the plugin after the caller has already timed out.
                self._d._plugin_queue[:] = [
                    c for c in self._d._plugin_queue if c.get("id") != cmd_id
                ]

            if responded and entry[2] is not None:
                self._send_json(200, entry[2])
            else:
                self._send_json(200, {
                    "success": False,
                    "error": f"Timeout waiting for response to {action}",
                })

        elif self.path.startswith("/response/"):
            cmd_id = self.path[len("/response/"):]
            try:
                body = json.loads(self._read_body())
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json"})
                return
            with self._d._lock:
                entry = self._d._routing.get(cmd_id)
                if entry is not None:
                    entry[2] = body
            if entry is not None:
                # event.set() is intentionally called outside the lock:
                # the waiting thread may wake and pop the routing entry immediately,
                # but entry[2] is already written and the local reference remains valid.
                entry[1].set()
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": f"unknown command id: {cmd_id}"})

        elif self.path == "/config":
            try:
                data = json.loads(self._read_body())
            except (json.JSONDecodeError, ValueError):
                self._send_json(400, {"error": "invalid json"})
                return
            with self._d._lock:
                self._d._config.update(data)
            self._send_json(200, {"ok": True})

        elif self.path == "/events":
            self._read_body()
            self._send_json(200, {"ok": True})

        elif self.path == "/shutdown":
            self._send_json(200, {"ok": True})
            threading.Thread(target=self._d.stop, daemon=True).start()

        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path.startswith("/session/"):
            session_id = self.path[len("/session/"):]
            with self._d._lock:
                self._d._sessions.discard(session_id)
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


class BridgeDaemon:
    """Singleton HTTP server bridging MCP sessions to the SP plugin.

    Owns DAEMON_PORT permanently. Multiple MCP server processes register
    as sessions and submit commands via long-polling HTTP. The SP plugin
    side is unchanged from v2.0.0.
    """

    def __init__(self, port: int = DAEMON_PORT):
        self.port = port
        self._lock = threading.Lock()
        self._sessions: Set[str] = set()
        self._plugin_queue: List[dict] = []
        self._routing: Dict[str, list] = {}  # cmd_id → [session_id, Event, response]
        self._config: Dict[str, Any] = {
            "commandCheckIntervalMs": 2000,
            "logLevel": "info",
        }
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        def make_handler(*args, **kwargs):
            return _DaemonHandler(*args, daemon=self, **kwargs)

        self._server = ThreadingHTTPServer(("localhost", self.port), make_handler)
        # daemon=False so the thread keeps the process alive when run as standalone daemon
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=False)
        self._thread.start()
        self._write_pid()
        logging.info("BridgeDaemon started on port %d (pid %d)", self.port, os.getpid())

    def stop(self) -> None:
        with self._lock:
            for entry in self._routing.values():
                entry[1].set()  # wake all waiting handlers so they exit promptly
        if self._server:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._remove_pid()

    def _write_pid(self) -> None:
        try:
            _PID_DIR.mkdir(parents=True, exist_ok=True)
            (_PID_DIR / "bridge.pid").write_text(str(os.getpid()))
        except OSError:
            pass

    def _remove_pid(self) -> None:
        try:
            (_PID_DIR / "bridge.pid").unlink(missing_ok=True)
        except OSError:
            pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    daemon = BridgeDaemon()
    daemon.start()
    try:
        assert daemon._thread is not None
        daemon._thread.join()
    except KeyboardInterrupt:
        logging.info("Shutting down bridge daemon...")
        daemon.stop()


if __name__ == "__main__":
    main()
