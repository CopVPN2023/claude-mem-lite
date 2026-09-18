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
   ├─ Stop hook (per turn) ──► summarize_trigger.py ─┘ (spawns, detached)
   │                              │
   │                              └─ summarize_worker.py
   │                                    ├─ claude -p (headless, guarded)
   │                                    │     → 0-3 short observations, JSON
   │                                    └─ embed.py (local model)
   │                                          → observations (SQLite, with vector)
   │
   └─ SessionStart hook ──► digest.py ──► stdout ──► injected as context

   (on demand, via Skill) ──► search.py "query" ──► ranked observations
   (on demand, via Skill) ──► timeline.py --anchor/--query ──► chronological window
   (on demand, via Skill) ──► knowledge_agent.py "question"
                                ├─ search.py's ranking (reused, not duplicated)
                                └─ claude -p (headless, guarded) ──► one cited,
                                      synthesized answer over the top matches
   (on demand, via Skill) ──► get_tool_uses.py --ids ... ──► raw tool_input/tool_response
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
  tool_input    TEXT,                 -- trimmed/truncated JSON (first 500 chars)
  tool_response TEXT,                 -- trimmed/truncated JSON (first 2000 chars), optional
  processed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_raw_session_unprocessed ON raw_events(session_id, processed);

CREATE TABLE observations (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                    TEXT NOT NULL,
  session_id            TEXT NOT NULL,
  project               TEXT NOT NULL,
  category              TEXT NOT NULL,        -- bugfix|feature|refactor|change|discovery|decision|security
  summary               TEXT NOT NULL,
  related_files         TEXT,                 -- JSON array, optional
  embedding             BLOB NOT NULL,        -- float32 vector, packed with struct
  related_raw_event_ids TEXT                  -- JSON array of raw_events.id, optional
);
CREATE INDEX idx_obs_project ON observations(project);
```

`tool_response` and `related_raw_event_ids` were added post-v1 (see
"Raw tool-use lookup" below); both are nullable so old rows are
unaffected, and existing installs upgrade via an idempotent
`ALTER TABLE ... ADD COLUMN` run on every `get_connection()` call rather
than a one-time migration step. `store.db` is `chmod 600`'d for the same
reason — `tool_response` now persists real command/file *output*, not
just the command/input `tool_input` already captured (see Privacy below).

`project` is derived once per hook call: `git rev-parse --show-toplevel`
when inside a repo, else the raw `cwd`. This is what "scoped per project but
one global store" means in practice — `search.py --project` filters on this
column; with no flag, search spans everything, same as claude-mem's
cross-session recall.

### Capture — `PostToolUse` hook

`capture.py` reads the hook's stdin JSON (`session_id`, `cwd`, `tool_name`,
`tool_input`, `tool_response`, per the confirmed common hook fields —
`json.load(..., strict=False)` since a real payload has contained a raw
control character), derives `project`, truncates `tool_input` to its
first 500 characters and `tool_response` to its first 2000, and appends
one row to `raw_events`. No network call, no LLM, sub-millisecond — this
stage cannot fail from any quota, ever. Registered without a matcher
(captures every tool call); noise is filtered later, at summarization,
not at capture.

### Summarization — `Stop` hook

Confirmed: `Stop` fires once per assistant turn finishing (not once per
session — that's `SessionEnd`), which is the right granularity for
frequent, small observations like claude-mem produced.

Split across two scripts: `summarize_trigger.py` (the actual `Stop` hook —
reads stdin, spawns the worker fully detached via `subprocess.Popen(...,
start_new_session=True)`, and returns immediately, so the hook itself adds
no latency) and `summarize_worker.py` (the detached process that does the
real work, guarded by a per-session lock file so two overlapping Stop
events can't both summarize the same rows):

1. Selects `raw_events WHERE session_id = ? AND processed = 0`.
2. Skips entirely if that set is empty, or contains fewer than 3 rows whose
   `tool_name` is not a read-only tool (`Read`, `Grep`, `Glob`, `WebSearch`)
   — no point paying for a summarization call when nothing changed.
3. Otherwise calls headless Claude Code (`claude -p`) with the raw tail and
   a prompt asking for 0–3 short observations as JSON
   (`{category, summary, related_files}` per item), using the categories
   above.
4. For each observation returned, computes its embedding (`embed.py`) and
   inserts a row into `observations`, tagged with `related_raw_event_ids`
   — the ids of every raw event in that batch. This is per-turn, not
   per-observation: if a turn produces 3 observations, all 3 share the
   identical id list (the LLM doesn't report which specific raw event each
   individual observation came from). See "Raw tool-use lookup" below.
5. Marks the consumed `raw_events` rows `processed = 1` in the same
   transaction as the insert, so a crash mid-run can't double-summarize or
   silently drop events. A `claude -p` call that fails (non-zero exit,
   timeout) raises, is caught, logged to `error.log`, and leaves the rows
   `processed = 0` for the next Stop event to retry — nothing is silently
   lost.

**Recursion hazard — resolved:** the `claude -p` call is itself a Claude
Code invocation, which could re-trigger this same `Stop` hook and loop.
Solved with an env-var sentinel (`CLAUDE_MEM_LITE_NO_HOOKS=1`, set on the
nested call and checked by `lib/guard.py`'s `hooks_disabled()` at the top
of every one of our own hook scripts), not any unconfirmed Claude Code
CLI flag. Verified empirically more than once, including by probe hooks
in a throwaway project during the final whole-branch review — no path
where a nested tool call escapes the guard.

**Latency — resolved:** `summarize_trigger.py` reads stdin, spawns
`summarize_worker.py` fully detached (`subprocess.Popen(...,
start_new_session=True)`, all streams `DEVNULL`) and returns immediately —
deliberately not relying on Claude Code's own `async`/`asyncRewake` hook
JSON fields, which weren't confirmed reliable enough to depend on.

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
   JSON (id, ts, project, category, summary, related_raw_event_ids — the
   last one is how a hit connects down to "Raw tool-use lookup" below).

At the scale one person's sessions produce (thousands, not millions, of
observations), loading everything into memory for an in-process cosine
scan is simpler and fast enough — no vector database needed. This is
revisited only if it ever actually gets slow.

Wrapped in a Skill (`mem-search` or similar) so I have clear, consistent
instructions for when and how to call it via Bash, mirroring how I already
use `claude-mem`'s own search tools today.

### Timeline — chronological context

A single search hit is isolated — no sense of what led up to it or what
happened right after. `timeline.py --anchor <id> [--before N] [--after N]`
(default 3 each side) sorts observations by `ts`, finds the anchor's
position, and returns that window as JSON, same shape as `mem-search`'s
hits plus `"is_anchor": true` on the centered one. `--query "<text>"`
resolves the anchor automatically via `search.py`'s own ranking instead of
requiring a known id. No LLM call — pure retrieval, same as `mem-search`.
`--anchor <id> --before 0 --after 0` doubles as a plain lookup-by-id, e.g.
to get an id's `related_raw_event_ids` when `knowledge_agent.py` cited it
without surrounding context. Wrapped in its own Skill (`timeline`).

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
`capture.py`/`summarize_trigger.py`/`summarize_worker.py`/`digest.py`, a
`claude -p` failure here fails loud (clear stderr message, non-zero exit)
rather than silently — there's a person waiting on the answer, not a turn
boundary to avoid blocking.

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

### Raw tool-use lookup

Observations, search hits, and knowledge-agent answers are all
*summaries*. `get_tool_uses.py --ids <id1,id2,...>` fetches the literal
`tool_input`/`tool_response` bytes behind specific `raw_events` rows —
the exact command output, diff, or file content a summary only
paraphrased. No LLM call, no ranking (direct id lookup, output reordered
to match the requested id order regardless of storage order).

The ids to fetch come from an observation's `related_raw_event_ids`
(returned by `search.py`/`timeline.py`), which is a **per-turn batch, not
per-observation provenance** — `claude -p` isn't asked which raw event
produced which specific observation, so every observation from one Stop
event's summarization batch shares the identical id list. In practice
this can be a lot of ids (a real batch: 46 raw events shared by 3
observations from the same turn, ~21KB if fetched in full) — fetch only
when the summary genuinely isn't enough, not as a first move.

Wrapped in its own Skill (`get-tool-uses`), which explicitly does *not*
claim `knowledge_agent.py` returns `related_raw_event_ids` (it doesn't —
its only output is prose with inline `(#N)` citations; use `timeline.py`
to look up an id it cited).

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
    "PostToolUse":  [{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/capture.py" }] }],
    "Stop":         [{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/summarize_trigger.py" }] }],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/digest.py" }] }]
  }
}
```

(This is `settings-snippet.json`'s actual shipped content. No `"async"`
field — see "Latency — resolved" above for why: the detached-`Popen`
pattern in `summarize_trigger.py` made the hook JSON's own async fields
unnecessary.)

## Failure modes & guardrails

- **Every hook script fails safe.** A crash or non-zero exit in
  `capture.py`, `summarize_trigger.py`, `summarize_worker.py`, or
  `digest.py` must never block or degrade the interactive session — each
  wraps its risky work in a top-level try/except that logs to `error.log`
  and exits 0 regardless.
- **`claude -p` unavailable or erroring** (rate limited, offline, whatever)
  — `summarize_worker.py` leaves the rows `processed = 0` and retries next
  turn; nothing is lost, capture is unaffected.
- **Local model missing on first run** — `embed.py`/model download happens
  once at install time (see below), not lazily mid-session, so a session
  is never blocked on a multi-hundred-MB download.
- **Privacy** — everything stays local in `~/claude-mem-lite/store.db`,
  nothing is transmitted anywhere. Unlike shell history, this also captures
  tool *output* (`tool_response`) — file contents from a `Read`, command
  stdout from a `Bash` call — not just the command/input. A `Read` of a
  secrets file or a `printenv` now has its output on disk here. The file's
  permissions are restricted to the owning user (`chmod 600`, applied by
  `get_connection()`), but be mindful of what that means if this machine or
  repo is ever shared.
- **Concurrent access** — `get_connection()` sets `PRAGMA journal_mode=WAL`
  and a 5s `busy_timeout` to shrink (not eliminate) the window where a
  concurrent writer could make `capture.py` see `database is locked` and
  silently drop an event (unlike `summarize_worker.py`, `capture.py` has
  no retry path — a dropped capture is gone). Real write transactions in
  this codebase are sub-millisecond, so the residual risk is small.

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
2. Seed a few `raw_events` rows, run `summarize_worker.py` by hand, confirm
   it calls `claude -p`, parses the response, writes `observations` rows
   with correctly-shaped embeddings and `related_raw_event_ids`, and flips
   `processed`.
3. Confirm the recursion guard: run `summarize_worker.py` and verify no
   nested `Stop` hook fires from the `claude -p` call it makes.
4. Run `search.py` with a query related to a seeded observation, confirm
   it ranks at or near the top.
5. Run `digest.py` against an empty database (fresh install) and confirm
   it exits cleanly with no error.
6. Register the hooks for real in `~/.claude/settings.json`, work normally
   in a throwaway project for a few turns, confirm observations accumulate
   and a new session shows a digest.

All six were run for real (not just unit-tested) during the original
implementation's final whole-branch review, and steps 2-4 again after each
follow-up feature (`timeline.py`, `knowledge_agent.py`, `get_tool_uses.py`)
via its own whole-codebase review and fix wave. See `git log` for the
history — `README.md`'s Status section has the summary.

## Resolved during implementation

These were open questions when this design was first written; kept here
as a record of what got decided, not as pending work:

- **Async hook behavior for `Stop`** — resolved by not depending on it.
  See "Latency — resolved" above: `summarize_trigger.py`'s detached
  `Popen` pattern made the hook JSON's own async fields unnecessary.
- **Recursion-suppression mechanism** — resolved with an env-var sentinel
  (`CLAUDE_MEM_LITE_NO_HOOKS`) checked in our own code, not a Claude Code
  CLI flag. See "Recursion hazard — resolved" above.
- **`tool_input` truncation limit** — set to 500 characters (`capture.py`).
  `tool_response`, added post-v1, is truncated to 2000 characters
  separately — see "Raw tool-use lookup" above and the Privacy note.
