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
            cmds = []
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                cmds = json.loads(
                    urllib.request.urlopen(f"http://localhost:{port}/commands", timeout=5).read()
                )
                if cmds:
                    break
                time.sleep(0.01)
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
        assert _get(port, "/status")["active_sessions"] == 0

    asyncio.run(_run())
    d.stop()
