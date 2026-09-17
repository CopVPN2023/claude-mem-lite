import pytest

import knowledge_agent
from lib.db import get_connection, insert_observation
from lib.embeddings import pack_embedding
from lib.guard import NO_HOOKS_ENV


def test_build_answer_prompt_includes_question_and_observations():
    obs = [
        {"id": 7, "category": "bugfix", "summary": "Fixed the login bug", "related_files": '["a.py"]'},
    ]
    prompt = knowledge_agent.build_answer_prompt("did we fix the login bug?", obs)
    assert "did we fix the login bug?" in prompt
    assert "#7" in prompt
    assert "Fixed the login bug" in prompt


def test_call_claude_headless_sets_guard_env_and_pins_model(monkeypatch):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "Yes, see #7."
        stderr = ""

    def fake_run(cmd, input, capture_output, text, env, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        return FakeResult()

    monkeypatch.setattr(knowledge_agent.subprocess, "run", fake_run)

    result = knowledge_agent.call_claude_headless("some prompt")

    assert result == "Yes, see #7."
    assert captured["input"] == "some prompt"
    assert captured["env"][NO_HOOKS_ENV] == "1"
    assert captured["cmd"] == ["claude", "-p", "--model", "sonnet", "--strict-mcp-config"]


def test_call_claude_headless_raises_on_nonzero_returncode(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(knowledge_agent.subprocess, "run", lambda *a, **kw: FakeResult())

    with pytest.raises(RuntimeError):
        knowledge_agent.call_claude_headless("some prompt")


def test_main_prints_message_when_no_observations(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    knowledge_agent.main(["anything"], db_path=db_path)
    captured = capsys.readouterr()
    assert captured.out.strip() == "No relevant history found."


def test_main_prints_synthesized_answer(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj", "bugfix", "Fixed the login bug", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(knowledge_agent, "embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(knowledge_agent, "call_claude_headless", lambda prompt: "Yes, fixed in #1.")

    knowledge_agent.main(["did we fix the login bug?"], db_path=db_path)

    captured = capsys.readouterr()
    assert captured.out.strip() == "Yes, fixed in #1."


def test_main_filters_by_project(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj-a", "change", "A thing", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "t", "s", "/proj-b", "change", "B thing", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    captured_prompt = {}

    def fake_call(prompt):
        captured_prompt["value"] = prompt
        return "synthesized"

    monkeypatch.setattr(knowledge_agent, "embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(knowledge_agent, "call_claude_headless", fake_call)

    knowledge_agent.main(["a thing", "--project", "/proj-a"], db_path=db_path)

    assert "A thing" in captured_prompt["value"]
    assert "B thing" not in captured_prompt["value"]


def test_main_exits_nonzero_and_reports_error_when_claude_call_fails(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj", "change", "A thing", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    def boom(prompt):
        raise RuntimeError("claude -p exited 1: boom")

    monkeypatch.setattr(knowledge_agent, "embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(knowledge_agent, "call_claude_headless", boom)

    with pytest.raises(SystemExit) as excinfo:
        knowledge_agent.main(["a thing"], db_path=db_path)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err
