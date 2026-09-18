import os
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
  tool_response TEXT,
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
  embedding BLOB NOT NULL,
  related_raw_event_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_project ON observations(project);
"""

# ponytail: idempotent ALTER-per-open, not a migrations table — fine at two
# columns; revisit with real migration versioning if this schema keeps growing.
_MIGRATIONS = (
    ("raw_events", "tool_response", "TEXT"),
    ("observations", "related_raw_event_ids", "TEXT"),
)


def _migrate(conn):
    for table, column, coltype in _MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise
    conn.commit()


def get_connection(db_path=None):
    path = str(db_path) if db_path else str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    if path != ":memory:":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
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


def insert_raw_event(conn, ts, session_id, project, tool_name, tool_input, tool_response=None) -> int:
    cur = conn.execute(
        "INSERT INTO raw_events (ts, session_id, project, tool_name, tool_input, tool_response, processed) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (ts, session_id, project, tool_name, tool_input, tool_response),
    )
    return cur.lastrowid


def get_raw_events_by_ids(conn, ids: list) -> list:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(f"SELECT * FROM raw_events WHERE id IN ({placeholders})", ids)
    return [dict(row) for row in cur.fetchall()]


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


def insert_observation(
    conn, ts, session_id, project, category, summary, related_files, embedding,
    related_raw_event_ids=None,
) -> int:
    cur = conn.execute(
        "INSERT INTO observations "
        "(ts, session_id, project, category, summary, related_files, embedding, related_raw_event_ids) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, session_id, project, category, summary, related_files, embedding, related_raw_event_ids),
    )
    return cur.lastrowid


def get_observations(conn, project=None) -> list:
    if project is not None:
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
