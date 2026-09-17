---
name: mem-search
description: Search claude-mem-lite's local observation store for past session activity across all projects (semantic search, not just keyword matching).
---

# mem-search

Use this when you need to recall what happened in a past session — a fix,
a decision, a piece of context — that isn't in the curated MEMORY.md files.
This is a granular, automatic activity log, not curated memory: expect many
small, specific entries rather than high-level facts.

Run:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/search.py "<natural language query>" [--project <path>] [--limit N]
```

Returns a JSON array of matches ordered by relevance, each with `id`, `ts`,
`project`, `category`, `summary`. Omit `--project` to search across every
project; pass the current project's git root to scope to just it.

Cross-check anything safety-relevant against the actual code or git history
before relying on it — this log is a memory aid, not a source of truth.
