import json

from lib.embeddings import pack_embedding
from lib.db import get_connection, insert_observation
import search


def _row(summary, vec):
    return {"id": 1, "ts": "t", "project": "/proj", "category": "change",
            "summary": summary, "embedding": pack_embedding(vec)}


def test_rank_observations_orders_by_similarity():
    rows = [
        _row("unrelated thing", [0.0, 1.0]),
        _row("closely matching thing", [0.99, 0.01]),
        _row("somewhat related", [0.7, 0.3]),
    ]
    ranked = search.rank_observations([1.0, 0.0], rows, limit=10)
    assert [r["summary"] for r in ranked] == [
        "closely matching thing",
        "somewhat related",
        "unrelated thing",
    ]


def test_rank_observations_respects_limit():
    rows = [_row(f"item {i}", [1.0, 0.0]) for i in range(5)]
    ranked = search.rank_observations([1.0, 0.0], rows, limit=2)
    assert len(ranked) == 2


def test_main_prints_empty_array_for_empty_db(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    search.main(["some query"], db_path=db_path)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_main_prints_ranked_results(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj", "bugfix", "Fixed the login bug", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "t", "s", "/proj", "feature", "Added dark mode", "[]", pack_embedding([0.0, 1.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["login bug"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert results[0]["summary"] == "Fixed the login bug"


def test_main_includes_related_raw_event_ids(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(
        conn, "t", "s", "/proj", "bugfix", "Fixed the login bug", "[]", pack_embedding([1.0, 0.0]),
        related_raw_event_ids="[5, 6]",
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["login bug"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert results[0]["related_raw_event_ids"] == [5, 6]


def test_main_related_raw_event_ids_defaults_to_empty_list(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj", "bugfix", "Fixed the login bug", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["login bug"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert results[0]["related_raw_event_ids"] == []


def test_main_filters_by_project(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj-a", "change", "A thing", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "t", "s", "/proj-b", "change", "B thing", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["thing", "--project", "/proj-a"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert len(results) == 1
    assert results[0]["summary"] == "A thing"
