"""Tests for the SQLite storage layer (tagteam.db).

Coverage is biased toward what the rankr spike corpus did NOT exercise:
non-approved states, AMEND/ESCALATE/NEED_HUMAN actions, render-parity
edge cases, the importer's pass-1/pass-2 ordering, and schema
constraints.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from tagteam import db


@pytest.fixture
def conn(tmp_path):
    """Open a fresh DB under tmp_path. Closes automatically on teardown."""
    c = db.connect(project_dir=str(tmp_path))
    yield c
    c.close()


@pytest.fixture
def project_dir(tmp_path):
    """Project dir with a docs/handoffs/ that the importer can read."""
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    return tmp_path


# ---------- Schema & connection ----------

class TestSchema:
    def test_default_path(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        c.close()
        assert (tmp_path / ".tagteam" / "tagteam.db").exists()

    def test_user_version_is_current(self, conn):
        v = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v == db.SCHEMA_VERSION

    def test_idempotent_connect(self, tmp_path):
        c1 = db.connect(project_dir=str(tmp_path))
        c1.execute(
            "INSERT INTO cycles (phase, type, state) VALUES ('a','plan','in-progress')"
        )
        c1.commit()
        c1.close()
        c2 = db.connect(project_dir=str(tmp_path))
        # Data preserved across reconnect; schema migration didn't drop it.
        n = c2.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
        assert n == 1
        c2.close()

    def test_singleton_state_constraint(self, conn):
        db.set_state(conn, phase="a", round=1)
        # Direct insert with id=2 must fail (CHECK id=1).
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO state (id, phase) VALUES (2, 'b')")

    def test_cycle_type_constraint(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO cycles (phase, type, state) "
                "VALUES ('p','bogus','in-progress')"
            )

    def test_role_constraint(self, conn):
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rounds (cycle_id, round, role, action, content, ts) "
                "VALUES (?, 1, 'human', 'APPROVE', '', '2026-05-01T00:00:00+00:00')",
                (cid,),
            )


# ---------- Action / role validation ----------

class TestValidation:
    def test_invalid_action_rejected(self, conn):
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress")
        with pytest.raises(ValueError, match="Invalid action"):
            db.add_round(conn, cid, 1, "lead", "BOGUS", "x",
                         "2026-05-01T00:00:00+00:00")

    def test_invalid_role_rejected(self, conn):
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress")
        with pytest.raises(ValueError, match="Invalid role"):
            db.add_round(conn, cid, 1, "human", "APPROVE", "x",
                         "2026-05-01T00:00:00+00:00")

    def test_invalid_cycle_type_rejected(self, conn):
        with pytest.raises(ValueError, match="Invalid cycle type"):
            db.upsert_cycle(conn, "p", "design", state="in-progress")

    def test_amend_action_accepted(self, conn):
        """rankr corpus had no AMEND rounds — verify the action is valid here."""
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress",
                              ready_for="reviewer", round_=1)
        db.add_round(conn, cid, 1, "lead", "SUBMIT_FOR_REVIEW", "v1",
                     "2026-05-01T00:00:00+00:00")
        db.add_round(conn, cid, 1, "lead", "AMEND", "addendum",
                     "2026-05-01T00:01:00+00:00")
        rounds = db.get_rounds(conn, "p", "plan")
        assert [r["action"] for r in rounds] == ["SUBMIT_FOR_REVIEW", "AMEND"]
        # AMEND keeps the same round number — both rounds carry round=1.
        assert all(r["round"] == 1 for r in rounds)

    def test_escalate_and_need_human_actions(self, conn):
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress",
                              ready_for="reviewer", round_=1)
        db.add_round(conn, cid, 1, "lead", "SUBMIT_FOR_REVIEW", "x",
                     "2026-05-01T00:00:00+00:00")
        db.add_round(conn, cid, 1, "reviewer", "NEED_HUMAN", "Open question",
                     "2026-05-01T00:01:00+00:00")
        db.add_round(conn, cid, 2, "reviewer", "ESCALATE", "No progress",
                     "2026-05-01T00:02:00+00:00")
        actions = [r["action"] for r in db.get_rounds(conn, "p", "plan")]
        assert actions == ["SUBMIT_FOR_REVIEW", "NEED_HUMAN", "ESCALATE"]


# ---------- Cycle states ----------

class TestCycleStates:
    """rankr corpus had only 'approved'. Cover the rest here."""

    @pytest.mark.parametrize("state,ready_for", [
        ("in-progress", "reviewer"),
        ("in-progress", "lead"),
        ("escalated", "human"),
        ("needs-human", "human"),
        ("aborted", None),
        ("approved", None),
    ])
    def test_state_round_trips(self, conn, state, ready_for):
        db.upsert_cycle(conn, "p", "plan",
                        state=state, ready_for=ready_for, round_=1,
                        lead="L", reviewer="R", date="2026-05-01")
        c = db.get_cycle(conn, "p", "plan")
        assert c["state"] == state
        assert c["ready_for"] == ready_for

    def test_render_for_each_state(self, conn):
        """Renderer must produce a valid status footer for every state."""
        for state, ready_for in [
            ("in-progress", "reviewer"),
            ("escalated", "human"),
            ("needs-human", "human"),
            ("aborted", None),
        ]:
            phase = f"phase-{state}"
            cid = db.upsert_cycle(conn, phase, "plan",
                                  state=state, ready_for=ready_for, round_=1,
                                  lead="L", reviewer="R", date="2026-05-01")
            db.add_round(conn, cid, 1, "lead", "SUBMIT_FOR_REVIEW", "x",
                         "2026-05-01T00:00:00+00:00")
            md = db.render_cycle(conn, phase, "plan")
            assert f"STATE: {state}" in md
            assert f"READY_FOR: {ready_for or 'None'}" in md


# ---------- Render parity with tagteam.cycle.render_cycle ----------

class TestRenderParity:
    """The promise: DB-rendered markdown is byte-identical to the
    file-based renderer for every input shape, so auto-export is a
    non-regression. This tests the full set of states/actions, going
    beyond what rankr's corpus exercised."""

    def _build_files_cycle(self, project, phase, cycle_type, status, rounds):
        """Write a status.json + rounds.jsonl directly, the way init_cycle/
        add_round would. This avoids `tagteam.cycle` enforcing transitions
        we want to test rendering of independently."""
        handoffs = Path(project) / "docs" / "handoffs"
        handoffs.mkdir(parents=True, exist_ok=True)
        (handoffs / f"{phase}_{cycle_type}_status.json").write_text(
            json.dumps(status, indent=2) + "\n"
        )
        with (handoffs / f"{phase}_{cycle_type}_rounds.jsonl").open("w") as f:
            for r in rounds:
                f.write(json.dumps(r) + "\n")

    def _import_then_render(self, project, phase, cycle_type):
        c = db.connect(db_path=Path(project) / "tmp.db")
        db.import_from_files(Path(project), c)
        out = db.render_cycle(c, phase, cycle_type)
        c.close()
        return out

    def _files_render(self, project, phase, cycle_type):
        # Use the production renderer.
        from tagteam import cycle as cycle_mod
        return cycle_mod.render_cycle(phase, cycle_type, project)

    def test_parity_approved_cycle(self, tmp_path):
        project = str(tmp_path)
        status = {
            "state": "approved", "ready_for": None, "round": 2,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }
        rounds = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "first draft", "ts": "2026-05-01T00:00:00+00:00"},
            {"round": 1, "role": "reviewer", "action": "REQUEST_CHANGES",
             "content": "needs work", "ts": "2026-05-01T00:01:00+00:00"},
            {"round": 2, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "revised", "ts": "2026-05-01T00:02:00+00:00"},
            {"round": 2, "role": "reviewer", "action": "APPROVE",
             "content": "Approved.", "ts": "2026-05-01T00:03:00+00:00"},
        ]
        self._build_files_cycle(project, "p", "plan", status, rounds)
        db_md = self._import_then_render(project, "p", "plan").rstrip("\n")
        files_md = self._files_render(project, "p", "plan").rstrip("\n")
        assert db_md == files_md

    def test_parity_escalated_cycle(self, tmp_path):
        project = str(tmp_path)
        status = {
            "state": "escalated", "ready_for": "human", "round": 3,
            "phase": "p", "type": "impl",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }
        rounds = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:00:00+00:00"},
            {"round": 1, "role": "reviewer", "action": "REQUEST_CHANGES",
             "content": "no", "ts": "2026-05-01T00:01:00+00:00"},
            {"round": 2, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "x", "ts": "2026-05-01T00:02:00+00:00"},
            {"round": 2, "role": "reviewer", "action": "REQUEST_CHANGES",
             "content": "no", "ts": "2026-05-01T00:03:00+00:00"},
            {"round": 3, "role": "reviewer", "action": "ESCALATE",
             "content": "Stuck.", "ts": "2026-05-01T00:04:00+00:00"},
        ]
        self._build_files_cycle(project, "p", "impl", status, rounds)
        c = db.connect(db_path=Path(project) / "tmp.db")
        db.import_from_files(Path(project), c)
        db_md = db.render_cycle(c, "p", "impl").rstrip("\n")
        cycle = db.get_cycle(c, "p", "impl")
        c.close()
        files_md = self._files_render(project, "p", "impl").rstrip("\n")
        assert db_md == files_md
        assert cycle["closed_at"] == "2026-05-01T00:04:00+00:00"

    def test_parity_missing_ready_for_key(self, tmp_path):
        project = str(tmp_path)
        status = {
            "state": "in-progress", "round": 1,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }
        rounds = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "draft", "ts": "2026-05-01T00:00:00+00:00"},
        ]
        self._build_files_cycle(project, "p", "plan", status, rounds)
        db_md = self._import_then_render(project, "p", "plan").rstrip("\n")
        files_md = self._files_render(project, "p", "plan").rstrip("\n")
        assert db_md == files_md
        assert "READY_FOR: ?" in db_md

    def test_parity_amend_mid_round(self, tmp_path):
        """Multiple entries on the same round number must render in
        insertion order, not collapse into one."""
        project = str(tmp_path)
        status = {
            "state": "in-progress", "ready_for": "reviewer", "round": 1,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }
        rounds = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "v1", "ts": "2026-05-01T00:00:00+00:00"},
            {"round": 1, "role": "lead", "action": "AMEND",
             "content": "addendum", "ts": "2026-05-01T00:01:00+00:00"},
        ]
        self._build_files_cycle(project, "p", "plan", status, rounds)
        db_md = self._import_then_render(project, "p", "plan").rstrip("\n")
        files_md = self._files_render(project, "p", "plan").rstrip("\n")
        assert db_md == files_md
        # Sanity: both AMEND and SUBMIT_FOR_REVIEW appear in the output.
        assert "**Action:** SUBMIT_FOR_REVIEW" in db_md
        assert "**Action:** AMEND" in db_md
        assert "addendum" in db_md

    def test_parity_needs_human_with_null_ready_for(self, tmp_path):
        project = str(tmp_path)
        status = {
            "state": "needs-human", "ready_for": "human", "round": 1,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }
        rounds = [
            {"round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
             "content": "q", "ts": "2026-05-01T00:00:00+00:00"},
            {"round": 1, "role": "reviewer", "action": "NEED_HUMAN",
             "content": "Need decision on X", "ts": "2026-05-01T00:01:00+00:00"},
        ]
        self._build_files_cycle(project, "p", "plan", status, rounds)
        db_md = self._import_then_render(project, "p", "plan").rstrip("\n")
        files_md = self._files_render(project, "p", "plan").rstrip("\n")
        assert db_md == files_md


# ---------- Importer ----------

class TestImporter:
    def test_pass_one_status_preserves_state(self, project_dir):
        """The bug found during the spike — pass 2 used to clobber pass 1's
        state with the upsert default. Regression guard."""
        handoffs = project_dir / "docs" / "handoffs"
        (handoffs / "p_plan_status.json").write_text(json.dumps({
            "state": "approved", "ready_for": None, "round": 1,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }))
        (handoffs / "p_plan_rounds.jsonl").write_text(json.dumps({
            "round": 1, "role": "lead", "action": "APPROVE",
            "content": "x", "ts": "2026-05-01T00:00:00+00:00",
        }) + "\n")
        c = db.connect(db_path=project_dir / "tmp.db")
        report = db.import_from_files(project_dir, c)
        cycle = db.get_cycle(c, "p", "plan")
        assert cycle["state"] == "approved"
        assert report["cycles"] == 1
        assert report["rounds"] == 1
        c.close()

    def test_rounds_without_status_creates_cycle(self, project_dir):
        """A cycle with rounds but no status (mid-flight) should still
        import; pass 2 creates the row."""
        handoffs = project_dir / "docs" / "handoffs"
        (handoffs / "p_plan_rounds.jsonl").write_text(json.dumps({
            "round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
            "content": "x", "ts": "2026-05-01T00:00:00+00:00",
        }) + "\n")
        c = db.connect(db_path=project_dir / "tmp.db")
        db.import_from_files(project_dir, c)
        cycle = db.get_cycle(c, "p", "plan")
        assert cycle is not None
        # Default state for an unstatus'd cycle is the upsert default.
        assert cycle["state"] == "in-progress"
        c.close()

    def test_derives_created_at_from_first_round(self, project_dir):
        handoffs = project_dir / "docs" / "handoffs"
        (handoffs / "p_plan_rounds.jsonl").write_text(
            json.dumps({"round": 1, "role": "lead",
                        "action": "SUBMIT_FOR_REVIEW", "content": "x",
                        "ts": "2026-05-01T10:00:00+00:00"}) + "\n" +
            json.dumps({"round": 1, "role": "reviewer",
                        "action": "APPROVE", "content": "ok",
                        "ts": "2026-05-01T11:00:00+00:00"}) + "\n"
        )
        c = db.connect(db_path=project_dir / "tmp.db")
        db.import_from_files(project_dir, c)
        cycle = db.get_cycle(c, "p", "plan")
        assert cycle["created_at"] == "2026-05-01T10:00:00+00:00"
        # closed_at gets set when last round was APPROVE
        assert cycle["closed_at"] == "2026-05-01T11:00:00+00:00"
        c.close()

    def test_terminal_escalated_cycle_gets_closed_at(self, project_dir):
        handoffs = project_dir / "docs" / "handoffs"
        (handoffs / "p_plan_status.json").write_text(json.dumps({
            "state": "escalated", "ready_for": "human", "round": 2,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }))
        (handoffs / "p_plan_rounds.jsonl").write_text(
            json.dumps({"round": 1, "role": "lead",
                        "action": "SUBMIT_FOR_REVIEW", "content": "x",
                        "ts": "2026-05-01T10:00:00+00:00"}) + "\n" +
            json.dumps({"round": 2, "role": "reviewer",
                        "action": "ESCALATE", "content": "stuck",
                        "ts": "2026-05-01T11:00:00+00:00"}) + "\n"
        )
        c = db.connect(db_path=project_dir / "tmp.db")
        db.import_from_files(project_dir, c)
        cycle = db.get_cycle(c, "p", "plan")
        assert cycle["closed_at"] == "2026-05-01T11:00:00+00:00"
        c.close()

    def test_state_and_history_imported(self, project_dir):
        (project_dir / "handoff-state.json").write_text(json.dumps({
            "phase": "p", "type": "plan", "round": 1, "status": "ready",
            "command": "go", "result": None, "updated_by": "Claude",
            "run_mode": "single-phase", "seq": 7,
            "updated_at": "2026-05-01T12:00:00+00:00",
            "history": [
                {"timestamp": "2026-05-01T11:00:00+00:00",
                 "turn": "lead", "status": "ready",
                 "phase": "p", "round": 1, "updated_by": "Claude"},
                {"timestamp": "2026-05-01T11:30:00+00:00",
                 "turn": "reviewer", "status": "ready",
                 "phase": "p", "round": 1, "updated_by": "Codex"},
            ],
        }))
        c = db.connect(db_path=project_dir / "tmp.db")
        report = db.import_from_files(project_dir, c)
        s = db.get_state(c)
        assert s["phase"] == "p"
        assert s["seq"] == 7
        assert s["updated_by"] == "Claude"
        assert report["history_entries"] == 2
        hist = db.get_history(c)
        assert len(hist) == 2
        assert hist[0]["turn"] == "lead"
        c.close()

    def test_baseline_imported_as_dict(self, project_dir):
        (project_dir / "docs" / "handoffs" / "p_impl_status.json").write_text(json.dumps({
            "state": "approved", "ready_for": None, "round": 1,
            "phase": "p", "type": "impl", "lead": "L", "reviewer": "R",
            "date": "2026-05-01",
            "baseline": {"sha": "abc123", "dirty_paths": [" M foo.py"],
                         "captured_at": "2026-05-01T00:00:00+00:00",
                         "source": "init"},
        }))
        c = db.connect(db_path=project_dir / "tmp.db")
        db.import_from_files(project_dir, c)
        cycle = db.get_cycle(c, "p", "impl")
        assert cycle["baseline"]["sha"] == "abc123"
        c.close()

    def test_missing_handoffs_dir_raises(self, tmp_path):
        c = db.connect(db_path=tmp_path / "tmp.db")
        with pytest.raises(FileNotFoundError):
            db.import_from_files(tmp_path, c)
        c.close()


# ---------- Tail-only reads (Phase 20 absorbed by Phase 28) ----------

class TestTailReads:
    def test_get_rounds_since_returns_only_new(self, conn):
        cid = db.upsert_cycle(conn, "p", "plan", state="in-progress")
        ids = []
        for r in range(1, 6):
            rid = db.add_round(conn, cid, r, "lead", "SUBMIT_FOR_REVIEW",
                               f"r{r}", f"2026-05-01T0{r}:00:00+00:00")
            ids.append(rid)

        # First read: everything since 0
        first = db.get_rounds_since(conn, "p", "plan", after_id=0)
        assert len(first) == 5
        last_seen = first[-1]["id"]

        # Add two more rounds
        for r in range(6, 8):
            db.add_round(conn, cid, r, "lead", "SUBMIT_FOR_REVIEW",
                         f"r{r}", f"2026-05-01T{r:02d}:00:00+00:00")

        # Second read: only the two new ones
        new = db.get_rounds_since(conn, "p", "plan", after_id=last_seen)
        assert len(new) == 2
        assert [r["content"] for r in new] == ["r6", "r7"]


# ---------- Diagnostics ----------

class TestDiagnostics:
    def test_add_and_query(self, conn):
        db.add_diagnostic(conn, "seq_mismatch",
                          {"expected": 5, "actual": 4},
                          "2026-05-01T00:00:00+00:00")
        cur = conn.execute(
            "SELECT kind, payload_json FROM diagnostics ORDER BY id"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "seq_mismatch"
        assert json.loads(rows[0][1]) == {"expected": 5, "actual": 4}


# ---------- State extra fields ----------

class TestStateExtra:
    def test_extra_json_round_trips(self, conn):
        db.set_state(
            conn, phase="p", round=1,
            extra_json=json.dumps({"roadmap_queue": "a,b,c", "roadmap_index": 1}),
        )
        s = db.get_state(conn)
        assert s["roadmap_queue"] == "a,b,c"
        assert s["roadmap_index"] == 1

    def test_get_state_returns_none_when_empty(self, conn):
        assert db.get_state(conn) is None


# ---------- Exporter (Phase 28 Step B) ----------

class TestExportToFiles:
    """`export_to_files` is the inverse of `import_from_files`.
    Round-trip fidelity is the load-bearing contract: re-importing
    the just-exported files must produce an equivalent DB."""

    def test_writes_cycle_files(self, project_dir):
        # Seed: import a clean fixture
        (project_dir / "docs" / "handoffs" / "p_plan_status.json").write_text(json.dumps({
            "state": "approved", "ready_for": None, "round": 1,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }))
        (project_dir / "docs" / "handoffs" / "p_plan_rounds.jsonl").write_text(json.dumps({
            "round": 1, "role": "lead", "action": "SUBMIT_FOR_REVIEW",
            "content": "v1", "ts": "2026-05-01T00:00:00+00:00",
        }) + "\n")
        c = db.connect(db_path=project_dir / "src.db")
        db.import_from_files(project_dir, c)

        # Export to a fresh dir
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        report = db.export_to_files(c, out_dir)
        c.close()

        assert report["cycles"] == 1
        assert report["rounds"] == 1
        assert (out_dir / "docs" / "handoffs" / "p_plan_status.json").exists()
        assert (out_dir / "docs" / "handoffs" / "p_plan_rounds.jsonl").exists()

    def test_round_trip_idempotent(self, project_dir):
        """Import → export → import — the two DBs must be
        equivalent (same cycles, rounds, state, history)."""
        # Seed source
        (project_dir / "docs" / "handoffs" / "p_plan_status.json").write_text(json.dumps({
            "state": "approved", "ready_for": None, "round": 2,
            "phase": "p", "type": "plan",
            "lead": "L", "reviewer": "R", "date": "2026-05-01",
        }))
        (project_dir / "docs" / "handoffs" / "p_plan_rounds.jsonl").write_text(
            json.dumps({"round": 1, "role": "lead",
                        "action": "SUBMIT_FOR_REVIEW", "content": "v1",
                        "ts": "2026-05-01T00:00:00+00:00"}) + "\n" +
            json.dumps({"round": 1, "role": "reviewer",
                        "action": "REQUEST_CHANGES", "content": "fix",
                        "ts": "2026-05-01T00:01:00+00:00"}) + "\n" +
            json.dumps({"round": 2, "role": "lead",
                        "action": "SUBMIT_FOR_REVIEW", "content": "v2",
                        "ts": "2026-05-01T00:02:00+00:00"}) + "\n" +
            json.dumps({"round": 2, "role": "reviewer",
                        "action": "APPROVE", "content": "ok",
                        "ts": "2026-05-01T00:03:00+00:00"}) + "\n"
        )
        (project_dir / "handoff-state.json").write_text(json.dumps({
            "phase": "p", "type": "plan", "round": 2, "status": "done",
            "result": "approved", "updated_by": "R", "seq": 5,
            "history": [
                {"timestamp": "2026-05-01T00:00:00+00:00", "turn": "reviewer",
                 "phase": "p", "round": 1, "updated_by": "L"},
            ],
        }))
        c1 = db.connect(db_path=project_dir / "db1.db")
        db.import_from_files(project_dir, c1)

        # Export to a fresh dir.
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c1, out_dir)
        c1.close()

        # Re-import the exported files into a different DB.
        c2 = db.connect(db_path=project_dir / "db2.db")
        db.import_from_files(out_dir, c2)

        # Cycle equivalence
        cyc1 = db.get_cycle(db.connect(db_path=project_dir / "db1.db"), "p", "plan")
        cyc2 = db.get_cycle(c2, "p", "plan")
        for k in ["state", "ready_for", "round", "lead", "reviewer", "date"]:
            assert cyc1[k] == cyc2[k], f"cycle field {k} differs"

        # Round equivalence
        r1 = db.get_rounds(db.connect(db_path=project_dir / "db1.db"), "p", "plan")
        r2 = db.get_rounds(c2, "p", "plan")
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a["round"] == b["round"]
            assert a["role"] == b["role"]
            assert a["action"] == b["action"]
            assert a["content"] == b["content"]
            assert a["ts"] == b["ts"]

        # State equivalence
        s2 = db.get_state(c2)
        assert s2["phase"] == "p"
        assert s2["seq"] == 5

        # History equivalence
        h2 = db.get_history(c2)
        assert len(h2) == 1
        assert h2[0]["phase"] == "p"
        c2.close()

    def test_preserves_ready_for_missing_vs_null(self, project_dir):
        """The `ready_for_present` schema flag must round-trip
        correctly: a cycle with NO ready_for key should export
        without the key, while a cycle with explicit null should
        export with `"ready_for": null`."""
        c = db.connect(db_path=project_dir / "src.db")
        # Cycle 1: ready_for missing
        db.upsert_cycle(c, "missing", "plan",
                        ready_for=None, ready_for_present=False,
                        state="approved", round_=1, lead="L",
                        reviewer="R", date="2026-05-01")
        # Cycle 2: ready_for explicit null
        db.upsert_cycle(c, "null", "plan",
                        ready_for=None, ready_for_present=True,
                        state="approved", round_=1, lead="L",
                        reviewer="R", date="2026-05-01")

        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c, out_dir)
        c.close()

        missing_status = json.loads(
            (out_dir / "docs" / "handoffs" / "missing_plan_status.json").read_text()
        )
        null_status = json.loads(
            (out_dir / "docs" / "handoffs" / "null_plan_status.json").read_text()
        )
        assert "ready_for" not in missing_status, (
            "ready_for_present=False must NOT include the key"
        )
        assert "ready_for" in null_status
        assert null_status["ready_for"] is None

    def test_omits_optional_round_fields_when_null(self, project_dir):
        """`updated_by` and `summary` are optional. Old-format files
        don't have them. Exported rounds must omit them when null,
        not write `null` literals (which older code would have to
        skip)."""
        c = db.connect(db_path=project_dir / "src.db")
        cid = db.upsert_cycle(c, "p", "plan", state="approved",
                              round_=1, lead="L", reviewer="R",
                              date="2026-05-01")
        db.add_round(c, cid, 1, "lead", "SUBMIT_FOR_REVIEW", "x",
                     "2026-05-01T00:00:00+00:00",
                     updated_by=None, summary=None)
        # And one round with both fields populated for contrast
        db.add_round(c, cid, 1, "reviewer", "APPROVE", "ok",
                     "2026-05-01T00:01:00+00:00",
                     updated_by="R", summary="lgtm")

        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c, out_dir)
        c.close()

        lines = (out_dir / "docs" / "handoffs" / "p_plan_rounds.jsonl"
                 ).read_text().strip().splitlines()
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert "updated_by" not in first
        assert "summary" not in first
        assert second["updated_by"] == "R"
        assert second["summary"] == "lgtm"

    def test_baseline_round_trips(self, project_dir):
        c = db.connect(db_path=project_dir / "src.db")
        db.upsert_cycle(
            c, "p", "impl", state="approved", round_=1,
            lead="L", reviewer="R", date="2026-05-01",
            baseline={"sha": "abc123",
                      "dirty_paths": [" M foo.py"],
                      "captured_at": "2026-05-01T00:00:00+00:00",
                      "source": "init"},
        )
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c, out_dir)
        c.close()

        status = json.loads(
            (out_dir / "docs" / "handoffs" / "p_impl_status.json").read_text()
        )
        assert status["baseline"]["sha"] == "abc123"

    def test_no_baseline_key_when_null(self, project_dir):
        c = db.connect(db_path=project_dir / "src.db")
        db.upsert_cycle(c, "p", "plan", state="approved", round_=1,
                        lead="L", reviewer="R", date="2026-05-01")
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c, out_dir)
        c.close()

        status = json.loads(
            (out_dir / "docs" / "handoffs" / "p_plan_status.json").read_text()
        )
        assert "baseline" not in status

    def test_state_with_extra_fields_round_trips(self, project_dir):
        """State `extra_json` fields (e.g. roadmap_queue,
        roadmap_index) must come out as top-level keys in the
        handoff-state.json, matching what update_state writes."""
        c = db.connect(db_path=project_dir / "src.db")
        db.set_state(
            c,
            phase="p", type="plan", round=1, status="ready",
            extra_json=json.dumps({"roadmap_queue": "a,b,c",
                                   "roadmap_index": 1}),
        )
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        db.export_to_files(c, out_dir)
        c.close()

        state = json.loads(
            (out_dir / "handoff-state.json").read_text()
        )
        assert state["roadmap_queue"] == "a,b,c"
        assert state["roadmap_index"] == 1
        assert "extra_json" not in state  # cleaned up

    def test_no_state_file_when_no_state_row(self, project_dir):
        c = db.connect(db_path=project_dir / "src.db")
        db.upsert_cycle(c, "p", "plan", state="approved", round_=1,
                        lead="L", reviewer="R", date="2026-05-01")
        out_dir = project_dir / "out"
        (out_dir / "docs" / "handoffs").mkdir(parents=True)
        report = db.export_to_files(c, out_dir)
        c.close()

        assert report["state_written"] is False
        assert not (out_dir / "handoff-state.json").exists()


# ---------------------------------------------------------------------------
# Schema v6 (Phase 34): rate_limits
# ---------------------------------------------------------------------------

class TestSchemaV6RateLimits:
    def test_v6_additive_and_in_non_file_backed(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 6
        assert c.execute("SELECT name FROM sqlite_master WHERE name='rate_limits'").fetchone()
        assert "rate_limits" in db.NON_FILE_BACKED_TABLES
        # v5 → v6 migration is additive (a v5 DB opens and gains the table)
        import sqlite3
        p5 = tmp_path / "v5" / ".tagteam" / "tagteam.db"; p5.parent.mkdir(parents=True)
        raw = sqlite3.connect(p5)
        for ddl in (db._SCHEMA_V1, db._SCHEMA_V3, db._SCHEMA_V4, db._SCHEMA_V5):
            raw.executescript(ddl)
        raw.execute("PRAGMA user_version = 5"); raw.commit(); raw.close()
        c5 = db.connect(project_dir=str(tmp_path / "v5"))
        assert c5.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert c5.execute("SELECT name FROM sqlite_master WHERE name='rate_limits'").fetchone()
        c5.close(); c.close()

    def test_upsert_and_latest(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        i1 = db.upsert_rate_limit(c, provider="claude", kind="five_hour", status="allowed",
                                  resets_at="2026-08-15T18:00:00+00:00", payload={"a": 1}, ts="t1")
        i2 = db.upsert_rate_limit(c, provider="claude", kind="five_hour", status="allowed_warning",
                                  resets_at="2026-08-15T19:00:00+00:00", payload={"a": 2}, ts="t2")
        assert i1 == i2                                       # one row per (provider, kind)
        db.upsert_rate_limit(c, provider="claude", kind="seven_day", status="allowed",
                             resets_at=None, payload=None, ts="t3")
        rows = db.latest_rate_limits(c)
        assert [(r["kind"], r["status"]) for r in rows] == [("five_hour", "allowed_warning"),
                                                             ("seven_day", "allowed")]
        assert rows[0]["payload"] == {"a": 2} and rows[0]["ts"] == "t2"
        assert rows[1]["payload"] is None
        assert db.latest_rate_limits(c, provider="codex") == []
        with pytest.raises(ValueError):
            db.upsert_rate_limit(c, provider="", kind="x", status=None, resets_at=None, payload=None, ts="t")
        c.close()

    def test_snapshot_restore_includes_rate_limits(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        db.upsert_rate_limit(c, provider="claude", kind="five_hour", status="allowed",
                             resets_at="2026-08-15T18:00:00+00:00", payload={"a": 1}, ts="t1")
        snap = db.snapshot_non_file_backed(c)
        assert "rate_limits" in snap and len(snap["rate_limits"]) == 2  # header + 1 row
        c.execute("DELETE FROM rate_limits"); c.commit()
        assert db.latest_rate_limits(c) == []
        counts = db.restore_non_file_backed(c, snap)
        assert counts["rate_limits"] == 1
        assert db.latest_rate_limits(c)[0]["status"] == "allowed"
        c.close()


# ---------------------------------------------------------------------------
# Schema v8 (Phase 38): gates + gatekeeper round role
# ---------------------------------------------------------------------------

class TestSchemaV8Gates:
    def _v7_db(self, root):
        import sqlite3
        p = root / ".tagteam" / "tagteam.db"; p.parent.mkdir(parents=True)
        raw = sqlite3.connect(p)
        for ddl in (db._SCHEMA_V1, db._SCHEMA_V3, db._SCHEMA_V4, db._SCHEMA_V5, db._SCHEMA_V6, db._SCHEMA_V7):
            raw.executescript(ddl)
        raw.execute("PRAGMA user_version = 7")
        raw.commit()
        return raw

    def test_v7_to_v8_rebuilds_rounds_preserving_rows_and_ids(self, tmp_path):
        raw = self._v7_db(tmp_path)
        raw.execute("INSERT INTO cycles (phase, type, lead, reviewer, state, ready_for, ready_for_present, round, date, created_at) "
                    "VALUES ('p','impl','L','R','in-progress','reviewer',1,1,'d','t')")
        cid = raw.execute("SELECT id FROM cycles").fetchone()[0]
        raw.execute("INSERT INTO rounds (id, cycle_id, round, role, action, content, ts) VALUES (7, ?, 1, 'lead', 'SUBMIT_FOR_REVIEW', 'x', 't')", (cid,))
        raw.execute("INSERT INTO rounds (id, cycle_id, round, role, action, content, ts) VALUES (9, ?, 1, 'reviewer', 'REQUEST_CHANGES', 'y', 't')", (cid,))
        raw.commit(); raw.close()
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 8
        assert [tuple(r) for r in c.execute("SELECT id, role, action FROM rounds ORDER BY id")] == \
            [(7, "lead", "SUBMIT_FOR_REVIEW"), (9, "reviewer", "REQUEST_CHANGES")]
        assert c.execute("SELECT name FROM sqlite_master WHERE name='gates'").fetchone()
        assert c.execute("SELECT name FROM sqlite_master WHERE name='idx_rounds_cycle'").fetchone()
        # a third role is now accepted; the DB vocab agrees
        rid = db.add_round(c, cid, 1, "gatekeeper", "GATE_PASS", "GATE: PASS", "t")
        assert rid > 9                                    # AUTOINCREMENT continues past the copied ids
        assert "gatekeeper" in db.VALID_ROLES and {"GATE_PASS", "GATE_BOUNCE"} <= db.VALID_ACTIONS
        assert "gates" in db.NON_FILE_BACKED_TABLES
        c.close()

    def test_claim_gate_at_most_once_and_attempt_cap(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        kw = dict(phase="p", cycle_type="impl", round_=2, submission_seq=5, event_key="p/impl/r2/5",
                  kind="auto", runner_pid=123, runner_ident="i")
        first = db.claim_gate(c, ts="t1", **kw)
        assert first and first[1] == 1
        assert db.claim_gate(c, ts="t2", **kw) is None      # a running row refuses a second claim
        db.finish_gate(c, first[0], status="abandoned", ts="t3", reason="dead")
        second = db.claim_gate(c, ts="t4", **kw)
        assert second and second[1] == 2                    # one automatic retry
        db.finish_gate(c, second[0], status="error", ts="t5", reason="boom")
        assert db.claim_gate(c, ts="t6", **kw) is None      # attempt 3 refused by default
        third = db.claim_gate(c, ts="t7", max_attempts=3, **kw)
        assert third and third[1] == 3
        db.finish_gate(c, third[0], status="pass", ts="t8", result_json="{}", stem="s")
        assert db.claim_gate(c, ts="t9", max_attempts=99, **kw) is None   # decided → never again
        assert db.decided_gate_for_event(c, "p/impl/r2/5")["id"] == third[0]
        assert [r["status"] for r in db.gates_for_event(c, "p/impl/r2/5")] == ["abandoned", "error", "pass"]
        assert db.last_gate(c, "p", "impl")["status"] == "pass"
        assert [r["status"] for r in db.unfinished_gates(c)] == ["abandoned", "error"]   # reconciliation candidates
        assert db.running_gates(c) == []
        with pytest.raises(ValueError):
            db.finish_gate(c, third[0], status="running", ts="t")
        with pytest.raises(ValueError):
            db.claim_gate(c, ts="t", **{**kw, "kind": "weird"})
        c.close()

    def test_uq_gates_decided_blocks_a_second_decision(self, tmp_path):
        import sqlite3
        c = db.connect(project_dir=str(tmp_path))
        a = db.claim_gate(c, ts="t", phase="p", cycle_type="impl", round_=1, submission_seq=1,
                          event_key="e", kind="auto", runner_pid=1, runner_ident=None)
        db.finish_gate(c, a[0], status="bounce", ts="t", applied_seq=2)
        assert db.get_gate(c, a[0])["applied_seq"] == 2
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO gates (event_key, phase, type, round, submission_seq, kind, status, attempt, started_at) "
                      "VALUES ('e','p','impl',1,1,'auto','pass',2,'t')")
        c.close()

    def test_snapshot_restore_includes_gates(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        a = db.claim_gate(c, ts="t", phase="p", cycle_type="impl", round_=1, submission_seq=1,
                          event_key="e", kind="manual", runner_pid=1, runner_ident="x")
        db.finish_gate(c, a[0], status="pass", ts="t")
        snap = db.snapshot_non_file_backed(c)
        assert len(snap["gates"]) == 2
        c.execute("DELETE FROM gates"); c.commit()
        assert db.restore_non_file_backed(c, snap)["gates"] == 1
        assert db.get_gate(c, a[0])["status"] == "pass"
        c.close()


# ---------------------------------------------------------------------------
# Schema v9 (Phase 39): panels + shared satellite claim
# ---------------------------------------------------------------------------

class TestSchemaV9Panels:
    def test_v8_to_v9_additive(self, tmp_path):
        import sqlite3
        p = tmp_path / ".tagteam" / "tagteam.db"; p.parent.mkdir(parents=True)
        raw = sqlite3.connect(p)
        for ddl in (db._SCHEMA_V1, db._SCHEMA_V3, db._SCHEMA_V4, db._SCHEMA_V5, db._SCHEMA_V6, db._SCHEMA_V7, db._SCHEMA_V8):
            raw.executescript(ddl)
        raw.execute("PRAGMA user_version = 8"); raw.commit(); raw.close()
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 9
        assert c.execute("SELECT name FROM sqlite_master WHERE name='panels'").fetchone()
        assert "panels" in db.NON_FILE_BACKED_TABLES
        c.close()

    def test_claim_panel_budget_counts_failed_only(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        kw = dict(phase="p", cycle_type="impl", round_=1, submission_seq=4, event_key="p/impl/r1/4", kind="auto",
                  runner_pid=1, runner_ident="i")
        a = db.claim_panel(c, ts="t", interjection_ids=[3, 4], **kw)
        assert a and a[1] == 1 and db.get_panel(c, a[0])["interjection_ids"] == "[3, 4]"
        assert db.claim_panel(c, ts="t", **kw) is None                       # running refuses
        db.finish_panel(c, a[0], status="superseded", ts="t", reason="moved")
        b = db.claim_panel(c, ts="t", **kw); assert b and b[1] == 2         # superseded did not count
        db.finish_panel(c, b[0], status="superseded", ts="t")
        cc = db.claim_panel(c, ts="t", **kw); assert cc and cc[1] == 3       # still not exhausted
        db.finish_panel(c, cc[0], status="error", ts="t", reason="x")
        d = db.claim_panel(c, ts="t", **kw); assert d and d[1] == 4          # first failure → retry
        db.finish_panel(c, d[0], status="abandoned", ts="t")
        assert db.claim_panel(c, ts="t", **kw) is None                       # 2 failed → refused
        e = db.claim_panel(c, ts="t", max_attempts=3, **kw); assert e        # forced (fallback path)
        db.finish_panel(c, e[0], status="fallback", ts="t", reason="exhausted")
        assert db.claim_panel(c, ts="t", max_attempts=99, **kw) is None      # decided → never again
        assert db.decided_panel_for_event(c, "p/impl/r1/4")["status"] == "fallback"
        assert [r["status"] for r in db.panels_for_event(c, "p/impl/r1/4")] == \
            ["superseded", "superseded", "error", "abandoned", "fallback"]
        assert db.last_panel(c, "p", "impl")["status"] == "fallback"
        assert [r["status"] for r in db.unfinished_panels(c)] == ["error", "abandoned"]
        with pytest.raises(ValueError):
            db.finish_panel(c, e[0], status="running", ts="t")
        with pytest.raises(ValueError):
            db.claim_panel(c, ts="t", **{**kw, "kind": "weird"})
        with pytest.raises(ValueError):
            db.update_panel(c, e[0], ts="t", nope=1)
        c.close()

    def test_gate_claim_uses_the_same_rule(self, tmp_path):
        """Phase 39 aligned the gate: superseded gate rows never consume the budget."""
        c = db.connect(project_dir=str(tmp_path))
        kw = dict(phase="p", cycle_type="impl", round_=1, submission_seq=4, event_key="e", kind="auto",
                  runner_pid=1, runner_ident="i")
        for _ in range(3):
            r = db.claim_gate(c, ts="t", **kw); assert r
            db.finish_gate(c, r[0], status="superseded", ts="t")
        r = db.claim_gate(c, ts="t", **kw); assert r and r[1] == 4
        db.finish_gate(c, r[0], status="error", ts="t"); r = db.claim_gate(c, ts="t", **kw); assert r
        db.finish_gate(c, r[0], status="error", ts="t")
        assert db.claim_gate(c, ts="t", **kw) is None
        c.close()

    def test_uq_panels_decided_and_snapshot_restore(self, tmp_path):
        import sqlite3
        c = db.connect(project_dir=str(tmp_path))
        a = db.claim_panel(c, ts="t", phase="p", cycle_type="impl", round_=1, submission_seq=1, event_key="e",
                           kind="manual", runner_pid=1, runner_ident=None)
        db.finish_panel(c, a[0], status="merged", ts="t", decision="APPROVE", applied_seq=2, lenses_json="[]")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO panels (event_key, phase, type, round, submission_seq, kind, status, attempt, started_at) "
                      "VALUES ('e','p','impl',1,1,'auto','fallback',2,'t')")
        snap = db.snapshot_non_file_backed(c)
        assert len(snap["panels"]) == 2
        c.execute("DELETE FROM panels"); c.commit()
        assert db.restore_non_file_backed(c, snap)["panels"] == 1
        assert db.get_panel(c, a[0])["decision"] == "APPROVE"
        c.close()


# ---------------------------------------------------------------------------
# Schema v10 (Phase 55): per-model tokens + target identity; the read path
# ---------------------------------------------------------------------------

def _raw_db_at(tmp_path, version: int):
    """A DB built from the schema scripts up to `version` (no migration run)."""
    import sqlite3
    p = tmp_path / ".tagteam" / "tagteam.db"; p.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(p)
    scripts = {1: db._SCHEMA_V1, 3: db._SCHEMA_V3, 4: db._SCHEMA_V4, 5: db._SCHEMA_V5, 6: db._SCHEMA_V6,
               7: db._SCHEMA_V7, 8: db._SCHEMA_V8, 9: db._SCHEMA_V9}
    for v, ddl in sorted(scripts.items()):
        if v <= version:
            raw.executescript(ddl)
    if version >= 7:
        raw.execute("ALTER TABLE usage ADD COLUMN kind TEXT")
    raw.execute(f"PRAGMA user_version = {version}"); raw.commit()
    return p, raw


class TestSchemaV10Usage:
    def test_v9_to_v10_adds_columns_keeps_rows(self, tmp_path):
        p, raw = _raw_db_at(tmp_path, 9)
        raw.execute("INSERT INTO usage (ts, status, phase, role, input_tokens, kind) "
                    "VALUES ('2026-09-14T00:00:00+00:00', 'ok', 'p', 'lead', 5, 'conversation')")
        raw.commit(); raw.close()
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION >= 10
        cols = db.table_columns(c, "usage")
        assert {"model_usage_json", "target_phase", "target_type", "target_round", "kind"} <= cols
        rows = db.get_usage(c)
        assert len(rows) == 1 and rows[0]["input_tokens"] == 5 and rows[0]["kind"] == "conversation"
        assert rows[0]["target_phase"] is None and rows[0]["model_usage"] is None
        c.close()

    def test_add_usage_v10_fields_and_target_filter(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        db.add_usage(c, ts="t1", status="ok", phase="a", type="impl", round=3, role="lead",
                     target_phase="b", target_type="plan", target_round=1,
                     model_usage_json='{"m1": {"input_tokens": 1}}')
        db.add_usage(c, ts="t2", status="ok", phase="b", type="plan", round=1, role="reviewer")
        assert [r["ts"] for r in db.get_usage(c, target_phase="b")] == ["t1"]
        assert [r["ts"] for r in db.get_usage(c, phase="b")] == ["t2"]
        assert db.get_usage(c, phase="a")[0]["model_usage"] == {"m1": {"input_tokens": 1}}
        c.close()

    def test_get_usage_on_older_schemas(self, tmp_path):
        p, raw = _raw_db_at(tmp_path, 6)       # no `kind`, no v10 columns
        raw.execute("INSERT INTO usage (ts, status, phase) VALUES ('t', 'ok', 'p')"); raw.commit(); raw.close()
        c, note = db.connect_for_read(project_dir=str(tmp_path))
        assert note is None
        rows = db.get_usage(c, phase="p")
        assert len(rows) == 1 and rows[0]["kind"] is None and rows[0]["target_round"] is None
        assert db.get_usage(c, target_phase="p") == []
        assert c.execute("PRAGMA user_version").fetchone()[0] == 6     # never migrated
        c.close()

    def test_get_usage_without_usage_table(self, tmp_path):
        p, raw = _raw_db_at(tmp_path, 1); raw.close()
        c, _ = db.connect_for_read(project_dir=str(tmp_path))
        assert db.get_usage(c) == [] and db.table_columns(c, "usage") == set()
        c.close()

    def test_connect_for_read_missing_db_creates_nothing(self, tmp_path):
        conn, note = db.connect_for_read(project_dir=str(tmp_path))
        assert conn is None and note == "no database"
        assert not (tmp_path / ".tagteam").exists()
