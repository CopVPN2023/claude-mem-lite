---
name: timeline
description: Get chronological context around a specific claude-mem-lite observation — what happened right before and after it — instead of a single isolated hit.
---

# timeline

Use this after `mem-search` (or `knowledge-agent`) surfaces a specific
observation and you need to know what surrounded it — what led up to it,
what happened right after — not just the hit itself in isolation.

Run:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/timeline.py --anchor <id> [--project <path>] [--before N] [--after N]
```

or, to find the anchor automatically instead of already having an id:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/timeline.py --query "<text>" [--project <path>] [--before N] [--after N]
```

Returns `before + 1 + after` observations (default 3 each side) in
chronological order as JSON, the same shape as `mem-search`'s hits plus
`"is_anchor": true` on the centered one. No LLM call — pure retrieval,
same as `mem-search`. Prints `[]` if the anchor can't be resolved (bad id,
empty store, no query match, or — the most likely case in practice — a
valid id that just doesn't belong to the `--project` you filtered to,
since ids typically come from an unscoped `mem-search` result) rather than
erroring.

This also doubles as a lookup-by-id: `--anchor <id> --before 0 --after 0`
returns just that one observation, e.g. to get the `related_raw_event_ids`
for an id `knowledge-agent` cited but didn't have context around. Feed
those ids to the `get-tool-uses` skill for the literal tool output behind
it.

Cross-check anything safety-relevant against the actual code or git history
before relying on it — this log is a memory aid, not a source of truth.
