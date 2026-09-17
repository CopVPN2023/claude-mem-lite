import subprocess
from lib.db import (
    get_connection,
    derive_project,
    insert_raw_event,
    get_unprocessed_events,
    mark_processed,
)


def test_get_connection_creates_schema(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(str(db_path))
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "raw_events" in tables
    assert "observations" in tables


def test_derive_project_inside_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    result = derive_project(str(repo))
    assert result == str(repo.resolve())


def test_derive_project_outside_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = derive_project(str(plain))
    assert result == str(plain)


def test_insert_and_get_unprocessed_events(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", '{"file": "a.py"}')
    insert_raw_event(conn, "2026-01-01T00:00:01Z", "sess1", "/proj", "Read", '{"file": "b.py"}')
    insert_raw_event(conn, "2026-01-01T00:00:02Z", "sess2", "/proj", "Edit", '{"file": "c.py"}')
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 2
    assert events[0]["tool_name"] == "Edit"
    assert events[1]["tool_name"] == "Read"


def test_mark_processed_excludes_from_unprocessed(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    id1 = insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", "{}")
    id2 = insert_raw_event(conn, "2026-01-01T00:00:01Z", "sess1", "/proj", "Edit", "{}")
    conn.commit()

    mark_processed(conn, [id1])
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 1
    assert events[0]["id"] == id2


def test_mark_processed_with_empty_list_does_nothing(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", "{}")
    conn.commit()
    mark_processed(conn, [])  # must not raise
    assert len(get_unprocessed_events(conn, "sess1")) == 1
