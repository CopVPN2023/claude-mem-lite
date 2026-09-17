#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_observations
from lib.embeddings import embed_text
from lib.guard import NO_HOOKS_ENV
from search import rank_observations

CLAUDE_TIMEOUT_SECONDS = 120


def build_answer_prompt(question: str, observations: list) -> str:
    lines = [
        f"- #{obs['id']} [{obs['category']}] {obs['summary']} (files: {obs.get('related_files') or '[]'})"
        for obs in observations
    ]
    context = "\n".join(lines)
    return (
        "You are answering a question using a personal coding-activity history. "
        "Use ONLY the observations below as evidence; do not invent details not "
        "supported by them. Cite the observation IDs (e.g. \"(#42)\") that support "
        "each claim. If the observations don't answer the question, say so.\n\n"
        f"Observations:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer conversationally in a few sentences."
    )


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
    return result.stdout.strip()


def main(argv=None, db_path=None) -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question over claude-mem-lite's observation history"
    )
    parser.add_argument("question")
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)

    conn = get_connection(db_path)
    rows = get_observations(conn, project=args.project)
    conn.close()

    if not rows:
        print("No relevant history found.")
        return

    query_vec = embed_text(args.question)
    top = rank_observations(query_vec, rows, args.limit)
    prompt = build_answer_prompt(args.question, top)

    try:
        answer = call_claude_headless(prompt)
    except Exception as exc:
        print(f"knowledge_agent: failed to get an answer: {exc}", file=sys.stderr)
        sys.exit(1)

    print(answer)


if __name__ == "__main__":
    main()
