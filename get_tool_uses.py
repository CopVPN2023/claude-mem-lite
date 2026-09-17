#!/usr/bin/env python3
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_raw_events_by_ids


def main(argv=None, db_path=None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch raw tool_input/tool_response for specific claude-mem-lite raw event ids"
    )
    parser.add_argument("--ids", required=True, help="Comma-separated raw_events ids")
    args = parser.parse_args(argv)

    try:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
    except ValueError:
        ids = []

    conn = get_connection(db_path)
    rows = get_raw_events_by_ids(conn, ids)
    conn.close()

    results = [
        {
            "id": r["id"], "ts": r["ts"], "project": r["project"],
            "tool_name": r["tool_name"], "tool_input": r["tool_input"],
            "tool_response": r["tool_response"],
        }
        for r in rows
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
