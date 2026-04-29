# Packaging & Distribution Design

**Date:** 2026-04-29  
**Status:** Approved

## Goal

Make the MCP server distributable via PyPI so end users can run it with `uvx superproductivity-mcp` — no repo clone, no manual Python setup. Simultaneously restructure the repo into an obvious top-level split between the SP plugin and the Python server.

## Out of Scope

- Plugin distribution (already handled via GitHub Actions zip workflow — unchanged)
- Changes to MCP server logic or tool surface
- Docker or Homebrew distribution (not worth the complexity given uvx solves the problem cleanly)

---

## Directory Structure

**Before:**
```
mcp_server.py
plugin/
tests/
setup.sh
setup.bat
merge_config.py
pyproject.toml
uv.lock
```

**After:**
```
src/
  superproductivity_mcp/
    __init__.py       # version string only
    server.py         # mcp_server.py logic moved here, unchanged
    __main__.py       # calls main() so `python -m superproductivity_mcp` works
plugin/               # unchanged
tests/                # unchanged, import path updated
pyproject.toml        # updated (see below)
uv.lock               # regenerated
```

**Removed:** `mcp_server.py`, `setup.sh`, `setup.bat`, `merge_config.py`

---

## `pyproject.toml` Changes

```toml
[tool.uv]
package = true    # was false

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/superproductivity_mcp"]

[project.scripts]
superproductivity-mcp = "superproductivity_mcp.server:main"
```

The `mcp_server.py` top-level `asyncio.run(...)` call is wrapped in a `main()` function in `server.py`. `__main__.py` calls `main()`.

---

## GitHub Actions: PyPI Publish Workflow

New workflow file: `.github/workflows/publish.yml`

- Trigger: push of a tag matching `v*`
- Steps: checkout → `uv build` → `uv publish` (using `UV_PUBLISH_TOKEN` secret)
- The existing plugin zip workflow is separate and untouched

---

## End-User Experience After This Change

Claude Desktop config:
```json
"super-productivity": {
  "command": "uvx",
  "args": ["superproductivity-mcp"]
}
```

`uvx` fetches the latest published version from PyPI and runs it — no Python install, no repo clone, no path configuration.

---

## README Updates

The Installation section needs updating:
- Remove manual `pip install mcp` + copy-file instructions
- Replace with `uvx` Claude Desktop config snippet
- Keep the plugin install steps (unchanged)
- Remove references to `setup.bat` / `setup.sh`

---

## Naming Rationale

`mcp/` as a top-level directory was considered but rejected: it would shadow the `mcp` PyPI package that the server depends on, breaking imports at runtime. `src/superproductivity_mcp/` avoids the conflict and follows modern Python src-layout conventions.
