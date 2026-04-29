# Packaging & Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the repo into a proper Python package under `src/superproductivity_mcp/` and publish it to PyPI so end users can run the MCP server with `uvx superproductivity-mcp`.

**Architecture:** Move `mcp_server.py` into `src/superproductivity_mcp/server.py` with a sync `main()` entry point. Update `pyproject.toml` to build a proper wheel. Add a GH Actions publish workflow triggered on `v*` tags. Delete legacy setup scripts.

**Tech Stack:** Python 3.10+, uv, hatchling (build backend), PyPI, GitHub Actions

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `src/superproductivity_mcp/__init__.py` | Package marker + `__version__` |
| Create | `src/superproductivity_mcp/server.py` | `mcp_server.py` content, sync `main()` added |
| Create | `src/superproductivity_mcp/__main__.py` | `python -m superproductivity_mcp` support |
| Modify | `tests/test_mcp_logic.py` | Update imports from `mcp_server` → `superproductivity_mcp.server` |
| Modify | `pyproject.toml` | `package=true`, build-system, hatch target, scripts entry point |
| Create | `.github/workflows/publish.yml` | PyPI publish on `v*` tag push |
| Modify | `README.md` | Replace manual install instructions with `uvx` config snippet |
| Delete | `mcp_server.py` | Replaced by `src/superproductivity_mcp/server.py` |
| Delete | `setup.sh` | Legacy; superseded by uvx |
| Delete | `setup.bat` | Legacy; superseded by uvx |
| Delete | `merge_config.py` | Used only by setup.bat |

---

## Task 1: Update test imports (TDD — break first)

**Files:**
- Modify: `tests/test_mcp_logic.py`

- [ ] **Step 1: Replace all `mcp_server` import references in the test file**

Open `tests/test_mcp_logic.py`. The file has these four import lines (lines 4, 6, 36, 56, 78, 123):

```python
# Line 4 — remove sys.path manipulation entirely, replace with:
from superproductivity_mcp.server import parse_duration, today_str

# Line 36 — replace with:
from superproductivity_mcp.server import parse_due_day, parse_due_datetime

# Line 56 — replace with:
from superproductivity_mcp.server import merge_tag_ids

# Line 78 — replace with:
from superproductivity_mcp.server import apply_task_filters, today_str

# Line 123 — replace with:
from superproductivity_mcp.server import filter_completed_since
```

Remove lines 2-4 (the `sys.path.insert` block) entirely — they were a workaround for the flat-file layout and are no longer needed with a proper package.

Final top of file should look like:

```python
# tests/test_mcp_logic.py
from superproductivity_mcp.server import parse_duration, today_str
from superproductivity_mcp.server import parse_due_day, parse_due_datetime
from superproductivity_mcp.server import merge_tag_ids
from superproductivity_mcp.server import apply_task_filters
from superproductivity_mcp.server import filter_completed_since
```

- [ ] **Step 2: Run tests to confirm they fail with ModuleNotFoundError**

```bash
cd /Users/Ben.Elliot/repos/superproductivity-mcp
uv run pytest tests/ -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'superproductivity_mcp'`

---

## Task 2: Create the `src/superproductivity_mcp` package

**Files:**
- Create: `src/superproductivity_mcp/__init__.py`
- Create: `src/superproductivity_mcp/server.py`
- Create: `src/superproductivity_mcp/__main__.py`

- [ ] **Step 1: Create `src/superproductivity_mcp/__init__.py`**

```python
__version__ = "1.2.1"
```

- [ ] **Step 2: Create `src/superproductivity_mcp/server.py`**

Copy the full content of `mcp_server.py` into `server.py`, then make two changes at the bottom:

1. Rename `async def main()` to `async def _main()`
2. Replace the `if __name__` block with a sync `main()` that entry points can call:

The bottom of `server.py` should end with:

```python
async def _main():
    server = SuperProductivityMCPServer()
    await server.run()


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
```

Everything else in the file is identical to `mcp_server.py`.

- [ ] **Step 3: Create `src/superproductivity_mcp/__main__.py`**

```python
from superproductivity_mcp.server import main

main()
```

---

## Task 3: Update `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Replace `pyproject.toml` with the updated version**

```toml
[project]
name = "superproductivity-mcp"
version = "1.2.1"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0.0",
]

[project.scripts]
superproductivity-mcp = "superproductivity_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/superproductivity_mcp"]

[tool.uv]
package = true

[dependency-groups]
dev = [
    "pytest>=9.0.3",
]
```

- [ ] **Step 2: Re-sync the environment so uv installs the package in editable mode**

```bash
cd /Users/Ben.Elliot/repos/superproductivity-mcp
uv sync
```

Expected: uv resolves dependencies, installs hatchling as a build backend, and installs `superproductivity-mcp` in editable mode into `.venv`. No errors.

- [ ] **Step 3: Run tests to confirm they now pass**

```bash
uv run pytest tests/ -v
```

Expected: all existing tests pass. If any fail, the import path changes in Task 1 have a typo — fix before continuing.

- [ ] **Step 4: Smoke-test the entry point**

```bash
uv run superproductivity-mcp --help 2>&1 || uv run superproductivity-mcp &
sleep 1 && kill %1 2>/dev/null; echo "Entry point executed OK"
```

The server starts (it blocks on stdio), so we just confirm it launches without an import error. Expected output includes `Starting Super Productivity MCP Server...` in stderr before the kill.

- [ ] **Step 5: Commit**

```bash
git add src/ tests/test_mcp_logic.py pyproject.toml uv.lock
git commit -m "feat: restructure into src layout and package for PyPI distribution"
```

---

## Task 4: Delete legacy files

**Files:**
- Delete: `mcp_server.py`, `setup.sh`, `setup.bat`, `merge_config.py`

- [ ] **Step 1: Delete the legacy files**

```bash
cd /Users/Ben.Elliot/repos/superproductivity-mcp
rm mcp_server.py setup.sh setup.bat merge_config.py
```

- [ ] **Step 2: Confirm tests still pass**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (they no longer depend on `mcp_server.py`).

- [ ] **Step 3: Commit**

```bash
git add -u
git commit -m "chore: remove legacy setup scripts and flat mcp_server.py"
```

---

## Task 5: Add PyPI publish workflow

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Create `.github/workflows/publish.yml`**

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # required for trusted publishing

    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true

      - name: Build package
        run: uv build

      - name: Publish to PyPI
        run: uv publish
        env:
          UV_PUBLISH_TOKEN: ${{ secrets.UV_PUBLISH_TOKEN }}
```

**Note on PyPI setup (do before first release):**
1. Create a PyPI account at pypi.org if you don't have one
2. Create an API token scoped to the `superproductivity-mcp` project
3. Add it as a GitHub Actions secret named `UV_PUBLISH_TOKEN` in repo Settings → Secrets → Actions

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add PyPI publish workflow on v* tag push"
```

---

## Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the Installation section**

Find the `## Installation` section. Remove the `### Automatic Setup` subsection (the setup.bat/setup.sh instructions) entirely.

Replace `### Manual Setup` with a new `### Setup` section:

```markdown
## Installation

### 1. Install the MCP server

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "super-productivity": {
      "command": "uvx",
      "args": ["superproductivity-mcp"]
    }
  }
}
```

`uvx` fetches the latest version from PyPI automatically — no Python install or repo clone needed. Requires [uv](https://docs.astral.sh/uv/) to be installed (`brew install uv`).

### 2. Install the plugin

- Open Super Productivity → Settings → Plugins
- Click "Upload Plugin"
- Select the `plugin.zip` from the [latest release](https://github.com/johannesjo/super-productivity/releases)

### 3. Restart Claude Desktop
```

- [ ] **Step 2: Update the `### Local Environment` / `### Running the Server` section**

The `.mcp.json` workflow is still valid for local dev. Keep the `Local Environment` section. Update `Running the Server` to:

```markdown
### Running the Server

```bash
uv run superproductivity-mcp
```

Or let Claude Code pick it up automatically via `.mcp.json`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: update install instructions to use uvx"
```

---

## Self-Review Checklist

- [x] Spec coverage: directory restructure ✓, pyproject.toml changes ✓, publish workflow ✓, README update ✓, legacy file deletion ✓
- [x] No TBDs or placeholders
- [x] `main()` is sync in Task 2, matches `[project.scripts]` entry point in Task 3
- [x] Test imports updated in Task 1 match module path `superproductivity_mcp.server` created in Task 2
- [x] `_main()` rename in Task 2 is consistent — only one reference site at the bottom of server.py
- [x] PyPI setup note included so the workflow doesn't silently fail on first push
