#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_unprocessed_events, mark_processed, insert_observation
from lib.embeddings import embed_text, pack_embedding
from lib.guard import NO_HOOKS_ENV

ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
LOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".locks")
LOCK_STALE_SECONDS = 300

READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "WebSearch"}
CATEGORIES = {"bugfix", "feature", "refactor", "change", "discovery", "decision", "security"}


def _acquire_lock(session_id: str):
    """One worker per session. Returns the lock path, or None if another holds it."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(LOCK_DIR, f"{session_id}.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return lock_path
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock_path) > LOCK_STALE_SECONDS:
                os.remove(lock_path)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return lock_path
        except (FileNotFoundError, FileExistsError):
            pass
        return None


def skip_summarization(events: list) -> bool:
    if not events:
        return True
    substantive = [e for e in events if e.get("tool_name") not in READ_ONLY_TOOLS]
    return len(substantive) < 3


def build_summarize_prompt(events: list) -> str:
    lines = [f"- {e['tool_name']}: {e['tool_input']}" for e in events]
    activity = "\n".join(lines)
    return (
        "Given this raw tool-activity log from a coding session, extract 0-3 short, "
        "human-readable observations about what was actually accomplished. Ignore "
        "routine reads that didn't lead anywhere.\n\n"
        "Return ONLY a JSON array (no prose, no markdown fences). Each item:\n"
        '{"category": one of bugfix|feature|refactor|change|discovery|decision|security, '
        '"summary": "one sentence", "related_files": ["path", ...]}\n'
        "If nothing noteworthy happened, return [].\n\n"
        f"Activity log:\n{activity}"
    )


def parse_observations(response_text: str) -> list:
    text = response_text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not summary or not isinstance(summary, str):
            continue
        category = item.get("category")
        if category not in CATEGORIES:
            category = "change"
        related_files = item.get("related_files")
        if not isinstance(related_files, list):
            related_files = []
        results.append({"category": category, "summary": summary, "related_files": related_files})
    return results


CLAUDE_TIMEOUT_SECONDS = 120


def call_claude_headless(prompt: str) -> str:
    env = os.environ.copy()
    env[NO_HOOKS_ENV] = "1"
    result = subprocess.run(
        ["claude", "-p", "--model", "sonnet", "--strict-mcp-config"],
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        timeout=CLAUDE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p exited {result.returncode}: {result.stderr or result.stdout}"
        )
    return result.stdout


def main(session_id: str, db_path=None) -> None:
    lock_path = None
    conn = None
    try:
        lock_path = _acquire_lock(session_id)
        if lock_path is None:
            return
        conn = get_connection(db_path)
        events = get_unprocessed_events(conn, session_id)
        if skip_summarization(events):
            return

        response = call_claude_headless(build_summarize_prompt(events))
        observations = parse_observations(response)

        ts = datetime.now(timezone.utc).isoformat()
        project = events[0]["project"]
        raw_event_ids = json.dumps([e["id"] for e in events])
        for obs in observations:
            vec = embed_text(obs["summary"])
            blob = pack_embedding(vec)
            insert_observation(
                conn, ts, session_id, project,
                obs["category"], obs["summary"],
                json.dumps(obs["related_files"]), blob,
                raw_event_ids,
            )

        # Only after everything that can fail has succeeded: any exception above
        # leaves the rows processed = 0 so the next Stop hook retries them.
        mark_processed(conn, [e["id"] for e in events])
        conn.commit()
    except Exception:
        try:
            with open(ERROR_LOG, "a") as f:
                f.write(traceback.format_exc() + "\n")
        except Exception:
            pass
    finally:
        if conn is not None:
            conn.close()
        if lock_path is not None:
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main(sys.argv[1])
    sys.exit(0)
