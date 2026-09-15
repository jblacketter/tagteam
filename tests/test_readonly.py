"""Phase 50: read-only mode (`TAGTEAM_READ_ONLY`).

A helper process (panel lens, brief drafter, verifier) reads the cycle and
never writes it. Enforced at the chokepoints — `dualwrite.writer_lock`,
`db.connect` (which becomes `db.read_only_connect`), the `db` writer
functions, the runtime-marker writers — and surfaced by the CLI as one
refusal with exit 2. The tree-snapshot matrix proves read commands leave
the project byte-identical in every SQLite sidecar state.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tagteam import cli, db, dualwrite, headless as h, hub_api
from tagteam import cycle as cycle_mod
from tagteam.dualwrite import DatabaseMissing, ReadOnlyError, SchemaBehind, WalWithoutIndex

from tests.test_headless import project, fake_path, _engine, _init_cycle  # noqa: F401
from tests.test_panel import _enable as _enable_panel, _open_impl, _run as _run_panel, _verdicts  # noqa: F401
from tests.test_briefer import _enable as _enable_briefer, _escalate, _run as _run_briefer  # noqa: F401

RO = dualwrite.READ_ONLY_ENV
PHASE = "feat-x"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def ro(monkeypatch):
    monkeypatch.setenv(RO, "1")


def _proj(tmp_path: Path, name: str = "proj") -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "tagteam.yaml").write_text(
        "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n", encoding="utf-8")
    (d / "docs" / "handoffs").mkdir(parents=True)
    (d / "docs" / "phases").mkdir()
    (d / "docs" / "phases" / f"{PHASE}.md").write_text("# plan\n- do x\n", encoding="utf-8")
    (d / "docs" / "roadmap.md").write_text(
        "# Roadmap\n\n## Phases\n\n### Phase 1: Feat X\n- **Status:** Not started\n", encoding="utf-8")
    return d


def _with_cycle(d: Path) -> Path:
    cycle_mod.init_cycle(PHASE, "plan", "Claude", "Codex", "initial", str(d), updated_by="Claude")
    return d


def _dbp(d: Path) -> Path:
    return d / ".tagteam" / "tagteam.db"


def _snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _diff(before: dict, after: dict) -> dict:
    return {"created": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "changed": sorted(k for k in after if k in before and before[k] != after[k])}


def _cli(monkeypatch, capsys, d: Path, *argv: str) -> tuple[int, str, str]:
    from tagteam import state as state_mod
    monkeypatch.chdir(d)
    monkeypatch.setattr(state_mod, "_cached_project_root", None)   # cwd-resolved per call
    monkeypatch.setattr(sys, "argv", ["tagteam", *argv])
    try:
        rc = cli.main()
    except SystemExit as e:  # a subcommand that exits directly
        rc = e.code if isinstance(e.code, int) else 1
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _checkpoint(dbp: Path) -> None:
    c = sqlite3.connect(dbp)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()


def _drop_sidecars(dbp: Path) -> None:
    for s in ("-wal", "-shm"):
        p = dbp.with_name(dbp.name + s)
        if p.exists():
            p.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_schema_db(dbp: Path) -> None:
    """Replace the project DB with a schema-v1 DB (rollback journal, no sidecars)."""
    _checkpoint(dbp)
    _drop_sidecars(dbp)
    dbp.unlink()
    raw = sqlite3.connect(dbp)
    raw.executescript(db._SCHEMA_V1)
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()


READ_COMMANDS = [
    ("cycle", "rounds", "--phase", PHASE, "--type", "plan"),
    ("cycle", "status", "--phase", PHASE, "--type", "plan"),
    ("state",),
    ("gate", "status"),
    ("panel", "status"),
    ("interject", "--list"),
    ("contract",),
    ("roadmap", "queue"),
    ("report", "--phase", PHASE),
    ("roadmap", "ready"),
    ("roadmap", "check"),
    ("usage", "--json"),
    ("brief", "--list", "--phase", PHASE, "--type", "plan"),
    ("state", "diagnose"),
    ("gate", "list"),
    ("panel", "lenses"),
    ("roadmap", "phases"),
    ("roadmap", "graph"),
    ("registry", "list"),
    ("hook", "session-start"),
]
DB_ONLY_READERS = {("usage", "--json"), ("interject", "--list"),
                   ("brief", "--list", "--phase", PHASE, "--type", "plan")}

WRITE_COMMANDS = [
    ("cycle", "add", "--phase", PHASE, "--type", "plan", "--role", "reviewer", "--action", "APPROVE",
     "--round", "1", "--updated-by", "Codex", "--content", "ok"),
    ("cycle", "init", "--phase", "feat-y", "--type", "plan", "--lead", "Claude", "--reviewer", "Codex",
     "--updated-by", "Claude", "--content", "x"),
    ("state", "set", "--turn", "lead", "--status", "ready", "--phase", PHASE, "--type", "plan",
     "--round", "1", "--updated-by", "Codex"),
    ("interject", "a note from the arbiter"),
    ("pause", "--reason", "hold"),
    ("resume",),
    ("cancel-turn",),
    ("gate", "run"),
    ("panel", "run"),
    ("state", "reset"),
    ("state", "diagnose", "--clean"),
    ("state", "sync"),
    ("state", "repair-db"),
    ("state", "repair-db", "--force-clear"),
    ("migrate", "--to-sqlite", "--force"),
    ("migrate", "--to-sqlite", "--dry-run"),
    ("roadmap", "resume"),
    ("interject", "--retire", "1"),
    ("brief", "--generate", "--phase", PHASE, "--type", "plan"),
    ("registry", "unregister", "/nowhere"),
    ("hub", "unregister", "/nowhere"),
    ("session", "start"),
    ("watch",),
    ("setup", "."),
]


# ---------------------------------------------------------------------------
# the switch
# ---------------------------------------------------------------------------

class TestSwitch:
    @pytest.mark.parametrize("value,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("anything", True), (" on ", True),
        ("0", False), ("false", False), ("No", False), ("", False), ("  ", False),
    ])
    def test_parse(self, monkeypatch, value, expected):
        monkeypatch.setenv(RO, value)
        assert dualwrite.read_only() is expected

    def test_unset(self, monkeypatch):
        monkeypatch.delenv(RO, raising=False)
        assert dualwrite.read_only() is False

    def test_message_is_one_refusal_plus_detail(self):
        e = ReadOnlyError("because")
        assert str(e).startswith("tagteam: refused") and "TAGTEAM_READ_ONLY" in str(e)
        assert str(e).endswith("\nbecause") and e.detail == "because"
        assert issubclass(DatabaseMissing, ReadOnlyError) and issubclass(SchemaBehind, ReadOnlyError) \
            and issubclass(WalWithoutIndex, ReadOnlyError)


# ---------------------------------------------------------------------------
# chokepoint 1: the writer lock
# ---------------------------------------------------------------------------

class TestWriterLock:
    def test_refuses_before_touching_disk(self, tmp_path, ro):
        d = _proj(tmp_path)
        with pytest.raises(ReadOnlyError):
            with dualwrite.writer_lock(d):
                pytest.fail("must not enter")
        assert not (d / ".tagteam").exists()

    def test_state_and_cycle_writes_refused(self, tmp_path, ro):
        d = _proj(tmp_path)
        from tagteam import state as state_mod
        with pytest.raises(ReadOnlyError):
            state_mod.update_state({"phase": PHASE}, str(d))
        with pytest.raises(ReadOnlyError):
            cycle_mod.init_cycle(PHASE, "plan", "Claude", "Codex", "x", str(d), updated_by="Claude")
        assert not (d / "handoff-state.json").exists() and not (d / ".tagteam").exists()
        assert list((d / "docs" / "handoffs").iterdir()) == []


# ---------------------------------------------------------------------------
# chokepoint 2: db.connect -> read_only_connect
# ---------------------------------------------------------------------------

def _seed_db(d: Path) -> Path:
    """A current-schema DB with one history row committed and checkpointed."""
    conn = db.connect(project_dir=str(d))
    db.add_history_entry(conn, {"timestamp": _now(), "turn": "lead", "status": "ready",
                                "phase": PHASE, "round": 1, "updated_by": "seed"})
    conn.commit()
    conn.close()
    _checkpoint(_dbp(d))
    return _dbp(d)


def _crash_copy(src: Path, dst_dir: Path, sidecars: tuple[str, ...]) -> Path:
    """Copy `src` DB (+ chosen sidecars) as they are on disk right now."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copy(src, dst)
    for s in sidecars:
        shutil.copy(src.with_name(src.name + s), dst.with_name(dst.name + s))
    return dst


class TestReadOnlyConnect:
    def test_absent_db_creates_nothing(self, tmp_path, ro):
        d = _proj(tmp_path)
        with pytest.raises(DatabaseMissing):
            db.connect(project_dir=str(d))
        with pytest.raises(DatabaseMissing):
            db.read_only_connect(project_dir=str(d))
        assert not (d / ".tagteam").exists()

    def test_neither_sidecar_reads_and_creates_nothing(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        _drop_sidecars(dbp)
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d / ".tagteam")
        conn = db.connect(project_dir=str(d))
        try:
            assert [r["updated_by"] for r in db.get_history(conn)] == ["seed"]
        finally:
            conn.close()
        assert _diff(before, _snapshot(d / ".tagteam")) == {"created": [], "removed": [], "changed": []}

    def test_shm_only_stale_index_is_inert(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        _drop_sidecars(dbp)
        dbp.with_name(dbp.name + "-shm").write_bytes(b"\0" * 32768)   # a stale index, no WAL
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d / ".tagteam")
        conn = db.connect(project_dir=str(d))
        try:
            assert len(db.get_history(conn)) == 1
        finally:
            conn.close()
        assert _diff(before, _snapshot(d / ".tagteam")) == {"created": [], "removed": [], "changed": []}

    def test_wal_and_shm_sees_committed_wal_frames(self, tmp_path, ro, monkeypatch):
        """The WAL-visibility property: a row a still-open writer committed into
        the WAL (autocheckpoint off) is visible; .db and -wal bytes unchanged."""
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        w = sqlite3.connect(dbp)
        w.execute("PRAGMA wal_autocheckpoint=0")
        db.add_history_entry(w, {"timestamp": _now(), "turn": "reviewer", "status": "ready",
                                 "phase": PHASE, "round": 2, "updated_by": "wal-held"})
        w.commit()
        try:
            crash = _crash_copy(dbp, tmp_path / "crash" / ".tagteam", ("-wal", "-shm"))
            assert crash.with_name(crash.name + "-wal").stat().st_size > 0
            monkeypatch.setenv(RO, "1")
            before = _snapshot(crash.parent)
            conn = db.read_only_connect(db_path=crash)
            try:
                assert [r["updated_by"] for r in db.get_history(conn)] == ["seed", "wal-held"]
                with pytest.raises(sqlite3.OperationalError):
                    conn.execute("INSERT INTO history (timestamp) VALUES ('x')")
            finally:
                conn.close()
            diff = _diff(before, _snapshot(crash.parent))
            assert diff["created"] == [] and diff["removed"] == []
            assert set(diff["changed"]) <= {"tagteam.db-shm"}, diff     # the WAL index only
        finally:
            w.close()

    def test_wal_without_shm_fails_closed(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        w = sqlite3.connect(dbp)
        w.execute("PRAGMA wal_autocheckpoint=0")
        db.add_history_entry(w, {"timestamp": _now(), "turn": "reviewer", "status": "ready",
                                 "phase": PHASE, "round": 2, "updated_by": "wal-held"})
        w.commit()
        try:
            crash = _crash_copy(dbp, tmp_path / "crash" / ".tagteam", ("-wal",))
            monkeypatch.setenv(RO, "1")
            before = _snapshot(crash.parent)
            with pytest.raises(WalWithoutIndex):
                db.read_only_connect(db_path=crash)
            with pytest.raises(WalWithoutIndex):
                db.connect(db_path=crash)
            assert not crash.with_name(crash.name + "-shm").exists()
            assert _diff(before, _snapshot(crash.parent)) == {"created": [], "removed": [], "changed": []}
        finally:
            w.close()

    def test_old_schema_refused_never_migrated(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        _old_schema_db(dbp)
        monkeypatch.setenv(RO, "1")
        before = dbp.read_bytes()
        with pytest.raises(SchemaBehind):
            db.connect(project_dir=str(d))
        # opt-out for readers that only touch tables they know (the hub)
        conn = db.read_only_connect(project_dir=str(d), require_current_schema=False)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        conn.close()
        assert dbp.read_bytes() == before
        assert sorted(p.name for p in dbp.parent.iterdir()) == ["tagteam.db"]

    def test_newer_schema_is_readable(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        c = sqlite3.connect(dbp); c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}"); c.commit(); c.close()
        _checkpoint(dbp)
        monkeypatch.setenv(RO, "1")
        conn = db.connect(project_dir=str(d))
        assert len(db.get_history(conn)) == 1
        conn.close()

    def test_query_only_and_decorated_writers(self, tmp_path, ro, monkeypatch):
        monkeypatch.delenv(RO)
        d = _proj(tmp_path)
        _seed_db(d)
        monkeypatch.setenv(RO, "1")
        conn = db.connect(project_dir=str(d))
        try:
            with pytest.raises(ReadOnlyError):
                db.add_history_entry(conn, {"timestamp": _now()})
            with pytest.raises(ReadOnlyError):
                db.add_usage(conn, ts=_now(), status="ok")
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("DELETE FROM history")
            assert len(db.get_history(conn)) == 1
        finally:
            conn.close()

    def test_every_db_writer_is_decorated(self):
        src = Path(db.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        writers = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                   and re.search(r"\b(INSERT|UPDATE|DELETE)\b", ast.get_source_segment(src, n) or "")]
        assert len(writers) >= 20
        undecorated = [n for n in writers if not getattr(getattr(db, n), "__tagteam_writes__", False)]
        assert undecorated == [], undecorated
        # and only writers carry the marker
        marked = [n for n in dir(db) if getattr(getattr(db, n), "__tagteam_writes__", False)]
        assert sorted(marked) == sorted(writers)

    def test_off_switch_connect_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv(RO, "0")
        d = _proj(tmp_path)
        conn = db.connect(project_dir=str(d))          # creates + migrates as always
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        conn.close()
        assert _dbp(d).is_file()


# ---------------------------------------------------------------------------
# runtime markers (pause / cancel / turn slots) — written outside the lock
# ---------------------------------------------------------------------------

class TestRuntimeMarkers:
    def test_markers_refused(self, tmp_path, ro):
        d = _proj(tmp_path)
        for fn in (lambda: h.write_pause(d, {"reason": "x"}), lambda: h.clear_pause(d),
                   lambda: h.write_cancel(d, {"by": "x"}), lambda: h.clear_cancel(d),
                   lambda: h.claim_turn_slot(d, kind="turn", role="lead", fields={})):
            with pytest.raises(ReadOnlyError):
                fn()
        assert not (d / ".tagteam").exists()


# ---------------------------------------------------------------------------
# the CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_write_commands_refused_exit_2_tree_identical(self, tmp_path, monkeypatch, capsys):
        d = _with_cycle(_proj(tmp_path))
        _checkpoint(_dbp(d))
        _drop_sidecars(_dbp(d))
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        for argv in WRITE_COMMANDS:
            rc, out, err = _cli(monkeypatch, capsys, d, *argv)
            assert rc == 2, (argv, rc, out, err)
            assert "tagteam: refused" in err and "TAGTEAM_READ_ONLY" in err, (argv, err)
            assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}, argv

    def test_rule_refused_on_escalated_cycle(self, tmp_path, monkeypatch, capsys):
        d = _with_cycle(_proj(tmp_path))
        cycle_mod.add_round(PHASE, "plan", "reviewer", "ESCALATE", 1, "why", str(d), updated_by="Codex")
        _checkpoint(_dbp(d)); _drop_sidecars(_dbp(d))
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        rc, out, err = _cli(monkeypatch, capsys, d, "rule", "approve", "--content", "fine")
        assert rc == 2 and "tagteam: refused" in err, (rc, out, err)
        assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}

    def test_read_commands_work(self, tmp_path, monkeypatch, capsys):
        d = _with_cycle(_proj(tmp_path))
        _checkpoint(_dbp(d)); _drop_sidecars(_dbp(d))
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        for argv in READ_COMMANDS:
            rc, out, err = _cli(monkeypatch, capsys, d, *argv)
            ok = {0, 1} if argv[0] == "brief" else {0}    # `brief --list` says "No briefs" with 1
            assert rc in ok and "tagteam: refused" not in err, (argv, rc, out, err)
            assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}, argv

    def test_off_switch_write_commands_work(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(RO, "false")
        d = _with_cycle(_proj(tmp_path))
        rc, out, err = _cli(monkeypatch, capsys, d, "cycle", "add", "--phase", PHASE, "--type", "plan",
                            "--role", "reviewer", "--action", "APPROVE", "--round", "1",
                            "--updated-by", "Codex", "--content", "ok")
        assert rc == 0, (out, err)


# ---------------------------------------------------------------------------
# the tree-snapshot matrix: fixtures x read commands
# ---------------------------------------------------------------------------

def _fixture(tmp_path: Path, state: str) -> Path:
    """`fresh` | `neither` | `wal+shm` | `wal-only` | `shm-only` | `old-schema`."""
    if state == "fresh":
        return _proj(tmp_path, "fresh")
    base = _with_cycle(_proj(tmp_path, "base"))
    dbp = _dbp(base)
    if state == "old-schema":
        _old_schema_db(dbp)
        return base
    if state in ("neither", "shm-only"):
        _checkpoint(dbp)
        _drop_sidecars(dbp)
        if state == "shm-only":
            dbp.with_name(dbp.name + "-shm").write_bytes(b"\0" * 32768)
        return base
    # WAL-bearing states: a crash copy taken while a writer holds a committed
    # round in the WAL (so the WAL really has frames).
    w = sqlite3.connect(dbp)
    w.execute("PRAGMA wal_autocheckpoint=0")
    cycle_id = w.execute("SELECT id FROM cycles WHERE phase=? AND type=?", (PHASE, "plan")).fetchone()[0]
    db.add_round(w, cycle_id, 2, "reviewer", "REQUEST_CHANGES", "wal-held round", _now())
    db.add_usage(w, ts=_now(), status="ok", phase=PHASE, type="plan", round=2, role="reviewer",
                 agent="wal-held", provider="fake")
    w.commit()
    target = tmp_path / state
    shutil.copytree(base, target, ignore=shutil.ignore_patterns(".tagteam"))
    _crash_copy(dbp, target / ".tagteam", ("-wal", "-shm") if state == "wal+shm" else ("-wal",))
    w.close()
    return target


class TestSnapshotMatrix:
    @pytest.mark.parametrize("state", ["fresh", "neither", "wal+shm", "wal-only", "shm-only", "old-schema"])
    def test_read_commands_leave_the_tree_alone(self, tmp_path, monkeypatch, capsys, state):
        d = _fixture(tmp_path, state)
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        for argv in READ_COMMANDS:
            rc, out, err = _cli(monkeypatch, capsys, d, *argv)
            diff = _diff(before, _snapshot(d))
            assert diff["created"] == [] and diff["removed"] == [], (state, argv, diff)
            if state == "wal+shm":
                assert set(diff["changed"]) <= {".tagteam/tagteam.db-shm"}, (argv, diff)
            else:
                assert diff["changed"] == [], (state, argv, diff)
            if state == "fresh":
                assert not (d / ".tagteam").exists()
                assert rc != 2 and "tagteam: refused" not in err, (argv, rc, out, err)
                continue
            db_only = tuple(argv) in DB_ONLY_READERS
            if argv[0] == "usage" and state == "wal-only":
                # Phase 55: usage reads through `connect_for_read`; unreadable → its own refusal
                assert rc == 2 and "cannot read the database without changing it" in out, (state, rc, out, err)
                continue
            if state in ("old-schema", "wal-only") and db_only and argv[0] != "usage":
                assert rc == 2 and "tagteam: refused" in err, (state, argv, rc, out, err)
                continue
            if argv[0] == "report" and state == "wal-only":
                assert rc == 0 and "figures from cycle files only" in out, (out, err)
            ok = {0, 1} if argv[0] == "brief" else {0}
            assert rc in ok and "tagteam: refused" not in err, (state, argv, rc, out, err)
            if state == "wal+shm" and argv[0] == "usage":
                # the WAL-held usage row is visible through the DB-backed CLI reader
                assert [t["agent"] for t in json.loads(out.split("\n", 1)[1] if out.startswith("[tagteam]") else out)["turns"]] == ["wal-held"]
            if state == "wal-only" and argv[:2] == ("cycle", "rounds"):
                assert "initial" in out                  # canonical-file fallback still answers
        if state == "old-schema":
            c = sqlite3.connect(_dbp(d)); assert c.execute("PRAGMA user_version").fetchone()[0] == 1; c.close()
        if state == "wal-only":
            assert not (_dbp(d).with_name("tagteam.db-shm")).exists()


# ---------------------------------------------------------------------------
# who sets it: panel lens yes, briefer child yes, headless turn no
# ---------------------------------------------------------------------------

class TestChildEnvironment:
    def test_panel_lens_child_is_read_only(self, project, fake_path, monkeypatch, tmp_path):
        _enable_panel(project)
        _open_impl(project)
        _verdicts(monkeypatch, default="approve")
        cap = tmp_path / "capture.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        monkeypatch.delenv(RO, raising=False)
        res = _run_panel(project)
        assert cap.is_file(), res
        assert json.loads(cap.read_text(encoding="utf-8"))["read_only"] == "1"

    def test_briefer_child_is_read_only(self, project, fake_path, monkeypatch, tmp_path):
        _enable_briefer(project)
        _escalate(project)
        cap = tmp_path / "capture.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        monkeypatch.delenv(RO, raising=False)
        res = _run_briefer(project)
        assert cap.is_file(), res
        assert json.loads(cap.read_text(encoding="utf-8"))["read_only"] == "1"

    def test_headless_turn_child_is_not_read_only(self, project, fake_path, monkeypatch, tmp_path):
        state = _init_cycle(project)
        cap = tmp_path / "capture.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        monkeypatch.delenv(RO, raising=False)
        eng = _engine(project)
        res = eng.run_owed_turn(state)
        assert cap.is_file(), res
        assert json.loads(cap.read_text(encoding="utf-8"))["read_only"] is None
        rounds = cycle_mod.read_rounds_file(PHASE, "plan", str(project))
        assert any(r["role"] == "reviewer" for r in rounds), rounds   # the turn could write


# ---------------------------------------------------------------------------
# hub reader consolidation
# ---------------------------------------------------------------------------

class TestHubReader:
    def test_sidecar_less_current_db_opens(self, tmp_path):
        d = _proj(tmp_path)
        dbp = _seed_db(d)
        _drop_sidecars(dbp)
        conn = hub_api.read_only_connect(d)
        assert conn is not None
        assert len(db.get_history(conn)) == 1
        conn.close()
        assert sorted(p.name for p in dbp.parent.iterdir()) == ["tagteam.db"]

    def test_absent_is_none_corrupt_is_error(self, tmp_path):
        d = _proj(tmp_path)
        assert hub_api.read_only_connect(d) is None
        (d / ".tagteam").mkdir()
        _dbp(d).write_bytes(b"not a database at all" * 10)
        with pytest.raises(hub_api.ProjectDataError):
            hub_api.read_only_connect(d)


# ---------------------------------------------------------------------------
# impl r2: the three bypasses + the CLI allowlist
# ---------------------------------------------------------------------------

def _armed(tmp_path: Path) -> Path:
    """A populated project with every runtime artefact a write path could
    touch: pause marker, db_invalid sentinel, stale inflight metadata."""
    d = _with_cycle(_proj(tmp_path))
    h.write_pause(d, {"reason": "held", "by": "arbiter"})
    dualwrite.mark_db_invalid(d, reason="test sentinel")
    ip = h.inflight_path(d)
    ip.parent.mkdir(parents=True, exist_ok=True)
    ip.write_text(json.dumps({"stem": "stale", "pid": None}), encoding="utf-8")
    _checkpoint(_dbp(d)); _drop_sidecars(_dbp(d))
    return d


class TestBypassesClosed:
    def test_every_write_command_refused_on_an_armed_project(self, tmp_path, monkeypatch, capsys):
        d = _armed(tmp_path)
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        for argv in WRITE_COMMANDS:
            rc, out, err = _cli(monkeypatch, capsys, d, *argv)
            assert rc == 2 and "tagteam: refused" in err, (argv, rc, out, err)
            assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}, argv

    def test_migrate_force_cannot_delete_the_db(self, tmp_path, monkeypatch, capsys):
        """Bypass 1 — the allowlist stops the CLI; the destructive helper is
        guarded on its own (called directly, below the allowlist)."""
        from tagteam import migrate
        d = _armed(tmp_path)
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        monkeypatch.chdir(d)
        with pytest.raises(ReadOnlyError):
            migrate.migrate_to_sqlite_command(["--to-sqlite", "--force"])
        with pytest.raises(ReadOnlyError):
            migrate._remove_sqlite_db_files(_dbp(d))
        assert _dbp(d).is_file()
        assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}

    def test_db_invalid_sentinel_untouchable(self, tmp_path, monkeypatch):
        """Bypass 2 — `state repair-db --force-clear` reached clear_db_invalid()
        outside the lock; the sentinel mutators now refuse themselves."""
        d = _armed(tmp_path)
        flag = d / ".tagteam" / "DB_INVALID"
        before = flag.read_bytes()
        monkeypatch.setenv(RO, "1")
        with pytest.raises(ReadOnlyError):
            dualwrite.clear_db_invalid(d)
        with pytest.raises(ReadOnlyError):
            dualwrite.mark_db_invalid(d, reason="again")
        assert flag.read_bytes() == before and dualwrite.is_db_invalid(d)

    def test_stale_inflight_cleanup_refused(self, tmp_path, monkeypatch, capsys):
        """Bypass 3 — cancel-turn's stale-metadata unlink bypassed write_cancel."""
        from tagteam import controls
        d = _armed(tmp_path)
        ip = h.inflight_path(d)
        before = ip.read_bytes()
        monkeypatch.setenv(RO, "1")
        with pytest.raises(ReadOnlyError):
            controls.cancel_turn_command([], project_root=d)
        assert ip.read_bytes() == before
        capsys.readouterr()

    def test_off_switch_bypass_paths_still_work(self, tmp_path, monkeypatch, capsys):
        from tagteam import controls
        monkeypatch.setenv(RO, "0")
        d = _armed(tmp_path)
        assert controls.cancel_turn_command([], project_root=d) == 1     # stale → cleaned
        assert not h.inflight_path(d).exists()
        dualwrite.clear_db_invalid(d)
        assert not dualwrite.is_db_invalid(d)
        capsys.readouterr()


class TestAllowlist:
    @pytest.mark.parametrize("argv", [
        ["cycle", "rounds", "--phase", "p", "--type", "plan"], ["cycle", "status"], ["state"],
        ["state", "diagnose"], ["gate", "status"], ["gate", "list"], ["panel", "status"], ["panel", "lenses"],
        ["roadmap", "queue"], ["roadmap", "ready"], ["roadmap", "check"], ["roadmap", "graph"],
        ["interject", "--list"], ["brief", "--list"], ["usage"], ["contract"], ["hook", "session-start"],
        ["registry", "list"], ["hub", "list"], ["tail"], ["help"], ["--help"], ["-h"],
        ["cycle", "rounds", "--help"],
    ])
    def test_reads_allowed(self, argv):
        assert cli.read_only_refusal(argv) is None, argv

    @pytest.mark.parametrize("argv", [
        ["cycle", "add"], ["cycle", "init"], ["cycle", "render"], ["state", "set"], ["state", "reset"],
        ["state", "diagnose", "--clean"], ["state", "sync"], ["state", "repair-db"], ["gate", "run"],
        ["gate", "check"], ["panel", "run"], ["roadmap", "resume"], ["roadmap", "worktree", "x"],
        ["interject", "a note"], ["interject", "--retire", "1"], ["brief", "--generate"], ["migrate"],
        ["migrate", "--to-sqlite", "--dry-run"], ["pause"], ["resume"], ["cancel-turn"], ["rule", "approve"],
        ["hub", "unregister", "x"], ["registry", "unregister", "x"], ["session", "start"], ["serve"],
        ["watch"], ["setup", "."], ["init"], ["quickstart"], ["upgrade"], ["lead"], ["tui"], ["rollback", "1.0"],
        ["no-such-command"],
        # impl r3: no command-level help exception — fail closed
        ["setup", "--help"], ["setup", ".", "--help"], ["setup", "-h"], ["cycle", "add", "--help"],
        ["watch", "--help"], ["session", "start", "-h"], ["migrate", "help"],
    ])
    def test_writes_refused(self, argv):
        detail = cli.read_only_refusal(argv)
        assert detail is not None and "is not a read command" in detail, argv

    @pytest.mark.parametrize("command", cli.READ_ONLY_REFUSED)
    @pytest.mark.parametrize("args", [(), ("--help",), ("-h",), (".", "--help"), ("--help", "."), ("help",)])
    def test_every_refused_command_fails_closed_with_help_variants(self, tmp_path, monkeypatch, capsys,
                                                                    command, args):
        """Through the real CLI: refused BEFORE dispatch, so nothing blocks,
        prompts, installs or writes — `setup --help` would otherwise treat
        `--help` as its target directory."""
        d = _with_cycle(_proj(tmp_path))
        _checkpoint(_dbp(d)); _drop_sidecars(_dbp(d))
        monkeypatch.setenv(RO, "1")
        before = _snapshot(d)
        rc, out, err = _cli(monkeypatch, capsys, d, command, *args)
        assert rc == 2 and "tagteam: refused" in err and "is not a read command" in err, (command, args, out, err)
        assert _diff(before, _snapshot(d)) == {"created": [], "removed": [], "changed": []}, (command, args)
        assert not (d / "--help").exists() and not (d / "-h").exists()

    def test_every_dispatched_command_is_classified(self):
        src = Path(cli.__file__).read_text(encoding="utf-8")
        dispatched = set(re.findall(r'command == "([\w-]+)"', src[src.index("def _dispatch("):]))
        assert len(dispatched) >= 20
        unclassified = dispatched - set(cli.READ_ONLY_COMMANDS) - set(cli.READ_ONLY_REFUSED)
        assert unclassified == set(), unclassified
        assert set(cli.READ_ONLY_COMMANDS) & set(cli.READ_ONLY_REFUSED) == set()
