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
SCHEMA_VERSION = 2

VALID_ACTIONS = {
    "SUBMIT_FOR_REVIEW", "REQUEST_CHANGES", "APPROVE",
    "ESCALATE", "NEED_HUMAN", "AMEND",
}
VALID_ROLES = {"lead", "reviewer"}
VALID_TYPES = {"plan", "impl"}
TERMINAL_CYCLE_STATES = {"approved", "escalated", "aborted"}
_ACTION_TO_STATUS = {
    "SUBMIT_FOR_REVIEW": ("in-progress", "reviewer"),
    "REQUEST_CHANGES": ("in-progress", "lead"),
    "APPROVE": ("approved", None),
    "ESCALATE": ("escalated", "human"),
    "NEED_HUMAN": ("needs-human", "human"),
    "AMEND": ("in-progress", "reviewer"),
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
    # Future migrations land here.
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
    if db_path is None:
        db_path = _resolve_db_path(project_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


# ---------- Cycle CRUD ----------

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

def add_diagnostic(conn: sqlite3.Connection, kind: str,
                   payload: dict, ts: str) -> int:
    cur = conn.execute(
        "INSERT INTO diagnostics (ts, kind, payload_json) VALUES (?, ?, ?)",
        (ts, kind, json.dumps(payload)),
    )
    return cur.lastrowid


# ---------- Importer (one-way, from existing tagteam project files) ----------

_ROUNDS_RE = re.compile(r"^(.+)_(plan|impl)_rounds\.jsonl$")
_STATUS_RE = re.compile(r"^(.+)_(plan|impl)_status\.json$")


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
            role_label = "Lead" if e["role"] == "lead" else "Reviewer"
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
