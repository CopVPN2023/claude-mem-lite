import io
import json

import capture
from lib.db import get_connection, get_unprocessed_events


def test_capture_inserts_row(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": "a.py"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 1
    assert events[0]["tool_name"] == "Edit"
    assert "a.py" in events[0]["tool_input"]


def test_capture_truncates_large_tool_input(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"content": "x" * 5000},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events[0]["tool_input"]) <= 500


def test_capture_stores_tool_response(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {"stdout": "a.py\nb.py"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert "a.py" in events[0]["tool_response"]


def test_capture_truncates_large_tool_response(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Read",
        "tool_input": {"file_path": "a.py"},
        "tool_response": {"content": "x" * 5000},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events[0]["tool_response"]) <= capture.TOOL_RESPONSE_TRUNCATE


def test_capture_tolerates_raw_control_character_in_payload(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    # A literal, unescaped control character inside a JSON string value is
    # technically invalid JSON but real hook payloads have contained one
    # (see error.log). json.load(strict=False) should tolerate it.
    raw = (
        '{"session_id": "sess1", "cwd": ' + json.dumps(str(tmp_path)) +
        ', "tool_name": "Bash", "tool_input": {"command": "echo\x01hi"}}'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 1
    assert events[0]["tool_name"] == "Bash"


def test_capture_never_raises_on_malformed_stdin(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    capture.main(db_path=str(db_path))  # must not raise


def test_capture_noop_when_hooks_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("CLAUDE_MEM_LITE_NO_HOOKS", "1")
    payload = {"session_id": "sess1", "cwd": str(tmp_path), "tool_name": "Edit", "tool_input": {}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    assert get_unprocessed_events(conn, "sess1") == []


def test_capture_swallows_log_write_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    # Point ERROR_LOG to a nonexistent directory to cause file-write to fail
    nonexistent_log = tmp_path / "nonexistent" / "dir" / "error.log"
    monkeypatch.setattr("capture.ERROR_LOG", str(nonexistent_log))
    # Feed malformed stdin to trigger the exception handler
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    # This must not raise, even though both stdin parsing and log-write will fail
    capture.main(db_path=str(db_path))
