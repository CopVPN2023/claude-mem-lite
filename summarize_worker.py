#!/usr/bin/env python3
import json
import re

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
