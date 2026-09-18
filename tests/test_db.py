import sqlite3
import stat
import subprocess
from lib.db import (
    get_connection,
    derive_project,
    insert_raw_event,
    get_unprocessed_events,
    mark_processed,
    get_raw_events_by_ids,
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


def test_get_connection_restricts_file_permissions_to_owner(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(str(db_path))
    conn.close()
    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600


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


def test_insert_and_get_observations(tmp_path):
    from lib.embeddings import pack_embedding
    from lib.db import insert_observation, get_observations

    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1, 0.2, 0.3])
    insert_observation(
        conn, "2026-01-01T00:00:00Z", "sess1", "/proj",
        "bugfix", "Fixed the login redirect", "[]", blob,
    )
    conn.commit()

    rows = get_observations(conn, project="/proj")
    assert len(rows) == 1
    assert rows[0]["category"] == "bugfix"
    assert rows[0]["summary"] == "Fixed the login redirect"
    assert rows[0]["embedding"] == blob


def test_get_observations_filters_by_project(tmp_path):
    from lib.embeddings import pack_embedding
    from lib.db import insert_observation, get_observations

    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(conn, "t", "s", "/proj-a", "change", "A", "[]", blob)
    insert_observation(conn, "t", "s", "/proj-b", "change", "B", "[]", blob)
    conn.commit()

    assert len(get_observations(conn, project="/proj-a")) == 1
    assert len(get_observations(conn, project=None)) == 2


def test_get_observations_empty_project_is_a_filter_not_a_wildcard(tmp_path):
    from lib.embeddings import pack_embedding
    from lib.db import insert_observation, get_observations

    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(conn, "t", "s", "/proj-a", "change", "A", "[]", blob)
    insert_observation(conn, "t", "s", "/proj-b", "change", "B", "[]", blob)
    conn.commit()

    assert get_observations(conn, project="") == []


def test_migration_adds_new_columns_to_a_legacy_database(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    # Build the pre-migration schema by hand, without tool_response/related_raw_event_ids.
    legacy_conn = sqlite3.connect(db_path)
    legacy_conn.executescript(
        """
        CREATE TABLE raw_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          session_id TEXT NOT NULL,
          project TEXT NOT NULL,
          tool_name TEXT,
          tool_input TEXT,
          processed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          session_id TEXT NOT NULL,
          project TEXT NOT NULL,
          category TEXT NOT NULL,
          summary TEXT NOT NULL,
          related_files TEXT,
          embedding BLOB NOT NULL
        );
        """
    )
    # Seed real rows through the OLD schema (no tool_response/related_raw_event_ids
    # columns exist yet, so the new insert_* helpers can't be used here).
    embedding_bytes = b"\x01\x02\x03\x04not-really-a-vector"
    legacy_conn.execute(
        "INSERT INTO raw_events (id, ts, session_id, project, tool_name, tool_input, processed) "
        "VALUES (1, 't1', 'sess1', '/proj', 'Edit', '{\"file\": \"a.py\"}', 0)"
    )
    legacy_conn.execute(
        "INSERT INTO observations (id, ts, session_id, project, category, summary, related_files, embedding) "
        "VALUES (1, 't1', 'sess1', '/proj', 'bugfix', 'Fixed the thing', '[\"a.py\"]', ?)",
        (embedding_bytes,),
    )
    legacy_conn.commit()
    legacy_conn.close()

    conn = get_connection(db_path)  # should migrate in place, not error

    raw_columns = {row["name"] for row in conn.execute("PRAGMA table_info(raw_events)")}
    obs_columns = {row["name"] for row in conn.execute("PRAGMA table_info(observations)")}
    assert "tool_response" in raw_columns
    assert "related_raw_event_ids" in obs_columns

    # The pre-existing rows must survive the migration intact.
    raw_row = conn.execute("SELECT * FROM raw_events WHERE id = 1").fetchone()
    assert raw_row["session_id"] == "sess1"
    assert raw_row["tool_name"] == "Edit"
    assert raw_row["tool_input"] == '{"file": "a.py"}'
    assert raw_row["tool_response"] is None  # new column, backfilled NULL

    obs_row = conn.execute("SELECT * FROM observations WHERE id = 1").fetchone()
    assert obs_row["summary"] == "Fixed the thing"
    assert obs_row["category"] == "bugfix"
    assert obs_row["embedding"] == embedding_bytes  # byte-identical, not just present
    assert obs_row["related_raw_event_ids"] is None

    conn.close()

    # And the migration is idempotent with real data: opening it again must not
    # raise, and must not duplicate or lose the rows already there.
    conn2 = get_connection(db_path)
    assert conn2.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0] == 1
    assert conn2.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    conn2.close()


def test_insert_raw_event_stores_tool_response(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    event_id = insert_raw_event(
        conn, "t", "sess1", "/proj", "Bash", '{"command": "ls"}', tool_response='{"stdout": "a.py"}'
    )
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert events[0]["id"] == event_id
    assert events[0]["tool_response"] == '{"stdout": "a.py"}'


def test_insert_raw_event_tool_response_defaults_to_none(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert events[0]["tool_response"] is None


def test_insert_observation_stores_related_raw_event_ids(tmp_path):
    from lib.embeddings import pack_embedding
    from lib.db import insert_observation, get_observations

    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(
        conn, "t", "s", "/proj", "bugfix", "Fixed it", "[]", blob,
        related_raw_event_ids="[1, 2, 3]",
    )
    conn.commit()

    rows = get_observations(conn, project="/proj")
    assert rows[0]["related_raw_event_ids"] == "[1, 2, 3]"


def test_get_raw_events_by_ids_returns_matching_rows(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    id1 = insert_raw_event(conn, "t", "s", "/proj", "Edit", "{}", tool_response="r1")
    id2 = insert_raw_event(conn, "t", "s", "/proj", "Bash", "{}", tool_response="r2")
    insert_raw_event(conn, "t", "s", "/proj", "Read", "{}", tool_response="r3")
    conn.commit()

    rows = get_raw_events_by_ids(conn, [id1, id2])
    assert {r["tool_response"] for r in rows} == {"r1", "r2"}


def test_get_raw_events_by_ids_empty_list_returns_empty(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    assert get_raw_events_by_ids(conn, []) == []


def test_get_recent_observations_orders_by_ts_desc_and_limits(tmp_path):
    from lib.embeddings import pack_embedding
    from lib.db import insert_observation, get_recent_observations

    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "change", "first", "[]", blob)
    insert_observation(conn, "2026-01-02T00:00:00Z", "s", "/proj", "change", "second", "[]", blob)
    insert_observation(conn, "2026-01-03T00:00:00Z", "s", "/proj", "change", "third", "[]", blob)
    conn.commit()

    rows = get_recent_observations(conn, "/proj", limit=2)
    assert [r["summary"] for r in rows] == ["third", "second"]
