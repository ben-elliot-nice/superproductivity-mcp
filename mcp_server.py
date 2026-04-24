#!/usr/bin/env python3
# MCP Server for Super Productivity Integration

import asyncio
import json
import logging
import os
import re
import sys
import time as _time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

USAGE_GUIDE = """
# Super Productivity MCP — Usage Guide

## Tool Hierarchy

### Primary (use these most)
- **get_tasks** — filtered task lookup with inline tag labels. Start here.
- **update_task** — modify any task property (title, notes, tags, done, estimates)
- **create_task** — add a new task or subtask

### Discovery (use once to get IDs)
- **get_projects** — get project IDs and titles; use project_id to scope get_tasks
- **get_tags** — rarely needed; tag labels are resolved inline in get_tasks results

### Actions
- **complete_and_archive_task** — mark a task done (true deletion not supported)
- **show_notification** — push a notification into Super Productivity

### Setup / Maintenance
- **create_project**, **create_tag** — one-time setup
- **debug_directories** — troubleshoot IPC if commands aren't responding

## get_tasks Filter Guide

Default behaviour: returns open, top-level tasks only (no subtasks, no done tasks).
Scope with filters before committing to a large fetch.

| Param            | Type    | Default | Description                              |
|------------------|---------|---------|------------------------------------------|
| project_id       | string  | —       | Scope to one project                     |
| task_id          | string  | —       | Single task lookup by ID                 |
| search           | string  | —       | Case-insensitive title substring match   |
| include_done     | boolean | false   | Include completed tasks                  |
| include_subtasks | boolean | false   | Include subtasks (top-level only default)|

Tag IDs are automatically resolved to labels in every response — no separate get_tags call needed.

## Recommended Discovery Pattern
1. `get_projects` → find the right project_id
2. `get_tasks(project_id=...)` → list open top-level tasks, note IDs and tags
3. `get_tasks(task_id=...)` → drill into a specific task with full detail
4. `update_task` / `create_task` → act on what you found
""".strip()


def today_str() -> str:
    """Return today's date as YYYY-MM-DD."""
    return date.today().isoformat()


def parse_duration(value) -> Optional[int]:
    """Parse duration to milliseconds.

    Accepts:
      int/float  — treated as ms already
      "2h"       → 7200000
      "30m"      → 1800000
      "2h30m"    → 9000000
      "1.5h"     → 5400000
    Returns None for None input.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    # compound: 2h30m
    m = re.fullmatch(r'(\d+(?:\.\d+)?)h(\d+)m', s)
    if m:
        return int((float(m.group(1)) * 60 + int(m.group(2))) * 60 * 1000)
    # hours: 2h or 1.5h
    m = re.fullmatch(r'(\d+(?:\.\d+)?)h', s)
    if m:
        return int(float(m.group(1)) * 3600 * 1000)
    # minutes: 30m
    m = re.fullmatch(r'(\d+(?:\.\d+)?)m', s)
    if m:
        return int(float(m.group(1)) * 60 * 1000)
    # plain number string
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def parse_due_day(value) -> Optional[str]:
    """Accept YYYY-MM-DD string or None."""
    return value if value else None


def parse_due_datetime(value) -> Optional[int]:
    """Accept ms timestamp (int) or ISO 8601 string. Returns ms timestamp."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


class SuperProductivityMCPServer:
    def __init__(self):
        self.server = Server("super-productivity")
        self._tag_cache: Dict[str, str] = {}  # id -> title
        self.setup_directories()
        self.setup_logging()
        self.setup_tools()

    def setup_directories(self):
        if os.name == 'nt':  # Windows
            data_dir = os.environ.get('APPDATA', os.path.expanduser('~/AppData/Roaming'))
        else:  # Linux/Mac
            data_dir = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))

        self.base_dir = Path(data_dir) / 'super-productivity-mcp'
        self.command_dir = self.base_dir / 'plugin_commands'
        self.response_dir = self.base_dir / 'plugin_responses'

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.command_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)

        logging.info(f"MCP Server using directory: {self.base_dir}")

    def setup_logging(self):
        log_file = self.base_dir / 'mcp_server.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(sys.stderr)
            ]
        )

    def setup_tools(self):
        """Set up MCP tools"""

        @self.server.list_tools()
        async def handle_list_tools() -> List[types.Tool]:
            return [
                types.Tool(
                    name="get_usage",
                    description="Returns the tool usage guide — tool hierarchy, get_tasks filter reference, and recommended discovery pattern. Call this first in a new session.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="get_tasks",
                    description=(
                        "Get tasks from Super Productivity. Defaults to open top-level tasks only. "
                        "Use filters to scope results — always filter by project_id when you know it. "
                        "Tag labels are resolved inline; no separate get_tags call needed. "
                        "Use task_id for single-task lookup or search for title substring match."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "project_id": {
                                "type": "string",
                                "description": "Scope results to a specific project (get IDs from get_projects)"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Return a single task by ID"
                            },
                            "search": {
                                "type": "string",
                                "description": "Case-insensitive title substring match"
                            },
                            "include_done": {
                                "type": "boolean",
                                "description": "Include completed tasks (default: false)",
                                "default": False
                            },
                            "include_subtasks": {
                                "type": "boolean",
                                "description": "Include subtasks — default false returns top-level only",
                                "default": False
                            }
                        }
                    }
                ),
                types.Tool(
                    name="get_subtasks",
                    description=(
                        "Get subtasks of a parent task using partial name matching. "
                        "Optionally scope to a project by partial name. "
                        "Single call — resolves project, finds parent, returns subtasks with inline tag labels."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_name": {
                                "type": "string",
                                "description": "Partial name of the parent task (case-insensitive substring)"
                            },
                            "project_name": {
                                "type": "string",
                                "description": "Partial project name to scope the search (optional)"
                            }
                        },
                        "required": ["task_name"]
                    }
                ),
                types.Tool(
                    name="get_tasks_by_tag",
                    description=(
                        "Get all tasks that have a given tag, matched by partial tag name. "
                        "Single call — resolves tag from cache, filters tasks, returns with inline tag labels."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tag_name": {
                                "type": "string",
                                "description": "Partial tag name to match (case-insensitive substring)"
                            },
                            "include_done": {
                                "type": "boolean",
                                "description": "Include completed tasks (default: false)",
                                "default": False
                            }
                        },
                        "required": ["tag_name"]
                    }
                ),
                types.Tool(
                    name="create_task",
                    description="Create a new task in Super Productivity. Convert natural language time/date references to SP syntax in the title (@1days, @fri 3pm, @7days, etc). Add #tags and +projects as needed.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Task title. Use @Xdays/@Yweeks/@Zmonths for scheduling, #tag for tags, +project for projects."
                            },
                            "notes": {"type": "string", "description": "Task notes/description"},
                            "project_id": {"type": "string", "description": "Project ID"},
                            "parent_id": {"type": "string", "description": "Parent task ID for subtasks"},
                            "tag_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tag IDs to assign"
                            },
                            "time_estimate": {"type": "integer", "description": "Time estimate in milliseconds"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="update_task",
                    description="Update an existing task. Only include fields you want to change. Convert natural language time/date references to SP syntax.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string", "description": "Task ID to update"},
                            "title": {"type": "string", "description": "New title (use @syntax for scheduling)"},
                            "notes": {"type": "string", "description": "New notes"},
                            "is_done": {"type": "boolean", "description": "Mark done/undone"},
                            "time_estimate": {"type": "integer", "description": "Time estimate in milliseconds"},
                            "time_spent": {"type": "integer", "description": "Time spent in milliseconds"},
                            "tag_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Replace tag IDs (full list, not additive)"
                            }
                        },
                        "required": ["task_id"]
                    }
                ),
                types.Tool(
                    name="complete_and_archive_task",
                    description="Mark a task as done. True deletion is not supported.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string", "description": "Task ID to complete"}
                        },
                        "required": ["task_id"]
                    }
                ),
                types.Tool(
                    name="get_projects",
                    description="Get all projects. Use this first to get project IDs for scoping get_tasks.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="create_project",
                    description="Create a new project",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Project title"},
                            "description": {"type": "string", "description": "Project description"},
                            "color": {"type": "string", "description": "Hex color code"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="get_tags",
                    description="Get all tags. Rarely needed — tag labels are already resolved inline in get_tasks results. Use this only if you need tag IDs for create_task or update_task.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="create_tag",
                    description="Create a new tag",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Tag title"},
                            "color": {"type": "string", "description": "Hex color code"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="show_notification",
                    description="Show a notification in Super Productivity",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Notification message"},
                            "type": {
                                "type": "string",
                                "enum": ["success", "info", "warning", "error"],
                                "default": "info"
                            }
                        },
                        "required": ["message"]
                    }
                ),
                types.Tool(
                    name="debug_directories",
                    description="Debug the IPC communication directories. Use if commands are timing out.",
                    inputSchema={"type": "object", "properties": {}}
                )
            ]

        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: Dict[str, Any]
        ) -> List[types.TextContent]:
            try:
                if name == "get_usage":
                    result = {"success": True, "result": USAGE_GUIDE}
                elif name == "get_tasks":
                    result = await self.get_tasks(arguments)
                elif name == "get_subtasks":
                    result = await self.get_subtasks(arguments)
                elif name == "get_tasks_by_tag":
                    result = await self.get_tasks_by_tag(arguments)
                elif name == "create_task":
                    result = await self.create_task(arguments)
                elif name == "update_task":
                    result = await self.update_task(arguments)
                elif name == "complete_and_archive_task":
                    result = await self.complete_and_archive_task(arguments)
                elif name == "get_projects":
                    result = await self.get_projects(arguments)
                elif name == "create_project":
                    result = await self.create_project(arguments)
                elif name == "get_tags":
                    result = await self.get_tags(arguments)
                elif name == "create_tag":
                    result = await self.create_tag(arguments)
                elif name == "show_notification":
                    result = await self.show_notification(arguments)
                elif name == "debug_directories":
                    result = await self.debug_directories(arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")

                return [types.TextContent(type="text", text=str(result))]

            except Exception as e:
                logging.error(f"Error in tool {name}: {str(e)}")
                return [types.TextContent(type="text", text=f"Error: {str(e)}")]

    async def send_command(self, action: str, **kwargs) -> Dict[str, Any]:
        """Send a command to Super Productivity plugin"""
        command = {
            "action": action,
            "id": f"{action}_{asyncio.get_event_loop().time()}",
            "timestamp": asyncio.get_event_loop().time(),
            **kwargs
        }

        command_file = self.command_dir / f"{command['id']}.json"
        with open(command_file, 'w') as f:
            json.dump(command, f, indent=2)

        logging.info(f"Sent command: {action} -> {command_file}")

        response_file = self.response_dir / f"{command['id']}_response.json"

        for _ in range(30):
            if response_file.exists():
                try:
                    with open(response_file, 'r') as f:
                        response = json.load(f)
                    response_file.unlink()
                    logging.info(f"Received response for {action}: {response.get('success', 'unknown')}")
                    return response
                except Exception as e:
                    logging.error(f"Error reading response file: {e}")
                    break
            await asyncio.sleep(1)

        logging.warning(f"Timeout waiting for response to {action}")
        return {"success": False, "error": "Timeout waiting for response"}

    async def _ensure_tag_cache(self):
        """Populate tag cache if empty."""
        if not self._tag_cache:
            response = await self.send_command("getAllTags")
            if response.get("success"):
                self._tag_cache = {
                    t["id"]: t["title"]
                    for t in response.get("result", [])
                }

    def _resolve_tags(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Inject a 'tags' list of label strings alongside raw tagIds."""
        tag_ids = task.get("tagIds", [])
        task["tags"] = [self._tag_cache.get(tid, tid) for tid in tag_ids]
        return task

    async def get_tasks(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Filtered task fetch with inline tag resolution."""
        tasks_resp, _ = await asyncio.gather(
            self.send_command("getTasks"),
            self._ensure_tag_cache()
        )

        if not tasks_resp.get("success"):
            return tasks_resp

        tasks = tasks_resp.get("result", [])

        # Filters
        task_id = args.get("task_id")
        if task_id:
            tasks = [t for t in tasks if t.get("id") == task_id]

        project_id = args.get("project_id")
        if project_id:
            tasks = [t for t in tasks if t.get("projectId") == project_id]

        if not args.get("include_done", False):
            tasks = [t for t in tasks if not t.get("isDone")]

        if not args.get("include_subtasks", False):
            tasks = [t for t in tasks if not t.get("parentId")]

        search = args.get("search")
        if search:
            tasks = [t for t in tasks if search.lower() in t.get("title", "").lower()]

        # Inline tag resolution
        tasks = [self._resolve_tags(t) for t in tasks]

        return {**tasks_resp, "result": tasks}

    async def get_subtasks(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Find a parent task by partial name, optionally scoped to a partial project name, return its subtasks."""
        task_name = args.get("task_name", "").lower()
        project_name = args.get("project_name", "").lower()

        if project_name:
            tasks_resp, _, projects_resp = await asyncio.gather(
                self.send_command("getTasks"),
                self._ensure_tag_cache(),
                self.send_command("getAllProjects")
            )
        else:
            tasks_resp, _ = await asyncio.gather(
                self.send_command("getTasks"),
                self._ensure_tag_cache()
            )
            projects_resp = None

        if not tasks_resp.get("success"):
            return tasks_resp

        all_tasks = tasks_resp.get("result", [])
        task_index = {t["id"]: t for t in all_tasks}

        project_ids = None
        if project_name and projects_resp and projects_resp.get("success"):
            matching_projects = [
                p for p in projects_resp.get("result", [])
                if project_name in p.get("title", "").lower()
            ]
            if not matching_projects:
                return {"success": False, "error": f"No project matching '{args.get('project_name')}'"}
            project_ids = {p["id"] for p in matching_projects}

        parents = [
            t for t in all_tasks
            if task_name in t.get("title", "").lower()
            and (project_ids is None or t.get("projectId") in project_ids)
            and t.get("subTaskIds")  # must have subtasks
        ]

        if not parents:
            return {"success": False, "error": f"No parent task matching '{args.get('task_name')}' with subtasks"}

        if len(parents) > 1:
            return {
                "success": False,
                "error": f"Multiple tasks match '{args.get('task_name')}' — narrow your search.",
                "matches": [{"id": p["id"], "title": p["title"], "projectId": p.get("projectId")} for p in parents]
            }

        parent = parents[0]
        subtasks = [
            self._resolve_tags(task_index[sid])
            for sid in parent.get("subTaskIds", [])
            if sid in task_index
        ]

        return {
            "success": True,
            "parent": self._resolve_tags(parent),
            "subtasks": subtasks,
            "count": len(subtasks)
        }

    async def get_tasks_by_tag(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Return all tasks matching a partial tag name, with inline tag resolution."""
        tag_name = args.get("tag_name", "").lower()

        tasks_resp, _ = await asyncio.gather(
            self.send_command("getTasks"),
            self._ensure_tag_cache()
        )

        if not tasks_resp.get("success"):
            return tasks_resp

        matching_tag_ids = {
            tid for tid, label in self._tag_cache.items()
            if tag_name in label.lower()
        }

        if not matching_tag_ids:
            return {"success": False, "error": f"No tag matching '{args.get('tag_name')}'"}

        include_done = args.get("include_done", False)
        tasks = [
            self._resolve_tags(t)
            for t in tasks_resp.get("result", [])
            if any(tid in matching_tag_ids for tid in t.get("tagIds", []))
            and (include_done or not t.get("isDone"))
        ]

        return {
            "success": True,
            "matched_tags": [self._tag_cache[tid] for tid in matching_tag_ids],
            "result": tasks,
            "count": len(tasks)
        }

    async def create_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_data = {
            "title": args.get("title", ""),
            "notes": args.get("notes", ""),
            "timeEstimate": args.get("time_estimate", 0),
            "projectId": args.get("project_id"),
            "parentId": args.get("parent_id"),
            "tagIds": args.get("tag_ids", [])
        }
        return await self.send_command("addTask", data=task_data)

    async def update_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args.get("task_id")
        if not task_id:
            return {"success": False, "error": "task_id is required"}

        updates = {}
        if "title" in args:
            updates["title"] = args["title"]
        if "notes" in args:
            updates["notes"] = args["notes"]
        if "is_done" in args:
            updates["isDone"] = args["is_done"]
            updates["doneOn"] = asyncio.get_event_loop().time() * 1000 if args["is_done"] else None
        if "time_estimate" in args:
            updates["timeEstimate"] = args["time_estimate"]
        if "time_spent" in args:
            updates["timeSpent"] = args["time_spent"]
        if "tag_ids" in args:
            updates["tagIds"] = args["tag_ids"]

        return await self.send_command("updateTask", taskId=task_id, data=updates)

    async def complete_and_archive_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task_id = args.get("task_id")
        if not task_id:
            return {"success": False, "error": "task_id is required"}
        return await self.send_command("setTaskDone", taskId=task_id)

    async def get_projects(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self.send_command("getAllProjects")

    async def create_project(self, args: Dict[str, Any]) -> Dict[str, Any]:
        project_data = {
            "title": args.get("title", ""),
            "description": args.get("description", ""),
            "color": args.get("color", "#2196F3")
        }
        return await self.send_command("addProject", data=project_data)

    async def get_tags(self, args: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.send_command("getAllTags")
        if response.get("success"):
            self._tag_cache = {
                t["id"]: t["title"]
                for t in response.get("result", [])
            }
        return response

    async def create_tag(self, args: Dict[str, Any]) -> Dict[str, Any]:
        tag_data = {
            "title": args.get("title", ""),
            "color": args.get("color", "#FF9800")
        }
        result = await self.send_command("addTag", data=tag_data)
        # Invalidate cache so new tag is picked up on next get_tasks
        self._tag_cache = {}
        return result

    async def show_notification(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self.send_command("showSnack", message=args.get("message", ""))

    async def debug_directories(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "success": True,
            "base_directory": str(self.base_dir),
            "command_directory": str(self.command_dir),
            "response_directory": str(self.response_dir),
            "directories_exist": {
                "base": self.base_dir.exists(),
                "commands": self.command_dir.exists(),
                "responses": self.response_dir.exists()
            },
            "tag_cache_size": len(self._tag_cache)
        }

    async def run(self):
        logging.info("Starting Super Productivity MCP Server...")
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="super-productivity",
                    server_version="1.1.0",
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )


async def main():
    server = SuperProductivityMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
