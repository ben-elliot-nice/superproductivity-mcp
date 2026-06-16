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
