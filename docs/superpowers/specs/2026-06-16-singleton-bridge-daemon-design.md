# Singleton Bridge Daemon Design

**Date:** 2026-06-16  
**Status:** Approved  
**Problem:** Multiple Claude sessions each spawn their own `superproductivity-mcp` process and their own `PluginBridge` HTTP server. The SP plugin connects to the first port it finds and ignores the rest — commands from other sessions time out.  
**Solution:** Extract the bridge into a long-lived singleton daemon process. All MCP sessions register with it as clients. The SP plugin is unchanged.

---

## Architecture

```
Claude Session 1              Claude Session 2
superproductivity-mcp         superproductivity-mcp
(PluginBridgeClient)          (PluginBridgeClient)
        │                             │
        │  HTTP  POST /session/{id}/command
        │  HTTP  long-poll response   │
        └──────────────┬──────────────┘
                       │
             ┌─────────▼──────────┐
             │   Bridge Daemon    │  port 27833, always
             │   (daemon.py)      │
             └─────────┬──────────┘
                       │  HTTP (plugin API — unchanged)
             ┌─────────▼──────────┐
             │   SP Plugin        │
             │   (plugin.js)      │
             └────────────────────┘
```

The daemon owns port 27833 permanently. MCP servers are clients. The plugin side is identical to v2.0.0.

---

## Components

### `src/superproductivity_mcp/daemon.py` — new

The singleton HTTP server. Exposes two sets of endpoints on the same port (27833):

#### Plugin-facing endpoints (unchanged from v2.0.0)

| Method | Path | Description |
|---|---|---|
| GET | `/status` | Health check. Now also returns `active_sessions` count |
| GET | `/commands` | Returns all queued commands, clears queue atomically |
| POST | `/response/{command_id}` | Plugin posts result; daemon routes to waiting session |
| GET | `/config` | Returns current config dict |
| POST | `/config` | Merges provided keys into config |
| POST | `/events` | No-op (hook events from plugin, acknowledged) |

#### Session-facing endpoints (new)

| Method | Path | Description |
|---|---|---|
| POST | `/session/register` | MCP server registers; returns `{"session_id": "<uuid>"}` |
| DELETE | `/session/{session_id}` | MCP server unregisters on clean shutdown |
| POST | `/session/{session_id}/command` | Submit command; **long-polls** up to 30s for plugin response |
| POST | `/shutdown` | Graceful daemon shutdown (optional, for tooling) |

#### Internal routing table

```python
# command_id → (session_id, threading.Event, response_dict)
_routing: Dict[str, Tuple[str, threading.Event, dict]] = {}
```

Flow:
1. MCP session POSTs command to `/session/{id}/command`
2. Daemon assigns `command_id`, writes to `_routing`, enqueues in plugin command queue
3. Handler thread waits on `Event` (timeout 30s)
4. Plugin GETs `/commands`, executes via PluginAPI, POSTs to `/response/{command_id}`
5. Daemon handler sets `Event`, stores response in `_routing` entry
6. Long-polling MCP session's handler returns the response
7. Routing entry is cleaned up

Thread safety: a single `threading.Lock` protects `_routing`, the command queue, and the session registry — same pattern as v2.0.0 `PluginBridge`.

#### Daemon lifecycle

- Daemon is started as a **detached subprocess** by the first MCP server that finds port 27833 unresponsive
- Daemon survives after its spawning MCP server exits (double-fork / `start_new_session=True`)
- Daemon has no auto-shutdown timer — it is lightweight (one thread, no polling loops) and stays running until the machine reboots or the user kills it
- On startup the daemon writes a PID file to `~/.local/share/super-productivity-mcp/bridge.pid` for tooling

---

### `src/superproductivity_mcp/bridge.py` — rewritten as client

`PluginBridge` → `PluginBridgeClient`. Public interface unchanged so `server.py` needs no changes beyond the import alias.

#### `start(loop)` → `start()`

- No longer async or loop-aware (no futures needed)
- Pings `GET /status` on 27833
  - If 200: daemon already running, proceed
  - If connection refused: spawn daemon via `subprocess.Popen(['superproductivity-mcp-bridge'], start_new_session=True)`, poll until ready (up to 3s, 100ms intervals)
- POSTs to `/session/register`, stores `session_id`

#### `send_command(action, timeout=30.0, **kwargs)`

```python
async def send_command(self, action, timeout=30.0, **kwargs):
    cmd = {"action": action, **kwargs}
    # POST to /session/{id}/command with a timeout — daemon long-polls
    response = await asyncio.to_thread(
        self._http_post_with_timeout,
        f"http://localhost:27833/session/{self.session_id}/command",
        cmd,
        timeout
    )
    return response
```

No `asyncio.Future`, no threading in the client — the blocking HTTP call runs in a thread pool via `asyncio.to_thread`.

#### `stop()`

- POSTs to `/session/{session_id}` DELETE to unregister
- No server to shut down

#### Port constants

`PORT_RANGE_START` / `PORT_RANGE_END` are removed from `bridge.py` — the daemon always owns 27833. The constants move to `daemon.py` (used only for the daemon's bind).

---

### `src/superproductivity_mcp/server.py` — minimal change

Replace:
```python
from superproductivity_mcp.bridge import PluginBridge
self._bridge = PluginBridge()
self._bridge.start(loop)
```
with:
```python
from superproductivity_mcp.bridge import PluginBridgeClient
self._bridge = PluginBridgeClient()
self._bridge.start()
```

`send_command` delegate and `debug_directories` update to reflect daemon URL. Everything else unchanged.

---

### `plugin/plugin.js` — no changes

Still probes ports 27833–27840. Daemon always owns 27833 so the probe succeeds immediately on the first port. All endpoints (`/commands`, `/response/{id}`, `/config`, `/events`) are identical.

---

### `pyproject.toml` — new entry point

```toml
[project.scripts]
superproductivity-mcp = "superproductivity_mcp.server:main"
superproductivity-mcp-bridge = "superproductivity_mcp.daemon:main"
```

Users can run the daemon manually (launchd, login item) if they prefer not to rely on auto-start.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Daemon not running when MCP server starts | Auto-spawn; if spawn fails, MCP server logs error and tools return `{"success": false, "error": "Bridge daemon unavailable"}` |
| Daemon dies mid-session | MCP server gets `ConnectionRefusedError` on next `send_command`; returns timeout error; does not crash |
| MCP server dies mid-command | Long-poll times out (30s); daemon cleans up routing entry; plugin response (if it arrives late) returns 404 and is discarded |
| Two MCP servers race to start daemon | Second spawn attempt hits a listening port immediately; `register` as normal; PID file is advisory only |
| Plugin disconnects | Commands queue up in daemon; plugin reconnects on next poll (2s); queued commands are served |

---

## Testing

- `tests/test_daemon.py` — unit tests for daemon HTTP endpoints using the same `_free_port()` + `urllib.request` pattern as `test_bridge.py`
- `tests/test_bridge_client.py` — unit tests for `PluginBridgeClient` with a mock daemon
- `tests/test_multi_session.py` — integration test: two `PluginBridgeClient` instances registered against one daemon; commands from each session route correctly

Existing `tests/test_bridge.py` and `tests/test_mcp_logic.py` updated to reflect renamed/removed symbols.

---

## What Does Not Change

- `plugin/plugin.js` — zero changes
- `plugin/manifest.json` — zero changes (plugin version bump handled separately)
- All MCP tool definitions in `server.py`
- All business logic (task filtering, tag resolution, etc.)
- The plugin's HTTP protocol (endpoint paths, request/response shapes)
