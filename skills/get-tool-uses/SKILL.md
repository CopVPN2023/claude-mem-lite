---
name: get-tool-uses
description: Fetch the exact raw tool_input/tool_response bytes behind a claude-mem-lite observation — the literal command output, diff, or API response an observation only summarized.
---

# get-tool-uses

Observations are *summaries*. Use this only when the answer needs the
literal bytes a tool returned — the exact command output, the exact diff,
the exact file content — not a paraphrase of it.

**Don't start here.** `mem-search` and `timeline` return an observation's
`related_raw_event_ids` — every raw tool call from the same turn, shared by
every observation that turn produced (not unique per observation — this can
be a lot of ids: one real turn produced 46 raw events shared across 3
observations, ~21KB if all fetched). `knowledge-agent` does not return
these ids (it only returns a prose answer citing observation ids); if it
cites an id you need raw events for, look it up with `timeline` instead.
Get the ids from `mem-search` or `timeline` first, then fetch:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/get_tool_uses.py --ids <id1,id2,...>
```

Returns each raw event's `id`, `ts`, `project`, `tool_name`, `tool_input`,
`tool_response` as JSON. `tool_response` is truncated to 2000 characters at
capture time (large Reads/Bash output get cut off, not omitted) — this is
raw, unsummarized data, so expect it to be noisier and more verbose than an
observation's `summary`.

Cross-check anything safety-relevant against the actual code or git history
before relying on it — this log is a memory aid, not a source of truth.
