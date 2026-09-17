#!/usr/bin/env python3
import sys
import os
import json
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_connection, derive_project, insert_raw_event
from lib.guard import hooks_disabled

ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def main(db_path=None):
    if hooks_disabled():
        return
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id", "unknown")
        cwd = payload.get("cwd", os.getcwd())
        tool_name = payload.get("tool_name", "")
        tool_input = json.dumps(payload.get("tool_input", {}))[:500]
        project = derive_project(cwd)
        ts = datetime.now(timezone.utc).isoformat()

        conn = get_connection(db_path)
        insert_raw_event(conn, ts, session_id, project, tool_name, tool_input)
        conn.commit()
        conn.close()
    except Exception:
        try:
            with open(ERROR_LOG, "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
