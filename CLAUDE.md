# Superproductivity MCP — Contributor Notes

## First-time setup

```bash
mise trust   # required once per clone — allows mise to auto-source .env
cp .env.example .env
```

## Dev hot-reload

Add to `.env`:
```
SP_MCP_DEV=1
SP_MCP_SOURCE_DIR=/absolute/path/to/this/repo
```

Then use the `reload_mcp` tool in Claude Code to hot-reload after source changes — no session restart needed.

## Tests

```bash
uv run pytest tests/ -v
```
