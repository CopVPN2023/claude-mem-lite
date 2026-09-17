#!/usr/bin/env python3
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.db import get_connection, get_observations
from lib.embeddings import embed_text
from search import rank_observations

DEFAULT_DEPTH = 3


def select_window(sorted_rows: list, anchor_id: int, before: int, after: int) -> list:
    idx = next((i for i, row in enumerate(sorted_rows) if row["id"] == anchor_id), None)
    if idx is None:
        return []
    start = max(0, idx - before)
    end = idx + after + 1
    window = []
    for i, row in enumerate(sorted_rows[start:end], start=start):
        item = dict(row)
        item["is_anchor"] = (i == idx)
        window.append(item)
    return window


def resolve_anchor_id(rows: list, args):
    if args.anchor is not None:
        return args.anchor
    if not rows:
        return None
    query_vec = embed_text(args.query)
    top = rank_observations(query_vec, rows, limit=1)
    return top[0]["id"] if top else None


def main(argv=None, db_path=None) -> None:
    parser = argparse.ArgumentParser(
        description="Chronological context around a claude-mem-lite observation"
    )
    anchor_group = parser.add_mutually_exclusive_group(required=True)
    anchor_group.add_argument("--anchor", type=int, default=None)
    anchor_group.add_argument("--query", default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--before", type=int, default=DEFAULT_DEPTH)
    parser.add_argument("--after", type=int, default=DEFAULT_DEPTH)
    args = parser.parse_args(argv)

    conn = get_connection(db_path)
    rows = get_observations(conn, project=args.project)
    conn.close()

    anchor_id = resolve_anchor_id(rows, args)
    if anchor_id is None:
        print(json.dumps([]))
        return

    sorted_rows = sorted(rows, key=lambda r: r["ts"])
    window = select_window(sorted_rows, anchor_id, args.before, args.after)

    results = [
        {
            "id": r["id"], "ts": r["ts"], "project": r["project"],
            "category": r["category"], "summary": r["summary"],
            "is_anchor": r["is_anchor"],
        }
        for r in window
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
