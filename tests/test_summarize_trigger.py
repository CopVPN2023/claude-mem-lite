import io
import json

import summarize_trigger


def test_spawns_worker_with_session_id(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "sess1", "cwd": "/tmp"}))
    )
    monkeypatch.delenv("CLAUDE_MEM_LITE_NO_HOOKS", raising=False)

    summarize_trigger.main()

    assert "sess1" in captured["cmd"]
    assert captured["cmd"][1] == summarize_trigger.WORKER
    assert captured["kwargs"]["start_new_session"] is True


def test_does_not_spawn_when_hooks_disabled(monkeypatch):
    called = {"count": 0}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            called["count"] += 1

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("CLAUDE_MEM_LITE_NO_HOOKS", "1")
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "sess1", "cwd": "/tmp"}))
    )

    summarize_trigger.main()

    assert called["count"] == 0


def test_does_not_spawn_when_session_id_missing(monkeypatch):
    called = {"count": 0}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            called["count"] += 1

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.delenv("CLAUDE_MEM_LITE_NO_HOOKS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/tmp"})))

    summarize_trigger.main()

    assert called["count"] == 0


def test_never_raises_on_malformed_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    summarize_trigger.main()  # must not raise
