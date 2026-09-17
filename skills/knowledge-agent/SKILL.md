---
name: knowledge-agent
description: Ask a natural-language question over claude-mem-lite's observation history and get a synthesized, conversational answer with citations — use instead of mem-search when you want an answer, not a list of hits.
---

# knowledge-agent

Use this when the question needs synthesis across several past observations
("what have I actually done about X", "did we ever fix Y", "how did we
handle Z last time") rather than a ranked list to skim yourself. For "find
me the entries about X" prefer the `mem-search` skill instead — it's cheaper
(no LLM call) and returns raw hits you can inspect directly.

Run:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/knowledge_agent.py "<question>" [--project <path>] [--limit N]
```

Retrieves the top `N` (default 8) most relevant observations by the same
semantic search `mem-search` uses, then makes one headless `claude -p` call
to synthesize a conversational answer that cites observation IDs (e.g.
`(#42)`) it drew on. Omit `--project` to draw from every project.

Prints "No relevant history found." and exits with no LLM call when the
store (or the given project) has no observations at all. On a `claude -p`
failure, prints an error to stderr and exits non-zero — this is an
interactive on-demand tool, not a background hook, so it fails loud rather
than silently.

Cross-check anything safety-relevant against the actual code or git history
before relying on the answer — it's synthesized from a memory aid, not a
source of truth, and can be wrong or incomplete if the underlying
observations are.
