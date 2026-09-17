#!/usr/bin/env python3
import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_connection, get_observations
from lib.embeddings import embed_text, unpack_embedding, cosine_similarity


def rank_observations(query_vec, rows: list, limit: int) -> list:
    scored = []
    for row in rows:
        vec = unpack_embedding(row["embedding"])
        score = cosine_similarity(query_vec, vec)
        scored.append((score, row))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:limit]]


def main(argv=None, db_path=None):
    parser = argparse.ArgumentParser(description="Search claude-mem-lite observations")
    parser.add_argument("query")
    parser.add_argument("--project", default=None)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    conn = get_connection(db_path)
    rows = get_observations(conn, project=args.project)
    conn.close()

    if not rows:
        print(json.dumps([]))
        return

    query_vec = embed_text(args.query)
    top = rank_observations(query_vec, rows, args.limit)
    results = [
        {
            "id": r["id"], "ts": r["ts"], "project": r["project"],
            "category": r["category"], "summary": r["summary"],
            "related_raw_event_ids": json.loads(r["related_raw_event_ids"]) if r["related_raw_event_ids"] else [],
        }
        for r in top
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
