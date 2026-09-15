"""
SQLite storage layer for handoff state, cycles, and rounds.

This module is the pure storage layer for Phase 28 — schema, connect,
low-level CRUD, and a markdown renderer that produces the same output
as `tagteam.cycle.render_cycle`. Business rules (state machine
transitions, stale-round detection, baseline capture) belong to the
caller, not to this layer.

Default database location: `<project_root>/.tagteam/tagteam.db`.

Schema version is tracked via `PRAGMA user_version`. Bump it and add
a forward migration whenever the schema changes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

# Bump when the schema changes; add a migration step in `_migrate`.
# 3.0-arc rule (docs/tagteam-3.0-proposal.md §2): migrations are ADDITIVE
# ONLY — new tables / nullable columns, never renames or drops — so an
# older release can still open a newer DB after a downgrade.
SCHEMA_VERSION = 10

USAGE_STATUSES = {"ok", "timeout", "nonzero_exit", "no_round", "spawn_failed",
                  "cancelled"}
INTERJECTION_ROLES = {"lead", "reviewer"}
BRIEF_STATUSES = {"running", "ok", "partial", "failed", "abandoned"}
BRIEF_KINDS = {"auto", "manual"}
# Non-file-backed tables: preserved verbatim across `repair` rebuilds.
# parent-before-child order matters for restore_non_file_backed()
NON_FILE_BACKED_TABLES = ("usage", "interjections", "briefs", "rate_limits",
                          "conversations", "conversation_turns", "launches", "gates", "panels")

VALID_ACTIONS = {
    "SUBMIT_FOR_REVIEW", "REQUEST_CHANGES", "APPROVE",
    "ESCALATE", "NEED_HUMAN", "AMEND",
    "GATE_PASS", "GATE_BOUNCE",          # Phase 38: gatekeeper entries
}
VALID_ROLES = {"lead", "reviewer", "gatekeeper"}
VALID_TYPES = {"plan", "impl"}
TERMINAL_CYCLE_STATES = {"approved", "escalated", "aborted"}
_ACTION_TO_STATUS = {
    "SUBMIT_FOR_REVIEW": ("in-progress", "reviewer"),
    "REQUEST_CHANGES": ("in-progress", "lead"),
    "APPROVE": ("approved", None),
    "ESCALATE": ("escalated", "human"),
    "NEED_HUMAN": ("needs-human", "human"),
    "AMEND": ("in-progress", "reviewer"),
    # Phase 38 gatekeeper entries: PASS keeps the reviewer's turn, BOUNCE
    # is the REQUEST_CHANGES-shaped hand-back to the lead.
    "GATE_PASS": ("in-progress", "reviewer"),
    "GATE_BOUNCE": ("in-progress", "lead"),
}

DEFAULT_DB_RELPATH = Path(".tagteam") / "tagteam.db"


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS cycles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phase       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('plan','impl')),
    lead        TEXT,
    reviewer    TEXT,
    state       TEXT NOT NULL,
    ready_for   TEXT,
    ready_for_present INTEGER NOT NULL DEFAULT 1,
    round       INTEGER NOT NULL DEFAULT 0,
    date        TEXT,
    created_at  TEXT,
    closed_at   TEXT,
    baseline_json TEXT,
    UNIQUE(phase, type)
);

CREATE TABLE IF NOT EXISTS rounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id    INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
    round       INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('lead','reviewer')),
    action      TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    updated_by  TEXT,
    summary     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rounds_cycle ON rounds(cycle_id, round, id);

CREATE TABLE IF NOT EXISTS state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    phase       TEXT,
    type        TEXT,
    round       INTEGER,
    status      TEXT,
    command     TEXT,
    result      TEXT,
    updated_by  TEXT,
    run_mode    TEXT,
    seq         INTEGER,
    updated_at  TEXT,
    extra_json  TEXT
);

CREATE TABLE IF NOT EXISTS state_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    turn        TEXT,
    status      TEXT,
    phase       TEXT,
    round       INTEGER,
    updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    payload_json TEXT
);
"""


# Schema v3 (Phase 31): per-turn token usage for headless turns.
# Recording only; surfacing is Phase 32 (`tagteam usage`) / 34 (cockpit).
_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    phase         TEXT,
    type          TEXT,
    round         INTEGER,
    role          TEXT,
    agent         TEXT,
    provider      TEXT,
    model         TEXT,
    status        TEXT NOT NULL,
    exit_code     INTEGER,
    duration_ms   INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    cost_usd      REAL,
    num_turns     INTEGER,
    session_id    TEXT,
    log_path      TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_phase ON usage(phase, type, round);
"""


# Schema v4 (Phase 32): arbiter interjections. Append-only sibling of
# `rounds` (the rounds role CHECK forbids a third role); phase/type/round/
# turn are all NULL when nothing was owed at write time (observed_state
# keeps what the CLI saw, provenance only). Delivery is stamped by the
# headless engine for exactly the ids it rendered; retirement closes a note
# without delivery.
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS interjections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    by              TEXT,
    note            TEXT NOT NULL,
    target_role     TEXT CHECK (target_role IS NULL OR target_role IN ('lead','reviewer')),
    phase           TEXT,
    type            TEXT,
    round           INTEGER,
    turn            TEXT,
    observed_state  TEXT,
    delivered_role  TEXT,
    delivered_round INTEGER,
    delivered_stem  TEXT,
    delivered_ts    TEXT,
    retired_ts      TEXT,
    retired_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_interjections_phase ON interjections(phase, type, round);
"""


# Schema v5 (Phase 33): escalation briefs. The row is the CLAIM as well as
# the record: inserted `running` before the briefer spawns (under the
# project writer lock), finished afterwards. Partial unique indexes give
# at-most-one automatic attempt per escalation event and at-most-one
# running attempt per event across kinds. `event_key` is repair-safe
# (derived from file-backed round data); `event_row_id` is informational.
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    phase        TEXT NOT NULL,
    type         TEXT NOT NULL,
    round        INTEGER NOT NULL,
    cycle_state  TEXT NOT NULL,
    event_key    TEXT NOT NULL,
    event_row_id INTEGER,
    kind         TEXT NOT NULL CHECK (kind IN ('auto','manual')),
    attempt      INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('running','ok','partial','failed','abandoned')),
    started_at   TEXT,
    finished_at  TEXT,
    runner_pid   INTEGER,
    runner_ident TEXT,
    stem         TEXT,
    path         TEXT,
    content      TEXT,
    provider     TEXT,
    model        TEXT,
    usage_row_id INTEGER,
    duration_ms  INTEGER,
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_briefs_cycle ON briefs(phase, type, round);
CREATE UNIQUE INDEX IF NOT EXISTS uq_briefs_auto    ON briefs(event_key) WHERE kind = 'auto';
CREATE UNIQUE INDEX IF NOT EXISTS uq_briefs_running ON briefs(event_key) WHERE status = 'running';
"""


# Schema v6 (Phase 34): latest provider rate-limit signal. One row per
# (provider, kind) — e.g. ("claude", "five_hour") — upserted from the
# `rate_limit_event` frames of the Claude stream after each headless turn /
# brief. Non-file-backed (preserved verbatim across `repair` rebuilds).
# `resets_at` is ISO-8601 UTC; `payload_json` keeps the raw frame.
_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS rate_limits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT,
    resets_at    TEXT,
    payload_json TEXT,
    ts           TEXT NOT NULL,
    UNIQUE(provider, kind)
);
"""

# Phase 37 (3.1): lead conversations, their turns, and composite launch
# claims. Additive: new tables + one nullable usage column.
_SCHEMA_V7 = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    provider    TEXT,
    session_id  TEXT,
    title       TEXT,
    last_ts     TEXT,
    continuity  TEXT
);
CREATE TABLE IF NOT EXISTS conversation_turns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL,
    n                INTEGER NOT NULL,
    ts               TEXT NOT NULL,
    user_text        TEXT NOT NULL,
    status           TEXT NOT NULL,
    session_id       TEXT,
    owner_pid        INTEGER,
    owner_ident      TEXT,
    usage_row_id     INTEGER,
    log_path         TEXT,
    events_path      TEXT,
    finished_at      TEXT,
    error            TEXT,
    reply            TEXT,
    continuity       TEXT,
    UNIQUE(conversation_id, n)
);
CREATE TABLE IF NOT EXISTS launches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    key              TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL,
    attempt          INTEGER NOT NULL DEFAULT 1,
    intent_json      TEXT,
    owner_pid        INTEGER,
    owner_ident      TEXT,
    watcher_pid      INTEGER,
    watcher_ident    TEXT,
    conversation_id  TEXT,
    turn_n           INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    partial_json     TEXT
);
"""

# Phase 38 (3.2): gatekeeper decisions. Additive.
_SCHEMA_V8 = """
-- Phase 38: the gatekeeper writes round entries (role 'gatekeeper',
-- actions GATE_PASS / GATE_BOUNCE). The v1 `rounds` role CHECK forbade a
-- third role, so the table is rebuilt in place with a widened CHECK —
-- same columns, same ids, same index; nothing else changes.
CREATE TABLE IF NOT EXISTS rounds_v8 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id    INTEGER NOT NULL REFERENCES cycles(id) ON DELETE CASCADE,
    round       INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('lead','reviewer','gatekeeper')),
    action      TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL,
    updated_by  TEXT,
    summary     TEXT
);
INSERT INTO rounds_v8 (id, cycle_id, round, role, action, content, ts, updated_by, summary)
    SELECT id, cycle_id, round, role, action, content, ts, updated_by, summary FROM rounds ORDER BY id;
DROP TABLE rounds;
ALTER TABLE rounds_v8 RENAME TO rounds;
CREATE INDEX IF NOT EXISTS idx_rounds_cycle ON rounds(cycle_id, round, id);

CREATE TABLE IF NOT EXISTS gates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key      TEXT NOT NULL,
    phase          TEXT NOT NULL,
    type           TEXT NOT NULL,
    round          INTEGER NOT NULL,
    submission_seq INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    status         TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    runner_pid     INTEGER,
    runner_ident   TEXT,
    started_at     TEXT NOT NULL,
    updated_at     TEXT,
    finished_at    TEXT,
    duration_s     REAL,
    result_json    TEXT,
    stem           TEXT,
    reason         TEXT,
    applied_seq    INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_running ON gates(event_key) WHERE status = 'running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_gates_decided ON gates(event_key) WHERE status IN ('pass', 'bounce');
"""


def _resolve_db_path(project_dir: str | Path | None) -> Path:
    """Resolve where the database lives.

    If `project_dir` is given, the DB is at `<project_dir>/.tagteam/tagteam.db`.
    If None, walk up from cwd to the nearest `tagteam.yaml` (matching
    `tagteam.state._resolve_project_root` semantics).
    """
    if project_dir is not None:
        return Path(project_dir) / DEFAULT_DB_RELPATH
    from tagteam.state import _resolve_project_root
    return Path(_resolve_project_root()) / DEFAULT_DB_RELPATH


# Schema v9 (Phase 39): reviewer panels — one row per panel attempt on a
# submission (same event identity as `gates`); the merged decision is an
# ordinary reviewer round entry (see cycle.add_round meta=).
_SCHEMA_V9 = """
CREATE TABLE IF NOT EXISTS panels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key        TEXT NOT NULL,
    phase            TEXT NOT NULL,
    type             TEXT NOT NULL,
    round            INTEGER NOT NULL,
    submission_seq   INTEGER NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('auto','manual')),
    status           TEXT NOT NULL CHECK (status IN ('running','merged','fallback','superseded','error','abandoned')),
    attempt          INTEGER NOT NULL,
    runner_pid       INTEGER,
    runner_ident     TEXT,
    started_at       TEXT NOT NULL,
    updated_at       TEXT,
    finished_at      TEXT,
    duration_s       REAL,
    lenses_json      TEXT,
    decision         TEXT,
    interjection_ids TEXT,
    stem             TEXT,
    reason           TEXT,
    applied_seq      INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_panels_running ON panels(event_key) WHERE status='running';
CREATE UNIQUE INDEX IF NOT EXISTS uq_panels_decided ON panels(event_key) WHERE status IN ('merged','fallback');
CREATE INDEX IF NOT EXISTS idx_panels_cycle ON panels(phase, type, id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply schema migrations forward to SCHEMA_VERSION.

    Currently a single CREATE-IF-NOT-EXISTS pass. New migrations get
    added as `if current < N: <ddl>; current = N` blocks.
    """
    cur = conn.execute("PRAGMA user_version")
    current = cur.fetchone()[0]
    if current < 1:
        conn.executescript(_SCHEMA_V1)
        conn.execute(f"PRAGMA user_version = 1")
        current = 1
    if current < 2:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(cycles)").fetchall()
        }
        if "ready_for_present" not in cols:
            conn.execute(
                "ALTER TABLE cycles "
                "ADD COLUMN ready_for_present INTEGER NOT NULL DEFAULT 1"
            )
        conn.execute("PRAGMA user_version = 2")
        current = 2
    if current < 3:
        conn.executescript(_SCHEMA_V3)
        conn.execute("PRAGMA user_version = 3")
        current = 3
    if current < 4:
        conn.executescript(_SCHEMA_V4)
        conn.execute("PRAGMA user_version = 4")
        current = 4
    if current < 5:
        conn.executescript(_SCHEMA_V5)
        conn.execute("PRAGMA user_version = 5")
        current = 5
    if current < 6:
        conn.executescript(_SCHEMA_V6)
        conn.execute("PRAGMA user_version = 6")
        current = 6
    if current < 7:
        conn.executescript(_SCHEMA_V7)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(usage)").fetchall()}
        if "kind" not in cols:
            conn.execute("ALTER TABLE usage ADD COLUMN kind TEXT")
        conn.execute("PRAGMA user_version = 7")
        current = 7
    if current < 8:
        conn.executescript(_SCHEMA_V8)
        conn.execute("PRAGMA user_version = 8")
        current = 8
    if current < 9:
        conn.executescript(_SCHEMA_V9)
        conn.execute("PRAGMA user_version = 9")
        current = 9
    if current < 10:
        # Phase 55: per-model token split (Claude `modelUsage`) and the cycle
        # entry a turn was dispatched to produce. Additive, nullable.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(usage)").fetchall()}
        for name, decl in _USAGE_V10_COLUMNS:
            if name not in cols:
                conn.execute(f"ALTER TABLE usage ADD COLUMN {name} {decl}")
        conn.execute("PRAGMA user_version = 10")
        current = 10
    # Future migrations land here.
    # NOTE: `current > SCHEMA_VERSION` (a newer release wrote this DB) is
    # deliberately tolerated — additive-only migrations mean older code
    # can still read/write the tables it knows about.
    if current < SCHEMA_VERSION:
        raise RuntimeError(
            f"DB schema at version {current}, code expects {SCHEMA_VERSION}. "
            "Forgot to add a migration step?"
        )
    conn.commit()


def connect(project_dir: str | Path | None = None,
            db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and initialize) the project's tagteam database.

    Either `project_dir` or `db_path` may be given; if both are None,
    the project root is auto-resolved. Idempotent — calling again on
    an existing DB only re-runs CREATE IF NOT EXISTS guards.
    """
    from tagteam import dualwrite
    if dualwrite.read_only():
        # Phase 50: the DB chokepoint. A read-only process never creates,
        # migrates or journals — every caller inherits this.
        return read_only_connect(project_dir, db_path)
    if db_path is None:
        db_path = _resolve_db_path(project_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def connect_for_read(project_dir: str | Path | None = None,
                     db_path: Path | None = None) -> tuple[sqlite3.Connection | None, str | None]:
    """Phase 55: the read path for report-style commands, in BOTH modes.

    `read_only_connect(require_current_schema=False)`: never creates,
    migrates or checkpoints, and an older schema stays readable (callers
    query only what exists). Returns `(conn, None)`, or `(None, note)` when
    the DB is absent or cannot be read without writing. `connect` and its
    Phase 50 guard are unchanged for every other caller.
    """
    from tagteam.dualwrite import DatabaseMissing, ReadOnlyError
    try:
        return read_only_connect(project_dir, db_path, require_current_schema=False), None
    except DatabaseMissing:
        return None, "no database"
    except ReadOnlyError as e:
        return None, getattr(e, "detail", None) or str(e)
    except sqlite3.DatabaseError as e:
        return None, f"database unreadable: {e}"


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of `table` (empty set when the table does not exist)."""
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.DatabaseError:
        return set()


def _sqlite_uri(db_path: Path, *params: str) -> str:
    from urllib.parse import quote
    return "file:" + quote(db_path.resolve().as_posix(), safe="/:") + "?" + "&".join(params)


def read_only_connect(project_dir: str | Path | None = None,
                      db_path: Path | None = None, *,
                      require_current_schema: bool = True) -> sqlite3.Connection:
    """Open an EXISTING project DB without creating, changing or migrating
    anything (Phase 50). Raises a `dualwrite.ReadOnlyError` subclass when
    that is not possible:

      absent file                -> `DatabaseMissing`
      `-wal` present, no `-shm`  -> `WalWithoutIndex` (fail closed: `mode=ro`
                                    would create the `-shm`; `immutable=1`
                                    would ignore committed WAL frames)
      user_version < SCHEMA_VERSION and `require_current_schema`
                                 -> `SchemaBehind` (never migrates)

    URI by sidecar state (measured, docs/phases/read-only-mode.md §2):
    `-wal` + `-shm` -> `mode=ro` (honours WAL content; may rewrite the
    existing `-shm`, the WAL index); no `-wal` (with or without a stale
    `-shm`) -> `mode=ro&immutable=1` (creates nothing; safe because there
    is no WAL to miss). A newer `user_version` is tolerated (additive-only
    migrations). Caller closes.
    """
    from tagteam.dualwrite import DatabaseMissing, SchemaBehind, WalWithoutIndex
    if db_path is None:
        db_path = _resolve_db_path(project_dir)
    db_path = Path(db_path)
    if not db_path.is_file():
        raise DatabaseMissing(f"no database at {db_path}; read-only mode will not create one")
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    if wal.exists() and not shm.exists():
        raise WalWithoutIndex(
            f"{wal.name} exists without {shm.name}; reading it would create the index "
            "or hide committed data — run any normal (writing) tagteam command to checkpoint it")
    if wal.exists():
        conn = sqlite3.connect(_sqlite_uri(db_path, "mode=ro"), uri=True)
    else:
        conn = sqlite3.connect(_sqlite_uri(db_path, "mode=ro", "immutable=1"), uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        if require_current_schema:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current < SCHEMA_VERSION:
                raise SchemaBehind(
                    f"database schema is at version {current}, this release expects "
                    f"{SCHEMA_VERSION}; read-only mode never migrates — any normal (writing) "
                    "tagteam command will")
    except BaseException:
        conn.close()
        raise
    return conn


def _writes(fn):
    """Mark a DB writer (Phase 50): refuses under `TAGTEAM_READ_ONLY` with the
    read-only message. The connection is already `query_only` in that mode;
    this is the clear refusal, not the guarantee."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from tagteam import dualwrite
        if dualwrite.read_only():
            raise dualwrite.ReadOnlyError(f"db.{fn.__name__} refused")
        return fn(*args, **kwargs)
    wrapper.__tagteam_writes__ = True
    return wrapper


# ---------- Cycle CRUD ----------

@_writes
def upsert_cycle(
    conn: sqlite3.Connection,
    phase: str,
    cycle_type: str,
    *,
    lead: str | None = None,
    reviewer: str | None = None,
    state: str = "in-progress",
    ready_for: str | None = None,
    ready_for_present: bool = True,
    round_: int = 0,
    date: str | None = None,
    created_at: str | None = None,
    closed_at: str | None = None,
    baseline: dict | None = None,
) -> int:
    """Create or update a cycle. Returns the cycle id."""
    if cycle_type not in VALID_TYPES:
        raise ValueError(f"Invalid cycle type: {cycle_type}")
    baseline_json = json.dumps(baseline) if baseline is not None else None
    cur = conn.execute(
        "SELECT id FROM cycles WHERE phase=? AND type=?", (phase, cycle_type)
    )
    row = cur.fetchone()
    if row:
        cycle_id = row[0]
        conn.execute(
            """UPDATE cycles SET lead=?, reviewer=?, state=?, ready_for=?,
                   ready_for_present=?, round=?, date=?,
                   created_at=COALESCE(created_at, ?),
                   closed_at=?,
                   baseline_json=COALESCE(?, baseline_json)
               WHERE id=?""",
            (lead, reviewer, state, ready_for, int(ready_for_present),
             round_, date,
             created_at, closed_at, baseline_json, cycle_id),
        )
        return cycle_id
    cur = conn.execute(
        """INSERT INTO cycles (phase, type, lead, reviewer, state, ready_for,
                               ready_for_present, round, date, created_at,
                               closed_at, baseline_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (phase, cycle_type, lead, reviewer, state, ready_for,
         int(ready_for_present),
         round_, date, created_at, closed_at, baseline_json),
    )
    return cur.lastrowid


def get_cycle(
    conn: sqlite3.Connection, phase: str, cycle_type: str
) -> dict | None:
    cur = conn.execute(
        """SELECT phase, type, lead, reviewer, state, ready_for,
                  ready_for_present, round, date, created_at, closed_at,
                  baseline_json
             FROM cycles WHERE phase=? AND type=?""",
        (phase, cycle_type),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    out = dict(zip(cols, row))
    if out.get("baseline_json"):
        out["baseline"] = json.loads(out["baseline_json"])
    out.pop("baseline_json", None)
    return out


def list_cycles(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT phase, type FROM cycles ORDER BY phase, type")
    return [{"phase": p, "type": t} for p, t in cur.fetchall()]


# ---------- Round CRUD ----------

@_writes
def add_round(
    conn: sqlite3.Connection,
    cycle_id: int,
    round_: int,
    role: str,
    action: str,
    content: str,
    ts: str,
    *,
    updated_by: str | None = None,
    summary: str | None = None,
) -> int:
    """Append a round to a cycle. Returns the new row id.

    Validates action and role against the same vocabularies as
    `tagteam.cycle`. Does NOT enforce state-machine transitions —
    that is the caller's responsibility.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Invalid action: {action}. Must be one of: "
            f"{', '.join(sorted(VALID_ACTIONS))}"
        )
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    cur = conn.execute(
        """INSERT INTO rounds (cycle_id, round, role, action, content, ts,
                               updated_by, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (cycle_id, round_, role, action, content, ts, updated_by, summary),
    )
    return cur.lastrowid


def get_rounds(
    conn: sqlite3.Connection, phase: str, cycle_type: str
) -> list[dict]:
    """Return rounds for a cycle in insertion order."""
    cur = conn.execute(
        """SELECT r.round, r.role, r.action, r.content, r.ts,
                  r.updated_by, r.summary
             FROM rounds r
             JOIN cycles c ON r.cycle_id = c.id
            WHERE c.phase=? AND c.type=?
            ORDER BY r.id""",
        (phase, cycle_type),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_rounds_since(
    conn: sqlite3.Connection,
    phase: str,
    cycle_type: str,
    after_id: int = 0,
) -> list[dict]:
    """Return rounds with id > after_id (tail-only reads). The id is
    monotonically increasing in insertion order."""
    cur = conn.execute(
        """SELECT r.id, r.round, r.role, r.action, r.content, r.ts,
                  r.updated_by, r.summary
             FROM rounds r
             JOIN cycles c ON r.cycle_id = c.id
            WHERE c.phase=? AND c.type=? AND r.id > ?
            ORDER BY r.id""",
        (phase, cycle_type, after_id),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------- State CRUD ----------

@_writes
def set_state(conn: sqlite3.Connection, **fields) -> None:
    """Upsert the singleton state row. Unspecified columns are set to NULL."""
    cols = ["phase", "type", "round", "status", "command", "result",
            "updated_by", "run_mode", "seq", "updated_at", "extra_json"]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    update_set = ", ".join(f"{c}=excluded.{c}" for c in cols)
    conn.execute(
        f"""INSERT INTO state (id, {col_list}) VALUES (1, {placeholders})
            ON CONFLICT(id) DO UPDATE SET {update_set}""",
        tuple(fields.get(c) for c in cols),
    )


def get_state(conn: sqlite3.Connection) -> dict | None:
    cur = conn.execute(
        """SELECT phase, type, round, status, command, result, updated_by,
                  run_mode, seq, updated_at, extra_json
             FROM state WHERE id=1""",
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cur.description]
    out = dict(zip(cols, row))
    if out.get("extra_json"):
        out.update(json.loads(out["extra_json"]))
    out.pop("extra_json", None)
    return out


@_writes
def add_history_entry(conn: sqlite3.Connection, entry: dict) -> None:
    conn.execute(
        """INSERT INTO state_history (ts, turn, status, phase, round, updated_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            entry.get("timestamp") or entry.get("ts"),
            entry.get("turn"),
            entry.get("status"),
            entry.get("phase"),
            entry.get("round"),
            entry.get("updated_by"),
        ),
    )


def get_history(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    sql = ("SELECT ts, turn, status, phase, round, updated_by "
           "FROM state_history ORDER BY id")
    if limit is not None:
        sql += f" DESC LIMIT {int(limit)}"
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------- Diagnostics ----------

@_writes
def add_diagnostic(conn: sqlite3.Connection, kind: str,
                   payload: dict, ts: str) -> int:
    cur = conn.execute(
        "INSERT INTO diagnostics (ts, kind, payload_json) VALUES (?, ?, ?)",
        (ts, kind, json.dumps(payload)),
    )
    return cur.lastrowid


# ---------- Usage (Phase 31, headless turns) ----------

_USAGE_COLS = [
    "ts", "phase", "type", "round", "role", "agent", "provider", "model",
    "status", "exit_code", "duration_ms", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "cost_usd", "num_turns",
    "session_id", "log_path", "kind",
    # v10 (Phase 55)
    "model_usage_json", "target_phase", "target_type", "target_round",
]
_USAGE_V10_COLUMNS = (("model_usage_json", "TEXT"), ("target_phase", "TEXT"),
                      ("target_type", "TEXT"), ("target_round", "INTEGER"))


@_writes
def add_usage(conn: sqlite3.Connection, **fields) -> int:
    """Insert one per-turn usage row. `ts` and `status` are required;
    everything else is nullable. Unknown keys raise. Commits."""
    unknown = set(fields) - set(_USAGE_COLS)
    if unknown:
        raise ValueError(f"Unknown usage fields: {sorted(unknown)}")
    if not fields.get("ts"):
        raise ValueError("usage.ts is required")
    status = fields.get("status")
    if status not in USAGE_STATUSES:
        raise ValueError(
            f"Invalid usage.status: {status!r}. Must be one of: "
            f"{', '.join(sorted(USAGE_STATUSES))}"
        )
    cols = [c for c in _USAGE_COLS if c in fields]
    placeholders = ", ".join("?" for _ in cols)
    cur = conn.execute(
        f"INSERT INTO usage ({', '.join(cols)}) VALUES ({placeholders})",
        [fields[c] for c in cols],
    )
    conn.commit()
    return cur.lastrowid


def get_usage(conn: sqlite3.Connection, phase: str | None = None,
              cycle_type: str | None = None,
              limit: int | None = None, *,
              target_phase: str | None = None) -> list[dict]:
    """Return usage rows (oldest first), optionally filtered by stored
    phase/type or (Phase 55) by `target_phase`.

    Column-tolerant: selects only the columns this DB has (an older schema
    read through `connect_for_read`); absent columns come back as None, an
    absent table or target column as no rows. `model_usage_json` is also
    returned parsed as `model_usage` (None when absent or unparseable).
    """
    present = table_columns(conn, "usage")
    if not present:
        return []
    if target_phase is not None and "target_phase" not in present:
        return []
    where, params = [], []
    if phase is not None:
        where.append("phase = ?"); params.append(phase)
    if cycle_type is not None:
        where.append("type = ?"); params.append(cycle_type)
    if target_phase is not None:
        where.append("target_phase = ?"); params.append(target_phase)
    cols = [c for c in _USAGE_COLS if c in present]
    sql = "SELECT id, " + ", ".join(cols) + " FROM usage"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"; params.append(int(limit))
    cur = conn.execute(sql, params)
    names = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        d = {c: None for c in _USAGE_COLS}
        d.update(zip(names, row))
        d["model_usage"] = _parse_model_usage(d.get("model_usage_json"))
        out.append(d)
    return out


def _parse_model_usage(raw) -> dict | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        val = json.loads(raw)
    except ValueError:
        return None
    return val if isinstance(val, dict) else None


# ---------- Interjections (Phase 32) ----------

_INTERJECTION_COLS = [
    "id", "ts", "by", "note", "target_role", "phase", "type", "round", "turn",
    "observed_state", "delivered_role", "delivered_round", "delivered_stem",
    "delivered_ts", "retired_ts", "retired_by",
]


@_writes
def add_interjection(conn: sqlite3.Connection, *, ts: str, note: str,
                     by: str | None = None, target_role: str | None = None,
                     phase: str | None = None, cycle_type: str | None = None,
                     round_: int | None = None, turn: str | None = None,
                     observed_state: dict | None = None) -> int:
    """Insert an arbiter note. Commits. Returns the row id."""
    if not note or not note.strip():
        raise ValueError("interjection note must be non-empty")
    if target_role is not None and target_role not in INTERJECTION_ROLES:
        raise ValueError(
            f"Invalid target_role: {target_role!r} (must be one of "
            f"{', '.join(sorted(INTERJECTION_ROLES))})")
    cur = conn.execute(
        """INSERT INTO interjections
           (ts, by, note, target_role, phase, type, round, turn, observed_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ts, by, note, target_role, phase, cycle_type, round_, turn,
         json.dumps(observed_state) if observed_state is not None else None),
    )
    conn.commit()
    return cur.lastrowid


def _rows(cur) -> list[dict]:
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def get_interjections(conn: sqlite3.Connection, phase: str | None = None,
                      cycle_type: str | None = None,
                      undelivered_only: bool = False,
                      include_retired: bool = True) -> list[dict]:
    where, params = [], []
    if phase is not None:
        where.append("phase = ?"); params.append(phase)
    if cycle_type is not None:
        where.append("type = ?"); params.append(cycle_type)
    if undelivered_only:
        where.append("delivered_ts IS NULL")
    if not include_retired:
        where.append("retired_ts IS NULL")
    sql = "SELECT " + ", ".join(_INTERJECTION_COLS) + " FROM interjections"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    return _rows(conn.execute(sql, params))


def pending_interjections_for(conn: sqlite3.Connection, role: str,
                              phase: str | None, cycle_type: str | None) -> list[dict]:
    """Eligible notes for a turn of `role` whose verification target is the
    cycle (phase, type): undelivered, unretired, untargeted-or-matching the
    role, and either scoped to that cycle or written with nothing owed
    (phase IS NULL). Ordered by id."""
    if role not in INTERJECTION_ROLES:
        raise ValueError(f"Invalid role: {role!r}")
    sql = ("SELECT " + ", ".join(_INTERJECTION_COLS) + " FROM interjections "
           "WHERE delivered_ts IS NULL AND retired_ts IS NULL "
           "AND (target_role IS NULL OR target_role = ?) "
           "AND (phase IS NULL OR (phase = ? AND type = ?)) ORDER BY id")
    return _rows(conn.execute(sql, (role, phase, cycle_type)))


@_writes
def mark_interjections_delivered(conn: sqlite3.Connection, ids: list[int], *,
                                 role: str, round_: int, stem: str, ts: str) -> int:
    """Stamp delivery on exactly `ids`. Commits. Returns rows updated."""
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"""UPDATE interjections
               SET delivered_role=?, delivered_round=?, delivered_stem=?, delivered_ts=?
             WHERE id IN ({marks}) AND delivered_ts IS NULL""",
        [role, round_, stem, ts, *ids],
    )
    conn.commit()
    return cur.rowcount


@_writes
def retire_interjection(conn: sqlite3.Connection, id_: int, *, by: str | None,
                        ts: str) -> bool:
    """Close a note without delivery. Returns False if not found or already
    delivered/retired. Commits."""
    cur = conn.execute(
        "UPDATE interjections SET retired_ts=?, retired_by=? "
        "WHERE id=? AND delivered_ts IS NULL AND retired_ts IS NULL",
        (ts, by, id_),
    )
    conn.commit()
    return cur.rowcount == 1


# ---------- Briefs (Phase 33) ----------

_BRIEF_COLS = [
    "id", "ts", "phase", "type", "round", "cycle_state", "event_key", "event_row_id",
    "kind", "attempt", "status", "started_at", "finished_at", "runner_pid",
    "runner_ident", "stem", "path", "content", "provider", "model", "usage_row_id",
    "duration_ms", "reason",
]


@_writes
def claim_brief(conn: sqlite3.Connection, *, ts: str, phase: str, cycle_type: str,
                round_: int, cycle_state: str, event_key: str, kind: str,
                runner_pid: int | None, runner_ident: str | None,
                event_row_id: int | None = None, provider: str | None = None
                ) -> tuple[int, int] | None:
    """Atomically claim a briefer attempt for `event_key`.

    Single INSERT … SELECT … WHERE NOT EXISTS (an ok|partial row for the
    event) that also allocates `attempt = 1 + max(attempt)` over the
    event's rows (both kinds). The two partial unique indexes reject a
    second automatic attempt / a second running attempt. Returns
    (row_id, attempt) or None when the claim is refused. Caller holds the
    project writer lock. Commits.
    """
    if kind not in BRIEF_KINDS:
        raise ValueError(f"Invalid brief kind: {kind!r}")
    if cycle_state not in ("escalated", "needs-human"):
        raise ValueError(f"Invalid cycle_state for a brief: {cycle_state!r}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """INSERT INTO briefs
               (ts, phase, type, round, cycle_state, event_key, event_row_id, kind,
                attempt, status, started_at, runner_pid, runner_ident, provider)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?,
                      COALESCE((SELECT MAX(attempt) FROM briefs WHERE event_key = ?), 0) + 1,
                      'running', ?, ?, ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM briefs
                                  WHERE event_key = ? AND status IN ('ok','partial'))""",
            (ts, phase, cycle_type, round_, cycle_state, event_key, event_row_id, kind,
             event_key, ts, runner_pid, runner_ident, provider, event_key),
        )
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return None
        row_id = cur.lastrowid
        attempt = conn.execute("SELECT attempt FROM briefs WHERE id=?", (row_id,)).fetchone()[0]
        conn.execute("COMMIT")
        return row_id, attempt
    except sqlite3.IntegrityError:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        return None


@_writes
def finish_brief(conn: sqlite3.Connection, id_: int, *, status: str, ts: str,
                 stem: str | None = None, path: str | None = None,
                 content: str | None = None, model: str | None = None,
                 usage_row_id: int | None = None, duration_ms: int | None = None,
                 reason: str | None = None) -> None:
    if status not in BRIEF_STATUSES or status == "running":
        raise ValueError(f"Invalid final brief status: {status!r}")
    conn.execute(
        """UPDATE briefs SET status=?, finished_at=?, stem=COALESCE(?, stem), path=?,
               content=?, model=?, usage_row_id=?, duration_ms=?, reason=?
           WHERE id=?""",
        (status, ts, stem, path, content, model, usage_row_id, duration_ms, reason, id_),
    )
    conn.commit()


@_writes
def set_brief_stem(conn: sqlite3.Connection, id_: int, stem: str) -> None:
    conn.execute("UPDATE briefs SET stem=? WHERE id=?", (stem, id_))
    conn.commit()


@_writes
def mark_brief_abandoned(conn: sqlite3.Connection, id_: int, *, ts: str,
                         reason: str) -> bool:
    cur = conn.execute(
        "UPDATE briefs SET status='abandoned', finished_at=?, reason=? "
        "WHERE id=? AND status='running'", (ts, reason, id_))
    conn.commit()
    return cur.rowcount == 1


def get_brief(conn: sqlite3.Connection, id_: int) -> dict | None:
    rows = _rows(conn.execute("SELECT " + ", ".join(_BRIEF_COLS) +
                              " FROM briefs WHERE id=?", (id_,)))
    return rows[0] if rows else None


def successful_brief_for_event(conn: sqlite3.Connection, event_key: str) -> dict | None:
    """Highest-id ok|partial brief for THIS event, or None. The only lookup
    `tagteam brief` / `tagteam rule` use — never a cycle-wide latest."""
    rows = _rows(conn.execute(
        "SELECT " + ", ".join(_BRIEF_COLS) + " FROM briefs WHERE event_key=? "
        "AND status IN ('ok','partial') ORDER BY id DESC LIMIT 1", (event_key,)))
    return rows[0] if rows else None


def briefs_for_event(conn: sqlite3.Connection, event_key: str) -> list[dict]:
    return _rows(conn.execute("SELECT " + ", ".join(_BRIEF_COLS) +
                              " FROM briefs WHERE event_key=? ORDER BY id", (event_key,)))


def running_briefs(conn: sqlite3.Connection, event_key: str | None = None) -> list[dict]:
    if event_key is None:
        return _rows(conn.execute("SELECT " + ", ".join(_BRIEF_COLS) +
                                  " FROM briefs WHERE status='running' ORDER BY id"))
    return _rows(conn.execute("SELECT " + ", ".join(_BRIEF_COLS) +
                              " FROM briefs WHERE status='running' AND event_key=? ORDER BY id",
                              (event_key,)))


def brief_history(conn: sqlite3.Connection, phase: str | None = None,
                  cycle_type: str | None = None) -> list[dict]:
    """All rows (newest first) — for `--list` / `--event` only."""
    where, params = [], []
    if phase is not None:
        where.append("phase=?"); params.append(phase)
    if cycle_type is not None:
        where.append("type=?"); params.append(cycle_type)
    sql = "SELECT " + ", ".join(_BRIEF_COLS) + " FROM briefs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    return _rows(conn.execute(sql, params))


# ---------- Rate limits (Phase 34) ----------

_RATE_LIMIT_COLS = ["id", "provider", "kind", "status", "resets_at", "payload_json", "ts"]


@_writes
def upsert_rate_limit(conn: sqlite3.Connection, *, provider: str, kind: str,
                      status: str | None, resets_at: str | None,
                      payload: dict | None, ts: str) -> int:
    """Insert or replace the latest signal for (provider, kind). Commits.
    Returns the row id (stable across updates)."""
    if not provider or not kind:
        raise ValueError("rate_limits.provider and .kind are required")
    if not ts:
        raise ValueError("rate_limits.ts is required")
    payload_json = json.dumps(payload) if payload is not None else None
    conn.execute(
        """INSERT INTO rate_limits (provider, kind, status, resets_at, payload_json, ts)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(provider, kind) DO UPDATE SET
               status=excluded.status, resets_at=excluded.resets_at,
               payload_json=excluded.payload_json, ts=excluded.ts""",
        (provider, kind, status, resets_at, payload_json, ts),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM rate_limits WHERE provider=? AND kind=?",
                       (provider, kind)).fetchone()
    return int(row[0])


def latest_rate_limits(conn: sqlite3.Connection, provider: str | None = None) -> list[dict]:
    """All (provider, kind) rows, ordered by provider then kind. `payload`
    is the decoded JSON (or None)."""
    sql = "SELECT " + ", ".join(_RATE_LIMIT_COLS) + " FROM rate_limits"
    params: list = []
    if provider is not None:
        sql += " WHERE provider=?"; params.append(provider)
    sql += " ORDER BY provider, kind"
    rows = _rows(conn.execute(sql, params))
    for r in rows:
        try:
            r["payload"] = json.loads(r["payload_json"]) if r.get("payload_json") else None
        except ValueError:
            r["payload"] = None
    return rows


# ---------- Phase 37: conversations / turns / launches ----------

CONVERSATION_ID_RE = re.compile(r"^c-[0-9a-f]{12}$")
TURN_STATUSES = ("running", "ok", "failed", "cancelled")
LAUNCH_STATUSES = ("pending", "succeeded", "failed")


def _row(cur) -> dict | None:
    r = cur.fetchone()
    if r is None:
        return None
    return dict(zip([d[0] for d in cur.description], r))


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


@_writes
def new_conversation(conn: sqlite3.Connection, *, id_: str, ts: str, provider: str | None,
                     title: str | None = None) -> dict:
    if not CONVERSATION_ID_RE.match(id_ or ""):
        raise ValueError(f"invalid conversation id: {id_!r}")
    conn.execute("INSERT INTO conversations (id, created_at, provider, title, last_ts) VALUES (?,?,?,?,?)",
                 (id_, ts, provider, title, ts))
    conn.commit()
    return get_conversation(conn, id_)


def get_conversation(conn: sqlite3.Connection, id_: str) -> dict | None:
    if not CONVERSATION_ID_RE.match(id_ or ""):
        return None
    return _row(conn.execute("SELECT * FROM conversations WHERE id=?", (id_,)))


def list_conversations(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    return _rows(conn.execute(
        "SELECT c.*, (SELECT COUNT(*) FROM conversation_turns t WHERE t.conversation_id=c.id) AS turns "
        "FROM conversations c ORDER BY COALESCE(last_ts, created_at) DESC, id LIMIT ?", (int(limit),)))


@_writes
def update_conversation(conn: sqlite3.Connection, id_: str, **fields) -> None:
    allowed = {"session_id", "title", "last_ts", "continuity", "provider"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown conversation fields: {sorted(bad)}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE conversations SET {sets} WHERE id=?", [*fields.values(), id_])
    conn.commit()


@_writes
def add_conversation_turn(conn: sqlite3.Connection, *, conversation_id: str, ts: str,
                          user_text: str, owner_pid: int | None, owner_ident: str | None,
                          log_path: str | None = None, events_path: str | None = None) -> dict:
    """Append the next turn (n = max+1) as `running`. Commits."""
    n = conn.execute("SELECT COALESCE(MAX(n), 0) + 1 FROM conversation_turns WHERE conversation_id=?",
                     (conversation_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO conversation_turns (conversation_id, n, ts, user_text, status, owner_pid, owner_ident, log_path, events_path) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (conversation_id, n, ts, user_text, "running", owner_pid, owner_ident, log_path, events_path))
    conn.execute("UPDATE conversations SET last_ts=? WHERE id=?", (ts, conversation_id))
    conn.commit()
    return get_conversation_turn(conn, conversation_id, n)


def get_conversation_turn(conn: sqlite3.Connection, conversation_id: str, n: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM conversation_turns WHERE conversation_id=? AND n=?",
                             (conversation_id, int(n))))


def list_conversation_turns(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM conversation_turns WHERE conversation_id=? ORDER BY n",
                              (conversation_id,)))


@_writes
def finish_conversation_turn(conn: sqlite3.Connection, conversation_id: str, n: int, *,
                             status: str, ts: str, session_id: str | None = None,
                             usage_row_id: int | None = None, error: str | None = None,
                             log_path: str | None = None, events_path: str | None = None,
                             reply: str | None = None, continuity: str | None = None) -> None:
    if status not in TURN_STATUSES or status == "running":
        raise ValueError(f"invalid final turn status: {status!r}")
    conn.execute(
        "UPDATE conversation_turns SET status=?, finished_at=?, session_id=COALESCE(?, session_id), "
        "usage_row_id=COALESCE(?, usage_row_id), error=?, log_path=COALESCE(?, log_path), "
        "events_path=COALESCE(?, events_path), reply=COALESCE(?, reply), "
        "continuity=COALESCE(?, continuity) WHERE conversation_id=? AND n=?",
        (status, ts, session_id, usage_row_id, error, log_path, events_path, reply, continuity,
         conversation_id, int(n)))
    conn.commit()


def running_conversation_turns(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM conversation_turns WHERE status='running' ORDER BY id"))


@_writes
def claim_launch(conn: sqlite3.Connection, *, key: str, ts: str, intent_json: str,
                 owner_pid: int, owner_ident: str | None) -> tuple[dict, bool]:
    """Insert the launch claim for `key` if none exists. Returns (row,
    created). Never overwrites an existing row (UNIQUE key)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO launches (key, status, attempt, intent_json, owner_pid, owner_ident, created_at, updated_at) "
        "VALUES (?, 'pending', 1, ?, ?, ?, ?, ?)", (key, intent_json, owner_pid, owner_ident, ts, ts))
    created = cur.rowcount == 1
    conn.commit()
    return get_launch(conn, key), created


def get_launch(conn: sqlite3.Connection, key: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM launches WHERE key=?", (key,)))


@_writes
def update_launch(conn: sqlite3.Connection, key: str, *, ts: str, **fields) -> None:
    allowed = {"status", "watcher_pid", "watcher_ident", "conversation_id", "turn_n",
               "finished_at", "error", "partial_json", "owner_pid", "owner_ident"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown launch fields: {sorted(bad)}")
    if "status" in fields and fields["status"] not in LAUNCH_STATUSES:
        raise ValueError(f"invalid launch status: {fields['status']!r}")
    fields["updated_at"] = ts
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE launches SET {sets} WHERE key=?", [*fields.values(), key])
    conn.commit()


@_writes
def retry_launch(conn: sqlite3.Connection, key: str, *, ts: str, owner_pid: int,
                 owner_ident: str | None) -> bool:
    """Atomic failed → pending transition with attempt+1 and a new owner
    (no second insert under the UNIQUE key). Returns True if transitioned."""
    cur = conn.execute(
        "UPDATE launches SET status='pending', attempt=attempt+1, owner_pid=?, owner_ident=?, "
        "updated_at=?, finished_at=NULL, error=NULL WHERE key=? AND status='failed'",
        (owner_pid, owner_ident, ts, key))
    conn.commit()
    return cur.rowcount == 1


def pending_launches(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM launches WHERE status='pending' ORDER BY id"))


# ---------- Phase 38: gates ----------

GATE_KINDS = ("auto", "manual")
GATE_STATUSES = ("running", "pass", "bounce", "superseded", "error", "abandoned")


# Satellite claim tables (Phase 38 gates, Phase 39 panels) share ONE
# at-most-once implementation: table → (decided statuses, failed statuses).
_SATELLITE_TABLES = {
    "gates": {"decided": ("pass", "bounce"), "failed": ("error", "abandoned")},
    "panels": {"decided": ("merged", "fallback"), "failed": ("error", "abandoned")},
}


@_writes
def _claim_satellite(conn: sqlite3.Connection, table: str, *, ts: str, phase: str, cycle_type: str,
                     round_: int, submission_seq: int, event_key: str, kind: str,
                     runner_pid: int | None, runner_ident: str | None,
                     max_attempts: int, extra: dict | None = None) -> tuple[int, int] | None:
    """Atomically claim an attempt row for `event_key` in `table`: one
    INSERT … SELECT … WHERE NOT EXISTS (a decided row) AND NOT EXISTS (a
    running row) AND the number of FAILED attempts (`error|abandoned` —
    superseded attempts never consume the budget) is below `max_attempts`;
    allocates `attempt = 1 + max(attempt)`. Returns (row_id, attempt) or
    None when refused. Caller holds the writer lock. Commits."""
    spec = _SATELLITE_TABLES[table]
    decided = ", ".join(f"'{x}'" for x in spec["decided"])
    failed = ", ".join(f"'{x}'" for x in spec["failed"])
    extra = dict(extra or {})
    cols = ["event_key", "phase", "type", "round", "submission_seq", "kind", "status", "attempt",
            "runner_pid", "runner_ident", "started_at", "updated_at", *extra.keys()]
    sel = ["?", "?", "?", "?", "?", "?", "'running'",
           f"COALESCE((SELECT MAX(attempt) FROM {table} WHERE event_key = ?), 0) + 1",
           "?", "?", "?", "?", *(["?"] * len(extra))]
    params = [event_key, phase, cycle_type, int(round_), int(submission_seq), kind,
              event_key, runner_pid, runner_ident, ts, ts, *extra.values(),
              event_key, event_key, event_key, int(max_attempts)]
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            f"""INSERT INTO {table} ({", ".join(cols)})
                SELECT {", ".join(sel)}
                WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE event_key = ? AND status IN ({decided}))
                  AND NOT EXISTS (SELECT 1 FROM {table} WHERE event_key = ? AND status = 'running')
                  AND (SELECT COUNT(*) FROM {table} WHERE event_key = ? AND status IN ({failed})) < ?""",
            params,
        )
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return None
        row_id = cur.lastrowid
        attempt = conn.execute(f"SELECT attempt FROM {table} WHERE id=?", (row_id,)).fetchone()[0]
        conn.execute("COMMIT")
        return row_id, attempt
    except sqlite3.IntegrityError:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        return None


def claim_gate(conn: sqlite3.Connection, *, ts: str, phase: str, cycle_type: str,
               round_: int, submission_seq: int, event_key: str, kind: str,
               runner_pid: int | None, runner_ident: str | None,
               max_attempts: int = 2) -> tuple[int, int] | None:
    """Claim a gate attempt (see `_claim_satellite`). `max_attempts` bounds
    FAILED attempts (`error|abandoned`); superseded rows do not count."""
    if kind not in GATE_KINDS:
        raise ValueError(f"Invalid gate kind: {kind!r}")
    return _claim_satellite(conn, "gates", ts=ts, phase=phase, cycle_type=cycle_type, round_=round_,
                            submission_seq=submission_seq, event_key=event_key, kind=kind,
                            runner_pid=runner_pid, runner_ident=runner_ident, max_attempts=max_attempts)


@_writes
def finish_gate(conn: sqlite3.Connection, id_: int, *, status: str, ts: str,
                duration_s: float | None = None, result_json: str | None = None,
                stem: str | None = None, reason: str | None = None,
                applied_seq: int | None = None) -> None:
    if status not in GATE_STATUSES or status == "running":
        raise ValueError(f"Invalid final gate status: {status!r}")
    conn.execute(
        """UPDATE gates SET status=?, finished_at=?, updated_at=?, duration_s=COALESCE(?, duration_s),
               result_json=COALESCE(?, result_json), stem=COALESCE(?, stem), reason=?,
               applied_seq=COALESCE(?, applied_seq) WHERE id=?""",
        (status, ts, ts, duration_s, result_json, stem, reason, applied_seq, int(id_)))
    conn.commit()


@_writes
def update_gate(conn: sqlite3.Connection, id_: int, *, ts: str, **fields) -> None:
    allowed = {"stem", "result_json", "reason", "applied_seq", "runner_pid", "runner_ident"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown gate fields: {sorted(bad)}")
    fields["updated_at"] = ts
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE gates SET {sets} WHERE id=?", [*fields.values(), int(id_)])
    conn.commit()


def get_gate(conn: sqlite3.Connection, id_: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM gates WHERE id=?", (int(id_),)))


def gates_for_event(conn: sqlite3.Connection, event_key: str) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM gates WHERE event_key=? ORDER BY attempt, id", (event_key,)))


def decided_gate_for_event(conn: sqlite3.Connection, event_key: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM gates WHERE event_key=? AND status IN ('pass','bounce') ORDER BY id DESC LIMIT 1",
                             (event_key,)))


def gates_for_cycle(conn: sqlite3.Connection, phase: str, cycle_type: str) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM gates WHERE phase=? AND type=? ORDER BY id", (phase, cycle_type)))


def last_gate(conn: sqlite3.Connection, phase: str, cycle_type: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM gates WHERE phase=? AND type=? ORDER BY id DESC LIMIT 1", (phase, cycle_type)))


def running_gates(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM gates WHERE status='running' ORDER BY id"))


def unfinished_gates(conn: sqlite3.Connection) -> list[dict]:
    """running / abandoned / error rows — candidates for reconciliation."""
    return _rows(conn.execute("SELECT * FROM gates WHERE status IN ('running','abandoned','error') ORDER BY id"))


# ---------------------------------------------------------------------------
# panels (Phase 39)
# ---------------------------------------------------------------------------

PANEL_KINDS = ("auto", "manual")
PANEL_STATUSES = ("running", "merged", "fallback", "superseded", "error", "abandoned")


def claim_panel(conn: sqlite3.Connection, *, ts: str, phase: str, cycle_type: str,
                round_: int, submission_seq: int, event_key: str, kind: str,
                runner_pid: int | None, runner_ident: str | None,
                interjection_ids: list[int] | None = None,
                max_attempts: int = 2) -> tuple[int, int] | None:
    """Claim a panel attempt (see `_claim_satellite`); the reviewer-note
    snapshot the lenses will see is recorded on the row at claim time."""
    if kind not in PANEL_KINDS:
        raise ValueError(f"Invalid panel kind: {kind!r}")
    return _claim_satellite(conn, "panels", ts=ts, phase=phase, cycle_type=cycle_type, round_=round_,
                            submission_seq=submission_seq, event_key=event_key, kind=kind,
                            runner_pid=runner_pid, runner_ident=runner_ident, max_attempts=max_attempts,
                            extra={"interjection_ids": json.dumps(list(interjection_ids or []))})


@_writes
def finish_panel(conn: sqlite3.Connection, id_: int, *, status: str, ts: str,
                 duration_s: float | None = None, lenses_json: str | None = None,
                 decision: str | None = None, stem: str | None = None, reason: str | None = None,
                 applied_seq: int | None = None) -> None:
    if status not in PANEL_STATUSES or status == "running":
        raise ValueError(f"Invalid final panel status: {status!r}")
    conn.execute(
        """UPDATE panels SET status=?, finished_at=?, updated_at=?, duration_s=COALESCE(?, duration_s),
               lenses_json=COALESCE(?, lenses_json), decision=COALESCE(?, decision), stem=COALESCE(?, stem),
               reason=?, applied_seq=COALESCE(?, applied_seq) WHERE id=?""",
        (status, ts, ts, duration_s, lenses_json, decision, stem, reason, applied_seq, int(id_)))
    conn.commit()


@_writes
def update_panel(conn: sqlite3.Connection, id_: int, *, ts: str, **fields) -> None:
    allowed = {"stem", "lenses_json", "decision", "reason", "applied_seq", "runner_pid", "runner_ident",
               "interjection_ids"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown panel fields: {sorted(bad)}")
    fields["updated_at"] = ts
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE panels SET {sets} WHERE id=?", [*fields.values(), int(id_)])
    conn.commit()


def get_panel(conn: sqlite3.Connection, id_: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM panels WHERE id=?", (int(id_),)))


def panels_for_event(conn: sqlite3.Connection, event_key: str) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM panels WHERE event_key=? ORDER BY attempt, id", (event_key,)))


def decided_panel_for_event(conn: sqlite3.Connection, event_key: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM panels WHERE event_key=? AND status IN ('merged','fallback') "
                             "ORDER BY id DESC LIMIT 1", (event_key,)))


def panels_for_cycle(conn: sqlite3.Connection, phase: str, cycle_type: str) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM panels WHERE phase=? AND type=? ORDER BY id", (phase, cycle_type)))


def last_panel(conn: sqlite3.Connection, phase: str, cycle_type: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM panels WHERE phase=? AND type=? ORDER BY id DESC LIMIT 1", (phase, cycle_type)))


def running_panels(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM panels WHERE status='running' ORDER BY id"))


def unfinished_panels(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn.execute("SELECT * FROM panels WHERE status IN ('running','abandoned','error') ORDER BY id"))


def snapshot_non_file_backed(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Copy every row of the non-file-backed tables (repair preservation)."""
    out: dict[str, list[tuple]] = {}
    for table in NON_FILE_BACKED_TABLES:
        try:
            cur = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
            cols = [d[0] for d in cur.description]
            out[table] = [tuple(cols)] + [tuple(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            out[table] = []
    return out


@_writes
def restore_non_file_backed(conn: sqlite3.Connection, snapshot: dict[str, list[tuple]]) -> dict[str, int]:
    """Re-insert snapshotted rows unchanged (ids preserved). Commits."""
    counts: dict[str, int] = {}
    for table, rows in snapshot.items():
        if not rows:
            counts[table] = 0
            continue
        cols = rows[0]
        # only columns the current schema still has (additive schema → all of them)
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        keep = [i for i, c in enumerate(cols) if c in existing]
        names = [cols[i] for i in keep]
        placeholders = ", ".join("?" for _ in names)
        n = 0
        for row in rows[1:]:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
                [row[i] for i in keep])
            n += 1
        counts[table] = n
    conn.commit()
    return counts


# ---------- Importer (one-way, from existing tagteam project files) ----------

_ROUNDS_RE = re.compile(r"^(.+)_(plan|impl)_rounds\.jsonl$")
_STATUS_RE = re.compile(r"^(.+)_(plan|impl)_status\.json$")


@_writes
def import_from_files(project_dir: Path, conn: sqlite3.Connection) -> dict:
    """Read tagteam project files, populate the database. Read-only on
    source. Idempotent in spirit but not safe to re-run on a DB that
    has had additional rounds added since the last import — the
    importer will re-insert them. Production migrate command should
    refuse to run on a non-empty DB unless `--force`.

    Returns a small report dict with counts.
    """
    handoffs = project_dir / "docs" / "handoffs"
    if not handoffs.is_dir():
        raise FileNotFoundError(f"No docs/handoffs in {project_dir}")

    cycles_imported = 0
    rounds_imported = 0
    history_imported = 0

    # Pass 1: status files create cycle rows with terminal state info.
    for f in sorted(handoffs.iterdir()):
        m = _STATUS_RE.match(f.name)
        if not m:
            continue
        phase, cycle_type = m.group(1), m.group(2)
        s = json.loads(f.read_text(encoding="utf-8"))
        upsert_cycle(
            conn,
            phase,
            cycle_type,
            lead=s.get("lead"),
            reviewer=s.get("reviewer"),
            state=s.get("state", "unknown"),
            ready_for=s.get("ready_for"),
            ready_for_present="ready_for" in s,
            round_=s.get("round", 0),
            date=s.get("date"),
            baseline=s.get("baseline"),
        )
        cycles_imported += 1

    # Pass 2: rounds files. Don't clobber pass-1 cycle rows; only
    # create new rows if the cycle had rounds but no status file.
    for f in sorted(handoffs.iterdir()):
        m = _ROUNDS_RE.match(f.name)
        if not m:
            continue
        phase, cycle_type = m.group(1), m.group(2)
        cur = conn.execute(
            "SELECT id FROM cycles WHERE phase=? AND type=?",
            (phase, cycle_type),
        )
        row = cur.fetchone()
        created_from_rounds = row is None
        cycle_id = row[0] if row else upsert_cycle(conn, phase, cycle_type)

        ts_min = ts_max = None
        last_action = None
        max_round = None
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            add_round(
                conn,
                cycle_id,
                r["round"],
                r["role"],
                r["action"],
                r.get("content", ""),
                r["ts"],
                updated_by=r.get("updated_by"),
                summary=r.get("summary"),
            )
            rounds_imported += 1
            last_action = r.get("action")
            round_num = r["round"]
            if max_round is None or round_num > max_round:
                max_round = round_num
            ts = r["ts"]
            # tagteam writes UTC ISO 8601 strings; lexical order matches time.
            if ts_min is None or ts < ts_min:
                ts_min = ts
            if ts_max is None or ts > ts_max:
                ts_max = ts
        if created_from_rounds and last_action in _ACTION_TO_STATUS:
            state, ready_for = _ACTION_TO_STATUS[last_action]
            conn.execute(
                "UPDATE cycles SET state=?, ready_for=?, round=? WHERE id=?",
                (state, ready_for, max_round or 0, cycle_id),
            )
        cycle_state = conn.execute(
            "SELECT state FROM cycles WHERE id=?", (cycle_id,)
        ).fetchone()[0]
        closed = ts_max if cycle_state in TERMINAL_CYCLE_STATES else None
        conn.execute(
            """UPDATE cycles
                  SET created_at = COALESCE(created_at, ?),
                      closed_at  = COALESCE(closed_at, ?)
                WHERE id=?""",
            (ts_min, closed, cycle_id),
        )

    # State + history
    state_path = project_dir / "handoff-state.json"
    if state_path.exists():
        st = json.loads(state_path.read_text(encoding="utf-8"))
        known = {"phase", "type", "round", "status", "command", "result",
                 "updated_by", "run_mode", "seq", "updated_at", "history"}
        extra = {k: v for k, v in st.items() if k not in known}
        set_state(
            conn,
            phase=st.get("phase"),
            type=st.get("type"),
            round=st.get("round"),
            status=st.get("status"),
            command=st.get("command"),
            result=st.get("result"),
            updated_by=st.get("updated_by"),
            run_mode=st.get("run_mode"),
            seq=st.get("seq"),
            updated_at=st.get("updated_at"),
            extra_json=json.dumps(extra) if extra else None,
        )
        for h in st.get("history", []):
            add_history_entry(conn, h)
            history_imported += 1

    conn.commit()
    return {
        "cycles": cycles_imported,
        "rounds": rounds_imported,
        "history_entries": history_imported,
    }


# ---------- Exporter (inverse of import_from_files) ----------

def export_to_files(conn: sqlite3.Connection, project_dir: Path) -> dict:
    """Write the canonical file-side state from the DB.

    Inverse of `import_from_files`. Used by Phase 28 Step B's
    `--reverse` migration (to restore files when downgrading from
    DB-canonical) and by post-rebuild auto-export hooks.

    Output round-trips with `import_from_files`: re-importing the
    just-exported files produces an equivalent DB. Round-trip
    fidelity covers:

      - `ready_for` missing-key vs explicit-null (preserved via the
        `cycles.ready_for_present` flag).
      - Round entries with optional `updated_by` / `summary` only
        included when non-null, matching pre-Phase-28 file shape.
      - Status `baseline` block written only when non-null.
      - `state.extra_json` fields flattened back to top-level keys
        in handoff-state.json.

    The function does NOT delete files that exist on disk but not
    in the DB — callers that want a true mirror must clean target
    paths themselves. This is intentional: a partial export is
    safer than silently dropping cycles the caller didn't know
    about.
    """
    project_dir = Path(project_dir)
    handoffs = project_dir / "docs" / "handoffs"
    handoffs.mkdir(parents=True, exist_ok=True)

    cycles_written = 0
    rounds_written = 0

    # Per-cycle status + rounds files.
    for entry in list_cycles(conn):
        phase, cycle_type = entry["phase"], entry["type"]
        cycle = get_cycle(conn, phase, cycle_type)
        if cycle is None:
            continue  # listed but disappeared; race or test artifact
        rounds = get_rounds(conn, phase, cycle_type)

        status: dict = {
            "state": cycle["state"],
            "round": cycle.get("round") or 0,
            "phase": phase,
            "type": cycle_type,
            "lead": cycle.get("lead"),
            "reviewer": cycle.get("reviewer"),
            "date": cycle.get("date"),
        }
        # Preserve ready_for missing-vs-null distinction.
        cur = conn.execute(
            "SELECT ready_for_present FROM cycles WHERE phase=? AND type=?",
            (phase, cycle_type),
        )
        row = cur.fetchone()
        ready_for_present = bool(row[0]) if row else True
        if ready_for_present:
            status["ready_for"] = cycle.get("ready_for")
        # Baseline block — write only when populated.
        baseline = cycle.get("baseline")
        if baseline is not None:
            status["baseline"] = baseline

        status_path = handoffs / f"{phase}_{cycle_type}_status.json"
        status_path.write_text(
            json.dumps(status, indent=2) + "\n", encoding="utf-8"
        )

        rounds_path = handoffs / f"{phase}_{cycle_type}_rounds.jsonl"
        with rounds_path.open("w", encoding="utf-8") as f:
            for r in rounds:
                # Match pre-Phase-28 minimal shape: include
                # updated_by / summary only when present, so older
                # consumers that don't know those fields don't see
                # explicit nulls they have to ignore.
                entry: dict = {
                    "round": r["round"],
                    "role": r["role"],
                    "action": r["action"],
                    "content": r.get("content") or "",
                    "ts": r["ts"],
                }
                if r.get("updated_by") is not None:
                    entry["updated_by"] = r["updated_by"]
                if r.get("summary") is not None:
                    entry["summary"] = r["summary"]
                f.write(json.dumps(entry) + "\n")
                rounds_written += 1
        cycles_written += 1

    # Top-level state file.
    state_written = False
    state = get_state(conn)
    if state is not None:
        history = get_history(conn)
        # `get_state` already unpacks extra_json into the dict; we
        # just need to drop the (now-meaningless) extra_json key
        # itself if present, attach history, and write.
        out = dict(state)
        out.pop("extra_json", None)
        out["history"] = [
            {
                "turn": h.get("turn"),
                "status": h.get("status"),
                "timestamp": h.get("ts"),
                "phase": h.get("phase"),
                "round": h.get("round"),
                "updated_by": h.get("updated_by"),
            }
            for h in history
        ]
        state_path = project_dir / "handoff-state.json"
        state_path.write_text(
            json.dumps(out, indent=2) + "\n", encoding="utf-8"
        )
        state_written = True

    return {
        "cycles": cycles_written,
        "rounds": rounds_written,
        "state_written": state_written,
    }


# ---------- Renderer (matches tagteam.cycle.render_cycle byte-for-byte) ----------

def render_cycle(
    conn: sqlite3.Connection, phase: str, cycle_type: str
) -> str | None:
    """Synthesize human-readable markdown for a cycle.

    Output is byte-identical to `tagteam.cycle.render_cycle` for cycles
    imported from existing project files. Used as the auto-export when
    the DB becomes the canonical store, so PR-reviewable conversation
    history is preserved.
    """
    cycle = get_cycle(conn, phase, cycle_type)
    if cycle is None:
        return None
    entries = get_rounds(conn, phase, cycle_type)

    step_label = "Plan" if cycle_type == "plan" else "Implementation"
    lines = [
        f"# {step_label} Review Cycle: {phase}",
        "",
        f"- **Phase:** {phase}",
        f"- **Type:** {cycle_type}",
        f"- **Date:** {cycle.get('date') or '?'}",
        f"- **Lead:** {cycle.get('lead') or '?'}",
        f"- **Reviewer:** {cycle.get('reviewer') or '?'}",
        "",
    ]

    rounds: dict[int, list[dict]] = {}
    for e in entries:
        r = e.get("round", 0)
        rounds.setdefault(r, []).append(e)

    for round_num in sorted(rounds.keys()):
        lines.append(f"## Round {round_num}")
        lines.append("")
        for e in rounds[round_num]:
            role_label = {"lead": "Lead", "gatekeeper": "Gatekeeper"}.get(e["role"], "Reviewer")
            lines.append(f"### {role_label}")
            lines.append("")
            lines.append(f"**Action:** {e.get('action') or '?'}")
            lines.append("")
            lines.append(e.get("content") or "")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("<!-- CYCLE_STATUS -->")
    ready_for = (
        "?" if not cycle.get("ready_for_present", True)
        else cycle.get("ready_for")
    )
    lines.append(f"READY_FOR: {ready_for}")
    lines.append(f"ROUND: {cycle.get('round')}")
    lines.append(f"STATE: {cycle.get('state')}")

    return "\n".join(lines)
