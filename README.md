# superproductivity-mcp

Bridge between [Super Productivity](https://github.com/johannesjo/super-productivity/) and Claude Desktop via the Model Context Protocol (MCP). Lets Claude create, update, and query tasks, projects, and tags directly in Super Productivity.

> **Backup your Super Productivity data before use.**

---

## Requirements

- Super Productivity 14.0.0 or higher
- Claude Desktop
- [uv](https://docs.astral.sh/uv/) (`brew install uv` on macOS)

---

## Installation

### 1. Install the MCP server

Add to your Claude Desktop config:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

`uvx` fetches the latest version from PyPI automatically — no Python install or repo clone required.

### 2. Install the plugin

Download `superproductivity-mcp-plugin-v<version>.zip` from the [latest release](https://github.com/ben-elliot-nice/superproductivity-mcp/releases/latest), then in Super Productivity:

**Settings → Plugins → Upload Plugin**

The plugin dashboard includes a **Claude Desktop** card with a copy-pastable config snippet pinned to your installed plugin version.

### 3. Restart Claude Desktop

---

## Usage

### Tasks

```
Create a task to review the quarterly budget #finance
Show me all my open tasks
Mark the budget review task as complete
Update the task 'Meeting prep' with notes about the agenda
```

### Subtasks

```
Create subtasks under 'Website Redesign': design mockups, build frontend, write tests
```

### Projects & Tags

```
Create a new project called 'Website Redesign'
Show me all tasks in the Infrastructure project
Get all tags
```

### Scheduling

```
Create a task 'Send invoice' due tomorrow with a 30 minute estimate
Show me everything due this week
```

---

## Tools

| Tool | Description |
|------|-------------|
| `get_tasks` | Fetch tasks — filter by project, tag, date, search, today |
| `create_task` | Create a task with optional project, tags, subtask nesting, due date, time estimate |
| `create_tasks` | Batch create multiple tasks in one round trip |
| `update_task` | Update title, notes, tags, time, due date, done state |
| `complete_task` | Mark a task done |
| `get_subtasks` | Get subtasks of a parent task by partial name |
| `get_tasks_by_tag` | Filter tasks by partial tag name |
| `get_completed_tasks` | Completed tasks, optionally filtered by recency |
| `convert_to_subtask` | Move a task under a parent |
| `get_projects` | List all projects |
| `create_project` | Create a project |
| `get_tags` | List all tags |
| `create_tag` | Create a tag |
| `show_notification` | Show a notification in Super Productivity |
| `debug_directories` | Show MCP data directory paths |
| `explain` | Get usage hints for tools, filters, or scheduling syntax |

---

## Communication

The plugin uses file-based IPC. Commands and responses are exchanged through:

| Platform | Path |
|----------|------|
| macOS / Linux | `~/.local/share/super-productivity-mcp/` |
| Windows | `%APPDATA%\super-productivity-mcp\` |

---

## Pinning to a plugin version

The plugin dashboard shows a copy-pastable config with the exact version pinned:

```json
{
  "mcpServers": {
    "super-productivity": {
      "command": "uvx",
      "args": ["superproductivity-mcp==1.3.0"]
    }
  }
}
```

This ensures the MCP server version matches your installed plugin exactly.

---

## Contributing

Pull requests welcome. Open an issue first for significant changes.

### Local setup

Requires [mise](https://mise.jdx.dev/) and [uv](https://docs.astral.sh/uv/):

```bash
brew install mise uv
mise install       # installs Python runtime
uv sync            # installs dependencies into .venv
```

Copy `.mcp.json.example` to `.mcp.json` and update the path:

```bash
cp .mcp.json.example .mcp.json
```

### Running locally

```bash
uv run superproductivity-mcp
```

Or let Claude Code pick it up via `.mcp.json`.

### Running tests

```bash
uv run pytest
```

### Repo structure

```
src/superproductivity_mcp/   # MCP server Python package
  server.py                  # All tool logic
  __init__.py                # Version
  __main__.py                # Entry point for python -m
plugin/                      # Super Productivity plugin
  plugin.js                  # Plugin logic
  index.html                 # Dashboard UI
  manifest.json              # Plugin manifest
build-plugin.sh              # Builds the plugin zip (injects version into UI)
```

### Branching

- `main` — stable releases, protected (PR + CI gates required)
- `dev` — integration branch, direct push allowed

Pre-releases publish to PyPI automatically on `[publish]` commits or pre-release tags from `dev`.

---

## Troubleshooting

**Plugin not loading**
- Super Productivity 14.0.0+ required
- Plugin permissions must include `nodeExecution`

**Commands not working**
- Verify both the plugin and MCP server are running
- Check `mcp_server.log` in the data directory
- Check the plugin dashboard for connection status and activity logs
