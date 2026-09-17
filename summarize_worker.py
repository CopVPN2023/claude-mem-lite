#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_unprocessed_events, mark_processed, insert_observation
from lib.embeddings import embed_text, pack_embedding
from lib.guard import NO_HOOKS_ENV

READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "WebSearch"}
CATEGORIES = {"bugfix", "feature", "refactor", "change", "discovery", "decision", "security"}


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
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        timeout=CLAUDE_TIMEOUT_SECONDS,
    )
    return result.stdout


def main(session_id: str, db_path=None) -> None:
    conn = get_connection(db_path)
    events = get_unprocessed_events(conn, session_id)
    if skip_summarization(events):
        conn.close()
        return

    prompt = build_summarize_prompt(events)
    response = call_claude_headless(prompt)
    observations = parse_observations(response)

    ts = datetime.now(timezone.utc).isoformat()
    project = events[0]["project"]
    for obs in observations:
        vec = embed_text(obs["summary"])
        blob = pack_embedding(vec)
        insert_observation(
            conn, ts, session_id, project,
            obs["category"], obs["summary"],
            json.dumps(obs["related_files"]), blob,
        )

    mark_processed(conn, [e["id"] for e in events])
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
    sys.exit(0)
