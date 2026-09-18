#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_observations
from lib.guard import NO_HOOKS_ENV

CLAUDE_TIMEOUT_SECONDS = 300

REPORT_INSTRUCTIONS = """\
You are a technical historian analyzing a personal coding project's
complete development history, recorded as a chronological list of
observations (each with a timestamp, category, one-line summary, and
related files). Write a narrative report titled "Journey Into {project}"
covering:

1. **Genesis** -- when and how the project started, the earliest visible
   decisions and problems being solved.
2. **Architectural Evolution** -- how the approach changed over time, any
   major pivots and why.
3. **Key Breakthroughs** -- the "aha" moments where a hard problem got
   solved or a new approach unlocked progress.
4. **Work Patterns** -- the rhythm of development: debugging clusters,
   feature sprints, refactoring phases, exploration phases.
5. **Technical Debt** -- where shortcuts were taken and whether/when they
   were paid back.
6. **Challenges and Debugging Sagas** -- the hardest problems, especially
   ones that took multiple sessions or required backtracking.
7. **Timeline Statistics** -- date range, observation count, breakdown by
   category, most active periods -- computed from the observations below.
8. **Lessons and Meta-Observations** -- recurring themes a new contributor
   would benefit from knowing.

Cite observation timestamps when referencing specific events. Be honest
about struggles and dead ends, not just successes. Let the length match
the material -- do not pad a small project's history to hit a word count,
and do not truncate a large one artificially. Use markdown with headers.

Observations:
{observations}
"""


def estimate_tokens(observations: list) -> int:
    total_chars = sum(len(obs.get("summary", "")) for obs in observations)
    return total_chars // 4


def build_report_prompt(project: str, observations: list) -> str:
    lines = [
        f"- [{obs['ts']}] [{obs['category']}] {obs['summary']} "
        f"(files: {obs.get('related_files') or '[]'})"
        for obs in observations
    ]
    return REPORT_INSTRUCTIONS.format(project=project, observations="\n".join(lines))


def default_output_path(project: str) -> str:
    basename = os.path.basename(project.rstrip("/"))
    return f"./journey-into-{basename}.md"


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
        description="Generate a narrative development-history report for one project"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    conn = get_connection(db_path)
    observations = get_observations(conn, project=args.project)
    conn.close()

    if not observations:
        print(f"No observations found for project '{args.project}'.")
        return

    observations = sorted(observations, key=lambda o: o["ts"])

    if args.dry_run:
        count = len(observations)
        noun = "observation" if count == 1 else "observations"
        print(f"{count} {noun}, estimated ~{estimate_tokens(observations)} input tokens.")
        return

    prompt = build_report_prompt(args.project, observations)
    try:
        report = call_claude_headless(prompt)
    except Exception as exc:
        print(f"timeline_report: failed to generate report: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or default_output_path(args.project)
    with open(output_path, "w") as f:
        f.write(report)

    print(
        f"Report saved to {output_path} "
        f"({len(observations)} observations, {observations[0]['ts']} to {observations[-1]['ts']})."
    )


if __name__ == "__main__":
    main()
