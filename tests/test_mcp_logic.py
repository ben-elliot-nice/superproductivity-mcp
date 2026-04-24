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
