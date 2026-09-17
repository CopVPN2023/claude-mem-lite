import json

import get_tool_uses
from lib.db import get_connection, insert_raw_event


def test_main_prints_empty_array_for_empty_ids(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    get_tool_uses.main(["--ids", ""], db_path=db_path)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_main_returns_tool_input_and_response_for_given_ids(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    id1 = insert_raw_event(conn, "t", "s", "/proj", "Bash", '{"command": "ls"}', tool_response='{"stdout": "a.py"}')
    id2 = insert_raw_event(conn, "t", "s", "/proj", "Edit", '{"file_path": "a.py"}', tool_response='{"ok": true}')
    insert_raw_event(conn, "t", "s", "/proj", "Read", "{}", tool_response="ignored")
    conn.commit()
    conn.close()

    get_tool_uses.main(["--ids", f"{id1},{id2}"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert {r["id"] for r in results} == {id1, id2}
    by_id = {r["id"]: r for r in results}
    assert by_id[id1]["tool_response"] == '{"stdout": "a.py"}'
    assert by_id[id2]["tool_name"] == "Edit"


def test_main_ignores_unparseable_ids(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    get_tool_uses.main(["--ids", "not-a-number"], db_path=db_path)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
