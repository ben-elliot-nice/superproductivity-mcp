# tests/test_bridge.py
import asyncio
import json
import socket
import urllib.request
from superproductivity_mcp.bridge import PluginBridge, PORT_RANGE_START, PORT_RANGE_END


def _free_port() -> int:
    """Return an ephemeral free port for test isolation."""
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://localhost:{port}{path}") as r:
        return json.loads(r.read())


def _post(port: int, path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_status():
    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = PluginBridge(port=port)
    bridge.start(loop)
    try:
        result = _get(port, "/status")
        assert result["status"] == "ok"
        assert result["port"] == port
    finally:
        bridge.stop()
        loop.close()


def test_commands_empty():
    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = PluginBridge(port=port)
    bridge.start(loop)
    try:
        assert _get(port, "/commands") == []
    finally:
        bridge.stop()
        loop.close()


def test_commands_enqueue_and_drain():
    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = PluginBridge(port=port)
    bridge.start(loop)
    try:
        with bridge._lock:
            bridge._queue.append({"id": "test_123", "action": "getTasks"})
        cmds = _get(port, "/commands")
        assert len(cmds) == 1
        assert cmds[0]["action"] == "getTasks"
        assert _get(port, "/commands") == []  # second GET sees empty queue
    finally:
        bridge.stop()
        loop.close()


def test_response_resolves_future():
    """Full round-trip: send_command → GET /commands → POST /response → future resolves."""
    results = {}

    async def _run():
        port = _free_port()
        bridge = PluginBridge(port=port)
        bridge.start(asyncio.get_running_loop())
        try:
            task = asyncio.create_task(bridge.send_command("getTasks"))
            await asyncio.sleep(0.1)

            cmds = _get(port, "/commands")
            assert len(cmds) == 1
            cmd_id = cmds[0]["id"]

            _post(port, f"/response/{cmd_id}", {"success": True, "result": [{"id": "t1"}]})

            result = await asyncio.wait_for(task, timeout=2.0)
            results["result"] = result
        finally:
            bridge.stop()

    asyncio.run(_run())
    assert results["result"]["success"] is True
    assert results["result"]["result"][0]["id"] == "t1"


def test_send_command_timeout():
    async def _run():
        port = _free_port()
        bridge = PluginBridge(port=port)
        bridge.start(asyncio.get_running_loop())
        try:
            result = await bridge.send_command("getTasks", timeout=0.1)
            assert result["success"] is False
            assert "Timeout" in result["error"]
        finally:
            bridge.stop()

    asyncio.run(_run())


def test_config_get_and_post():
    port = _free_port()
    loop = asyncio.new_event_loop()
    bridge = PluginBridge(port=port)
    bridge.start(loop)
    try:
        cfg = _get(port, "/config")
        assert cfg["commandCheckIntervalMs"] == 2000

        _post(port, "/config", {"commandCheckIntervalMs": 5000})
        assert _get(port, "/config")["commandCheckIntervalMs"] == 5000
    finally:
        bridge.stop()
        loop.close()


def test_port_range_constants_are_sane():
    assert PORT_RANGE_START < PORT_RANGE_END
    assert PORT_RANGE_END - PORT_RANGE_START >= 7  # at least 8 candidates


def test_auto_port_selection_skips_occupied_port():
    """PluginBridge(port=None) should skip a port already in use."""
    # Occupy the first port in the range
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    blocker.bind(("localhost", PORT_RANGE_START))
    blocker.listen(1)

    loop = asyncio.new_event_loop()
    bridge = PluginBridge()  # auto-select
    bridge.start(loop)
    try:
        assert bridge.port != PORT_RANGE_START
        result = _get(bridge.port, "/status")
        assert result["status"] == "ok"
    finally:
        bridge.stop()
        loop.close()
        blocker.close()
