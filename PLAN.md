# claude-mem-lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, quota-proof replacement for the claude-mem plugin's
capture + semantic-search capability: a `PostToolUse` hook logs raw
activity, a `Stop` hook turns it into short observations via headless
Claude Code, a local embedding model makes those observations searchable
by meaning, and a `SessionStart` hook surfaces a digest automatically.

**Architecture:** Five small Python scripts (`capture.py`,
`summarize_trigger.py`, `summarize_worker.py`, `digest.py`, `search.py`)
share two library modules (`lib/db.py` for SQLite storage,
`lib/embeddings.py` for local embeddings) and a small `lib/guard.py` that
prevents the headless-Claude summarization call from re-triggering its own
hook. Everything is wired into the global `~/.claude/settings.json`.

**Tech Stack:** Python 3.9 (system default) in a project-local venv,
`sqlite3` (stdlib), `fastembed` (local ONNX embedding model, no PyTorch),
`numpy`, `pytest` for tests.

**Spec:** `/Users/mdainalhaque/claude-mem-lite/DESIGN.md`

## Global Constraints

- Every hook script (`capture.py`, `summarize_trigger.py`, `digest.py`)
  must **never raise past its own `main()`** — all exceptions are caught,
  logged to `error.log`, and the process exits cleanly. A bug in this tool
  must never degrade or block a real Claude Code session.
- `tool_input` is truncated to its first 500 characters before being
  stored (DESIGN.md, Capture section).
- Summarization is skipped when a session's unprocessed events contain
  fewer than 3 non-read-only tool calls (`Read`, `Grep`, `Glob`,
  `WebSearch` count as read-only) — DESIGN.md, Summarization section.
- The nested `claude -p` call used for summarization must set the
  environment variable `CLAUDE_MEM_LITE_NO_HOOKS=1`, and every one of our
  own hook scripts must check `lib.guard.hooks_disabled()` first and no-op
  if it's set. This is the recursion guard — it is enforced in our own
  code, not by relying on an unconfirmed Claude Code CLI flag.
- The `Stop` hook must never add perceptible latency to a turn:
  `summarize_trigger.py` reads its stdin, then spawns
  `summarize_worker.py` as a fully detached background process
  (`subprocess.Popen(..., start_new_session=True)`) and returns
  immediately — it does not wait on the worker.
- Categories for observations are exactly:
  `bugfix | feature | refactor | change | discovery | decision | security`
  (DESIGN.md summarization section). An LLM response with an unrecognized
  category is coerced to `change`, never dropped.
- Nothing in this system ever calls a network service other than headless
  Claude Code itself — embeddings are local, storage is local.

## File Structure

```
~/claude-mem-lite/
  DESIGN.md                  (already written)
  PLAN.md                    (this file)
  requirements.txt
  requirements-dev.txt
  setup.sh
  settings-snippet.json
  .gitignore
  store.db                   (created by setup.sh; gitignored)
  error.log                  (created at runtime; gitignored)
  lib/
    __init__.py
    db.py                    (schema, connection, raw events, observations)
    guard.py                 (recursion guard)
    embeddings.py            (local embedding model wrapper)
  capture.py                 (PostToolUse hook)
  summarize_trigger.py       (Stop hook — thin, spawns the worker)
  summarize_worker.py        (actual summarization logic, runs detached)
  digest.py                  (SessionStart hook)
  search.py                  (on-demand CLI, wrapped by a Skill)
  skills/mem-search/SKILL.md
  tests/
    conftest.py
    test_db.py
    test_guard.py
    test_capture.py
    test_embeddings.py
    test_summarize_worker.py
    test_summarize_trigger.py
    test_digest.py
    test_search.py
```

---

### Task 1: Storage foundation — schema, connection, project derivation, raw events

**Files:**
- Create: `lib/__init__.py` (empty)
- Create: `lib/db.py`
- Create: `tests/conftest.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Produces: `DB_PATH: Path`, `get_connection(db_path=None) -> sqlite3.Connection`
  (rows come back as `sqlite3.Row`, schema is created if missing),
  `derive_project(cwd: str) -> str`,
  `insert_raw_event(conn, ts: str, session_id: str, project: str, tool_name: str, tool_input: str) -> int`,
  `get_unprocessed_events(conn, session_id: str) -> list[dict]`,
  `mark_processed(conn, ids: list[int]) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

`tests/test_db.py`:
```python
import subprocess
from lib.db import (
    get_connection,
    derive_project,
    insert_raw_event,
    get_unprocessed_events,
    mark_processed,
)


def test_get_connection_creates_schema(tmp_path):
    db_path = tmp_path / "test.db"
    conn = get_connection(str(db_path))
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "raw_events" in tables
    assert "observations" in tables


def test_derive_project_inside_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    result = derive_project(str(repo))
    assert result == str(repo.resolve())


def test_derive_project_outside_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = derive_project(str(plain))
    assert result == str(plain)


def test_insert_and_get_unprocessed_events(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", '{"file": "a.py"}')
    insert_raw_event(conn, "2026-01-01T00:00:01Z", "sess1", "/proj", "Read", '{"file": "b.py"}')
    insert_raw_event(conn, "2026-01-01T00:00:02Z", "sess2", "/proj", "Edit", '{"file": "c.py"}')
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 2
    assert events[0]["tool_name"] == "Edit"
    assert events[1]["tool_name"] == "Read"


def test_mark_processed_excludes_from_unprocessed(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    id1 = insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", "{}")
    id2 = insert_raw_event(conn, "2026-01-01T00:00:01Z", "sess1", "/proj", "Edit", "{}")
    conn.commit()

    mark_processed(conn, [id1])
    conn.commit()

    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 1
    assert events[0]["id"] == id2


def test_mark_processed_with_empty_list_does_nothing(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    insert_raw_event(conn, "2026-01-01T00:00:00Z", "sess1", "/proj", "Edit", "{}")
    conn.commit()
    mark_processed(conn, [])  # must not raise
    assert len(get_unprocessed_events(conn, "sess1")) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/claude-mem-lite && python3 -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lib'` (or similar) — `lib/db.py` doesn't exist yet.

- [ ] **Step 3: Write the implementation**

`lib/__init__.py`: (empty file)

`lib/db.py`:
```python
import sqlite3
import subprocess
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "store.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  tool_name TEXT,
  tool_input TEXT,
  processed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_session_unprocessed ON raw_events(session_id, processed);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  project TEXT NOT NULL,
  category TEXT NOT NULL,
  summary TEXT NOT NULL,
  related_files TEXT,
  embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_project ON observations(project);
"""


def get_connection(db_path=None):
    path = str(db_path) if db_path else str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def derive_project(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return cwd


def insert_raw_event(conn, ts, session_id, project, tool_name, tool_input) -> int:
    cur = conn.execute(
        "INSERT INTO raw_events (ts, session_id, project, tool_name, tool_input, processed) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (ts, session_id, project, tool_name, tool_input),
    )
    return cur.lastrowid


def get_unprocessed_events(conn, session_id: str) -> list:
    cur = conn.execute(
        "SELECT * FROM raw_events WHERE session_id = ? AND processed = 0 ORDER BY id",
        (session_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def mark_processed(conn, ids: list) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE raw_events SET processed = 1 WHERE id IN ({placeholders})", ids
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/claude-mem-lite && python3 -m pytest tests/test_db.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd ~/claude-mem-lite
git add lib/__init__.py lib/db.py tests/conftest.py tests/test_db.py
git commit -m "feat: storage foundation — schema, connection, raw event log"
```

---

### Task 2: Recursion guard

**Files:**
- Create: `lib/guard.py`
- Create: `tests/test_guard.py`

**Interfaces:**
- Produces: `NO_HOOKS_ENV: str` (value `"CLAUDE_MEM_LITE_NO_HOOKS"`),
  `hooks_disabled() -> bool`

- [ ] **Step 1: Write the failing test**

`tests/test_guard.py`:
```python
from lib.guard import hooks_disabled, NO_HOOKS_ENV


def test_hooks_disabled_false_by_default(monkeypatch):
    monkeypatch.delenv(NO_HOOKS_ENV, raising=False)
    assert hooks_disabled() is False


def test_hooks_disabled_true_when_env_set(monkeypatch):
    monkeypatch.setenv(NO_HOOKS_ENV, "1")
    assert hooks_disabled() is True


def test_hooks_disabled_false_for_other_values(monkeypatch):
    monkeypatch.setenv(NO_HOOKS_ENV, "0")
    assert hooks_disabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_guard.py -v`
Expected: FAIL — `lib.guard` doesn't exist.

- [ ] **Step 3: Write the implementation**

`lib/guard.py`:
```python
import os

NO_HOOKS_ENV = "CLAUDE_MEM_LITE_NO_HOOKS"


def hooks_disabled() -> bool:
    return os.environ.get(NO_HOOKS_ENV) == "1"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_guard.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add lib/guard.py tests/test_guard.py
git commit -m "feat: recursion guard for nested headless-claude calls"
```

---

### Task 3: `capture.py` — PostToolUse hook

**Files:**
- Create: `capture.py`
- Create: `tests/test_capture.py`

**Interfaces:**
- Consumes: `lib.db.get_connection(db_path=None)`, `lib.db.derive_project(cwd)`,
  `lib.db.insert_raw_event(conn, ts, session_id, project, tool_name, tool_input)`,
  `lib.guard.hooks_disabled()`
- Produces: `main(db_path=None) -> None` (reads a hook JSON payload from
  `sys.stdin`)

- [ ] **Step 1: Write the failing test**

`tests/test_capture.py`:
```python
import io
import json

import capture
from lib.db import get_connection, get_unprocessed_events


def test_capture_inserts_row(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": "a.py"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events) == 1
    assert events[0]["tool_name"] == "Edit"
    assert "a.py" in events[0]["tool_input"]


def test_capture_truncates_large_tool_input(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    payload = {
        "session_id": "sess1",
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"content": "x" * 5000},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    events = get_unprocessed_events(conn, "sess1")
    assert len(events[0]["tool_input"]) <= 500


def test_capture_never_raises_on_malformed_stdin(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    capture.main(db_path=str(db_path))  # must not raise


def test_capture_noop_when_hooks_disabled(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("CLAUDE_MEM_LITE_NO_HOOKS", "1")
    payload = {"session_id": "sess1", "cwd": str(tmp_path), "tool_name": "Edit", "tool_input": {}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    capture.main(db_path=str(db_path))

    conn = get_connection(str(db_path))
    assert get_unprocessed_events(conn, "sess1") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_capture.py -v`
Expected: FAIL — `capture.py` doesn't exist.

- [ ] **Step 3: Write the implementation**

`capture.py`:
```python
#!/usr/bin/env python3
import sys
import os
import json
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_connection, derive_project, insert_raw_event
from lib.guard import hooks_disabled

ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def main(db_path=None):
    if hooks_disabled():
        return
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id", "unknown")
        cwd = payload.get("cwd", os.getcwd())
        tool_name = payload.get("tool_name", "")
        tool_input = json.dumps(payload.get("tool_input", {}))[:500]
        project = derive_project(cwd)
        ts = datetime.now(timezone.utc).isoformat()

        conn = get_connection(db_path)
        insert_raw_event(conn, ts, session_id, project, tool_name, tool_input)
        conn.commit()
        conn.close()
    except Exception:
        with open(ERROR_LOG, "a") as f:
            f.write(traceback.format_exc() + "\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_capture.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add capture.py tests/test_capture.py
git commit -m "feat: capture.py PostToolUse hook"
```

---

### Task 4: Local embeddings

**Files:**
- Create: `lib/embeddings.py`
- Create: `requirements.txt`
- Create: `tests/test_embeddings.py`

**Interfaces:**
- Produces: `MODEL_NAME: str`, `embed_text(text: str) -> np.ndarray`,
  `pack_embedding(vec) -> bytes`, `unpack_embedding(blob: bytes) -> np.ndarray`,
  `cosine_similarity(a, b) -> float`

- [ ] **Step 1: Write the failing tests**

`requirements.txt`:
```
fastembed
numpy
```

`tests/test_embeddings.py`:
```python
import numpy as np

from lib.embeddings import pack_embedding, unpack_embedding, cosine_similarity, embed_text


def test_pack_unpack_round_trip():
    vec = [1.0, 2.0, 3.0, -4.5]
    blob = pack_embedding(vec)
    result = unpack_embedding(blob)
    assert np.allclose(result, vec, atol=1e-5)


def test_cosine_similarity_identical_vectors():
    a = [1.0, 0.0, 0.0]
    assert cosine_similarity(a, a) == pytest_approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest_approx(0.0)


def test_cosine_similarity_zero_vector_no_crash():
    a = [0.0, 0.0]
    b = [1.0, 1.0]
    assert cosine_similarity(a, b) == 0.0


def test_embed_text_returns_expected_shape():
    vec = embed_text("fixing a bug in the login form")
    assert isinstance(vec, np.ndarray)
    assert vec.shape[0] == 384  # BAAI/bge-small-en-v1.5 dimensionality


def pytest_approx(value, tol=1e-4):
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol
    return _Approx()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_embeddings.py -v`
Expected: FAIL — `lib.embeddings` doesn't exist. (Also install deps first:
`pip install -r requirements.txt` inside the project's venv — see Task 11
for the real setup script; for local iteration during this task, run
`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt pytest`.)

- [ ] **Step 3: Write the implementation**

`lib/embeddings.py`:
```python
from pathlib import Path

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_CACHE_DIR = Path(__file__).resolve().parent.parent / ".model-cache"

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_CACHE_DIR))
    return _model


def embed_text(text: str) -> np.ndarray:
    model = _get_model()
    return next(iter(model.embed([text])))


def pack_embedding(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_embedding(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a, b) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_embeddings.py -v`
Expected: 5 passed. Note: `test_embed_text_returns_expected_shape` downloads
the model on first run (one-time, ~100MB) — this is expected to be slower
than the other tests.

- [ ] **Step 5: Commit**

```bash
git add lib/embeddings.py requirements.txt tests/test_embeddings.py
git commit -m "feat: local embedding wrapper (fastembed, no network at query time)"
```

---

### Task 5: Observation storage functions

**Files:**
- Modify: `lib/db.py` (append functions)
- Modify: `tests/test_db.py` (append tests)

**Interfaces:**
- Consumes: `lib.embeddings.pack_embedding` (tests only, to build fixture blobs)
- Produces: `insert_observation(conn, ts, session_id, project, category, summary, related_files, embedding) -> int`,
  `get_observations(conn, project=None) -> list[dict]`,
  `get_recent_observations(conn, project: str, limit: int = 5) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:
```python
from lib.embeddings import pack_embedding
from lib.db import insert_observation, get_observations, get_recent_observations


def test_insert_and_get_observations(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1, 0.2, 0.3])
    insert_observation(
        conn, "2026-01-01T00:00:00Z", "sess1", "/proj",
        "bugfix", "Fixed the login redirect", "[]", blob,
    )
    conn.commit()

    rows = get_observations(conn, project="/proj")
    assert len(rows) == 1
    assert rows[0]["category"] == "bugfix"
    assert rows[0]["summary"] == "Fixed the login redirect"
    assert rows[0]["embedding"] == blob


def test_get_observations_filters_by_project(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(conn, "t", "s", "/proj-a", "change", "A", "[]", blob)
    insert_observation(conn, "t", "s", "/proj-b", "change", "B", "[]", blob)
    conn.commit()

    assert len(get_observations(conn, project="/proj-a")) == 1
    assert len(get_observations(conn, project=None)) == 2


def test_get_recent_observations_orders_by_ts_desc_and_limits(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    blob = pack_embedding([0.1])
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", "/proj", "change", "first", "[]", blob)
    insert_observation(conn, "2026-01-02T00:00:00Z", "s", "/proj", "change", "second", "[]", blob)
    insert_observation(conn, "2026-01-03T00:00:00Z", "s", "/proj", "change", "third", "[]", blob)
    conn.commit()

    rows = get_recent_observations(conn, "/proj", limit=2)
    assert [r["summary"] for r in rows] == ["third", "second"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: 6 previously-passing tests still pass, 3 new tests FAIL —
`insert_observation` etc. don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `lib/db.py`:
```python
def insert_observation(conn, ts, session_id, project, category, summary, related_files, embedding) -> int:
    cur = conn.execute(
        "INSERT INTO observations (ts, session_id, project, category, summary, related_files, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, session_id, project, category, summary, related_files, embedding),
    )
    return cur.lastrowid


def get_observations(conn, project=None) -> list:
    if project:
        cur = conn.execute("SELECT * FROM observations WHERE project = ?", (project,))
    else:
        cur = conn.execute("SELECT * FROM observations")
    return [dict(row) for row in cur.fetchall()]


def get_recent_observations(conn, project: str, limit: int = 5) -> list:
    cur = conn.execute(
        "SELECT * FROM observations WHERE project = ? ORDER BY ts DESC LIMIT ?",
        (project, limit),
    )
    return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_db.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add lib/db.py tests/test_db.py
git commit -m "feat: observation storage functions"
```

---

### Task 6: `summarize_worker.py` — pure logic (skip check, prompt, response parsing)

**Files:**
- Create: `summarize_worker.py` (partial — pure functions only, `main` comes in Task 7)
- Create: `tests/test_summarize_worker.py` (partial)

**Interfaces:**
- Produces: `READ_ONLY_TOOLS: set[str]`, `CATEGORIES: set[str]`,
  `skip_summarization(events: list[dict]) -> bool`,
  `build_summarize_prompt(events: list[dict]) -> str`,
  `parse_observations(response_text: str) -> list[dict]` (each item:
  `{"category": str, "summary": str, "related_files": list[str]}`)

- [ ] **Step 1: Write the failing tests**

`tests/test_summarize_worker.py`:
```python
from summarize_worker import skip_summarization, build_summarize_prompt, parse_observations


def _event(tool_name, tool_input="{}"):
    return {"id": 1, "tool_name": tool_name, "tool_input": tool_input, "project": "/proj"}


def test_skip_summarization_empty_list():
    assert skip_summarization([]) is True


def test_skip_summarization_below_threshold():
    events = [_event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True


def test_skip_summarization_at_threshold():
    events = [_event("Edit"), _event("Edit"), _event("Write")]
    assert skip_summarization(events) is False


def test_skip_summarization_ignores_read_only_tools():
    events = [_event("Read"), _event("Grep"), _event("WebSearch"), _event("Edit"), _event("Edit")]
    assert skip_summarization(events) is True  # only 2 substantive


def test_build_summarize_prompt_includes_all_events():
    events = [_event("Edit", '{"file_path": "a.py"}'), _event("Bash", '{"command": "ls"}')]
    prompt = build_summarize_prompt(events)
    assert "JSON array" in prompt
    assert "Edit" in prompt
    assert "Bash" in prompt
    assert "a.py" in prompt


def test_parse_observations_valid_json():
    response = '[{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]'
    result = parse_observations(response)
    assert result == [{"category": "bugfix", "summary": "Fixed X", "related_files": ["a.py"]}]


def test_parse_observations_strips_markdown_fences():
    response = '```json\n[{"category": "feature", "summary": "Added Y", "related_files": []}]\n```'
    result = parse_observations(response)
    assert len(result) == 1
    assert result[0]["summary"] == "Added Y"


def test_parse_observations_invalid_category_defaults_to_change():
    response = '[{"category": "not-a-real-category", "summary": "Did something", "related_files": []}]'
    result = parse_observations(response)
    assert result[0]["category"] == "change"


def test_parse_observations_malformed_json_returns_empty():
    assert parse_observations("not json at all") == []


def test_parse_observations_empty_array():
    assert parse_observations("[]") == []


def test_parse_observations_non_list_returns_empty():
    assert parse_observations('{"not": "a list"}') == []


def test_parse_observations_skips_items_missing_summary():
    response = '[{"category": "bugfix", "related_files": []}]'
    assert parse_observations(response) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_summarize_worker.py -v`
Expected: FAIL — `summarize_worker.py` doesn't exist.

- [ ] **Step 3: Write the implementation**

`summarize_worker.py`:
```python
#!/usr/bin/env python3
import json
import re

READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "WebSearch"}
CATEGORIES = {"bugfix", "feature", "refactor", "change", "discovery", "decision", "security"}


def skip_summarization(events: list) -> bool:
    if not events:
        return True
    substantive = [e for e in events if e.get("tool_name") not in READ_ONLY_TOOLS]
    return len(substantive) < 3


def build_summarize_prompt(events: list) -> str:
    lines = [f"- {e['tool_name']}: {e['tool_input']}" for e in events]
    activity = "\n".join(lines)
    return (
        "Given this raw tool-activity log from a coding session, extract 0-3 short, "
        "human-readable observations about what was actually accomplished. Ignore "
        "routine reads that didn't lead anywhere.\n\n"
        "Return ONLY a JSON array (no prose, no markdown fences). Each item:\n"
        '{"category": one of bugfix|feature|refactor|change|discovery|decision|security, '
        '"summary": "one sentence", "related_files": ["path", ...]}\n'
        "If nothing noteworthy happened, return [].\n\n"
        f"Activity log:\n{activity}"
    )


def parse_observations(response_text: str) -> list:
    text = response_text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []

    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not summary or not isinstance(summary, str):
            continue
        category = item.get("category")
        if category not in CATEGORIES:
            category = "change"
        related_files = item.get("related_files")
        if not isinstance(related_files, list):
            related_files = []
        results.append({"category": category, "summary": summary, "related_files": related_files})
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_summarize_worker.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add summarize_worker.py tests/test_summarize_worker.py
git commit -m "feat: summarization pure logic (skip check, prompt, response parsing)"
```

---

### Task 7: `summarize_worker.py` — orchestration (headless Claude call + main)

**Files:**
- Modify: `summarize_worker.py` (append)
- Modify: `tests/test_summarize_worker.py` (append)

**Interfaces:**
- Consumes: `lib.guard.NO_HOOKS_ENV`, `lib.db.get_connection`,
  `lib.db.get_unprocessed_events`, `lib.db.mark_processed`,
  `lib.db.insert_observation`, `lib.embeddings.embed_text`,
  `lib.embeddings.pack_embedding`
- Produces: `CLAUDE_TIMEOUT_SECONDS: int`, `call_claude_headless(prompt: str) -> str`,
  `main(session_id: str, db_path=None) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_summarize_worker.py`:
```python
import json as jsonlib

import summarize_worker
from lib.db import get_connection, insert_raw_event, get_observations, get_unprocessed_events
from lib.guard import NO_HOOKS_ENV


def test_call_claude_headless_sets_guard_env_and_pipes_prompt(monkeypatch):
    captured = {}

    class FakeResult:
        stdout = "[]"

    def fake_run(cmd, input, capture_output, text, env, timeout):
        captured["cmd"] = cmd
        captured["input"] = input
        captured["env"] = env
        captured["timeout"] = timeout
        return FakeResult()

    monkeypatch.setattr(summarize_worker.subprocess, "run", fake_run)

    result = summarize_worker.call_claude_headless("summarize this")

    assert result == "[]"
    assert captured["input"] == "summarize this"
    assert captured["env"][NO_HOOKS_ENV] == "1"
    assert captured["cmd"] == ["claude", "-p"]


def test_main_writes_observations_and_marks_processed(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    id1 = insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    id2 = insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    id3 = insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    conn.close()

    fake_response = jsonlib.dumps(
        [{"category": "bugfix", "summary": "Fixed the thing", "related_files": ["a.py"]}]
    )
    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: fake_response)
    monkeypatch.setattr(summarize_worker, "embed_text", lambda text: [0.1, 0.2, 0.3])

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    observations = get_observations(conn, project="/proj")
    assert len(observations) == 1
    assert observations[0]["summary"] == "Fixed the thing"
    assert get_unprocessed_events(conn, "sess1") == []


def test_main_skips_when_below_threshold(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    conn.commit()
    conn.close()

    called = {"count": 0}
    monkeypatch.setattr(
        summarize_worker, "call_claude_headless", lambda prompt: called.__setitem__("count", called["count"] + 1)
    )

    summarize_worker.main("sess1", db_path=db_path)

    assert called["count"] == 0


def test_main_marks_processed_even_when_llm_returns_nothing_noteworthy(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_raw_event(conn, "t", "sess1", "/proj", "Edit", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Write", "{}")
    insert_raw_event(conn, "t", "sess1", "/proj", "Bash", "{}")
    conn.commit()
    conn.close()

    monkeypatch.setattr(summarize_worker, "call_claude_headless", lambda prompt: "[]")

    summarize_worker.main("sess1", db_path=db_path)

    conn = get_connection(db_path)
    assert get_unprocessed_events(conn, "sess1") == []
    assert get_observations(conn, project="/proj") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_summarize_worker.py -v`
Expected: previous 12 pass, 4 new FAIL — `call_claude_headless`/`main` don't exist yet.

- [ ] **Step 3: Write the implementation**

Append to `summarize_worker.py` (add these imports at the top of the file
alongside the existing `json`/`re` imports):
```python
import os
import subprocess
import sys
from datetime import datetime, timezone

from lib.db import get_connection, get_unprocessed_events, mark_processed, insert_observation
from lib.embeddings import embed_text, pack_embedding
from lib.guard import NO_HOOKS_ENV

CLAUDE_TIMEOUT_SECONDS = 120


def call_claude_headless(prompt: str) -> str:
    env = os.environ.copy()
    env[NO_HOOKS_ENV] = "1"
    result = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        timeout=CLAUDE_TIMEOUT_SECONDS,
    )
    return result.stdout


def main(session_id: str, db_path=None) -> None:
    conn = get_connection(db_path)
    events = get_unprocessed_events(conn, session_id)
    if skip_summarization(events):
        conn.close()
        return

    prompt = build_summarize_prompt(events)
    response = call_claude_headless(prompt)
    observations = parse_observations(response)

    ts = datetime.now(timezone.utc).isoformat()
    project = events[0]["project"]
    for obs in observations:
        vec = embed_text(obs["summary"])
        blob = pack_embedding(vec)
        insert_observation(
            conn, ts, session_id, project,
            obs["category"], obs["summary"],
            json.dumps(obs["related_files"]), blob,
        )

    mark_processed(conn, [e["id"] for e in events])
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1])
    sys.exit(0)
```

(Note: `lib/__init__.py` was already created in Task 1, and `sys.path`
already gets set up by `summarize_trigger.py`'s insert in Task 8 before it
spawns this script — but since this file can also run standalone for
manual testing, add `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))`
right after the `import sys` line, before the `from lib...` imports, mirroring
`capture.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_summarize_worker.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add summarize_worker.py tests/test_summarize_worker.py
git commit -m "feat: summarize_worker orchestration (headless claude -p call)"
```

---

### Task 8: `summarize_trigger.py` — Stop hook

**Files:**
- Create: `summarize_trigger.py`
- Create: `tests/test_summarize_trigger.py`

**Interfaces:**
- Consumes: `lib.guard.hooks_disabled()`, spawns `summarize_worker.py` via
  `subprocess.Popen`
- Produces: `main() -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_summarize_trigger.py`:
```python
import io
import json

import summarize_trigger


def test_spawns_worker_with_session_id(monkeypatch):
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "sess1", "cwd": "/tmp"}))
    )
    monkeypatch.delenv("CLAUDE_MEM_LITE_NO_HOOKS", raising=False)

    summarize_trigger.main()

    assert "sess1" in captured["cmd"]
    assert captured["cmd"][1] == summarize_trigger.WORKER
    assert captured["kwargs"]["start_new_session"] is True


def test_does_not_spawn_when_hooks_disabled(monkeypatch):
    called = {"count": 0}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            called["count"] += 1

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.setenv("CLAUDE_MEM_LITE_NO_HOOKS", "1")
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"session_id": "sess1", "cwd": "/tmp"}))
    )

    summarize_trigger.main()

    assert called["count"] == 0


def test_does_not_spawn_when_session_id_missing(monkeypatch):
    called = {"count": 0}

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            called["count"] += 1

    monkeypatch.setattr(summarize_trigger.subprocess, "Popen", FakePopen)
    monkeypatch.delenv("CLAUDE_MEM_LITE_NO_HOOKS", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": "/tmp"})))

    summarize_trigger.main()

    assert called["count"] == 0


def test_never_raises_on_malformed_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    summarize_trigger.main()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_summarize_trigger.py -v`
Expected: FAIL — `summarize_trigger.py` doesn't exist.

- [ ] **Step 3: Write the implementation**

`summarize_trigger.py`:
```python
#!/usr/bin/env python3
import sys
import os
import json
import subprocess
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.guard import hooks_disabled

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summarize_worker.py")
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def main():
    if hooks_disabled():
        return
    try:
        payload = json.load(sys.stdin)
        session_id = payload.get("session_id")
        if not session_id:
            return
        subprocess.Popen(
            [sys.executable, WORKER, session_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        with open(ERROR_LOG, "a") as f:
            f.write(traceback.format_exc() + "\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_summarize_trigger.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add summarize_trigger.py tests/test_summarize_trigger.py
git commit -m "feat: summarize_trigger Stop hook (detached, non-blocking)"
```

---

### Task 9: `digest.py` — SessionStart hook

**Files:**
- Create: `digest.py`
- Create: `tests/test_digest.py`

**Interfaces:**
- Consumes: `lib.db.get_connection`, `lib.db.derive_project`,
  `lib.db.get_recent_observations`
- Produces: `DIGEST_LIMIT: int`, `build_digest(observations: list[dict]) -> str`,
  `main(db_path=None) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_digest.py`:
```python
import io
import json

import digest
from lib.db import get_connection, insert_observation
from lib.embeddings import pack_embedding


def test_build_digest_empty_list():
    assert digest.build_digest([]) == ""


def test_build_digest_formats_observations():
    observations = [
        {"category": "bugfix", "summary": "Fixed the login redirect"},
        {"category": "feature", "summary": "Added CSV export"},
    ]
    result = digest.build_digest(observations)
    assert "Fixed the login redirect" in result
    assert "Added CSV export" in result
    assert "bugfix" in result
    assert "feature" in result


def test_main_prints_digest_for_matching_project(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    blob = pack_embedding([0.1])
    insert_observation(conn, "2026-01-01T00:00:00Z", "s", str(tmp_path), "bugfix", "Fixed X", "[]", blob)
    conn.commit()
    conn.close()

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    digest.main(db_path=db_path)

    captured = capsys.readouterr()
    assert "Fixed X" in captured.out


def test_main_prints_nothing_for_empty_db(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))

    digest.main(db_path=db_path)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_main_never_raises_on_malformed_stdin(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    digest.main(db_path=db_path)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_digest.py -v`
Expected: FAIL — `digest.py` doesn't exist.

- [ ] **Step 3: Write the implementation**

`digest.py`:
```python
#!/usr/bin/env python3
import sys
import os
import json
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.db import get_connection, derive_project, get_recent_observations

DIGEST_LIMIT = 5
ERROR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")


def build_digest(observations: list) -> str:
    if not observations:
        return ""
    lines = ["Recent activity in this project (from claude-mem-lite):"]
    for obs in observations:
        lines.append(f"- [{obs['category']}] {obs['summary']}")
    return "\n".join(lines)


def main(db_path=None):
    try:
        payload = json.load(sys.stdin)
        cwd = payload.get("cwd", os.getcwd())
        project = derive_project(cwd)

        conn = get_connection(db_path)
        observations = get_recent_observations(conn, project, DIGEST_LIMIT)
        conn.close()

        text = build_digest(observations)
        if text:
            print(text)
    except Exception:
        with open(ERROR_LOG, "a") as f:
            f.write(traceback.format_exc() + "\n")


if __name__ == "__main__":
    main()
    sys.exit(0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_digest.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add digest.py tests/test_digest.py
git commit -m "feat: digest.py SessionStart hook"
```

---

### Task 10: `search.py` — on-demand CLI

**Files:**
- Create: `search.py`
- Create: `tests/test_search.py`

**Interfaces:**
- Consumes: `lib.db.get_connection`, `lib.db.get_observations`,
  `lib.embeddings.embed_text`, `lib.embeddings.unpack_embedding`,
  `lib.embeddings.cosine_similarity`, `lib.embeddings.pack_embedding` (tests only)
- Produces: `rank_observations(query_vec, rows: list[dict], limit: int) -> list[dict]`,
  `main(argv=None, db_path=None) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_search.py`:
```python
import json

from lib.embeddings import pack_embedding
from lib.db import get_connection, insert_observation
import search


def _row(summary, vec):
    return {"id": 1, "ts": "t", "project": "/proj", "category": "change",
            "summary": summary, "embedding": pack_embedding(vec)}


def test_rank_observations_orders_by_similarity():
    rows = [
        _row("unrelated thing", [0.0, 1.0]),
        _row("closely matching thing", [0.99, 0.01]),
        _row("somewhat related", [0.7, 0.3]),
    ]
    ranked = search.rank_observations([1.0, 0.0], rows, limit=10)
    assert [r["summary"] for r in ranked] == [
        "closely matching thing",
        "somewhat related",
        "unrelated thing",
    ]


def test_rank_observations_respects_limit():
    rows = [_row(f"item {i}", [1.0, 0.0]) for i in range(5)]
    ranked = search.rank_observations([1.0, 0.0], rows, limit=2)
    assert len(ranked) == 2


def test_main_prints_empty_array_for_empty_db(tmp_path, capsys):
    db_path = str(tmp_path / "test.db")
    search.main(["some query"], db_path=db_path)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []


def test_main_prints_ranked_results(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj", "bugfix", "Fixed the login bug", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "t", "s", "/proj", "feature", "Added dark mode", "[]", pack_embedding([0.0, 1.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["login bug"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert results[0]["summary"] == "Fixed the login bug"


def test_main_filters_by_project(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    insert_observation(conn, "t", "s", "/proj-a", "change", "A thing", "[]", pack_embedding([1.0, 0.0]))
    insert_observation(conn, "t", "s", "/proj-b", "change", "B thing", "[]", pack_embedding([1.0, 0.0]))
    conn.commit()
    conn.close()

    monkeypatch.setattr(search, "embed_text", lambda text: [1.0, 0.0])

    search.main(["thing", "--project", "/proj-a"], db_path=db_path)

    captured = capsys.readouterr()
    results = json.loads(captured.out)
    assert len(results) == 1
    assert results[0]["summary"] == "A thing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_search.py -v`
Expected: FAIL — `search.py` doesn't exist.

- [ ] **Step 3: Write the implementation**

`search.py`:
```python
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
        {"id": r["id"], "ts": r["ts"], "project": r["project"], "category": r["category"], "summary": r["summary"]}
        for r in top
    ]
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_search.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add search.py tests/test_search.py
git commit -m "feat: search.py semantic-search CLI"
```

---

### Task 11: Packaging — deps, setup script, settings snippet, Skill, gitignore

**Files:**
- Create: `requirements-dev.txt`
- Create: `setup.sh`
- Create: `settings-snippet.json`
- Create: `skills/mem-search/SKILL.md`
- Create: `.gitignore`

**Interfaces:**
- Consumes: `lib.db.get_connection`, `lib.embeddings.embed_text` (from
  `setup.sh`'s inline Python init step)

- [ ] **Step 1: Write the files**

`requirements-dev.txt`:
```
pytest
```

`.gitignore`:
```
.venv/
.model-cache/
store.db
error.log
__pycache__/
*.pyc
.pytest_cache/
```

`settings-snippet.json`:
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

`setup.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "Creating virtualenv..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

echo "Running test suite..."
python3 -m pytest tests/ -v

echo "Initializing database and warming the embedding model..."
python3 - <<'PY'
from lib.db import get_connection
from lib.embeddings import embed_text

conn = get_connection()
conn.close()
print("Database initialized at store.db")

embed_text("warmup")
print("Embedding model ready.")
PY

chmod +x capture.py summarize_trigger.py summarize_worker.py digest.py search.py

echo ""
echo "Setup complete."
echo "Add the following to ~/.claude/settings.json under its top-level \"hooks\" key"
echo "(merge with any hooks you already have there — do not overwrite the file):"
echo ""
cat settings-snippet.json
```

`skills/mem-search/SKILL.md`:
```markdown
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
```

- [ ] **Step 2: Run setup and verify it succeeds**

Run: `cd ~/claude-mem-lite && chmod +x setup.sh && ./setup.sh`
Expected: venv created, all tests pass (should print the cumulative test
count from every prior task — 42 passed), `store.db` exists,
`.model-cache/` exists, the settings snippet is printed at the end.

- [ ] **Step 3: Commit**

```bash
git add requirements-dev.txt setup.sh settings-snippet.json skills/mem-search/SKILL.md .gitignore
git commit -m "feat: install script, settings snippet, mem-search skill"
```

---

### Task 12: Wire into settings.json and verify end-to-end

**Files:**
- Modify: `~/.claude/settings.json` (global — outside this repo)

This task has no automated test — it is the manual integration
verification the DESIGN.md's "Verification plan" section calls for.

- [ ] **Step 1: Back up the current global settings**

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak-$(date +%Y%m%d) 2>/dev/null || echo "no existing settings.json — starting fresh"
```

- [ ] **Step 2: Merge in the hooks**

Read `~/.claude/settings.json` (or note it doesn't exist yet). Merge the
contents of `~/claude-mem-lite/settings-snippet.json` into it under the
`hooks` key — if `~/.claude/settings.json` already has other hooks
registered for `PostToolUse`, `Stop`, or `SessionStart`, append this
project's entry into that event's existing array rather than replacing it.
If the file doesn't exist yet, the snippet can be copied in as-is.

- [ ] **Step 3: Confirm the recursion guard in isolation**

```bash
cd ~/claude-mem-lite
echo '{"session_id": "manual-test", "cwd": "'"$PWD"'"}' | CLAUDE_MEM_LITE_NO_HOOKS=1 .venv/bin/python3 summarize_trigger.py
ps aux | grep summarize_worker | grep -v grep
```
Expected: no `summarize_worker.py` process appears — the guard suppressed
the spawn.

- [ ] **Step 4: Confirm capture works in a throwaway project**

```bash
mkdir -p /tmp/mem-lite-smoketest && cd /tmp/mem-lite-smoketest && git init -q
echo '{"session_id": "manual-test-2", "cwd": "'"$PWD"'", "tool_name": "Edit", "tool_input": {"file_path": "x.py"}}' \
  | ~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/capture.py
~/claude-mem-lite/.venv/bin/python3 -c "
from lib.db import get_connection, get_unprocessed_events
import sys; sys.path.insert(0, '$HOME/claude-mem-lite')
conn = get_connection('$HOME/claude-mem-lite/store.db')
print(get_unprocessed_events(conn, 'manual-test-2'))
"
```
Expected: prints one event with `tool_name: "Edit"`.

- [ ] **Step 5: Confirm digest handles an empty project gracefully**

```bash
echo '{"cwd": "/tmp/mem-lite-smoketest-empty"}' | ~/claude-mem-lite/.venv/bin/python3 ~/claude-mem-lite/digest.py
```
Expected: no output, no error, exit code 0.

- [ ] **Step 6: Real end-to-end check in an actual Claude Code session**

Start a normal Claude Code session in any project, do a few real edits
(enough to cross the 3-non-read-tool threshold), let the turn finish, then
check:

```bash
sqlite3 ~/claude-mem-lite/store.db "SELECT category, summary FROM observations ORDER BY id DESC LIMIT 5;"
```
Expected: at least one row reflecting what was actually just done. Then
start a fresh session in the same project and confirm the `SessionStart`
digest appears in context.

- [ ] **Step 7: Commit the settings change note**

The settings file itself lives outside this repo, so there's nothing to
commit here — but note in this repo that wiring is done:

```bash
cd ~/claude-mem-lite
git commit --allow-empty -m "chore: hooks wired into ~/.claude/settings.json (manual step, verified end-to-end)"
```

---

## Self-Review Notes

- **Spec coverage:** every DESIGN.md section has a task — storage (1, 5),
  capture (3), summarization incl. recursion guard and non-blocking spawn
  (2, 6, 7, 8), embeddings/search (4, 10), SessionStart digest (9),
  install (11), wiring/verification (12).
- **Type consistency checked:** `get_connection(db_path=None)` signature
  is identical across `lib/db.py`, and every script (`capture.py`,
  `summarize_worker.py`, `digest.py`, `search.py`) threads `db_path`
  through its own `main()` the same way, rather than reaching for a global.
  `embed_text`/`pack_embedding`/`unpack_embedding`/`cosine_similarity`
  names match between `lib/embeddings.py`, `search.py`, and
  `summarize_worker.py` throughout.
- **No placeholders:** every step above has real, runnable code — nothing
  deferred to "later" except the two items DESIGN.md already scoped
  explicitly to implementation time (recursion-guard mechanism, async
  behavior), both of which this plan actually resolves concretely (env-var
  guard checked in our own code; detached `Popen` instead of relying on
  unconfirmed hook JSON fields) rather than leaving open.
