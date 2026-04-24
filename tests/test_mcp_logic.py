# tests/test_mcp_logic.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import parse_duration, today_str


def test_parse_duration_ms_passthrough():
    assert parse_duration(7200000) == 7200000

def test_parse_duration_hours():
    assert parse_duration("2h") == 7200000

def test_parse_duration_minutes():
    assert parse_duration("30m") == 1800000

def test_parse_duration_compound():
    assert parse_duration("2h30m") == 9000000

def test_parse_duration_decimal_hours():
    assert parse_duration("1.5h") == 5400000

def test_parse_duration_none():
    assert parse_duration(None) is None

def test_parse_duration_zero():
    assert parse_duration(0) == 0

def test_today_str_format():
    result = today_str()
    parts = result.split("-")
    assert len(parts) == 3 and len(parts[0]) == 4


from mcp_server import parse_due_day, parse_due_datetime


def test_parse_due_day_passthrough():
    assert parse_due_day("2026-04-25") == "2026-04-25"

def test_parse_due_day_none():
    assert parse_due_day(None) is None

def test_parse_due_datetime_int_passthrough():
    assert parse_due_datetime(1714000000000) == 1714000000000

def test_parse_due_datetime_iso():
    result = parse_due_datetime("2026-04-25T14:00:00")
    assert isinstance(result, int) and result > 0

def test_parse_due_datetime_none():
    assert parse_due_datetime(None) is None


from mcp_server import merge_tag_ids


def test_merge_add():
    assert merge_tag_ids(["a", "b"], add=["c"], remove=[]) == ["a", "b", "c"]

def test_merge_remove():
    assert merge_tag_ids(["a", "b", "c"], add=[], remove=["b"]) == ["a", "c"]

def test_merge_deduplicates():
    assert merge_tag_ids(["a", "b"], add=["b"], remove=[]) == ["a", "b"]

def test_merge_add_and_remove():
    assert merge_tag_ids(["a", "b"], add=["c"], remove=["a"]) == ["b", "c"]

def test_merge_remove_nonexistent():
    assert merge_tag_ids(["a"], add=[], remove=["z"]) == ["a"]

def test_merge_empty():
    assert merge_tag_ids([], add=[], remove=[]) == []


from mcp_server import apply_task_filters, today_str


def _task(id="t1", title="Test", is_done=False, parent_id=None,
          project_id=None, tag_ids=None, due_day=None):
    return {"id": id, "title": title, "isDone": is_done,
            "parentId": parent_id, "projectId": project_id,
            "tagIds": tag_ids or [], "dueDay": due_day}


def test_filter_excludes_done_by_default():
    tasks = [_task(id="a"), _task(id="b", is_done=True)]
    assert [t["id"] for t in apply_task_filters(tasks, {})] == ["a"]

def test_filter_include_done():
    tasks = [_task(id="a"), _task(id="b", is_done=True)]
    assert len(apply_task_filters(tasks, {"include_done": True})) == 2

def test_filter_excludes_subtasks_by_default():
    tasks = [_task(id="a"), _task(id="b", parent_id="a")]
    assert [t["id"] for t in apply_task_filters(tasks, {})] == ["a"]

def test_filter_due_before():
    tasks = [_task(id="a", due_day="2026-04-20"), _task(id="b", due_day="2026-04-25")]
    assert [t["id"] for t in apply_task_filters(tasks, {"due_before": "2026-04-22"})] == ["a"]

def test_filter_due_after():
    tasks = [_task(id="a", due_day="2026-04-20"), _task(id="b", due_day="2026-04-25")]
    assert [t["id"] for t in apply_task_filters(tasks, {"due_after": "2026-04-22"})] == ["b"]

def test_filter_is_today_by_due_day():
    tasks = [_task(id="a", due_day=today_str()), _task(id="b", due_day="2020-01-01")]
    result_ids = [t["id"] for t in apply_task_filters(tasks, {"is_today": True})]
    assert "a" in result_ids and "b" not in result_ids

def test_filter_is_today_by_tag():
    tasks = [_task(id="a", tag_ids=["TODAY"]), _task(id="b", tag_ids=["other"])]
    result_ids = [t["id"] for t in apply_task_filters(tasks, {"is_today": True})]
    assert "a" in result_ids and "b" not in result_ids

def test_filter_search():
    tasks = [_task(id="a", title="Buy milk"), _task(id="b", title="Do taxes")]
    assert [t["id"] for t in apply_task_filters(tasks, {"search": "milk"})] == ["a"]


from mcp_server import filter_completed_since

_NOW = 1714003200000  # fixed ms reference point

def test_completed_includes_recent():
    recent = {"id": "a", "isDone": True, "doneOn": _NOW - (3 * 86400 * 1000)}
    old    = {"id": "b", "isDone": True, "doneOn": _NOW - (10 * 86400 * 1000)}
    result = filter_completed_since([recent, old], since_days=7, now_ms=_NOW)
    assert [t["id"] for t in result] == ["a"]

def test_completed_excludes_null_done_on():
    task = {"id": "a", "isDone": True, "doneOn": None}
    assert filter_completed_since([task], since_days=7, now_ms=_NOW) == []
