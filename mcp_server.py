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


EXPLAIN_TOPICS = {
    "tools": """
Tool hierarchy:

Primary: get_tasks, update_task, create_task
Convenience: get_subtasks (by partial name), get_tasks_by_tag (by partial tag name), get_completed_tasks
Discovery: get_projects, get_tags (rarely needed — use tag names directly)
Actions: complete_task, show_notification
Batch: create_tasks (multiple tasks, one round trip)
Setup: create_project, create_tag
Debug: debug_directories
""".strip(),

    "filters": """
get_tasks params. Default: open, top-level tasks only.

project_id     string  —      Project ID (from get_projects)
task_id        string  —      Single task lookup by ID
search         string  —      Case-insensitive title substring
include_done   bool    false  Include completed tasks
include_subtasks bool  false  Include subtasks
due_before     string  —      YYYY-MM-DD — due on or before
due_after      string  —      YYYY-MM-DD — due on or after
is_today       bool    false  Due today OR tagged TODAY

Tag labels are resolved inline in every response — tagIds not returned.
""".strip(),

    "scheduling": """
Scheduling fields (on create_task and update_task):

  due_day       YYYY-MM-DD string — date-only due date
  due_datetime  ISO 8601 string or ms timestamp — due date + time

Time estimate / time spent (on create_task and update_task):

  time_estimate  "2h" | "30m" | "2h30m" | "1.5h" | integer ms
  time_spent     same format

Special values (use as project or tag name):
  TODAY   — built-in Today tag; adds task to Today view
  INBOX   — default inbox project

Examples:
  due_day: "2026-04-25"
  due_datetime: "2026-04-25T14:00:00"
  time_estimate: "2h30m"
""".strip(),

    "discovery": """
Common patterns:

Scope by project:
  get_projects() → note name/ID → get_tasks(project_id=...)

Today's work:
  get_tasks(is_today=true)

Weekly review:
  get_completed_tasks(since_days=7)

Drill into a task:
  get_tasks(search="partial name") → get_tasks(task_id=...)

Subtasks (no IDs needed):
  get_subtasks(task_name="partial") → resolves parent automatically

Tag-based:
  get_tasks_by_tag(tag_name="urgent") → partial name match

Batch capture:
  create_tasks(tasks=[{title: ..., due_day: ..., tags: [...]}, ...])

Tags by name (no get_tags needed):
  create_task(title="Fix login", tags=["urgent", "backend"])
  update_task(task_id="...", add_tags=["in-progress"], remove_tags=["backlog"])
""".strip(),
}


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


def merge_tag_ids(current: list, add: list, remove: list) -> list:
    """Additive tag merge. Order: existing (minus removed) + new."""
    result = [t for t in current if t not in remove]
    for t in add:
        if t not in result:
            result.append(t)
    return result


def apply_task_filters(tasks: list, args: dict) -> list:
    """Apply get_tasks filter args to a list. Pure — no I/O."""
    task_id = args.get("task_id")
    if task_id:
        return [t for t in tasks if t.get("id") == task_id]

    if args.get("project_id"):
        tasks = [t for t in tasks if t.get("projectId") == args["project_id"]]

    if not args.get("include_done", False):
        tasks = [t for t in tasks if not t.get("isDone")]

    if not args.get("include_subtasks", False):
        tasks = [t for t in tasks if not t.get("parentId")]

    if args.get("search"):
        needle = args["search"].lower()
        tasks = [t for t in tasks if needle in t.get("title", "").lower()]

    if args.get("is_today"):
        today = today_str()
        tasks = [
            t for t in tasks
            if t.get("dueDay") == today or "TODAY" in t.get("tagIds", [])
        ]

    if args.get("due_before"):
        tasks = [t for t in tasks if t.get("dueDay") and t["dueDay"] <= args["due_before"]]

    if args.get("due_after"):
        tasks = [t for t in tasks if t.get("dueDay") and t["dueDay"] >= args["due_after"]]

    return tasks


def filter_completed_since(tasks: list, since_days: int, now_ms: int = None) -> list:
    """Filter to tasks completed within the last since_days days."""
    if now_ms is None:
        now_ms = int(_time.time() * 1000)
    cutoff = now_ms - (since_days * 86400 * 1000)
    return [t for t in tasks if t.get("doneOn") and t["doneOn"] >= cutoff]


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
                    name="explain",
                    description="Reference docs on demand. Topics: tools, filters, scheduling, discovery.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "enum": ["tools", "filters", "scheduling", "discovery"]
                            }
                        },
                        "required": ["topic"]
                    }
                ),
                types.Tool(
                    name="get_tasks",
                    description="Fetch tasks. Defaults: open, top-level. Unfamiliar with params? Call explain('filters').",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "project_id": {"type": "string"},
                            "task_id": {"type": "string"},
                            "search": {"type": "string"},
                            "include_done": {"type": "boolean", "default": False},
                            "include_subtasks": {"type": "boolean", "default": False},
                            "due_before": {"type": "string", "description": "YYYY-MM-DD"},
                            "due_after": {"type": "string", "description": "YYYY-MM-DD"},
                            "is_today": {"type": "boolean", "default": False}
                        }
                    }
                ),
                types.Tool(
                    name="get_completed_tasks",
                    description="Archived/completed tasks. since_days defaults to 7.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "since_days": {"type": "integer", "default": 7}
                        }
                    }
                ),
                types.Tool(
                    name="get_subtasks",
                    description="Subtasks of a parent task, matched by partial name.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_name": {"type": "string"},
                            "project_name": {"type": "string"}
                        },
                        "required": ["task_name"]
                    }
                ),
                types.Tool(
                    name="get_tasks_by_tag",
                    description="Tasks with a given tag, matched by partial name.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tag_name": {"type": "string"},
                            "include_done": {"type": "boolean", "default": False}
                        },
                        "required": ["tag_name"]
                    }
                ),
                types.Tool(
                    name="create_task",
                    description="Create a task. For scheduling and time syntax call explain('scheduling').",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "notes": {"type": "string"},
                            "project": {"type": "string", "description": "Project name or ID"},
                            "parent_id": {"type": "string"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Tag names (resolved server-side)"
                            },
                            "time_estimate": {"description": "e.g. '2h', '30m', or ms integer"},
                            "due_day": {"type": "string", "description": "YYYY-MM-DD"},
                            "due_datetime": {"description": "ISO string or ms timestamp"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="create_tasks",
                    description="Batch create multiple tasks in one round trip.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "tasks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "notes": {"type": "string"},
                                        "project": {"type": "string"},
                                        "parent_id": {"type": "string"},
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                        "time_estimate": {},
                                        "due_day": {"type": "string"},
                                        "due_datetime": {}
                                    },
                                    "required": ["title"]
                                }
                            }
                        },
                        "required": ["tasks"]
                    }
                ),
                types.Tool(
                    name="update_task",
                    description="Update a task. add/remove_tags are additive; tags replaces all. Call explain('scheduling') for time/date syntax.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string"},
                            "title": {"type": "string"},
                            "notes": {"type": "string"},
                            "project": {"type": "string", "description": "Project name or ID"},
                            "is_done": {"type": "boolean"},
                            "time_estimate": {"description": "'2h', '30m', or ms integer"},
                            "time_spent": {"description": "'2h', '30m', or ms integer"},
                            "due_day": {"type": "string", "description": "YYYY-MM-DD"},
                            "due_datetime": {"description": "ISO string or ms timestamp"},
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Replace all tags (names)"
                            },
                            "add_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Add tags without replacing existing"
                            },
                            "remove_tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Remove specific tags by name"
                            }
                        },
                        "required": ["task_id"]
                    }
                ),
                types.Tool(
                    name="complete_task",
                    description="Mark a task done and archive it.",
                    inputSchema={
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"]
                    }
                ),
                types.Tool(
                    name="convert_to_subtask",
                    description="Move a task under a parent. Copies and re-creates — SP has no native reparent.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "task_id": {"type": "string", "description": "Task to move"},
                            "parent_task_id": {"type": "string", "description": "New parent task ID"}
                        },
                        "required": ["task_id", "parent_task_id"]
                    }
                ),
                types.Tool(
                    name="get_projects",
                    description="All projects with IDs and names.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="create_project",
                    description="Create a project.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "color": {"type": "string", "description": "Hex color"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="get_tags",
                    description="All tags. Rarely needed — use tag names directly in create/update.",
                    inputSchema={"type": "object", "properties": {}}
                ),
                types.Tool(
                    name="create_tag",
                    description="Create a tag.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "color": {"type": "string", "description": "Hex color"}
                        },
                        "required": ["title"]
                    }
                ),
                types.Tool(
                    name="show_notification",
                    description="Push a notification into Super Productivity.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message": {"type": "string"},
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
                    description="IPC directory status. Use if commands are timing out.",
                    inputSchema={"type": "object", "properties": {}}
                ),
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

    async def _resolve_tag_names(self, names: list) -> list:
        """Convert tag names (or IDs) to tag IDs. Resolves from cache.

        Accepts exact names, partial names, or raw IDs.
        Raises ValueError on ambiguous or unknown names.
        """
        if not names:
            return []
        await self._ensure_tag_cache()
        ids = []
        for name in names:
            # Direct ID passthrough
            if name in self._tag_cache:
                ids.append(name)
                continue
            # Exact label match (case-insensitive)
            exact = [tid for tid, label in self._tag_cache.items()
                     if label.lower() == name.lower()]
            if len(exact) == 1:
                ids.append(exact[0])
                continue
            # Partial label match
            partial = [tid for tid, label in self._tag_cache.items()
                       if name.lower() in label.lower()]
            if len(partial) == 1:
                ids.append(partial[0])
            elif len(partial) > 1:
                options = [self._tag_cache[t] for t in partial]
                raise ValueError(f"Ambiguous tag '{name}' — matches: {options}. Be more specific.")
            else:
                available = list(self._tag_cache.values())
                raise ValueError(f"Unknown tag '{name}'. Available tags: {available}")
        return ids

    async def _resolve_project(self, project: str) -> str:
        """Accept project name or ID. Returns the project ID.

        Resolution order:
          1. Exact ID match
          2. Exact title match (case-insensitive)
          3. Partial title match (if unambiguous)
        Raises ValueError on ambiguous or not found.
        """
        resp = await self.send_command("getAllProjects")
        if not resp.get("success"):
            raise ValueError("Could not fetch projects for name resolution")
        projects = resp.get("result", [])

        # Exact ID
        for p in projects:
            if p["id"] == project:
                return project

        # Exact title
        exact = [p for p in projects if p["title"].lower() == project.lower()]
        if len(exact) == 1:
            return exact[0]["id"]

        # Partial title
        partial = [p for p in projects if project.lower() in p["title"].lower()]
        if len(partial) == 1:
            return partial[0]["id"]
        elif len(partial) > 1:
            options = [p["title"] for p in partial]
            raise ValueError(f"Ambiguous project '{project}' — matches: {options}. Be more specific.")

        available = [p["title"] for p in projects]
        raise ValueError(f"Unknown project '{project}'. Available: {available}")

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
