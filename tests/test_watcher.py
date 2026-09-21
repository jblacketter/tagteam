"""Tests for the _StateProcessor class — the per-tick processing logic
extracted from watch() so it can be reused by event-driven watcher modes.

These tests exercise tick() with crafted state dicts and verify the right
notifications, send-keys, and roadmap-advance calls happen. We do not
test the polling loop itself (it's a thin wrapper); we test the processor
that both polling and event modes call.
"""
from unittest.mock import MagicMock, patch

import pytest

from tagteam.watcher import _StateProcessor
from tagteam.contract import STANDARD_TURN_COMMAND


def _make_processor(mode="notify", **overrides):
    defaults = dict(
        mode=mode,
        lead_name="Claude",
        reviewer_name="Codex",
        lead_pane="tagteam:0.0",
        reviewer_pane="tagteam:0.2",
        lead_session_id="lead-sid" if mode == "iterm2" else None,
        reviewer_session_id="rev-sid" if mode == "iterm2" else None,
        confirm=False,
        timeout_minutes=30,
        project_dir=".",
        max_retries=3,
        retry_delay=2.0,
        pre_send_delay=1.0,
    )
    defaults.update(overrides)
    return _StateProcessor(**defaults)


def _state(seq, status="ready", turn="lead", command="/handoff",
           phase="p1", round_=1, **extra):
    s = {
        "seq": seq,
        "status": status,
        "turn": turn,
        "command": command,
        "phase": phase,
        "round": round_,
        "updated_at": f"2026-05-03T00:00:{seq:02d}+00:00",
    }
    s.update(extra)
    return s


@pytest.fixture(autouse=True)
def isolate_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# --- First-poll bootstrap ---

def test_first_tick_with_non_ready_state_records_seq_and_returns():
    """First tick on a non-actionable state should just record seq."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="done"))
    assert p.last_processed_seq == 5
    notify.assert_not_called()


def test_first_tick_with_ready_state_processes_immediately():
    """First tick on a ready state should dispatch (watcher restart mid-cycle)."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready", turn="lead", command="/handoff"))
    assert p.last_processed_seq == 5
    notify.assert_called_once()
    args = notify.call_args[0]
    assert "Claude" in args[1]


# --- Seq dedup ---

def test_unchanged_seq_is_a_noop():
    """Same seq twice should not re-dispatch."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))
    notify.assert_not_called()


def test_advancing_seq_redispatches():
    """A new seq with status=ready should re-dispatch."""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        p.tick(_state(seq=6, status="ready", turn="reviewer"))
    assert notify.call_count == 2
    assert "Codex" in notify.call_args[0][1]


# --- Status dispatch (notify mode) ---

def test_done_status_notifies_lead_via_completion_message():
    """Transition to done after a ready state should notify completion.
    (First-poll bootstrap on done would just record seq and skip notify —
    notifications fire only when status TRANSITIONS, not on initial pickup.)"""
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))  # establish baseline
        notify.reset_mock()
        p.tick(_state(seq=2, status="done", result="approved"))
    notify.assert_called_once()
    assert "complete" in notify.call_args[0][1].lower()


def test_escalated_status_with_pause_reason_logs_pause():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=2, status="escalated", reason="needs human"))
    notify.assert_called_once()
    assert "needs human" in notify.call_args[0][1].lower()


def test_aborted_status_notifies_with_reason():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=2, status="aborted", reason="user-killed"))
    notify.assert_called_once()
    assert "user-killed" in notify.call_args[0][1]


def test_working_status_does_not_notify():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="working", turn="lead"))
    notify.assert_not_called()


# --- Watchdog re-send (Phase 41 state machine) ---

def _age(p, seconds):
    """Pretend the last send happened `seconds` ago."""
    p.last_ready_send_time = p.last_ready_send_time - seconds


def test_watchdog_resends_after_resend_interval_notify_mode():
    """notify mode has no pane: after the interval the human is re-notified."""
    p = _make_processor()
    assert p.resend_minutes == 15 and p.resend_s == 900.0
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        _age(p, p.resend_s + 1)
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))  # same seq
    notify.assert_called_once()
    assert p._watchdog["resends"] == 1 and p._watchdog["seq"] == 5


def test_watchdog_does_not_resend_within_interval():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        notify.reset_mock()
        p.tick(_state(seq=5, status="ready"))  # immediate, same seq
    notify.assert_not_called()


def test_watchdog_zero_disables():
    p = _make_processor(resend_minutes=0)
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        _age(p, 10 ** 6)
        notify.reset_mock()
        for _ in range(3):
            p.tick(_state(seq=5, status="ready"))
    notify.assert_not_called()


def test_watchdog_cap_then_one_notification_per_seq():
    p = _make_processor()
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=5, status="ready"))
        for _ in range(2):
            _age(p, p.resend_s + 1)
            p.tick(_state(seq=5, status="ready"))          # two re-sends
        assert p._watchdog["resends"] == 2 and notify.call_count == 3
        _age(p, p.resend_s + 1)
        p.tick(_state(seq=5, status="ready"))              # cap → one notification, no send
        assert p._watchdog["notified"] is True and notify.call_count == 4
        assert "still waiting" in notify.call_args[0][1]
        _age(p, p.resend_s + 1)
        p.tick(_state(seq=5, status="ready"))              # nothing more for this seq
        p.tick(_state(seq=5, status="ready"))
        assert notify.call_count == 4
        # a new seq gets a fresh record: dispatched, counters reset
        p.tick(_state(seq=6, status="ready", turn="reviewer"))
        assert p._watchdog == {"seq": 6, "resends": 0, "last_tail": None, "notified": False,
                               "last_probe": 0.0, "last_busy_log": 0.0}
        assert notify.call_count == 5


BUSY_CLAUDE = ("some output\n✶ Burrowing… (10m 30s · ↓ 12.8k tokens)\n\n"
               "╭──────────────╮\n│ ❯            │\n╰──────────────╯\n  ? for shortcuts")
BUSY_CLAUDE_SHELL = ("  ⎿  $ pytest -q\n     (ctrl+b to run in background)\n\n"
                     "╭──────────────╮\n│ ❯            │\n╰──────────────╯\n  ? for shortcuts")
IDLE_CLAUDE = ("done.\n\n╭──────────────╮\n│ ❯            │\n╰──────────────╯\n  ? for shortcuts")
BUSY_CODEX = "• Working (12s • Esc to interrupt)\n\n› \n  /skills to list"
IDLE_CODEX = "• Done\n\n› \n  /skills to list  •  100% context left"


@pytest.mark.parametrize("mode,capture_target", [
    ("iterm2", "tagteam.iterm.get_session_contents"),
    ("tmux", "tagteam.watcher.capture_pane"),
])
class TestWatchdogPane:
    def _sender(self, mode):
        return "tagteam.watcher.send_iterm_command" if mode == "iterm2" else "tagteam.watcher.send_tmux_keys"

    def _run(self, mode, capture_target, captures, *, ticks=None, resend_minutes=None):
        """Dispatch seq 5, then tick once per capture with the interval elapsed
        and the probe throttle bypassed. Returns (processor, send mock)."""
        kw = {} if resend_minutes is None else {"resend_minutes": resend_minutes}
        p = _make_processor(mode=mode, **kw)
        p.WATCHDOG_PROBE_S = 0.0
        with patch(self._sender(mode), return_value=True) as send, \
             patch("tagteam.watcher.notify_macos") as notify, \
             patch(capture_target, side_effect=captures) as cap:
            p.tick(_state(seq=5, status="ready"))
            for st in (ticks or [_state(seq=5, status="ready")] * len(captures)):
                _age(p, p.resend_s + 1)
                p.tick(st)
        return p, send, notify, cap

    def test_capture_failure_never_resends(self, mode, capture_target):
        p, send, notify, cap = self._run(mode, capture_target, ["", "", Exception("boom")])
        assert send.call_count == 1 and p._watchdog["resends"] == 0 and p._watchdog["last_tail"] is None
        notify.assert_not_called()

    def test_changing_tail_rebaselines_and_suppresses(self, mode, capture_target):
        p, send, notify, cap = self._run(mode, capture_target, [IDLE_CLAUDE, IDLE_CLAUDE + " ", IDLE_CLAUDE + "  "])
        assert send.call_count == 1 and p._watchdog["last_tail"] == IDLE_CLAUDE + "  "

    def test_busy_markers_suppress_even_when_tail_is_stable(self, mode, capture_target):
        for busy in (BUSY_CLAUDE, BUSY_CLAUDE_SHELL, BUSY_CODEX):
            p, send, notify, cap = self._run(mode, capture_target, [busy, busy, busy])
            assert send.call_count == 1, busy
            assert p._watchdog["resends"] == 0

    def test_positive_idle_twice_resends_then_cap(self, mode, capture_target):
        caps = [IDLE_CLAUDE, IDLE_CLAUDE,           # baseline, then identical → re-send 1
                IDLE_CODEX, IDLE_CODEX,             # new baseline (differs), identical → re-send 2
                IDLE_CODEX, IDLE_CODEX]             # cap: notify once, no send
        p, send, notify, cap = self._run(mode, capture_target, caps)
        assert send.call_count == 3 and p._watchdog["resends"] == 2 and p._watchdog["notified"] is True
        assert notify.call_count == 1 and cap.call_count == 4     # no capture once capped

    def test_seq_rollover_resets_baseline_and_counters(self, mode, capture_target):
        caps = [IDLE_CLAUDE, IDLE_CLAUDE, IDLE_CLAUDE]
        ticks = [_state(seq=5, status="ready"), _state(seq=5, status="ready"),
                 _state(seq=6, status="ready", turn="reviewer")]
        p, send, notify, cap = self._run(mode, capture_target, caps, ticks=ticks)
        # seq 5: baseline + one re-send; seq 6: fresh dispatch, record reset (stale tail dropped)
        assert send.call_count == 3
        assert p._watchdog["seq"] == 6 and p._watchdog["resends"] == 0 and p._watchdog["last_tail"] is None
        assert cap.call_count == 2

    def test_non_ready_state_leaves_record_alone(self, mode, capture_target):
        p, send, notify, cap = self._run(mode, capture_target, [IDLE_CLAUDE],
                                         ticks=[_state(seq=5, status="working")])
        assert send.call_count == 1 and cap.call_count == 0 and p._watchdog["seq"] == 5


def test_idle_patterns_busy_over_whole_capture_idle_over_tail():
    from tagteam.watcher import _check_idle_patterns
    assert _check_idle_patterns(IDLE_CLAUDE) is True
    assert _check_idle_patterns(IDLE_CODEX) is True
    assert _check_idle_patterns(BUSY_CLAUDE) is False        # spinner 6 lines above the prompt
    assert _check_idle_patterns(BUSY_CLAUDE_SHELL) is False
    assert _check_idle_patterns(BUSY_CODEX) is False
    assert _check_idle_patterns("") is False


def test_watcher_config_block():
    from tagteam.config import validate_watcher_config, get_watcher_spec
    assert validate_watcher_config({}) == [] and get_watcher_spec({})["resend_minutes"] == 15
    assert validate_watcher_config({"watcher": {"resend_minutes": 0}}) == []
    assert get_watcher_spec({"watcher": {"resend_minutes": 0}})["resend_minutes"] == 0
    for bad in ({"watcher": {"resend_minutes": -1}}, {"watcher": {"resend_minutes": True}},
                {"watcher": {"resend_minutes": "5"}}, {"watcher": {"nope": 1}}, {"watcher": []}):
        assert validate_watcher_config(bad), bad
    assert get_watcher_spec({"watcher": {"resend_minutes": "5"}})["resend_minutes"] == 15


# --- iterm2 mode dispatch ---

def test_iterm2_mode_calls_send_iterm_command():
    p = _make_processor(mode="iterm2")
    with patch("tagteam.watcher.send_iterm_command",
               return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="lead",
                      command="/handoff"))
    send.assert_called_once()
    assert send.call_args[0][0] == "lead-sid"
    assert send.call_args[0][1] == STANDARD_TURN_COMMAND


def test_iterm2_mode_uses_reviewer_session_when_turn_is_reviewer():
    p = _make_processor(mode="iterm2")
    with patch("tagteam.watcher.send_iterm_command",
               return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="reviewer"))
    assert send.call_args[0][0] == "rev-sid"


# --- tmux mode dispatch ---

def test_tmux_mode_calls_send_tmux_keys():
    p = _make_processor(mode="tmux")
    with patch("tagteam.watcher.send_tmux_keys", return_value=True) as send:
        p.tick(_state(seq=1, status="ready", turn="lead"))
    send.assert_called_once()
    assert send.call_args[0][0] == "tagteam:0.0"


# --- Roadmap advance ---

def test_done_status_with_roadmap_advance_skips_completion_message():
    """If _try_roadmap_advance returns a new state, no completion notify."""
    p = _make_processor()
    with patch("tagteam.watcher._try_roadmap_advance",
               return_value={"phase": "next", "type": "plan"}), \
         patch("tagteam.watcher.notify_macos") as notify:
        p.tick(_state(seq=1, status="ready"))  # baseline
        notify.reset_mock()
        p.tick(_state(seq=2, status="done", result="approved"))
    notify.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 34: watch() records a project-bound pidfile for its lifetime
# ---------------------------------------------------------------------------

_BASE_YAML = "agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n"
_COCKPIT_YAML = _BASE_YAML + "serve:\n  theme: cockpit\n"


def _snapshot(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _stub_watch(monkeypatch, watcher_mod, on_loop):
    monkeypatch.setattr(watcher_mod, "_build_processor", lambda **kw: MagicMock())
    monkeypatch.setattr(watcher_mod, "_log_startup_banner", lambda *a, **k: None)
    monkeypatch.setattr(watcher_mod, "_run_poll_loop", on_loop)


def test_flag_off_watch_creates_no_new_files(tmp_path, monkeypatch):
    """3.0-arc hard constraint: bare `tagteam watch` (no cockpit opt-in) must
    behave exactly as before — no watcher.json, no other new file."""
    from tagteam import watcher as watcher_mod
    (tmp_path / "tagteam.yaml").write_text(_BASE_YAML)
    before = _snapshot(tmp_path)
    seen = {}

    def loop(processor, project_dir, interval):
        seen["during"] = _snapshot(tmp_path)
        seen["pidfile"] = watcher_mod.read_pidfile(tmp_path)

    _stub_watch(monkeypatch, watcher_mod, loop)
    assert watcher_mod.watch(mode="iterm2", project_dir=str(tmp_path), force_poll=True) is True
    assert seen["pidfile"] is None
    assert seen["during"] == before                 # nothing written while running
    assert _snapshot(tmp_path) == before            # nothing left behind
    assert not (tmp_path / ".tagteam").exists()
    assert watcher_mod.pidfile_enabled(tmp_path) is False


def test_watch_writes_and_removes_pidfile_when_cockpit_configured(tmp_path, monkeypatch):
    import os
    from tagteam import watcher as watcher_mod
    (tmp_path / "tagteam.yaml").write_text(_COCKPIT_YAML)      # the config gate
    assert watcher_mod.pidfile_enabled(tmp_path) is True
    seen = {}

    def loop(processor, project_dir, interval):
        seen["during"] = watcher_mod.read_pidfile(tmp_path)

    _stub_watch(monkeypatch, watcher_mod, loop)
    assert watcher_mod.watch(mode="iterm2", project_dir=str(tmp_path), force_poll=True) is True
    assert seen["during"]["pid"] == os.getpid() and seen["during"]["mode"] == "iterm2"
    assert seen["during"]["project_dir"] == str(tmp_path.resolve())
    assert watcher_mod.read_pidfile(tmp_path) is None          # removed on exit


def test_watch_pidfile_explicit_flag_overrides_config(tmp_path, monkeypatch):
    from tagteam import watcher as watcher_mod
    (tmp_path / "tagteam.yaml").write_text(_BASE_YAML)         # no config gate
    seen = {}
    _stub_watch(monkeypatch, watcher_mod, lambda p, d, i: seen.__setitem__("rec", watcher_mod.read_pidfile(tmp_path)))
    assert watcher_mod.watch(mode="notify", project_dir=str(tmp_path), force_poll=True, pidfile=True) is True
    assert seen["rec"] is not None and seen["rec"]["mode"] == "notify"
    assert watcher_mod.read_pidfile(tmp_path) is None
    # explicit False wins over the config gate
    (tmp_path / "tagteam.yaml").write_text(_COCKPIT_YAML)
    seen.clear()
    assert watcher_mod.watch(mode="notify", project_dir=str(tmp_path), force_poll=True, pidfile=False) is True
    assert seen["rec"] is None
    # `--pidfile` is parsed by the CLI entry point
    called = {}
    monkeypatch.setattr(watcher_mod, "watch", lambda **kw: called.update(kw) or True)
    assert watcher_mod.watch_command(["--mode", "notify", "--pidfile"]) == 0
    assert called["pidfile"] is True
    called.clear()
    assert watcher_mod.watch_command(["--mode", "notify"]) == 0
    assert called["pidfile"] is None


def test_watch_pidfile_removed_on_exception(tmp_path, monkeypatch):
    from tagteam import watcher as watcher_mod
    (tmp_path / "tagteam.yaml").write_text(_COCKPIT_YAML)

    def boom(processor, project_dir, interval):
        assert watcher_mod.read_pidfile(tmp_path) is not None
        raise RuntimeError("loop died")

    _stub_watch(monkeypatch, watcher_mod, boom)
    with pytest.raises(RuntimeError):
        watcher_mod.watch(mode="notify", project_dir=str(tmp_path), force_poll=True)
    assert watcher_mod.read_pidfile(tmp_path) is None


@pytest.mark.parametrize("lead,reviewer", [("codex", "claude"), ("claude", "codex")])
def test_plan_completion_is_portable_and_requests_implementation(tmp_path, lead, reviewer):
    p = _make_processor(mode="iterm2", project_dir=str(tmp_path),
                        lead_name=lead, reviewer_name=reviewer)
    with patch("tagteam.watcher.send_iterm_command", return_value=True) as send, \
         patch("tagteam.watcher.notify_macos"):
        p._handle_done(_state(2, status="done", result="approved", type="plan"))
    message = send.call_args.args[1]
    assert send.call_args.args[0] == "lead-sid"
    assert not message.startswith("/")
    assert "tagteam contract" in message
    assert "implement it now" in message


@pytest.mark.parametrize("command", ["/handoff start p1 impl", "/tagteam:handoff start p1"])
def test_start_delivery_is_text_and_preserves_state_command(tmp_path, command):
    p = _make_processor(mode="iterm2", project_dir=str(tmp_path))
    state = _state(1, command=command)
    with patch("tagteam.watcher.send_iterm_command", return_value=True) as send:
        p.tick(state)
    assert not send.call_args.args[1].startswith("/")
    assert command in send.call_args.args[1]
    assert state["command"] == command


# --- Phase 68a: a headless watcher hands an approved plan to the lead ---
#
# A tab watcher types "the plan is approved: implement it now" into the lead's
# terminal. Headless had no branch for that, so a single-phase run stopped
# until a human clicked Start in the cockpit.

import json

from tagteam import state as state_mod
from tagteam import watcher as watcher_mod


class _Engine:
    """Stands in for HeadlessEngine: records the turns it is asked to run."""
    slot_busy = None

    def __init__(self):
        self.ran = []

    def run_owed_turn(self, state):
        self.ran.append(dict(state))


def _on_disk(tmp_path, monkeypatch, **fields):
    """A real handoff-state.json in cwd, as `update_state` needs one."""
    monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
    base = {"phase": "p1", "type": "plan", "round": 1, "status": "ready", "turn": "reviewer",
            "command": "/handoff", "run_mode": "single-phase"}
    base.update(fields)
    state_mod.write_state(base, str(tmp_path))
    return state_mod.read_state(str(tmp_path))


def _approve_plan(tmp_path, **fields):
    new = state_mod.update_state({"status": "done", "turn": None, "result": "approved", **fields}, str(tmp_path))
    assert new is not None
    return new


def _headless(tmp_path, **kw):
    eng = _Engine()
    p = _make_processor("headless", project_dir=str(tmp_path), **kw)
    p.engine = eng
    return p, eng


def _logged(capsys):
    return capsys.readouterr().out


def test_headless_plan_approval_becomes_the_leads_owed_turn(tmp_path, monkeypatch, capsys):
    seen = _on_disk(tmp_path, monkeypatch)
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos") as notify:
        p.tick(seen)                                   # a state this watcher has witnessed (the reviewer's turn)
        assert [t["turn"] for t in eng.ran] == ["reviewer"]
        p.tick(_approve_plan(tmp_path))
        after = state_mod.read_state(str(tmp_path))
        assert (after["turn"], after["status"], after["result"], after["type"]) == ("lead", "ready", None, "plan")
        assert after["command"].endswith(" start p1 impl") and after["run_mode"] == "single-phase"
        assert len(eng.ran) == 1                       # the transition only; the next tick runs it
        p.tick(after)
    assert [(t["turn"], t["command"]) for t in eng.ran][-1] == ("lead", after["command"])
    out = _logged(capsys)
    assert out.count("AUTO-ADVANCE: plan approved → lead implements (phase: p1)") == 1
    assert "Sending completion notice" not in out and "** Cycle complete: approved" in out
    assert any("complete" in c[0][1].lower() for c in notify.call_args_list)


def test_headless_first_tick_on_an_already_approved_plan_does_nothing(tmp_path, monkeypatch, capsys):
    """A restarted watcher did not witness the approval: same rule as a tab
    watcher, which sends no notice for it. The cockpit's Start card remains."""
    _on_disk(tmp_path, monkeypatch)
    done = _approve_plan(tmp_path)
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(done)
        p.tick(done)
    assert eng.ran == [] and state_mod.read_state(str(tmp_path))["status"] == "done"
    assert "AUTO-ADVANCE" not in _logged(capsys)


@pytest.mark.parametrize("fields, why", [
    ({"type": "impl"}, "impl approval ends a single-phase run"),
    ({"result": "rejected"}, "not approved"),
    ({"run_mode": "full-roadmap"}, "full-roadmap declined to advance (no roadmap metadata): not retried here"),
    ({"run_mode": "full-roadmap", "roadmap": {"queue": ["p1"], "current_index": 0, "completed": []},
      "_stale": True}, "full-roadmap declined on a staleness guard: not retried here"),
])
def test_headless_does_not_advance(tmp_path, monkeypatch, capsys, fields, why):
    fields = dict(fields)
    stale = fields.pop("_stale", False)
    seen = _on_disk(tmp_path, monkeypatch, **{k: v for k, v in fields.items() if k in ("run_mode", "roadmap", "type")})
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(seen)
        done = _approve_plan(tmp_path, **{k: v for k, v in fields.items() if k == "result"})
        if stale:       # the lead is already past this point by the time the watcher looks
            with patch("tagteam.watcher.read_state", return_value={**done, "status": "ready", "turn": "reviewer"}):
                p.tick(done)
        else:
            p.tick(done)
    after = state_mod.read_state(str(tmp_path))
    assert after["status"] == "done" and after["seq"] == done["seq"], why
    assert len(eng.ran) == 1, why
    assert "Sending completion notice" not in _logged(capsys)      # headless never claims a send


@pytest.mark.parametrize("fresh, line", [
    ({"type": "impl"}, "SKIP: plan→impl advance already happened"),
    ({"status": "ready", "turn": "reviewer"}, "SKIP: lead already submitted for review"),
])
def test_headless_advance_staleness_guards(tmp_path, monkeypatch, capsys, fresh, line):
    seen = _on_disk(tmp_path, monkeypatch)
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(seen)
        done = _approve_plan(tmp_path)
        with patch("tagteam.watcher.read_state", return_value={**done, **fresh}):
            p.tick(done)
    assert state_mod.read_state(str(tmp_path))["seq"] == done["seq"] and line in _logged(capsys)


def test_headless_advance_loses_a_seq_race_quietly(tmp_path, monkeypatch, capsys):
    seen = _on_disk(tmp_path, monkeypatch)
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(seen)
        done = _approve_plan(tmp_path)
        state_mod.update_state({"round": 2}, str(tmp_path))          # somebody else wrote first
        p.tick(done)
    after = state_mod.read_state(str(tmp_path))
    assert after["status"] == "done" and "SKIP: state changed since approval detected" in _logged(capsys)


def test_headless_advanced_turn_is_held_by_a_pause_and_dispatched_once_on_resume(tmp_path, monkeypatch):
    seen = _on_disk(tmp_path, monkeypatch)
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(seen)
        p.tick(_approve_plan(tmp_path))
        owed = state_mod.read_state(str(tmp_path))
        with patch.object(p, "_pause_info", return_value={"reason": "reading the plan first", "by": "arbiter"}):
            p.tick(owed); p.tick(owed)
        assert len(eng.ran) == 1                       # still only the reviewer's turn
        p.tick(owed); p.tick(owed)
    assert [t["turn"] for t in eng.ran] == ["reviewer", "lead"]


@pytest.mark.parametrize("mode", ["iterm2", "tmux", "notify"])
def test_terminal_watchers_keep_their_completion_notice(tmp_path, monkeypatch, capsys, mode):
    """Unchanged: they type (or notify) the 'implement it now' message and leave the state alone."""
    seen = _on_disk(tmp_path, monkeypatch)
    p = _make_processor(mode, project_dir=str(tmp_path))
    with patch("tagteam.watcher.notify_macos"), patch("tagteam.watcher.send_iterm_command", return_value=True) as tab, \
         patch("tagteam.watcher.send_tmux_keys", return_value=True) as tmux, \
         patch.object(p, "_maybe_gate", return_value=True), patch.object(p, "_maybe_panel", return_value=True):
        p.tick(seen)
        done = _approve_plan(tmp_path)
        p.tick(done)
    assert state_mod.read_state(str(tmp_path))["seq"] == done["seq"]
    out = _logged(capsys)
    assert "Sending completion notice to Claude..." in out and "AUTO-ADVANCE" not in out
    sent = {"iterm2": tab, "tmux": tmux}.get(mode)
    if sent is not None:
        assert "The plan for p1 is approved: implement it now" in sent.call_args_list[-1][0][1]


def test_full_roadmap_plan_advance_still_goes_through_the_shared_transition(tmp_path, monkeypatch, capsys):
    seen = _on_disk(tmp_path, monkeypatch, run_mode="full-roadmap",
                    roadmap={"queue": ["p1", "p2"], "current_index": 0, "completed": []})
    p, eng = _headless(tmp_path)
    with patch("tagteam.watcher.notify_macos"):
        p.tick(seen)
        p.tick(_approve_plan(tmp_path))
    after = state_mod.read_state(str(tmp_path))
    assert (after["turn"], after["status"], after["run_mode"]) == ("lead", "ready", "full-roadmap")
    assert after["command"].endswith(" start p1 impl")
    out = _logged(capsys)
    assert out.count("AUTO-ADVANCE: plan approved → lead implements") == 1 and "** Cycle complete" not in out
