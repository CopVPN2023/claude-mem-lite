---
name: mem-search
description: Search claude-mem-lite's local observation store for past session activity across all projects (semantic search, not just keyword matching).
---

# mem-search

Use this when you need to recall what happened in a past session — a fix,
a decision, a piece of context — that isn't in the curated MEMORY.md files.
This is a granular, automatic activity log, not curated memory: expect many
small, specific entries rather than high-level facts.

For "find me the entries about X" this is the right tool — cheap (no LLM
call), raw hits you inspect yourself. If the question instead needs
synthesis across several observations ("what have I actually done about X",
"did we ever fix Y"), use the `knowledge-agent` skill instead — it makes
one LLM call to turn the same underlying search into a cited, conversational
answer. If a specific hit needs its surrounding chronological context
("what led up to this", "what happened right after"), use the `timeline`
skill instead of re-searching around the same date. If a hit's `summary`
isn't precise enough and you need the literal command output or diff
behind it, use the `get-tool-uses` skill with that hit's
`related_raw_event_ids`.

Run:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/search.py "<natural language query>" [--project <path>] [--limit N]
```

Returns a JSON array of matches ordered by relevance, each with `id`, `ts`,
`project`, `category`, `summary`, and `related_raw_event_ids` (the raw tool
calls summarized into it — feed these to `get-tool-uses` for the literal
bytes). Omit `--project` to search across every project; pass the current
project's git root to scope to just it.

Cross-check anything safety-relevant against the actual code or git history
before relying on it — this log is a memory aid, not a source of truth.
