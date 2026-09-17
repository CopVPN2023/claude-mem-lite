from summarize_worker import skip_summarization, build_summarize_prompt, parse_observations


def _event(tool_name, tool_input="{}"):
    return {"id": 1, "tool_name": tool_name, "tool_input": tool_input, "project": "/proj"}


def test_skip_summarization_empty_list():
    assert skip_summarization([]) is True


def test_skip_summarization_below_threshold():
    events = [_event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True


def test_skip_summarization_at_threshold():
    events = [_event("Edit"), _event("Edit"), _event("Write")]
    assert skip_summarization(events) is False


def test_skip_summarization_ignores_read_only_tools():
    events = [_event("Read"), _event("Grep"), _event("WebSearch"), _event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True  # only 2 substantive


def test_build_summarize_prompt_includes_all_events():
    events = [_event("Edit", '{"file_path": "a.py"}'), _event("Bash", '{"command": "ls"}')]
    prompt = build_summarize_prompt(events)
    assert "JSON array" in prompt
    assert "Edit" in prompt
    assert "Bash" in prompt
    assert "a.py" in prompt


def test_parse_observations_valid_json():
    response = '[{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]'
    result = parse_observations(response)
    assert result == [{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]


def test_parse_observations_strips_markdown_fences():
    response = '```json\n[{"category": "feature", "summary": "Added Y", "related_files": []}]\n```'
    result = parse_observations(response)
    assert len(result) == 1
    assert result[0]["summary"] == "Added Y"


def test_parse_observations_invalid_category_defaults_to_change():
    response = '[{"category": "not-a-real-category", "summary": "Did something", "related_files": []}]'
    result = parse_observations(response)
    assert result[0]["category"] == "change"


def test_parse_observations_malformed_json_returns_empty():
    assert parse_observations("not json at all") == []


def test_parse_observations_empty_array():
    assert parse_observations("[]") == []


def test_parse_observations_non_list_returns_empty():
    assert parse_observations('{"not": "a list"}') == []


def test_parse_observations_skips_items_missing_summary():
    response = '[{"category": "bugfix", "related_files": []}]'
    assert parse_observations(response) == []


import json as jsonlib
import os
import time

import pytest

import summarize_worker
from lib.db import get_connection, insert_raw_event, get_observations, get_unprocessed_events
from lib.guard import NO_HOOKS_ENV


@pytest.fixture(autouse=True)
def isolate_lock_dir(tmp_path, monkeypatch):
    """Keep every test's session locks out of the real install's .locks dir."""
    monkeypatch.setattr(summarize_worker, "LOCK_DIR", str(tmp_path / "locks"))


def test_call_claude_headless_sets_guard_env_and_pipes_prompt(monkeypatch):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "[]"

    def fake_run(cmd, input, capture_output, text, env, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        captured["timeout"] = timeout
        return FakeResult()

    monkeypatch.setattr(summarize_worker.subprocess, "run", fake_run)

    result = summarize_worker.call_claude_headless("summarize this")

    assert result == "[]"
    assert captured["input"] == "summarize this"
    assert captured["env"][NO_HOOKS_ENV] == "1"
    assert captured["cmd"] == ["claude", "-p", "--model", "sonnet", "--strict-mcp-config"]


def test_call_claude_headless_raises_on_nonzero_returncode(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = "some error text"
        stderr = ""

    monkeypatch.setattr(
        summarize_worker.subprocess, "run", lambda *a, **kw: FakeResult()
    )

    with pytest.raises(RuntimeError):
        summarize_worker.call_claude_headless("summarize this")


def test_main_leaves_rows_unprocessed_when_claude_call_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    before = get_unprocessed_events(conn, "sess1")
    conn.close()

    def boom(prompt):
        raise RuntimeError("boom")

    monkeypatch.setattr(summarize_worker, "call_claude_headless", boom)
    monkeypatch.setattr(summarize_worker, "ERROR_LOG", str(tmp_path / "error.log"))

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    assert get_unprocessed_events(conn, "sess1") == before
    assert get_observations(conn, project="/proj") == []
    conn.close()
    assert "boom" in (tmp_path / "error.log").read_text()


def test_main_writes_observations_and_marks_processed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    id1 = insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    id2 = insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    id3 = insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    conn.close()

    fake_response = jsonlib.dumps(
        [{"category": "bugfix", "summary": "Fixed the thing", "related_files": ["a.py"]}]
    )
    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: fake_response)
    monkeypatch.setattr(summarize_worker, "embed_text", lambda text: [0.1, 0.2, 0.3])

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    observations = get_observations(conn, project="/proj")
    assert len(observations) == 1
    assert observations[0]["summary"] == "Fixed the thing"
    assert get_unprocessed_events(conn, "sess1") == []


def test_main_records_related_raw_event_ids_on_observations(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    id1 = insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    id2 = insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    id3 = insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    conn.close()

    fake_response = jsonlib.dumps(
        [{"category": "bugfix", "summary": "Fixed the thing", "related_files": ["a.py"]}]
    )
    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: fake_response)
    monkeypatch.setattr(summarize_worker, "embed_text", lambda text: [0.1, 0.2, 0.3])

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    observations = get_observations(conn, project="/proj")
    assert jsonlib.loads(observations[0]["related_raw_event_ids"]) == [id1, id2, id3]


def test_main_skips_when_below_threshold(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    conn.commit()
    conn.close()

    called = {"count": 0}
    monkeypatch.setattr(
        summarize_worker, "call_claude_headless", lambda prompt: called.__setitem__("count", called["count"] + 1)
    )

    summarize_worker.main("sess1", db_path=db_path)

    assert called["count"] == 0


def test_main_marks_processed_even_when_llm_returns_nothing_noteworthy(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    conn.close()

    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: "[]")

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    assert get_unprocessed_events(conn, "sess1") == []
    assert get_observations(conn, project="/proj") == []


def _seed_three_events(db_path, session_id="sess1"):
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", session_id, "/proj", "Edit", "{}")
    insert_raw_event(conn, "t", session_id, "/proj", "Write", "{}")
    insert_raw_event(conn, "t", session_id, "/proj", "Bash", "{}")
    conn.commit()
    conn.close()


def test_main_bails_out_when_lock_already_held(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _seed_three_events(db_path)

    assert summarize_worker._acquire_lock("sess1") is not None  # a worker is "running"

    called = {"count": 0}
    monkeypatch.setattr(
        summarize_worker,
        "call_claude_headless",
        lambda prompt: called.__setitem__("count", called["count"] + 1),
    )

    summarize_worker.main("sess1", db_path=db_path)

    assert called["count"] == 0
    conn = get_connection(db_path)
    assert len(get_unprocessed_events(conn, "sess1")) == 3
    conn.close()


def test_main_reclaims_a_stale_lock(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _seed_three_events(db_path)

    lock_path = summarize_worker._acquire_lock("sess1")
    old = time.time() - summarize_worker.LOCK_STALE_SECONDS - 60
    os.utime(lock_path, (old, old))

    fake_response = jsonlib.dumps(
        [{"category": "bugfix", "summary": "Fixed the thing", "related_files": []}]
    )
    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: fake_response)
    monkeypatch.setattr(summarize_worker, "embed_text", lambda text: [0.1, 0.2, 0.3])

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    assert len(get_observations(conn, project="/proj")) == 1
    assert get_unprocessed_events(conn, "sess1") == []
    conn.close()


def test_main_logs_and_returns_when_lock_acquisition_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _seed_three_events(db_path)
    monkeypatch.setattr(summarize_worker, "ERROR_LOG", str(tmp_path / "error.log"))

    def boom(session_id):
        raise PermissionError("boom")

    monkeypatch.setattr(summarize_worker, "_acquire_lock", boom)
    called = {"count": 0}
    monkeypatch.setattr(
        summarize_worker,
        "call_claude_headless",
        lambda prompt: called.__setitem__("count", called["count"] + 1),
    )

    summarize_worker.main("sess1", db_path=db_path)

    assert called["count"] == 0
    assert "boom" in (tmp_path / "error.log").read_text()
    conn = get_connection(db_path)
    assert len(get_unprocessed_events(conn, "sess1")) == 3
    conn.close()


def test_main_releases_lock_after_success(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    _seed_three_events(db_path)

    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: "[]")

    summarize_worker.main("sess1", db_path=db_path)

    assert not os.path.exists(os.path.join(summarize_worker.LOCK_DIR, "sess1.lock"))
