import json

import timeline
from lib.db import get_connection, insert_observation
from lib.embeddings import pack_embedding


def _seed(conn, n, project="/proj"):
    ids = []
    for i in range(n):
        ts = f"2026-01-01T00:00:{i:02d}Z"
        oid = insert_observation(conn, ts, "s", project, "change", f"item {i}", "[]", pack_embedding([1.0, 0.0]))
        ids.append(oid)
    return ids


def test_select_window_centers_on_anchor():
    rows = [{"id": i, "ts": f"t{i}"} for i in range(10)]
    window = timeline.select_window(rows, anchor_id=5, before=2, after=2)
    assert [r["id"] for r in window] == [3, 4, 5, 6, 7]
    assert [r["is_anchor"] for r in window] == [False, False, True, False, False]


def test_select_window_clamps_at_start():
    rows = [{"id": i, "ts": f"t{i}"} for i in range(5)]
    window = timeline.select_window(rows, anchor_id=1, before=5, after=1)
    assert [r["id"] for r in window] == [0, 1, 2]


def test_select_window_clamps_at_end():
    rows = [{"id": i, "ts": f"t{i}"} for i in range(5)]
    window = timeline.select_window(rows, anchor_id=3, before=1, after=5)
    assert [r["id"] for r in window] == [2, 3, 4]


def test_select_window_returns_empty_for_missing_anchor():
    rows = [{"id": i, "ts": f"t{i}"} for i in range(5)]
    assert timeline.select_window(rows, anchor_id=999, before=1, after=1) == []


def test_main_prints_empty_array_for_empty_db(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    timeline.main(["--anchor", "1"], db_path=db_path)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_main_windows_by_anchor_id(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    ids = _seed(conn, 5)
    conn.commit()
    conn.close()

    timeline.main(["--anchor", str(ids[2]), "--before", "1", "--after", "1"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert [r["summary"] for r in results] == ["item 1", "item 2", "item 3"]
    assert results[0]["is_anchor"] is False
    assert results[1]["is_anchor"] is True
    assert results[2]["is_anchor"] is False


def test_main_resolves_anchor_by_query(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "change", "unrelated", "[]", pack_embedding([0.0, 1.0]))
    insert_observation(conn, "2026-01-01T00:00:01Z", "s", "/proj", "bugfix", "login bug fixed", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "2026-01-01T00:00:02Z", "s", "/proj", "change", "something else", "[]", pack_embedding([0.0, 1.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(timeline, "embed_text", lambda text: [1.0, 0.0])

    timeline.main(["--query", "login bug", "--before", "1", "--after", "1"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert [r["summary"] for r in results] == ["unrelated", "login bug fixed", "something else"]
    assert results[1]["is_anchor"] is True


def test_main_filters_by_project(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t1", "s", "/proj-a", "change", "A thing", "[]", pack_embedding([1.0, 0.0]))
    id_b = insert_observation(conn, "t2", "s", "/proj-b", "change", "B thing", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    timeline.main(["--anchor", str(id_b), "--project", "/proj-a"], db_path=db_path)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
