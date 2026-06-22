import asyncio
import json
import logging
import subprocess
import threading
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
    Auto-reconnects on session eviction or daemon crash.
    """

    def __init__(self, daemon_url: str = DAEMON_URL, heartbeat_interval: float = 30.0):
        self.daemon_url = daemon_url
        self._heartbeat_interval = heartbeat_interval
        self.session_id: Optional[str] = None
        self._reconnect_lock = threading.Lock()
        self._heartbeat_stop: threading.Event = threading.Event()

    def start(self) -> None:
        self._ensure_daemon()
        r = _http_post(f"{self.daemon_url}/session/register", {})
        self.session_id = r["session_id"]
        logging.info("Registered with bridge daemon, session=%s", self.session_id)
        self._start_heartbeat()

    def stop(self) -> None:
        self._heartbeat_stop.set()
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

    def _reconnect(self) -> None:
        with self._reconnect_lock:
            logging.info("Reconnecting to bridge daemon...")
            self._ensure_daemon()
            r = _http_post(f"{self.daemon_url}/session/register", {})
            self.session_id = r["session_id"]
            logging.info("Reconnected, new session=%s", self.session_id)

    def _start_heartbeat(self) -> None:
        self._heartbeat_stop.clear()

        def _beat() -> None:
            while not self._heartbeat_stop.wait(timeout=self._heartbeat_interval):
                try:
                    _http_post(
                        f"{self.daemon_url}/session/{self.session_id}/heartbeat", {}
                    )
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        logging.info("Heartbeat 404 — session evicted, reconnecting")
                        try:
                            self._reconnect()
                        except Exception as re:
                            logging.warning("Heartbeat reconnect failed: %s", re)
                except (urllib.error.URLError, OSError):
                    logging.warning("Heartbeat failed — daemon unreachable, reconnecting")
                    try:
                        self._reconnect()
                    except Exception as re:
                        logging.warning("Heartbeat reconnect failed: %s", re)

        threading.Thread(target=_beat, daemon=True).start()

    async def send_command(self, action: str, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        if self.session_id is None:
            raise RuntimeError("PluginBridgeClient not started — call start() first")
        cmd = {"action": action, "_timeout": timeout, **kwargs}
        url = f"{self.daemon_url}/session/{self.session_id}/command"
        try:
            return await asyncio.to_thread(_http_post, url, cmd, timeout + 5.0)
        except Exception as e:
            is_dead_session = isinstance(e, urllib.error.HTTPError) and e.code == 404
            is_conn_err = not isinstance(e, urllib.error.HTTPError)
            if is_dead_session or is_conn_err:
                logging.warning("send_command %s failed (%s) — reconnecting", action, e)
                try:
                    await asyncio.to_thread(self._reconnect)
                    url = f"{self.daemon_url}/session/{self.session_id}/command"
                    return await asyncio.to_thread(_http_post, url, cmd, timeout + 5.0)
                except Exception as retry_e:
                    logging.warning("send_command retry failed: %s", retry_e)
                    return {"success": False, "error": str(retry_e)}
            logging.warning("send_command %s failed: %s", action, e)
            return {"success": False, "error": str(e)}
