# SP-MCP

Bridge between the amazing [Super Productivity](https://github.com/johannesjo/super-productivity/) app and MCP (Model Context Protocol) servers for Claude Desktop integration.

This MCP and plugin allows Claude Desktop to directly interact with Super Productivity through the MCP protocol. Create update,tasks, manage projects and tags, and get information from Super Productivity.

Make sure to backup your Super Productivity before using in case of data loss. I've provided a plugin.zip for convenience but feel free to make your own from the files.

(Can't delete tasks right now (but it can mark them as done))

## Demo

https://github.com/user-attachments/assets/cc118173-023f-48cb-8213-427027e475af


## Requirements

- Super Productivity 14.0.0 or higher
- Claude Desktop
- Python 3.10 or higher

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

`uvx` fetches the latest version from PyPI automatically — no Python install or repo clone needed. Requires [uv](https://docs.astral.sh/uv/) to be installed (`brew install uv` on macOS).

### 2. Install the plugin

- Open Super Productivity → Settings → Plugins
- Click "Upload Plugin"
- Select the `plugin.zip` from the [latest GitHub release](https://github.com/Ben-Elliot/superproductivity-mcp/releases)

### 3. Restart Claude Desktop

## Usage

### Creating Tasks
```
"Create a task to review the quarterly budget #finance +work"
```

### Task Management
```
"Show me all my tasks"
"Mark the budget review task as complete"
"Update the task 'Meeting prep' with notes about the agenda"
```

### Project and Tag Management
```
"Create a new project called 'Website Redesign'"
"Show me all projects"
"Get all tags"
```

## Dashboard

Access the SP-MCP dashboard from the menu. The dashboard shows:
- Real-time statistics
- Connection status
- Activity logs
- Settings (polling frequency: default 2 seconds)

## Communication

The plugin uses file-based communication through:
- Windows: `%APPDATA%\super-productivity-mcp\`
- Linux: `~/.local/share/super-productivity-mcp/`
- macOS: `~/Library/Application Support/super-productivity-mcp/`

Commands are exchanged through `plugin_commands/` and `plugin_responses/` directories.

## Contributing

Pull requests are welcome. Please open an issue first if you're planning a significant change.

### Local Environment

This project uses [mise](https://mise.jdx.dev/) to manage the Python runtime. Install it first:

```bash
brew install mise
```

Then from the repo root, let mise install the correct Python version:

```bash
mise install
```

Dependencies are managed with [uv](https://docs.astral.sh/uv/). Install it, then sync the project:

```bash
brew install uv
uv sync
```

This creates a `.venv` and installs all dependencies from `uv.lock`.

### MCP Config

Copy `.mcp.json.example` to `.mcp.json` and update the paths to point to your local clone:

```bash
cp .mcp.json.example .mcp.json
```

`.mcp.json` is gitignored — it contains machine-specific absolute paths and should not be committed.

### Running the Server

```bash
uv run superproductivity-mcp
```

Or let Claude Code pick it up automatically via `.mcp.json`.

### Repo Structure

```
src/superproductivity_mcp/  # MCP server Python package (install via uvx)
plugin/                     # Super Productivity plugin files
  plugin.js                 # Main plugin JS (install via SP Settings → Plugins)
  plugin.zip                # Pre-packaged zip for convenience
  index.html                # Plugin UI
  manifest.json             # Plugin manifest
```

## Troubleshooting

### Plugin Not Loading
- Check Super Productivity version (14.0.0+ required)
- Verify plugin permissions include `nodeExecution`

### Commands Not Working
- Verify both plugin and MCP server are running
- Check file permissions on communication directories
- Check `mcp_server.log` in the data directory
