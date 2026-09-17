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

import summarize_worker
from lib.db import get_connection, insert_raw_event, get_observations, get_unprocessed_events
from lib.guard import NO_HOOKS_ENV


def test_call_claude_headless_sets_guard_env_and_pipes_prompt(monkeypatch):
    captured = {}

    class FakeResult:
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
    assert captured["cmd"] == ["claude", "-p"]


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
