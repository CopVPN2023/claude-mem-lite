#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.guard import hooks_disabled

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summarize_worker.py")
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def main():
    if hooks_disabled():
        return
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id")
        if not session_id:
            return
        subprocess.Popen(
            [sys.executable, WORKER, session_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        try:
            with open(ERROR_LOG, "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)
