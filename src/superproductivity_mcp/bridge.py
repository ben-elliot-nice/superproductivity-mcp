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
        cmd = {"action": action, "_timeout": timeout, **kwargs}
        url = f"{self.daemon_url}/session/{self.session_id}/command"
        try:
            # HTTP timeout = command timeout + 5s buffer for network overhead
            return await asyncio.to_thread(_http_post, url, cmd, timeout + 5.0)
        except Exception as e:
            logging.warning("send_command %s failed: %s", action, e)
            return {"success": False, "error": str(e)}
