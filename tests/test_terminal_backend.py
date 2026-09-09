"""Phase 42: the `terminal` backend's plumbing — session.py backend
selection/dispatch, `session adopt|list-terminal|kill`, tagteam.tabs, the
watcher's tab-driver dispatch and auto-detect, and the server/state consumers.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from tagteam import session as sess
from tagteam import tabs
from tagteam import watcher as watcher_mod
from tagteam.watcher import _StateProcessor


# --- tagteam.tabs -------------------------------------------------------------

def test_tabs_session_backend_defaults_to_iterm2_for_pre_36_files(tmp_path):
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"tabs": {"lead": {"session_id": "A"}}}))
    assert tabs.session_backend(str(tmp_path)) == "iterm2"
    assert tabs.session_driver(str(tmp_path))[0] == "iterm2"
    assert tabs.session_driver(str(tmp_path))[1].__name__ == "tagteam.iterm"


def test_tabs_session_backend_reads_terminal(tmp_path):
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "terminal", "tabs": {"lead": {"session_id": "/dev/ttys004"}}}))
    assert tabs.session_backend(str(tmp_path)) == "terminal"
    backend, driver = tabs.session_driver(str(tmp_path))
    assert backend == "terminal" and driver.__name__ == "tagteam.terminal"
    assert tabs.get_session_id("lead", str(tmp_path)) == "/dev/ttys004"


def test_tabs_no_file_and_unknown_backend(tmp_path):
    assert tabs.session_backend(str(tmp_path)) is None
    assert tabs.session_driver(str(tmp_path)) is None
    (tmp_path / ".handoff-session.json").write_text(json.dumps({"backend": "tmux", "tabs": {}}))
    assert tabs.session_driver(str(tmp_path)) is None
    with pytest.raises(ValueError):
        tabs.driver_for("tmux")


def test_iterm_reexports_session_file_helpers():
    from tagteam import iterm
    assert iterm._read_session_file is tabs._read_session_file
    assert iterm.SESSION_FILE == tabs.SESSION_FILE == ".handoff-session.json"
    assert iterm.list_sessions is iterm.list_iterm_sessions


# --- session.py: backend list, detection, validation ----------------------------

def test_supported_backends_and_choice_text():
    assert sess.SUPPORTED_BACKENDS == ("iterm2", "tmux", "terminal", "manual")
    assert "'terminal'" in sess._backend_choices_text()


@pytest.mark.parametrize("iterm,tmux,term,expected", [
    (True, True, True, "iterm2"),
    (False, True, True, "tmux"),
    (False, False, True, "terminal"),
    (False, False, False, "manual"),
])
def test_default_backend_order(monkeypatch, iterm, tmux, term, expected):
    monkeypatch.setattr(sess, "_iterm2_supported", lambda: iterm)
    monkeypatch.setattr(sess, "_tmux_supported", lambda: tmux)
    monkeypatch.setattr(sess, "_terminal_supported", lambda: term)
    assert sess.default_backend() == expected


def test_terminal_supported_only_on_macos(monkeypatch):
    monkeypatch.setattr(sess.sys, "platform", "linux")
    assert sess._terminal_supported() is False
    monkeypatch.setattr(sess.sys, "platform", "darwin")
    monkeypatch.setattr(sess.shutil, "which", lambda name: None)
    assert sess._terminal_supported() is False
    monkeypatch.setattr(sess.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr("tagteam.terminal._TERMINAL_APP_PATHS", (str(sess.Path("/nonexistent/T.app")),))
    assert sess._terminal_supported() is False
    monkeypatch.setattr("tagteam.terminal._TERMINAL_APP_PATHS", ("/",))
    assert sess._terminal_supported() is True


def test_validate_backend_terminal_unavailable_messages(monkeypatch, capsys):
    monkeypatch.setattr(sess, "_terminal_supported", lambda: False)
    monkeypatch.setattr(sess, "_tmux_supported", lambda: True)
    monkeypatch.setattr(sess.sys, "platform", "linux")
    assert sess._validate_backend("terminal") is False
    out = capsys.readouterr().out
    assert "only available on macOS" in out and "--backend tmux" in out
    monkeypatch.setattr(sess.sys, "platform", "darwin")
    assert sess._validate_backend("terminal") is False
    assert "requires AppleScript and Terminal.app" in capsys.readouterr().out


def test_validate_backend_terminal_ok(monkeypatch):
    monkeypatch.setattr(sess, "_terminal_supported", lambda: True)
    assert sess._validate_backend("terminal") is True


def test_parse_backend_accepts_terminal():
    backend, rest = sess._parse_backend(["--backend", "terminal", "start"])
    assert backend == "terminal" and rest == ["start"]


# --- session.py: ensure_session dispatch ------------------------------------------

def test_ensure_session_terminal_dispatches_to_terminal_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "_terminal_supported", lambda: True)
    with patch("tagteam.terminal.create_session", return_value=True) as create:
        assert sess.ensure_session(str(tmp_path), "terminal", launch=False) == "created"
    create.assert_called_once_with(str(tmp_path), launch=False)


def test_ensure_session_terminal_reports_live_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sess, "_terminal_supported", lambda: True)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "terminal", "tabs": {"lead": {"session_id": "/dev/ttys004"}}}))
    with patch("tagteam.terminal._any_session_alive", return_value=True), \
         patch("tagteam.terminal.create_session") as create:
        assert sess.ensure_session(str(tmp_path), "terminal") == "exists"
    create.assert_not_called()
    assert "Terminal.app session already exists" in capsys.readouterr().out


def test_ensure_session_terminal_replaces_stale_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sess, "_terminal_supported", lambda: True)
    f = tmp_path / ".handoff-session.json"
    f.write_text(json.dumps({"backend": "terminal", "tabs": {"lead": {"session_id": "/dev/ttys004"}}}))
    with patch("tagteam.terminal._any_session_alive", return_value=False), \
         patch("tagteam.terminal.create_session", return_value=True) as create:
        assert sess.ensure_session(str(tmp_path), "terminal") == "created"
    assert not f.exists()
    create.assert_called_once()
    assert "Stale Terminal.app session file" in capsys.readouterr().out


def test_ensure_session_refuses_over_live_other_tab_backend(tmp_path, monkeypatch, capsys):
    """A live iTerm2 session must not be silently replaced by a terminal one."""
    monkeypatch.setattr(sess, "_terminal_supported", lambda: True)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "iterm2", "tabs": {"lead": {"session_id": "ABC"}}}))
    with patch("tagteam.iterm._any_session_alive", return_value=True), \
         patch("tagteam.terminal.create_session") as create:
        assert sess.ensure_session(str(tmp_path), "terminal") == "error"
    create.assert_not_called()
    out = capsys.readouterr().out
    assert "live iTerm2 session already exists" in out and "--backend iterm2" in out


def test_ensure_session_iterm2_path_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sess, "_iterm2_supported", lambda: True)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"tabs": {"lead": {"session_id": "ABC"}}}))   # pre-3.6 file, no backend key
    with patch("tagteam.iterm._any_session_alive", return_value=True):
        assert sess.ensure_session(str(tmp_path), "iterm2") == "exists"
    assert "iTerm2 session already exists" in capsys.readouterr().out


# --- session.py: adopt / list-terminal / kill / attach -----------------------------

def test_adopt_terminal_backend_validates_via_terminal_driver_and_writes_backend(tmp_path, capsys):
    with patch("tagteam.terminal.session_id_is_valid", return_value=True) as valid:
        rc = sess._adopt_command(["--lead", "/dev/ttys004", "--reviewer", "/dev/ttys005",
                                  "--dir", str(tmp_path)], "terminal")
    assert rc == 0
    assert valid.call_count == 2
    data = json.loads((tmp_path / ".handoff-session.json").read_text())
    assert data == {"backend": "terminal",
                    "tabs": {"lead": {"session_id": "/dev/ttys004"},
                             "reviewer": {"session_id": "/dev/ttys005"}}}
    assert "Adopted Terminal.app sessions" in capsys.readouterr().out


def test_adopt_terminal_rejects_dead_tty(tmp_path, capsys):
    with patch("tagteam.terminal.session_id_is_valid", return_value=False):
        rc = sess._adopt_command(["--lead", "/dev/ttys004", "--dir", str(tmp_path)], "terminal")
    assert rc == 1
    assert "not a live Terminal.app session" in capsys.readouterr().out


def test_adopt_help_names_backend(capsys):
    assert sess._adopt_command(["--help"], "terminal") == 0
    out = capsys.readouterr().out
    assert "--lead <tty>" in out and "list-terminal" in out


def test_list_terminal_command(capsys):
    rows = [{"unique_id": "/dev/ttys004", "tab_title": "Lead", "window_id": "12"}]
    with patch("tagteam.terminal.list_sessions", return_value=rows):
        assert sess._list_terminal_command() == 0
    out = capsys.readouterr().out
    assert "/dev/ttys004" in out and "Lead" in out and "12" in out
    with patch("tagteam.terminal.list_sessions", return_value=[]):
        assert sess._list_terminal_command() == 1
    assert "No Terminal.app tabs found" in capsys.readouterr().out


def test_session_command_list_terminal_and_kill_and_attach(monkeypatch, capsys):
    monkeypatch.setattr(sess, "default_backend", lambda: "terminal")
    with patch("tagteam.terminal.list_sessions", return_value=[]):
        assert sess.session_command(["list-terminal"]) == 1
    with patch("tagteam.terminal.kill_session", return_value=True) as kill:
        assert sess.session_command(["--backend", "terminal", "kill", "--dir", "/x"]) == 0
    kill.assert_called_once_with("/x")
    assert sess.session_command(["--backend", "terminal", "attach"]) == 0
    assert "not needed for Terminal.app" in capsys.readouterr().out


def test_session_command_kill_iterm2_still_uses_iterm_driver():
    with patch("tagteam.iterm.kill_session", return_value=True) as kill:
        assert sess.session_command(["--backend", "iterm2", "kill"]) == 0
    kill.assert_called_once_with(".")


def test_session_usage_mentions_terminal(capsys):
    sess._print_session_usage()
    out = capsys.readouterr().out
    assert "list-terminal" in out and "iterm2|tmux|terminal|manual" in out


def test_cli_backend_surface_has_terminal():
    from tagteam.cli import _BACKEND_SURFACE
    assert _BACKEND_SURFACE["terminal"] == "window"


# --- watcher: tab-driver helpers -------------------------------------------------

def _fake_driver(valid=True, write=True, contents="❯ "):
    d = MagicMock()
    d.session_id_is_valid.return_value = valid
    d.write_text_to_session.return_value = write
    d.get_session_contents.return_value = contents
    return d


def test_send_tab_command_uses_driver():
    d = _fake_driver()
    with patch("tagteam.watcher.time.sleep"):
        assert watcher_mod.send_tab_command(d, "/dev/ttys004", "/handoff") is True
    d.session_id_is_valid.assert_called_once_with("/dev/ttys004")
    d.write_text_to_session.assert_called_once_with("/dev/ttys004", "/handoff")


def test_send_tab_command_invalid_session_short_circuits():
    d = _fake_driver(valid=False)
    assert watcher_mod.send_tab_command(d, "/dev/ttys004", "x") is False
    d.write_text_to_session.assert_not_called()


def test_send_tab_command_retries_then_fails():
    d = _fake_driver(write=False)
    with patch("tagteam.watcher.time.sleep"):
        assert watcher_mod.send_tab_command(d, "sid", "x", max_retries=2, retry_delay=0) is False
    assert d.write_text_to_session.call_count == 2


def test_send_iterm_command_still_binds_iterm_driver():
    with patch("tagteam.iterm.session_id_is_valid", return_value=True), \
         patch("tagteam.iterm.get_session_contents", return_value="❯ "), \
         patch("tagteam.iterm.write_text_to_session", return_value=True) as w, \
         patch("tagteam.watcher.time.sleep"):
        assert watcher_mod.send_iterm_command("SID", "cmd") is True
    w.assert_called_once_with("SID", "cmd")


def test_send_terminal_command_binds_terminal_driver():
    with patch("tagteam.terminal.session_id_is_valid", return_value=True), \
         patch("tagteam.terminal.get_session_contents", return_value="❯ "), \
         patch("tagteam.terminal.write_text_to_session", return_value=True) as w, \
         patch("tagteam.watcher.time.sleep"):
        assert watcher_mod.send_terminal_command("/dev/ttys004", "cmd") is True
    w.assert_called_once_with("/dev/ttys004", "cmd")


def test_is_agent_idle_tab_uses_driver_contents():
    d = _fake_driver(contents="│ > ")
    with patch("tagteam.watcher._check_idle_patterns", return_value=True) as chk:
        assert watcher_mod.is_agent_idle_tab(d, "sid") is True
    chk.assert_called_once_with("│ > ")


# --- watcher: _StateProcessor in terminal mode --------------------------------------

def _make_processor(mode, **overrides):
    defaults = dict(
        mode=mode, lead_name="Claude", reviewer_name="Codex",
        lead_pane="tagteam:0.0", reviewer_pane="tagteam:0.2",
        lead_session_id="/dev/ttys001", reviewer_session_id="/dev/ttys002",
        confirm=False, timeout_minutes=30, project_dir=".",
        max_retries=3, retry_delay=2.0, pre_send_delay=1.0,
    )
    defaults.update(overrides)
    return _StateProcessor(**defaults)


def _state(seq, turn="lead", status="ready"):
    return {"seq": seq, "status": status, "turn": turn, "command": "/handoff",
            "phase": "p1", "round": 1, "updated_at": f"2026-05-03T00:00:{seq:02d}+00:00"}


def test_processor_terminal_mode_binds_terminal_driver_and_sends_via_send_tab_command():
    p = _make_processor("terminal")
    assert p.tabs.__name__ == "tagteam.terminal"
    with patch("tagteam.watcher.send_tab_command", return_value=True) as send, \
         patch("tagteam.watcher.send_iterm_command") as isend:
        p.tick(_state(1, turn="reviewer"))
    send.assert_called_once()
    driver, sid, cmd = send.call_args[0][:3]
    assert driver.__name__ == "tagteam.terminal" and sid == "/dev/ttys002" and "tagteam contract" in cmd and not cmd.startswith("/")
    isend.assert_not_called()


def test_processor_iterm2_mode_still_calls_send_iterm_command():
    p = _make_processor("iterm2", lead_session_id="L", reviewer_session_id="R")
    assert p.tabs.__name__ == "tagteam.iterm"
    with patch("tagteam.watcher.send_iterm_command", return_value=True) as send, \
         patch("tagteam.watcher.send_tab_command") as tsend:
        p.tick(_state(1, turn="lead"))
    send.assert_called_once()
    assert send.call_args[0][0] == "L"
    tsend.assert_not_called()


def test_processor_other_modes_have_no_driver():
    assert _make_processor("notify", lead_session_id=None, reviewer_session_id=None).tabs is None
    assert _make_processor("tmux", lead_session_id=None, reviewer_session_id=None).tabs is None


def test_capture_tail_terminal_mode_reads_terminal_contents():
    p = _make_processor("terminal")
    with patch("tagteam.terminal.get_session_contents", return_value="line1\n❯ ") as get:
        assert p._capture_tail({"turn": "lead"}) == "line1\n❯ "
    get.assert_called_once()
    assert get.call_args[0][0] == "/dev/ttys001"
    with patch("tagteam.terminal.get_session_contents", return_value=""):
        assert p._capture_tail({"turn": "reviewer"}) is None


# --- watcher: auto-detect + --mode/file mismatch --------------------------------------

def _both_roles(backend=None):
    d = {"tabs": {"lead": {"session_id": "L"}, "reviewer": {"session_id": "R"}}}
    if backend:
        d["backend"] = backend
    return d


def test_auto_detect_terminal_from_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr("tagteam.session.session_exists", lambda: False)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles("terminal")))
    mode, reason = watcher_mod._auto_detect_mode(str(tmp_path))
    assert mode == "terminal" and "terminal" in reason


def test_auto_detect_iterm2_when_backend_key_missing_or_iterm2(tmp_path, monkeypatch):
    monkeypatch.setattr("tagteam.session.session_exists", lambda: False)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles()))
    assert watcher_mod._auto_detect_mode(str(tmp_path))[0] == "iterm2"
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles("iterm2")))
    assert watcher_mod._auto_detect_mode(str(tmp_path))[0] == "iterm2"


def test_auto_detect_ignores_non_tab_backend_file(tmp_path, monkeypatch):
    monkeypatch.setattr("tagteam.session.session_exists", lambda: False)
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles("tmux")))
    mode, reason = watcher_mod._auto_detect_mode(str(tmp_path))
    assert mode == "notify" and "iterm2/terminal" in reason


def _build(mode, project_dir):
    return watcher_mod._build_processor(
        mode=mode, lead_pane="a", reviewer_pane="b", confirm=False, timeout_minutes=30,
        project_dir=str(project_dir), max_retries=1, retry_delay=0.0, pre_send_delay=0.0)


def test_build_processor_rejects_mode_file_mismatch(tmp_path, capsys):
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles("terminal")))
    assert _build("iterm2", tmp_path) is None
    out = capsys.readouterr().out
    assert "written by the 'terminal' backend" in out and "--mode iterm2 was requested" in out
    assert "tagteam watch --mode terminal" in out


def test_build_processor_terminal_reads_ids(tmp_path):
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles("terminal")))
    p = _build("terminal", tmp_path)
    assert p is not None and p.mode == "terminal"
    assert (p.lead_session_id, p.reviewer_session_id) == ("L", "R")
    assert p.tabs.__name__ == "tagteam.terminal"


def test_build_processor_iterm2_accepts_legacy_file(tmp_path):
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: A\n  reviewer:\n    name: B\n")
    (tmp_path / ".handoff-session.json").write_text(json.dumps(_both_roles()))
    p = _build("iterm2", tmp_path)
    assert p is not None and p.tabs.__name__ == "tagteam.iterm"


def test_watch_cli_accepts_terminal_mode(monkeypatch, capsys):
    """`tagteam watch --mode terminal` is a valid mode (help lists it)."""
    from tagteam.watcher import watch_command
    rc = watch_command(["--help"])
    assert rc == 0
    assert "'terminal'" in capsys.readouterr().out
    rc = watch_command(["--mode", "bogus"])
    assert rc == 1
    assert "'terminal'" in capsys.readouterr().out


def test_launch_start_watcher_accepts_terminal_mode(tmp_path):
    from tagteam import launch
    with patch("tagteam.cockpit_api.watcher_status", return_value={"running": False}), \
         patch("tagteam.launch.subprocess.Popen", side_effect=OSError("nope")):
        res = launch.start_watcher(tmp_path, mode="terminal", wait_s=0)
    # got past mode validation and tried to spawn
    assert res["ok"] is False and "could not start the watcher" in res["message"]


# --- server log tail + state health pick the driver from the file -----------------------

def test_server_pane_logs_use_terminal_driver(tmp_path, monkeypatch):
    from tagteam import server
    monkeypatch.setattr("tagteam.session.default_backend", lambda: "iterm2")
    (tmp_path / ".handoff-session.json").write_text(json.dumps({
        "backend": "terminal",
        "tabs": {"lead": {"session_id": "/dev/ttys001"}, "watcher": {"session_id": "/dev/ttys002"},
                 "reviewer": {"session_id": "/dev/ttys003"}}}))
    with patch("tagteam.terminal.session_id_is_valid", side_effect=lambda s: s != "/dev/ttys003"), \
         patch("tagteam.terminal.get_session_contents", return_value="tail") as get, \
         patch("tagteam.iterm.session_id_is_valid") as ivalid:
        res = server._get_pane_logs(str(tmp_path), n=5)
    ivalid.assert_not_called()
    assert res["backend"] == "terminal"
    assert res["lead"] == {"available": True, "content": "tail"}
    assert res["reviewer"] == {"available": False, "reason": "dead-session"}
    assert get.call_count == 2


def test_server_pane_logs_iterm2_file_without_backend_key(tmp_path, monkeypatch):
    from tagteam import server
    monkeypatch.setattr("tagteam.session.default_backend", lambda: "iterm2")
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"tabs": {"lead": {"session_id": "A"}}}))
    with patch("tagteam.iterm.session_id_is_valid", return_value=True), \
         patch("tagteam.iterm.get_session_contents", return_value="x"):
        res = server._get_pane_logs(str(tmp_path), n=5)
    assert res["backend"] == "iterm2" and res["lead"]["available"] is True


def test_state_health_uses_terminal_driver(tmp_path):
    from tagteam.state import _check_agent_health
    (tmp_path / ".handoff-session.json").write_text(json.dumps({
        "backend": "terminal",
        "tabs": {"lead": {"session_id": "/dev/ttys001"}, "reviewer": {"session_id": "/dev/ttys002"}}}))
    lines = []
    with patch("tagteam.terminal.session_id_is_valid", side_effect=lambda s: s == "/dev/ttys001"), \
         patch("tagteam.terminal.get_session_contents", return_value="❯ "), \
         patch("tagteam.iterm.session_id_is_valid") as ivalid:
        _check_agent_health(lines, str(tmp_path))
    ivalid.assert_not_called()
    assert any("lead: IDLE" in l for l in lines)
    assert any("reviewer: session /dev/ttys002 not found" in l for l in lines)
