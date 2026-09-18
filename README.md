# claude-mem-lite

A personal, local replacement for [claude-mem](https://github.com/thedotmack/claude-mem)'s
automatic-capture + semantic-search capability — built so that no single
external allowance (API quota, provider outage) can take it down.

It is **not** a replacement for Claude Code's own curated memory
(`~/.claude/projects/*/memory/MEMORY.md`), which stays exactly as it is:
hand-picked durable facts and preferences. This is the complementary
layer — a comprehensive, low-judgment activity log, running *alongside*
whatever else you use for memory, not instead of it.

## Why

- **Capture never depends on a quota.** A `PostToolUse` hook writes raw
  activity straight to SQLite — no LLM call, so it can't fail from
  a rate limit or an exhausted allowance.
- **Search never depends on a quota.** Observations are embedded with a
  local model ([fastembed](https://github.com/qdrant/fastembed)), so
  semantic search runs fully offline.
- **The one stage that does call an LLM** — turning raw activity into
  short observations — uses headless Claude Code (`claude -p`), billed
  against the Claude usage you already track day to day, not a second
  hidden pool.

## Architecture

```
Claude Code session
   │
   ├─ PostToolUse hook ──────► capture.py ──► raw_events (SQLite)
   │                                             │
   ├─ Stop hook (per turn) ──► summarize_trigger.py ─┘ (spawns, detached)
   │                              │
   │                              └─ summarize_worker.py
   │                                    ├─ claude -p (headless, guarded)
   │                                    │     → 0-3 short observations
   │                                    └─ embed.py (local model)
   │                                          → observations (SQLite, w/ vector)
   │
   └─ SessionStart hook ──► digest.py ──► stdout ──► injected as context

   (on demand) ──► search.py "query"          ──► ranked raw hits
   (on demand) ──► knowledge_agent.py "question" ──► synthesized, cited answer
```

Everything lives in one SQLite database, `store.db`, shared across every
project (scoped internally by a `project` column). See
[DESIGN.md](DESIGN.md) for the full schema, failure-mode guarantees, and
the recursion-guard mechanism that keeps the nested `claude -p` call from
re-triggering these same hooks.

## Install

```bash
git clone <this repo> ~/claude-mem-lite
cd ~/claude-mem-lite
./setup.sh
```

This creates a venv, installs `fastembed` + `numpy`, initializes
`store.db`, warms the embedding model, and installs the `mem-search` and
`knowledge-agent` skills as symlinks into `~/.claude/skills/`. At the end
it prints the hooks snippet — merge it into `~/.claude/settings.json`
under the top-level `"hooks"` key (append to any arrays you already have
there for `PostToolUse` / `Stop` / `SessionStart`, don't overwrite them):

```json
{
  "hooks": {
    "PostToolUse": [
      { "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/capture.py" }] }
    ],
    "Stop": [
      { "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/summarize_trigger.py" }] }
    ],
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/digest.py" }] }
    ]
  }
}
```

## Usage

**Search** — fast, no LLM call, raw ranked hits:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/search.py "<query>" [--project <path>] [--limit N]
```

**Knowledge agent** — one `claude -p` call to synthesize a conversational,
cited answer across the top matches instead of a raw list:

```bash
~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/knowledge_agent.py "<question>" [--project <path>] [--limit N]
```

Both are also wrapped as skills (`mem-search`, `knowledge-agent`) so
Claude picks the right one on its own — raw hits for "find me the entries
about X", synthesis for "what have I actually done about X".

## Status

All 11 build tasks, a final whole-branch review, one fix wave, a scoped
re-review, and the live `settings.json` wiring are complete and verified
end-to-end (see `git log` for the full history). The knowledge agent was
added afterward as a follow-up feature.

One known, non-blocking issue: the lock file that prevents concurrent
`summarize_worker.py` runs doesn't carry an ownership token, so a run that
somehow exceeds the 300s stale-lock window could have its lock reclaimed
and then deleted out from under the new holder. No data is ever lost
(unprocessed rows always retry), and the trigger condition is narrow —
tracked as follow-up debt, not fixed yet.

## Privacy

Everything stays local in `~/claude-mem-lite/store.db` — nothing is
transmitted anywhere except the short summarization prompt sent to your
own `claude -p` call. Unlike shell history, this also captures raw tool
*output* — file contents from a `Read`, command stdout from a `Bash` call —
not just what you typed. A `Read` of a secrets file, or a `printenv`, now
has its contents on disk here. The database file is restricted to your own
user (`chmod 600`, applied automatically) and is git-ignored, but be
mindful of what that means if you ever share this machine or repo.
