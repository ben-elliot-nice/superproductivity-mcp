# Singleton Bridge Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-session `PluginBridge` HTTP servers with a singleton `BridgeDaemon` process so multiple Claude sessions can share one SP plugin connection without command timeouts.

**Architecture:** A new `BridgeDaemon` (stdlib `ThreadingHTTPServer`) owns port 27833 permanently and exposes both plugin-facing endpoints (unchanged) and session-facing endpoints (new). Each MCP server process becomes a `PluginBridgeClient` that registers a session with the daemon and submits commands via long-polling HTTP (`POST /session/{id}/command` blocks up to 30s until the plugin responds). The daemon routes each response back to the correct waiting session via a `threading.Event` lookup table. `plugin.js` is unchanged.

**Tech Stack:** Python stdlib `http.server.ThreadingHTTPServer`, `threading`, `asyncio.to_thread`, `subprocess` (auto-spawn daemon). No new dependencies.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/superproductivity_mcp/daemon.py` | **Create** | Singleton HTTP server — plugin-facing + session-facing endpoints, routing table |
| `src/superproductivity_mcp/bridge.py` | **Rewrite** | `PluginBridgeClient` — registers session, submits commands, auto-spawns daemon |
| `src/superproductivity_mcp/server.py` | **Modify** | Swap `PluginBridge` → `PluginBridgeClient`, remove `loop` arg from `start()` |
| `tests/test_daemon.py` | **Create** | Tests for all daemon HTTP endpoints |
| `tests/test_bridge.py` | **Rewrite** | Tests for `PluginBridgeClient` (old `PluginBridge` tests deleted) |
| `tests/test_mcp_logic.py` | **Modify** | Update `test_server_has_no_command_dir` (still valid, no change needed) |
| `pyproject.toml` | **Modify** | Add `superproductivity-mcp-bridge` entry point, bump version to `2.1.0` |
| `src/superproductivity_mcp/__init__.py` | **Modify** | Bump `__version__` to `2.1.0` |
| `plugin/manifest.json` | **Modify** | Bump version to `2.1.0`, add changelog entry |

---

## Task 1: Create `daemon.py` and `tests/test_daemon.py`

**Files:**
- Create: `src/superproductivity_mcp/daemon.py`
- Create: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daemon.py`:

```python
# tests/test_daemon.py
import json
import socket
import threading
import time
import urllib.request
from superproductivity_mcp.daemon import BridgeDaemon


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def _post(port: int, path: str, body: dict, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _delete(port: int, path: str) -> dict:
    req = urllib.request.Request(f"http://localhost:{port}{path}", method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def test_status():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        r = _get(port, "/status")
        assert r["status"] == "ok"
        assert r["port"] == port
        assert r["active_sessions"] == 0
        assert r["queued_commands"] == 0
    finally:
        d.stop()


def test_session_register_and_unregister():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        r = _post(port, "/session/register", {})
        session_id = r["session_id"]
        assert len(session_id) > 10

        assert _get(port, "/status")["active_sessions"] == 1

        _delete(port, f"/session/{session_id}")
        assert _get(port, "/status")["active_sessions"] == 0
    finally:
        d.stop()


def test_commands_empty_before_any_session():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        assert _get(port, "/commands") == []
    finally:
        d.stop()


def test_command_round_trip():
    """Session submits command → plugin drains + responds → session gets result."""
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        session_id = _post(port, "/session/register", {})["session_id"]
        results: dict = {}

        def submit():
            results["resp"] = _post(
                port, f"/session/{session_id}/command", {"action": "getTasks"}, timeout=5
            )

        t = threading.Thread(target=submit)
        t.start()
        time.sleep(0.1)  # let command enqueue

        cmds = _get(port, "/commands")
        assert len(cmds) == 1
        cmd_id = cmds[0]["id"]
        assert cmds[0]["action"] == "getTasks"

        _post(port, f"/response/{cmd_id}", {"success": True, "result": [{"id": "t1"}]})
        t.join(timeout=3)

        assert results["resp"]["success"] is True
        assert results["resp"]["result"][0]["id"] == "t1"
    finally:
        d.stop()


def test_two_sessions_route_independently():
    """Commands from two sessions are routed to the correct long-poll callers."""
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        sess1 = _post(port, "/session/register", {})["session_id"]
        sess2 = _post(port, "/session/register", {})["session_id"]
        results: dict = {}

        def submit(sid: str, key: str) -> None:
            results[key] = _post(
                port, f"/session/{sid}/command", {"action": "getTasks"}, timeout=5
            )

        t1 = threading.Thread(target=submit, args=(sess1, "s1"))
        t2 = threading.Thread(target=submit, args=(sess2, "s2"))
        t1.start()
        t2.start()
        time.sleep(0.1)

        cmds = _get(port, "/commands")
        assert len(cmds) == 2

        for i, cmd in enumerate(cmds):
            _post(port, f"/response/{cmd['id']}", {"success": True, "result": f"result-{i}"})

        t1.join(timeout=3)
        t2.join(timeout=3)

        assert results["s1"]["success"] is True
        assert results["s2"]["success"] is True
        assert results["s1"]["result"] != results["s2"]["result"]
    finally:
        d.stop()


def test_command_timeout():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        session_id = _post(port, "/session/register", {})["session_id"]
        # _timeout < 1s so the test doesn't take 30s; daemon reads this field
        resp = _post(
            port,
            f"/session/{session_id}/command",
            {"action": "getTasks", "_timeout": 0.2},
            timeout=5,
        )
        assert resp["success"] is False
        assert "Timeout" in resp["error"]
    finally:
        d.stop()


def test_config_get_and_post():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        cfg = _get(port, "/config")
        assert cfg["commandCheckIntervalMs"] == 2000

        _post(port, "/config", {"commandCheckIntervalMs": 5000})
        assert _get(port, "/config")["commandCheckIntervalMs"] == 5000
    finally:
        d.stop()


def test_events_noop():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        r = _post(port, "/events", {"eventType": "taskUpdate"})
        assert r["ok"] is True
    finally:
        d.stop()


def test_unknown_session_command_rejected():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        import urllib.error
        try:
            _post(port, "/session/does-not-exist/command", {"action": "getTasks"}, timeout=2)
            assert False, "Expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        d.stop()


def test_response_unknown_id_returns_404():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        import urllib.error
        try:
            _post(port, "/response/no-such-id", {"success": True})
            assert False, "Expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        d.stop()
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
uv run pytest tests/test_daemon.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'superproductivity_mcp.daemon'`

- [ ] **Step 3: Write `daemon.py`**

Create `src/superproductivity_mcp/daemon.py`:

```python
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_daemon.py -v
```

Expected:
```
tests/test_daemon.py::test_status PASSED
tests/test_daemon.py::test_session_register_and_unregister PASSED
tests/test_daemon.py::test_commands_empty_before_any_session PASSED
tests/test_daemon.py::test_command_round_trip PASSED
tests/test_daemon.py::test_two_sessions_route_independently PASSED
tests/test_daemon.py::test_command_timeout PASSED
tests/test_daemon.py::test_config_get_and_post PASSED
tests/test_daemon.py::test_events_noop PASSED
tests/test_daemon.py::test_unknown_session_command_rejected PASSED
tests/test_daemon.py::test_response_unknown_id_returns_404 PASSED

10 passed
```

- [ ] **Step 5: Run full suite to confirm no regressions**

```bash
uv run pytest -v
```

Expected: all 38 existing tests + 10 new = 48 passed.

- [ ] **Step 6: Commit**

```bash
git add src/superproductivity_mcp/daemon.py tests/test_daemon.py
git commit -m "feat: add BridgeDaemon singleton HTTP server with session routing"
```

---

## Task 2: Rewrite `bridge.py` as `PluginBridgeClient` and update `tests/test_bridge.py`

**Files:**
- Rewrite: `src/superproductivity_mcp/bridge.py`
- Rewrite: `tests/test_bridge.py`

- [ ] **Step 1: Write the new `tests/test_bridge.py`**

Replace the entire file:

```python
# tests/test_bridge.py
import asyncio
import json
import socket
import threading
import time
import urllib.request
from superproductivity_mcp.bridge import PluginBridgeClient, DAEMON_URL
from superproductivity_mcp.daemon import BridgeDaemon


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


def _post(port: int, path: str, body: dict, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def test_daemon_url_constant():
    assert DAEMON_URL == "http://localhost:27833"


def test_client_registers_with_daemon():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()
    try:
        client = PluginBridgeClient(daemon_url=f"http://localhost:{port}")
        client.start()
        assert client.session_id is not None
        assert len(client.session_id) > 10
        assert _get(port, "/status")["active_sessions"] == 1
        client.stop()
        assert client.session_id is None
        assert _get(port, "/status")["active_sessions"] == 0
    finally:
        d.stop()


def test_client_send_command_round_trip():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()

    async def _run():
        client = PluginBridgeClient(daemon_url=f"http://localhost:{port}")
        client.start()

        def simulate_plugin():
            time.sleep(0.1)
            cmds = json.loads(
                urllib.request.urlopen(f"http://localhost:{port}/commands", timeout=5).read()
            )
            if cmds:
                cmd_id = cmds[0]["id"]
                data = json.dumps({"success": True, "result": [{"id": "task1"}]}).encode()
                req = urllib.request.Request(
                    f"http://localhost:{port}/response/{cmd_id}",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)

        t = threading.Thread(target=simulate_plugin)
        t.start()

        result = await client.send_command("getTasks", timeout=3.0)
        t.join(timeout=3)
        client.stop()
        return result

    result = asyncio.run(_run())
    d.stop()
    assert result["success"] is True
    assert result["result"][0]["id"] == "task1"


def test_client_send_command_daemon_timeout():
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()

    async def _run():
        client = PluginBridgeClient(daemon_url=f"http://localhost:{port}")
        client.start()
        # _timeout=0.2 so test is fast; no plugin responds
        result = await client.send_command("getTasks", timeout=0.2)
        client.stop()
        return result

    result = asyncio.run(_run())
    d.stop()
    assert result["success"] is False
    assert "Timeout" in result["error"]


def test_two_clients_one_daemon():
    """Two PluginBridgeClient instances share one daemon — each gets its own session."""
    port = _free_port()
    d = BridgeDaemon(port=port)
    d.start()

    async def _run():
        c1 = PluginBridgeClient(daemon_url=f"http://localhost:{port}")
        c2 = PluginBridgeClient(daemon_url=f"http://localhost:{port}")
        c1.start()
        c2.start()
        assert c1.session_id != c2.session_id
        assert _get(port, "/status")["active_sessions"] == 2
        c1.stop()
        c2.stop()

    asyncio.run(_run())
    d.stop()
    assert _get(port, "/status")["active_sessions"] == 0
```

- [ ] **Step 2: Run new tests to confirm they fail (bridge.py not yet rewritten)**

```bash
uv run pytest tests/test_bridge.py -v 2>&1 | head -15
```

Expected: `ImportError` — `PluginBridgeClient` not found in bridge.py.

- [ ] **Step 3: Rewrite `bridge.py`**

Replace the entire file:

```python
import asyncio
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

DAEMON_URL = "http://localhost:27833"
_SPAWN_TIMEOUT = 3.0
_SPAWN_POLL = 0.1


def _http_get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _http_post(url: str, body: dict, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_delete(url: str) -> None:
    req = urllib.request.Request(url, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=5.0)
    except Exception:
        pass


class PluginBridgeClient:
    """HTTP client connecting to the singleton BridgeDaemon.

    Multiple instances (one per MCP server process) can coexist.
    Each registers a unique session_id with the daemon on start().
    Auto-spawns the daemon via 'superproductivity-mcp-bridge' if not running.
    """

    def __init__(self, daemon_url: str = DAEMON_URL):
        self.daemon_url = daemon_url
        self.port: int = 27833  # informational
        self.session_id: Optional[str] = None

    def start(self) -> None:
        self._ensure_daemon()
        r = _http_post(f"{self.daemon_url}/session/register", {})
        self.session_id = r["session_id"]
        logging.info("Registered with bridge daemon, session=%s", self.session_id)

    def stop(self) -> None:
        if self.session_id:
            _http_delete(f"{self.daemon_url}/session/{self.session_id}")
            self.session_id = None

    def _ensure_daemon(self) -> None:
        try:
            _http_get(f"{self.daemon_url}/status")
            return
        except (urllib.error.URLError, OSError):
            pass

        logging.info("Bridge daemon not found — spawning superproductivity-mcp-bridge")
        subprocess.Popen(
            ["superproductivity-mcp-bridge"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.monotonic() + _SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(_SPAWN_POLL)
            try:
                _http_get(f"{self.daemon_url}/status")
                logging.info("Bridge daemon ready")
                return
            except (urllib.error.URLError, OSError):
                continue

        raise RuntimeError(
            f"Bridge daemon failed to start within {_SPAWN_TIMEOUT}s. "
            "Try running 'superproductivity-mcp-bridge' manually."
        )

    async def send_command(self, action: str, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        if self.session_id is None:
            raise RuntimeError("PluginBridgeClient not started — call start() first")
        cmd = {"action": action, **kwargs}
        # Pass _timeout so the daemon uses the right timeout for tests with short values
        cmd["_timeout"] = timeout
        url = f"{self.daemon_url}/session/{self.session_id}/command"
        try:
            # HTTP timeout = command timeout + 5s buffer for network overhead
            return await asyncio.to_thread(_http_post, url, cmd, timeout + 5.0)
        except Exception as e:
            logging.warning("send_command %s failed: %s", action, e)
            return {"success": False, "error": str(e)}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_bridge.py -v
```

Expected:
```
tests/test_bridge.py::test_daemon_url_constant PASSED
tests/test_bridge.py::test_client_registers_with_daemon PASSED
tests/test_bridge.py::test_client_send_command_round_trip PASSED
tests/test_bridge.py::test_client_send_command_daemon_timeout PASSED
tests/test_bridge.py::test_two_clients_one_daemon PASSED

5 passed
```

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -v
```

Expected: 48 previous + 5 new = 53 passed. Note: the old 8 bridge tests are deleted so the net new count is 53 - 8 + 5 = 50. Verify total is **50 passed**.

- [ ] **Step 6: Commit**

```bash
git add src/superproductivity_mcp/bridge.py tests/test_bridge.py
git commit -m "feat: rewrite bridge.py as PluginBridgeClient connecting to singleton daemon"
```

---

## Task 3: Update `server.py`

**Files:**
- Modify: `src/superproductivity_mcp/server.py`

Changes: swap import, remove `loop` arg from `start()`, update `debug_directories`, bump `server_version`.

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_mcp_logic.py`:

```python
def test_server_uses_bridge_client_not_bridge():
    """server.py must use PluginBridgeClient, not the old PluginBridge."""
    from superproductivity_mcp import bridge
    assert hasattr(bridge, "PluginBridgeClient"), "PluginBridgeClient not in bridge module"
    assert not hasattr(bridge, "PluginBridge"), "Old PluginBridge still exported — should be removed"
```

- [ ] **Step 2: Run to confirm test passes (bridge was already rewritten in Task 2)**

```bash
uv run pytest tests/test_mcp_logic.py::test_server_uses_bridge_client_not_bridge -v
```

Expected: PASS (bridge.py has `PluginBridgeClient`, no `PluginBridge`).

- [ ] **Step 3: Update the import in `server.py`**

Find this line near the top of `src/superproductivity_mcp/server.py`:

```python
from superproductivity_mcp.bridge import PluginBridge
```

Replace with:

```python
from superproductivity_mcp.bridge import PluginBridgeClient
```

- [ ] **Step 4: Update `__init__` in `server.py`**

Find:

```python
def __init__(self):
    self.server = Server("super-productivity")
    self._tag_cache: Dict[str, str] = {}
    self._bridge = PluginBridge()
    self.setup_logging()
    self.setup_tools()
```

Replace with:

```python
def __init__(self):
    self.server = Server("super-productivity")
    self._tag_cache: Dict[str, str] = {}
    self._bridge = PluginBridgeClient()
    self.setup_logging()
    self.setup_tools()
```

- [ ] **Step 5: Update `run()` in `server.py`**

Find:

```python
async def run(self):
    logging.info("Starting Super Productivity MCP Server...")
    loop = asyncio.get_running_loop()
    self._bridge.start(loop)
    logging.info("PluginBridge ready on port %d", self._bridge.port)
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="super-productivity",
                    server_version="2.0.0",
```

Replace with:

```python
async def run(self):
    logging.info("Starting Super Productivity MCP Server...")
    self._bridge.start()
    logging.info("PluginBridgeClient ready, session=%s", self._bridge.session_id)
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="super-productivity",
                    server_version="2.1.0",
```

- [ ] **Step 6: Update `debug_directories()` in `server.py`**

Find the `debug_directories` method body:

```python
async def debug_directories(self, args: Dict[str, Any]) -> Dict[str, Any]:
    with self._bridge._lock:
        pending = len(self._bridge._pending)
        queued = len(self._bridge._queue)
    return {
        "success": True,
        "transport": "http",
        "bridge_port": self._bridge.port,
        "bridge_url": f"http://localhost:{self._bridge.port}" if self._bridge.port else None,
        "pending_commands": pending,
        "queued_commands": queued,
        "tag_cache_size": len(self._tag_cache),
    }
```

Replace with:

```python
async def debug_directories(self, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "success": True,
        "transport": "http-daemon",
        "daemon_url": self._bridge.daemon_url,
        "session_id": self._bridge.session_id,
        "tag_cache_size": len(self._tag_cache),
    }
```

- [ ] **Step 7: Update the `debug_directories` tool description in `setup_tools()`**

Find:

```python
description="HTTP bridge status — port, queue depth, pending commands. Use if commands are timing out.",
```

Replace with:

```python
description="Daemon bridge status — URL, session ID, tag cache size. Use if commands are timing out.",
```

- [ ] **Step 8: Run full suite**

```bash
uv run pytest -v
```

Expected: all 51 tests pass (50 from Task 2 + 1 new).

- [ ] **Step 9: Commit**

```bash
git add src/superproductivity_mcp/server.py tests/test_mcp_logic.py
git commit -m "feat: swap PluginBridge → PluginBridgeClient in server.py"
```

---

## Task 4: Add daemon entry point, bump version to 2.1.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/superproductivity_mcp/__init__.py`
- Modify: `plugin/manifest.json`

- [ ] **Step 1: Add entry point and bump version in `pyproject.toml`**

Find:

```toml
[project.scripts]
superproductivity-mcp = "superproductivity_mcp.server:main"
```

Replace with:

```toml
[project.scripts]
superproductivity-mcp = "superproductivity_mcp.server:main"
superproductivity-mcp-bridge = "superproductivity_mcp.daemon:main"
```

Then find:

```toml
version = "2.0.0"
```

Replace with:

```toml
version = "2.1.0"
```

- [ ] **Step 2: Bump `__init__.py`**

In `src/superproductivity_mcp/__init__.py`, change:

```python
__version__ = "2.0.0"
```

to:

```python
__version__ = "2.1.0"
```

- [ ] **Step 3: Bump `plugin/manifest.json`**

Change `"version": "2.0.0"` to `"version": "2.1.0"` and add changelog entry:

```json
"2.1.0": "Singleton bridge daemon — multiple Claude sessions share one SP plugin connection via superproductivity-mcp-bridge",
```

The full `changelog` object should be:

```json
"changelog": {
  "2.1.0": "Singleton bridge daemon — multiple Claude sessions share one SP plugin connection via superproductivity-mcp-bridge",
  "2.0.0": "Rewrite plugin IPC from file-based (nodeExecution) to HTTP fetch — works as uploaded plugin, auto port selection 27833–27840",
  "1.3.0": "Subtask nesting fix, MCP install card, plugin artifact rename, httpx pin",
  "1.2.6": "Fix subtask nesting (set parentId + subTaskIds), add fix_subtask_links repair tool",
  "1.2.1": "Dashboard UI redesign — settings card with vertical form and directory listings, clear logs button in log card",
  "1.2.0": "MCP API redesign — 16-tool surface, server-side tag/project resolution, human-readable time, due dates, batch create, completed task history, convert_to_subtask",
  "1.1.1": "Fix SP double-write bug when creating subtasks with parentId",
  "1.1.0": "Structured log levels, MCP call instrumentation, dashboard log delivery fix, log level selector UI",
  "1.0.0": "Initial release with full MCP bridge functionality"
}
```

- [ ] **Step 4: Sync lockfile and verify entry point is installable**

```bash
uv sync
uv run superproductivity-mcp-bridge --help 2>&1 | head -3 || true
```

The command should start and show logging output (it starts the daemon), not crash with `ModuleNotFoundError`.

Kill it with Ctrl+C or run:
```bash
pkill -f superproductivity-mcp-bridge || true
```

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -v
```

Expected: all 51 tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/superproductivity_mcp/__init__.py plugin/manifest.json
git commit -m "chore: add superproductivity-mcp-bridge entry point, bump to v2.1.0"
```

---

## Self-Review

**Spec coverage:**
- ✅ `daemon.py` — BridgeDaemon with plugin-facing + session-facing endpoints — Task 1
- ✅ Session register/unregister — Task 1
- ✅ Command routing table (`_routing`) — Task 1
- ✅ Long-poll via `threading.Event` — Task 1
- ✅ `_timeout` field for per-command timeout override (used in tests) — Task 1
- ✅ `PluginBridgeClient` replaces `PluginBridge` — Task 2
- ✅ `_ensure_daemon()` auto-spawns via `superproductivity-mcp-bridge` — Task 2
- ✅ `asyncio.to_thread` for blocking HTTP call — Task 2
- ✅ `start()` takes no `loop` argument — Task 3
- ✅ `debug_directories` updated — Task 3
- ✅ `server_version` bumped to 2.1.0 — Task 3
- ✅ New entry point `superproductivity-mcp-bridge` — Task 4
- ✅ Version 2.1.0 in all four locations — Task 4
- ✅ `plugin.js` unchanged — confirmed: not in file map
- ✅ PID file written/removed — Task 1 (`_write_pid`/`_remove_pid`)

**Placeholder scan:** No TBDs, TODOs, or vague steps. All code blocks complete.

**Type consistency:**
- `BridgeDaemon.start()` — no args → consistent with test usage in Task 1
- `PluginBridgeClient.start()` — no args → consistent with Task 3 `self._bridge.start()`
- `PluginBridgeClient.send_command(action, timeout, **kwargs)` — passes `_timeout` to daemon → daemon reads `body.pop("_timeout", _DEFAULT_COMMAND_TIMEOUT)` ✅
- `_routing` entry is `list` not `tuple` (mutable, so `entry[2] = body` works) — consistent throughout ✅
- `daemon_url` parameter on `PluginBridgeClient` used in all tests → consistent with `DAEMON_URL` constant ✅
