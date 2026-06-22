# Bridge Hardening & Dev Tooling — Design Spec

**Date:** 2026-06-22  
**Target version:** 2.2.0  
**Branch:** `feat/bridge-hardening`

---

## Problem Statement

The v2.1.0 HTTP bridge works correctly under ideal conditions but fails ungracefully under five catalogued failure modes:

1. **Startup order** — plugin init scans ports 27833–27840 once at startup and throws if no bridge is found. If SP is open before the `superprod` session starts the bridge, the plugin is permanently broken until manually toggled off/on.
2. **Stale queue** — commands that time out (30 s) have their `_routing` entry cleaned up but remain in `_plugin_queue`. When the plugin later picks them up and tries to respond, it gets a 404 — the response is lost silently. *(Partially fixed in dev already.)*
3. **Bridge crash** — if the daemon dies mid-session, `bridge.py` has a stale `session_id` and every subsequent `send_command` fails with a connection error. No automatic recovery.
4. **Ghost sessions** — a crashed MCP server never calls `DELETE /session/{id}`. Sessions accumulate in the daemon forever.
5. **Dashboard not visible** — plugin panel disappeared in SP ≥ 18.10.0. Root cause confirmed: `isSkipMenuEntry: true` combined with a missing `icon.svg` in the zip. SP no longer surfaces iFrame plugins outside the main menu when the entry is suppressed.

Additionally, the dev workflow lacks a hot-reload loop — every source change requires restarting the Claude Code session.

---

## Architecture Overview

No structural changes to the three-tier model. All changes are hardening and tooling within the existing layers:

```
Claude Code  ←stdio→  scripts/mcp-proxy.py (NEW: mode-aware orchestrator)
                              ↓
                       MCP Server (server.py)
                              ↓  HTTP
                       Bridge Daemon (daemon.py)  ←  heartbeat thread (NEW)
                              ↓  HTTP polling
                       SP Plugin (plugin.js)      ←  reconnect loop (NEW)
```

---

## Section 1 — Plugin Reconnect (plugin.js)

### Connection state machine

The plugin tracks one of four states that drive the dashboard status card:

| State | Meaning |
|---|---|
| `connecting` | Initial probe or mid-retry (never previously connected) |
| `connected` | Bridge found, polling active |
| `reconnecting` | Mid-retry after a previously-established connection was lost |
| `failed` | 5-minute cap reached — manual action required |

### Exponential backoff

On any failure to find the bridge (startup or mid-session drop):

- Retry delays: 2 s → 4 s → 8 s → 16 s → 30 s (cap per attempt)
- Total retry window: 5 minutes, then transition to `failed`
- On reconnect after a prior connection: try the last-known port first before scanning 27833–27840

### Polling drop detection

`processNewCommands()` already catches fetch errors. After **3 consecutive poll failures** it transitions `connected → reconnecting` and re-enters the backoff loop.

### Reconnect button

Visible in the dashboard status card whenever state is `reconnecting` or `failed`. Clicking it resets the backoff counter and elapsed time, then re-enters the backoff loop from the beginning.

### UI status messages

| State | Message |
|---|---|
| `connecting` | "Connecting… (attempt N)" |
| `connected` | "✅ Connected and ready" |
| `reconnecting` | "⚠️ Reconnecting… retry in Xs" |
| `failed` | "❌ Bridge not found — click Reconnect" |

---

## Section 2 — MCP Server Reconnect + Session TTL (bridge.py / daemon.py)

### bridge.py — reconnect on send_command failure

`send_command` gains a single automatic retry with full re-establishment before returning an error:

1. Catch HTTP/connection error on the command POST
2. Also treat a `404` response on the session command endpoint as a dead session
3. Call `_ensure_daemon()` — spawns daemon if gone, no-op if alive
4. `POST /session/register` → store new `session_id`
5. Retry the original command once with the new session
6. If the retry also fails, return `{"success": False, "error": ...}` as today

### bridge.py — heartbeat thread

After `start()`, spin up a daemon thread that runs every **30 seconds**:

- `POST /session/{id}/heartbeat`
- On `404` or connection error: trigger the same re-establish flow (steps 3–4 above), then resume heartbeating with the new session

### daemon.py — session TTL reaper

New endpoint: `POST /session/{id}/heartbeat` — updates `last_seen` timestamp for the session.

A reaper thread wakes every **60 seconds** and evicts any session whose `last_seen` is older than **90 seconds**. The 3× margin (30 s heartbeat, 90 s TTL) is intentional — a single missed heartbeat does not evict the session. On eviction:
- Remove from `_sessions`
- Remove any queued commands for that session from `_plugin_queue` (same pattern as the stale-queue fix)
- Any in-flight `event.wait()` for that session's commands will hit their 30 s timeout naturally

### Stale queue fix (already applied, formally spec'd here)

In the existing command POST handler, after `event.wait()` times out and before returning the timeout error, the command is removed from `_plugin_queue`:

```python
with self._d._lock:
    self._d._routing.pop(cmd_id, None)
    self._d._plugin_queue[:] = [
        c for c in self._d._plugin_queue if c.get("id") != cmd_id
    ]
```

---

## Section 3 — Dashboard Visibility (manifest.json / build-plugin.sh)

### Root cause

Two issues confirmed by live test:

1. `"isSkipMenuEntry": true` — SP ≥ 18.10.0 no longer surfaces iFrame plugin content outside the main menu when the entry is suppressed. Fix: set to `false`.
2. `icon.svg` missing from the zip — SP rendered the manifest path string as fallback text. Fix: add `icon.svg` to the plugin directory and include it in `build-plugin.sh`.

### Changes

- `manifest.json`: `"isSkipMenuEntry": false`
- `plugin/icon.svg`: add a custom SVG icon (placeholder: sourced from b0x42 repo for testing; **to be replaced with original artwork before release**)
- `build-plugin.sh`: add `icon.svg` to the `zip` command

---

## Section 4 — Dev Tooling / git-mcp (scripts / config files)

### Mode-aware orchestrator

`scripts/mcp-proxy.py` is a long-lived stdio process that Claude Code connects to once. It detects its operating mode from `.env` on every inner-server spawn:

| Mode | Condition | Inner server command |
|---|---|---|
| `degraded` | No `.env` present | Returns setup guidance from all tools |
| `prod` | `.env` present, no dev flag | `uvx superproductivity-mcp` |
| `dev` | `SP_MCP_DEV=1` + `SP_MCP_SOURCE_DIR` set | `uv run` from local source; exposes `reload_mcp` tool |

Credential loading is anchored to `Path.cwd()/.env` — not the shell environment, not `__file__`. On every spawn, env vars are stripped and reloaded from `.env` explicitly.

**Two-pass transition:** if a tool call arrives in `degraded` mode and `.env` now exists, the orchestrator kills the degraded child, respawns in `prod`, replays the pending call, and returns real output — no Claude restart required.

### Hot-reload dev loop

```
1. Add SP_MCP_DEV=1 and SP_MCP_SOURCE_DIR=/path/to/repo to .env
2. Edit source files
3. Call reload_mcp tool in Claude Code
4. Next tool call runs updated source — session uninterrupted
```

### Files added / changed

| File | Action | Notes |
|---|---|---|
| `.mcp.json` | Replace | `uvx` → `scripts/mcp-proxy.py` |
| `scripts/mcp-proxy.py` | Add | Mode-aware orchestrator (adapted from git-mcp template) |
| `mise.toml` | Add | `_.file = ".env"` — auto-sources `.env` on `cd` |
| `.env.example` | Add | Documents dev mode vars; committed, `.env` gitignored |
| `CLAUDE.md` | Update | Add `mise trust` note for contributors |

`.env.example` content:
```
# Uncomment to enable dev mode (hot-reload from local source)
# SP_MCP_DEV=1
# SP_MCP_SOURCE_DIR=/path/to/superproductivity-mcp
```

---

## Version & Changelog

**Version:** 2.2.0

Changelog entry:
```
"2.2.0": "Bridge hardening — plugin exponential backoff reconnect, MCP server auto-reconnect,
          daemon session TTL, dashboard visibility fix, dev hot-reload proxy"
```

---

## Testing

### Existing tests

`tests/test_daemon.py` covers the stale-queue fix and session routing. Update to cover:
- Heartbeat endpoint updates `last_seen`
- Reaper evicts sessions older than 90 s and cleans their queued commands
- `send_command` retries once on connection failure and on 404 session response

### Manual verification checklist

- [ ] Start SP before starting the `superprod` session — plugin reconnects automatically
- [ ] Kill bridge daemon mid-session — MCP server recovers on next tool call
- [ ] Leave session idle 2+ minutes — ghost session is reaped; no error on reconnect
- [ ] Dashboard panel visible in SP left menu with correct icon
- [ ] `reload_mcp` tool hot-reloads server without disconnecting Claude Code session
- [ ] **Replace `icon.svg` with original artwork before tagging v2.2.0** — current file is sourced from b0x42 repo (test only)
