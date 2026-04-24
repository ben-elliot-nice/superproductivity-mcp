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
