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
