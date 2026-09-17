# claude-mem-lite — design

## Why this exists

`claude-mem` (a third-party Claude Code plugin) gives Claude a running,
searchable memory of past sessions: an automatic activity log, short
human-readable "observations" (e.g. "Fixed exhibitor signup form field
alignment"), and semantic search over them, injected as a digest at the
start of each session. On 2026-09-15 its inference allowance ran out and it
stopped capturing anything, with no clear reset time under our control.

This is a personal, local replacement for that specific capability —
automatic capture + semantic recall — built so that no single external
allowance can take it down again. It is **not** a replacement for the
existing curated memory system (`~/.claude/projects/*/memory/MEMORY.md` and
friends), which stays exactly as it is: hand-picked durable facts, rules and
preferences that I (Claude) decide are worth keeping. This system is the
complementary layer — a comprehensive, low-judgment activity log, the kind
of detail the curated memory explicitly excludes (see its own "What NOT to
save" rules).

## Scope

- Global: one store shared across every project, matching how claude-mem
  itself behaved.
- Automatic: a `PostToolUse` hook captures raw activity with no LLM
  involved, so capture itself can never fail from a quota.
- Semantic search: observations are embedded with a **local** model, so
  search can never fail from a quota either.
- The one stage that does call an LLM (turning raw activity into short
  observations) uses headless Claude Code (`claude -p`) — billed against
  the Claude usage already tracked day to day, not a second hidden pool.

Non-goals for v1: multi-machine sync, a UI/dashboard beyond a CLI, retention
/ pruning policy, replicating claude-mem's token-savings analytics.

**Post-v1 addition:** a knowledge agent (`knowledge_agent.py`) — see
"Knowledge agent" under Architecture below — was added as a follow-up once
the base system was built, reviewed, merged, and wired in. It mirrors one
piece of claude-mem's own feature set (its `/knowledge-agent`) but, per the
YAGNI stance below, without a separate corpus build/prime/rebuild pipeline:
our `observations` table is already the corpus, embedded at write time.

## Architecture

```
Claude Code session
   │
   ├─ PostToolUse hook ──────► capture.py ──► raw_events (SQLite)
   │                                             │
   ├─ Stop hook (per turn) ──► summarize.py ─────┘ (reads unprocessed rows)
   │                              │
   │                              ├─ claude -p  (headless, no hooks)
   │                              │     → 0-3 short observations, JSON
   │                              └─ embed.py (local model)
   │                                    → observations (SQLite, with vector)
   │
   └─ SessionStart hook ──► digest.py ──► stdout ──► injected as context
                                │
   (on demand, via Skill) ──► search.py "query" ──► ranked observations
                                │
   (on demand, via Skill) ──► knowledge_agent.py "question"
                                ├─ search.py's ranking (reused, not duplicated)
                                └─ claude -p (headless, guarded) ──► one cited,
                                      synthesized answer over the top matches
```

### Storage

Single SQLite database, `~/claude-mem-lite/store.db`, shared by every
project (scoped internally by a `project` column, not by separate files —
one DB is simpler to query across projects, which is the whole point of
"global").

```sql
CREATE TABLE raw_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,        -- ISO 8601, UTC
  session_id    TEXT NOT NULL,
  project       TEXT NOT NULL,        -- git root path if inside a repo, else cwd
  tool_name     TEXT,
  tool_input    TEXT,                 -- trimmed/truncated JSON
  processed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_raw_session_unprocessed ON raw_events(session_id, processed);

CREATE TABLE observations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  project       TEXT NOT NULL,
  category      TEXT NOT NULL,        -- bugfix|feature|refactor|change|discovery|decision|security
  summary       TEXT NOT NULL,
  related_files TEXT,                 -- JSON array, optional
  embedding     BLOB NOT NULL         -- float32 vector, packed with struct
);
CREATE INDEX idx_obs_project ON observations(project);
```

`project` is derived once per hook call: `git rev-parse --show-toplevel`
when inside a repo, else the raw `cwd`. This is what "scoped per project but
one global store" means in practice — `search.py --project` filters on this
column; with no flag, search spans everything, same as claude-mem's
cross-session recall.

### Capture — `PostToolUse` hook

`capture.py` reads the hook's stdin JSON (`session_id`, `cwd`, `tool_name`,
`tool_input`, per the confirmed common hook fields), derives `project`,
truncates `tool_input` to its first 500 characters, and appends one row to
`raw_events`. No network call, no LLM, sub-millisecond — this stage cannot
fail from any quota, ever. Registered without a matcher (captures every
tool call); noise is filtered later, at summarization, not at capture.

### Summarization — `Stop` hook

Confirmed: `Stop` fires once per assistant turn finishing (not once per
session — that's `SessionEnd`), which is the right granularity for
frequent, small observations like claude-mem produced.

`summarize.py`:
1. Selects `raw_events WHERE session_id = ? AND processed = 0`.
2. Skips entirely if that set is empty, or contains fewer than 3 rows whose
   `tool_name` is not a read-only tool (`Read`, `Grep`, `Glob`, `WebSearch`)
   — no point paying for a summarization call when nothing changed.
3. Otherwise calls headless Claude Code (`claude -p`) with the raw tail and
   a prompt asking for 0–3 short observations as JSON
   (`{category, summary, related_files}` per item), using the categories
   above.
4. For each observation returned, computes its embedding (`embed.py`) and
   inserts a row into `observations`.
5. Marks the consumed `raw_events` rows `processed = 1` in the same
   transaction as the insert, so a crash mid-run can't double-summarize or
   silently drop events.

**Recursion hazard (must be handled, not just noted):** the `claude -p`
call is itself a Claude Code invocation, which could re-trigger this same
`Stop` hook and loop. The implementation must confirm and use whatever
headless-mode option suppresses hook execution for that nested call (or
run it from a context — e.g. a different `--settings` file — where these
hooks aren't loaded) before this ships. This gets verified empirically
during implementation, not assumed.

**Latency:** this must not add perceptible delay after every one of my
turns. Claude Code's hook system exposes `async`/`asyncRewake` fields;
implementation must use them (or an equivalent fire-and-forget pattern,
e.g. backgrounding the process and returning immediately) so `summarize.py`
runs off the interactive path.

### Embedding + search

`embed.py` wraps a local embedding model — **fastembed** (ONNX-based, no
PyTorch, ~100MB) rather than `sentence-transformers`, since it gets the
same "real sentence embeddings, fully offline" outcome with a much lighter
install. Model and its cache live under `~/claude-mem-lite/.model-cache/`.

`search.py "query" [--project X] [--limit N]`:
1. Embeds the query with the same model.
2. Loads candidate rows (optionally filtered by `project`) from
   `observations`.
3. Ranks by cosine similarity in memory (`numpy`) and prints the top N as
   JSON (id, ts, project, category, summary).

At the scale one person's sessions produce (thousands, not millions, of
observations), loading everything into memory for an in-process cosine
scan is simpler and fast enough — no vector database needed. This is
revisited only if it ever actually gets slow.

Wrapped in a Skill (`mem-search` or similar) so I have clear, consistent
instructions for when and how to call it via Bash, mirroring how I already
use `claude-mem`'s own search tools today.

### Knowledge agent — on-demand synthesis

`search.py` returns raw ranked hits — the right tool when the question is
"find me the entries about X." Some questions instead need synthesis across
several observations ("what have I actually done about X", "did we ever fix
Y"), where reading N raw hits yourself is worse than one synthesized answer.

`knowledge_agent.py "question" [--project X] [--limit N]`:
1. Embeds the question and retrieves the top N observations via the same
   ranking `search.py` already implements (imported directly — no
   duplicated retrieval logic, no separate index/corpus to keep in sync).
2. If nothing exists at all for the given scope, prints a short message and
   returns — no LLM call spent on an empty store.
3. Otherwise builds a prompt from the retrieved observations' summaries,
   categories, and related files, and calls headless `claude -p` (the same
   guarded invocation as the summarization stage: recursion guard env var,
   pinned `--model`, `--strict-mcp-config`) asking for a conversational
   answer that cites observation IDs.
4. Prints the synthesized answer to stdout.

This is an on-demand, interactive tool, not a background hook: unlike
`capture.py`/`summarize.py`/`digest.py`, a `claude -p` failure here fails
loud (clear stderr message, non-zero exit) rather than silently — there's a
person waiting on the answer, not a turn boundary to avoid blocking.

Known side effect, inherited from the summarization stage's nested-call
design and not specific to this feature: the nested `claude -p` call still
runs the user's *other* global hooks (e.g. claude-mem's own `SessionStart`
hook), since our recursion guard only suppresses our own scripts and
`--strict-mcp-config` only restricts MCP servers, not hooks. Harmless
today — worth revisiting only if another plugin's hook output starts
polluting answers more than cosmetically.

Wrapped in its own Skill (`knowledge-agent`), cross-referenced from
`mem-search`'s so the right one gets picked regardless of which is found
first.

### Recall UX — `SessionStart` hook

`digest.py` runs a query scoped to the current project (recent + most
relevant observations) and prints a short plain-text digest to stdout.
Confirmed mechanism: for `SessionStart`, plain stdout on exit 0 is injected
as a system reminder automatically — no special JSON envelope needed (that
envelope, `hookSpecificOutput.additionalContext`, is specifically for
`UserPromptSubmit`). This reproduces the "recent context" dump claude-mem
showed at the top of a session, sourced from our own store instead.

Must handle a cold/empty database gracefully (fresh install, or a brand
new project with no history yet) — print nothing or a one-line "no history
yet" note, never an error.

### Wiring

All of the above registered once in the **global** `~/.claude/settings.json`
so it applies to every project without per-project setup:

```json
{
  "hooks": {
    "PostToolUse": [{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/capture.py" }] }],
    "Stop":        [{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/summarize.py", "async": true }] }],
    "SessionStart":[{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/digest.py" }] }]
  }
}
```

(Exact field names for the async behavior get confirmed against the hooks
reference during implementation — the table above is the intent, not
necessarily final syntax.)

## Failure modes & guardrails

- **Every hook script fails safe.** A crash or non-zero exit in
  `capture.py`, `summarize.py`, or `digest.py` must never block or degrade
  the interactive session — wrap each in a top-level try/except that logs
  to a local error file and exits 0 regardless.
- **`claude -p` unavailable or erroring** (rate limited, offline, whatever)
  — `summarize.py` leaves the rows `processed = 0` and retries next turn;
  nothing is lost, capture is unaffected.
- **Local model missing on first run** — `embed.py`/model download happens
  once at install time (see below), not lazily mid-session, so a session
  is never blocked on a multi-hundred-MB download.
- **Privacy** — everything stays local in `~/claude-mem-lite/store.db`,
  nothing is transmitted anywhere. Worth remembering this file can contain
  fragments of commands/code you've run, same as your shell history.

## Install

A one-time `setup.sh`: creates a Python virtualenv under
`~/claude-mem-lite/.venv` (the system Python is 3.9.6 — old enough that a
venv, not a global pip install, is the right call), installs `fastembed`
+ `numpy`, initializes `store.db` from the schema above, downloads the
embedding model once, and prints the JSON snippet to add to
`~/.claude/settings.json` (or offers to merge it in directly).

## Verification plan

1. Feed `capture.py` a synthetic hook-JSON payload on stdin, confirm a row
   lands in `raw_events`.
2. Seed a few `raw_events` rows, run `summarize.py` by hand, confirm it
   calls `claude -p`, parses the response, writes `observations` rows with
   correctly-shaped embeddings, and flips `processed`.
3. Confirm the recursion guard: run `summarize.py` and verify no nested
   `Stop` hook fires from the `claude -p` call it makes.
4. Run `search.py` with a query related to a seeded observation, confirm
   it ranks at or near the top.
5. Run `digest.py` against an empty database (fresh install) and confirm
   it exits cleanly with no error.
6. Register the hooks for real in `~/.claude/settings.json`, work normally
   in a throwaway project for a few turns, confirm observations accumulate
   and a new session shows a digest.

## Open items for the implementation plan

- Exact async hook field name/behavior for `Stop` (confirm against the
  full hooks reference, not just the summary this design used).
- Exact flag/mechanism to suppress hooks on the nested `claude -p` call.
- Truncation limits for `tool_input` in `raw_events` (large file edits
  shouldn't bloat the raw log unboundedly).
