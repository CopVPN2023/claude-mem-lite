import io
import json

import digest
from lib.db import get_connection, insert_observation
from lib.embeddings import pack_embedding


def test_build_digest_empty_list():
    assert digest.build_digest([]) == ""


def test_build_digest_formats_observations():
    observations = [
        {"category": "bugfix", "summary": "Fixed the login redirect"},
        {"category": "feature", "summary": "Added CSV export"},
    ]
    result = digest.build_digest(observations)
    assert "Fixed the login redirect" in result
    assert "Added CSV export" in result
    assert "bugfix" in result
    assert "feature" in result


def test_main_prints_digest_for_matching_project(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    blob = pack_embedding([0.1])
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", str(tmp_path), "bugfix", "Fixed X", "[]", blob)
    conn.commit()
    conn.close()

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    digest.main(db_path=db_path)

    captured = capsys.readouterr()
    assert "Fixed X" in captured.out


def test_main_prints_nothing_for_empty_db(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    digest.main(db_path=db_path)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_never_raises_on_malformed_stdin(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    digest.main(db_path=db_path)  # must not raise


def test_main_noop_when_hooks_disabled(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    blob = pack_embedding([0.1])
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", str(tmp_path), "bugfix", "Fixed X", "[]", blob)
    conn.commit()
    conn.close()

    monkeypatch.setenv("CLAUDE_MEM_LITE_NO_HOOKS", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    digest.main(db_path=db_path)

    captured = capsys.readouterr()
    assert captured.out == ""
