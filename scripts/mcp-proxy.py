#!/usr/bin/env python3
"""
Mode-aware MCP proxy for superproductivity-mcp.

Claude Code connects once via stdio; the inner server can be reloaded
without disconnecting the session (dev mode) or upgraded from degraded
to prod when .env appears (two-pass transition).

Modes (detected from .env on every spawn):
  degraded  No .env found          → returns setup guidance from all tools
  prod      .env present           → uvx superproductivity-mcp
  dev       SP_MCP_DEV=1 + source  → uv run from local source; reload_mcp tool

Credential loading is anchored to Path.cwd()/.env — never __file__.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────
_PKG = "superproductivity-mcp"
_MOD = "superproductivity_mcp.server"
_DEV_FLAG = "SP_MCP_DEV"
_SRC_KEY = "SP_MCP_SOURCE_DIR"
_RELOAD_TOOL = "reload_mcp"

_RELOAD_DEF = {
    "name": _RELOAD_TOOL,
    "description": "Hot-reload the MCP server from local source without disconnecting Claude Code.",
    "inputSchema": {"type": "object", "properties": {}},
}
_SETUP_TOOL = {
    "name": "setup_mcp",
    "description": "Get Superproductivity MCP setup instructions (degraded mode — .env not found).",
    "inputSchema": {"type": "object", "properties": {}},
}
_SETUP_TEXT = (
    "Superproductivity MCP is in degraded mode — no .env file found in the working directory.\n\n"
    "To activate:\n"
    "  cp .env.example .env\n\n"
    "For dev hot-reload, also add to .env:\n"
    "  SP_MCP_DEV=1\n"
    "  SP_MCP_SOURCE_DIR=/absolute/path/to/this/repo\n\n"
    "After creating .env, call any MCP tool — the server upgrades automatically (no restart needed)."
)


# ── .env loading ─────────────────────────────────────────────────────────────
def _load_env() -> dict:
    p = Path.cwd() / ".env"
    if not p.exists():
        return {}
    result: dict = {}
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        result[k.strip()] = v.strip().strip("\"'")
    return result


def _detect_mode(env: dict) -> str:
    if not (Path.cwd() / ".env").exists():
        return "degraded"
    if env.get(_DEV_FLAG) == "1" and env.get(_SRC_KEY):
        return "dev"
    return "prod"


# ── Child management ──────────────────────────────────────────────────────────
def _spawn(mode: str, env: dict) -> subprocess.Popen:
    if mode == "dev":
        cmd = ["uv", "run", "--directory", env[_SRC_KEY], "python", "-m", _MOD]
    else:
        cmd = ["uvx", _PKG]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, **env},
    )


def _kill(child: "subprocess.Popen | None") -> None:
    if child is None:
        return
    for fn in (lambda: child.stdin.close(),
               lambda: child.terminate(),
               lambda: child.wait(timeout=3)):
        try:
            fn()
        except Exception:
            pass


def _handshake(child: subprocess.Popen, init_params: dict) -> None:
    """Send initialize + notifications/initialized to freshly-spawned child.
    Reads and discards the init response — Claude already has an ack from us."""
    init = {"jsonrpc": "2.0", "method": "initialize", "params": init_params, "id": "__proxy__"}
    child.stdin.write((json.dumps(init) + "\n").encode())
    child.stdin.flush()
    child.stdout.readline()  # consume init response before pump starts
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    child.stdin.write((json.dumps(notif) + "\n").encode())
    child.stdin.flush()


def _call_one(child: subprocess.Popen, raw: bytes) -> bytes:
    """Forward raw JSON-RPC message to child and read one response synchronously.
    Only safe before the pump thread is started."""
    child.stdin.write(raw)
    child.stdin.flush()
    return child.stdout.readline()


# ── stdout pump ───────────────────────────────────────────────────────────────
def _pump(child: subprocess.Popen, stop: threading.Event,
          inject_ids: set, lock: threading.Lock) -> None:
    """Forward child stdout to our stdout, injecting reload_mcp into tools/list responses."""
    for raw in child.stdout:
        if stop.is_set():
            break
        out = raw
        try:
            msg = json.loads(raw.decode())
            mid = msg.get("id")
            with lock:
                if mid is not None and mid in inject_ids:
                    inject_ids.discard(mid)
                    msg["result"]["tools"].append(_RELOAD_DEF)
                    out = (json.dumps(msg) + "\n").encode()
        except Exception:
            pass
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.flush()


# ── JSON-RPC output ───────────────────────────────────────────────────────────
def _out(obj: dict) -> None:
    sys.stdout.buffer.write((json.dumps(obj) + "\n").encode())
    sys.stdout.buffer.flush()


def _ok(mid: "int | str | None", result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    env = _load_env()
    mode = _detect_mode(env)
    init_params: dict = {}

    child: "subprocess.Popen | None" = None
    stop_ev = threading.Event()
    inject_ids: set = set()
    inject_lock = threading.Lock()
    pump_thread: "threading.Thread | None" = None

    def start_pump() -> None:
        nonlocal pump_thread, stop_ev
        stop_ev = threading.Event()
        pump_thread = threading.Thread(
            target=_pump, args=(child, stop_ev, inject_ids, inject_lock), daemon=True
        )
        pump_thread.start()

    if mode != "degraded":
        child = _spawn(mode, env)
        start_pump()

    for raw in sys.stdin.buffer:
        try:
            msg = json.loads(raw.decode())
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        mid = msg.get("id")
        is_notif = mid is None

        # Track init params so we can re-handshake after reload/upgrade
        if method == "initialize":
            init_params = msg.get("params", {})

        # ── degraded mode ──────────────────────────────────────────────────
        if mode == "degraded":
            if method == "initialize":
                proto = init_params.get("protocolVersion", "2024-11-05")
                _out(_ok(mid, {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": _PKG, "version": "degraded"},
                }))
            elif method == "tools/list":
                _out(_ok(mid, {"tools": [_SETUP_TOOL]}))
            elif method == "tools/call":
                # Two-pass: check whether .env appeared since last check
                env = _load_env()
                new_mode = _detect_mode(env)
                if new_mode != "degraded":
                    mode = new_mode
                    child = _spawn(mode, env)
                    _handshake(child, init_params)
                    resp = _call_one(child, raw)
                    start_pump()
                    sys.stdout.buffer.write(resp)
                    sys.stdout.buffer.flush()
                else:
                    _out(_ok(mid, {"content": [{"type": "text", "text": _SETUP_TEXT}]}))
            elif not is_notif:
                _out(_ok(mid, {}))
            continue

        # ── dev mode: intercept tools/list to inject reload_mcp ───────────
        if mode == "dev" and method == "tools/list" and not is_notif:
            with inject_lock:
                inject_ids.add(mid)

        # ── dev mode: handle reload_mcp ────────────────────────────────────
        if mode == "dev" and method == "tools/call":
            name = msg.get("params", {}).get("name")
            if name == _RELOAD_TOOL:
                stop_ev.set()
                _kill(child)
                env = _load_env()
                mode = _detect_mode(env)
                child = _spawn(mode, env)
                with inject_lock:
                    inject_ids.clear()
                _handshake(child, init_params)
                start_pump()
                if not is_notif:
                    _out(_ok(mid, {"content": [{"type": "text", "text": "MCP server reloaded."}]}))
                continue

        # ── forward to child ──────────────────────────────────────────────
        if child is not None:
            try:
                child.stdin.write(raw)
                child.stdin.flush()
            except (BrokenPipeError, OSError):
                # Child died unexpectedly — respawn and replay
                stop_ev.set()
                _kill(child)
                env = _load_env()
                mode = _detect_mode(env)
                child = _spawn(mode, env)
                with inject_lock:
                    inject_ids.clear()
                _handshake(child, init_params)
                start_pump()
                try:
                    child.stdin.write(raw)
                    child.stdin.flush()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
