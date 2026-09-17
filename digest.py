#!/usr/bin/env python3
import sys
import os
import json
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_connection, derive_project, get_recent_observations
from lib.guard import hooks_disabled

DIGEST_LIMIT = 5
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def build_digest(observations: list) -> str:
    if not observations:
        return ""
    lines = ["Recent activity in this project (from claude-mem-lite):"]
    for obs in observations:
        lines.append(f"- [{obs['category']}] {obs['summary']}")
    return "\n".join(lines)


def main(db_path=None):
    if hooks_disabled():
        return
    try:
        payload = json.load(sys.stdin)
        cwd = payload.get("cwd", os.getcwd())
        project = derive_project(cwd)

        conn = get_connection(db_path)
        observations = get_recent_observations(conn, project, DIGEST_LIMIT)
        conn.close()

        text = build_digest(observations)
        if text:
            print(text)
    except Exception:
        try:
            with open(ERROR_LOG, "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
