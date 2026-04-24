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
- Python 3.8 or higher

## Installation

### Automatic Setup

**Windows:**
1. Clone this repo
2. Run `setup.bat`
3. Follow the prompts

**Linux/Mac UNTESTED:**
1. Clone this repo
2. Run `chmod +x setup.sh && ./setup.sh`
3. Follow the prompts

The setup scripts will preserve any existing MCP servers in your Claude Desktop configuration.

You'll still have to install the plugin.zip manually in Super Productivity in settings->plugins.

Once that's done, restart claude (and Super Prod for good measure) and you should be able to access your files

### Manual Setup

1. **Install Python dependencies:**
   ```bash
   pip install mcp
   ```

2. **Set up MCP server:**
   Copy `mcp_server.py` to your data directory:
   - Windows: `%APPDATA%\super-productivity-mcp\`
   - Linux: `~/.local/share/super-productivity-mcp/`
   - macOS: `~/Library/Application Support/super-productivity-mcp/`

3. **Configure Claude Desktop:**
   Edit Claude's config file and add to `mcpServers`:
   ```json
   "super-productivity": {
     "command": "python3",
     "args": ["/path/to/mcp_server.py"]
   }
   ```

4. **Install the plugin:**
   - Open Super Productivity → Settings → Plugins
   - Click "Upload Plugin"
   - Select `plugin.js`

5. **Restart Claude Desktop**

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
uv run mcp_server.py
```

Or let Claude Code pick it up automatically via `.mcp.json`.

### Repo Structure

```
mcp_server.py       # MCP server (Python, run with uv)
plugin/             # Super Productivity plugin files
  plugin.js         # Main plugin JS (install via SP Settings → Plugins)
  plugin.zip        # Pre-packaged zip for convenience
  index.html        # Plugin UI
  manifest.json     # Plugin manifest
setup.sh / .bat     # One-shot setup scripts for end users
merge_config.py     # Helper used by setup.bat to merge Claude Desktop config
```

## Troubleshooting

### Plugin Not Loading
- Check Super Productivity version (14.0.0+ required)
- Verify plugin permissions include `nodeExecution`

### Commands Not Working
- Verify both plugin and MCP server are running
- Check file permissions on communication directories
- Check `mcp_server.log` in the data directory
