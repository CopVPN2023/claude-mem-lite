import json

import pytest

import timeline_report
from lib.db import get_connection, insert_observation
from lib.embeddings import pack_embedding


def _obs(ts, category, summary, related_files="[]"):
    return {
        "id": 1, "ts": ts, "project": "/proj", "category": category,
        "summary": summary, "related_files": related_files,
    }


def test_estimate_tokens_scales_with_content_length():
    small = [_obs("t", "change", "short")]
    big = [_obs("t", "change", "x" * 4000)]
    assert timeline_report.estimate_tokens(big) > timeline_report.estimate_tokens(small)


def test_estimate_tokens_empty_list_is_zero():
    assert timeline_report.estimate_tokens([]) == 0


def test_build_report_prompt_includes_project_and_observations():
    obs = [_obs("2026-01-01T00:00:00Z", "bugfix", "Fixed the login bug")]
    prompt = timeline_report.build_report_prompt("/proj", obs)
    assert "/proj" in prompt
    assert "Fixed the login bug" in prompt
    assert "Genesis" in prompt
    assert "Timeline Statistics" in prompt
    # Dropped sections from claude-mem's version -- must not appear.
    assert "Token Economics" not in prompt


def test_default_output_path_uses_project_basename():
    assert timeline_report.default_output_path("/Users/x/claude-mem-lite") == "./journey-into-claude-mem-lite.md"


def test_default_output_path_handles_trailing_slash():
    assert timeline_report.default_output_path("/Users/x/claude-mem-lite/") == "./journey-into-claude-mem-lite.md"


def test_call_claude_headless_sets_guard_env_and_pins_model(monkeypatch):
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "# Journey Into proj\n..."
        stderr = ""

    def fake_run(cmd, input, capture_output, text, env, timeout):
        captured["cmd"] = cmd
        captured["env"] = env
        captured["timeout"] = timeout
        return FakeResult()

    monkeypatch.setattr(timeline_report.subprocess, "run", fake_run)

    result = timeline_report.call_claude_headless("prompt")

    assert result == "# Journey Into proj\n..."
    assert captured["cmd"] == ["claude", "-p", "--model", "sonnet", "--strict-mcp-config"]
    assert captured["env"]["CLAUDE_MEM_LITE_NO_HOOKS"] == "1"
    assert captured["timeout"] == timeline_report.CLAUDE_TIMEOUT_SECONDS


def test_call_claude_headless_raises_on_nonzero_returncode(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(timeline_report.subprocess, "run", lambda *a, **kw: FakeResult())

    with pytest.raises(RuntimeError):
        timeline_report.call_claude_headless("prompt")


def test_main_prints_message_when_no_observations(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    timeline_report.main(["--project", "/proj"], db_path=db_path)
    captured = capsys.readouterr()
    assert "No observations found" in captured.out


def test_main_dry_run_prints_estimate_and_skips_llm_call(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "bugfix", "Fixed it", "[]", pack_embedding([0.1]))
    conn.commit()
    conn.close()

    called = {"count": 0}
    monkeypatch.setattr(
        timeline_report, "call_claude_headless",
        lambda prompt: called.__setitem__("count", called["count"] + 1),
    )

    timeline_report.main(["--project", "/proj", "--dry-run"], db_path=db_path)

    assert called["count"] == 0
    captured = capsys.readouterr()
    assert "1 observation" in captured.out


def test_main_writes_report_to_default_output_path(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj/claude-mem-lite", "bugfix", "Fixed it", "[]", pack_embedding([0.1]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(timeline_report, "call_claude_headless", lambda prompt: "# Journey Into claude-mem-lite\n\nThe story.")
    monkeypatch.chdir(tmp_path)

    timeline_report.main(["--project", "/proj/claude-mem-lite"], db_path=db_path)

    out_file = tmp_path / "journey-into-claude-mem-lite.md"
    assert out_file.exists()
    assert out_file.read_text() == "# Journey Into claude-mem-lite\n\nThe story."
    captured = capsys.readouterr()
    assert "journey-into-claude-mem-lite.md" in captured.out


def test_main_writes_report_to_custom_output_path(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "bugfix", "Fixed it", "[]", pack_embedding([0.1]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(timeline_report, "call_claude_headless", lambda prompt: "report body")
    out_path = str(tmp_path / "custom-report.md")

    timeline_report.main(["--project", "/proj", "--output", out_path], db_path=db_path)

    assert (tmp_path / "custom-report.md").read_text() == "report body"


def test_main_exits_nonzero_and_writes_no_file_when_claude_call_fails(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "bugfix", "Fixed it", "[]", pack_embedding([0.1]))
    conn.commit()
    conn.close()

    def boom(prompt):
        raise RuntimeError("claude -p exited 1: boom")

    monkeypatch.setattr(timeline_report, "call_claude_headless", boom)
    out_path = str(tmp_path / "should-not-exist.md")

    with pytest.raises(SystemExit) as excinfo:
        timeline_report.main(["--project", "/proj", "--output", out_path], db_path=db_path)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert not (tmp_path / "should-not-exist.md").exists()
