"""Tests for tagteam/terminal.py — the Terminal.app session backend.

No AppleScript runs: `_osascript` is replaced by a scripted fake that models
Terminal.app's window list well enough to exercise the deterministic window
accounting rule (plan §A.1) and the per-tab operations keyed by tty.
"""

import json
import re
from unittest.mock import patch

import pytest

from tagteam import terminal
from tagteam.tabs import SUBMIT_DELAY_S


class FakeTerminal:
    """Answers the AppleScript snippets terminal.py emits.

    `windows` is the live list of window ids. Untargeted `do script` opens a
    new window (or `extra_on_create + 1` windows, to model interference); a
    targeted one reuses its window unless `ignore_target`.
    """

    def __init__(self, windows=(), *, busy=False, ignore_target=False,
                 extra_on_create=0, contents=None):
        self.windows = list(windows)
        self.next_id = (max(self.windows) if self.windows else 100) + 1
        self.busy = busy
        self.ignore_target = ignore_target
        self.extra_on_create = extra_on_create
        self.contents = contents or {}
        self.calls = []
        self.do_scripts = []      # (target_window_id | None, text)
        self.tab_do_scripts = []  # (tty, literal) via the repeat-loop path
        self.closed = []
        self.titles = {}
        self.bounds = {}

    def _new_window(self):
        wid = self.next_id
        self.next_id += 1
        self.windows.append(wid)
        return wid

    @staticmethod
    def tty_for(wid):
        return f"/dev/ttys{wid:03d}"

    def _tab_by_tty(self, script):
        m = re.search(r'if tty of tab i of w is "([^"]+)"', script)
        tty = m.group(1)
        for wid in self.windows:
            if self.tty_for(wid) == tty:
                return wid, tty
        return None, tty

    def __call__(self, script, timeout=None):
        self.calls.append(script)
        s = script.strip()
        if "repeat with i from 1 to (count of tabs of w)" in s:
            wid, tty = self._tab_by_tty(s)
            if "return contents of tab i of w" in s:
                return self.contents.get(tty, "") if wid else ""
            if 'return "found"' in s:
                return "found" if wid else "not_found"
            if "close tab i of w saving no" in s:
                if wid:
                    self.windows.remove(wid)
                    self.closed.append(wid)
                    return "ok"
                return "not_found"
            if "do script" in s:
                if wid:
                    for lit in re.findall(r"do script (.*) in tab i of w", s):
                        self.tab_do_scripts.append((tty, lit))
                    return "ok"
                return "not_found"
            raise AssertionError(f"unexpected tab script: {s}")
        if "count windows" in s:
            return str(len(self.windows))
        if "id of every window" in s:
            return ", ".join(str(w) for w in self.windows)
        if "busy of selected tab" in s:
            return "true" if self.busy else "false"
        if s.startswith('tell application "Terminal" to do script'):
            m = re.search(r'do script "(.*)"(?: in window id (\d+))?$', s)
            text, wid = m.group(1), m.group(2)
            target = int(wid) if wid else None
            self.do_scripts.append((target, text))
            if target is None or self.ignore_target:
                for _ in range(1 + self.extra_on_create):
                    self._new_window()
            return ""
        if "tty of selected tab of window id" in s:
            wid = int(re.search(r"window id (\d+)", s).group(1))
            return self.tty_for(wid)
        if "set custom title" in s:
            wid = int(re.search(r"window id (\d+)", s).group(1))
            self.titles[wid] = re.search(r'to "([^"]*)"$', s).group(1)
            return ""
        if "close window id" in s:
            wid = int(re.search(r"window id (\d+)", s).group(1))
            if wid in self.windows:
                self.windows.remove(wid)
            self.closed.append(wid)
            return ""
        if "set bounds of window id" in s:
            wid = int(re.search(r"window id (\d+)", s).group(1))
            self.bounds[wid] = s
            return ""
        if "bounds of window id" in s:
            return "0, 23, 800, 600"
        if s == 'tell application "Terminal" to activate':
            return ""
        raise AssertionError(f"unexpected script: {s}")


def _create(tmp_path, fake, was_running):
    with patch("tagteam.terminal._osascript", fake), \
         patch("tagteam.terminal.terminal_is_running", return_value=was_running), \
         patch("tagteam.terminal._ensure_terminal_ready", return_value=None):
        ok = terminal.create_session(str(tmp_path), launch=False)
    data = None
    f = tmp_path / ".handoff-session.json"
    if f.exists():
        data = json.loads(f.read_text())
    return ok, data


def _assert_three(data):
    tabs = data["tabs"]
    assert set(tabs) == {"lead", "watcher", "reviewer"}
    ttys = [tabs[r]["session_id"] for r in ("lead", "watcher", "reviewer")]
    wids = [tabs[r]["window_id"] for r in ("lead", "watcher", "reviewer")]
    assert len(set(ttys)) == 3 and all(t.startswith("/dev/ttys") for t in ttys)
    assert len(set(wids)) == 3
    return ttys, wids


# --- create_session: window accounting (§A.1) -------------------------------

def test_cold_launch_reuses_single_idle_launch_window(tmp_path, capsys):
    fake = FakeTerminal(windows=[7])           # Terminal's own launch window
    ok, data = _create(tmp_path, fake, was_running=False)
    assert ok is True and data["backend"] == "terminal"
    ttys, wids = _assert_three(data)
    # Lead was created targeted at the launch window; two untargeted creations
    assert fake.do_scripts[0] == (7, f"cd {tmp_path.resolve()}")
    assert [t for t, _ in fake.do_scripts[1:]] == [None, None]
    assert data["tabs"]["lead"]["window_id"] == 7
    # exactly three windows exist — no untracked fourth
    assert sorted(fake.windows) == sorted(wids)
    assert fake.closed == []
    assert fake.titles == {wids[0]: "Lead", wids[1]: "Watcher", wids[2]: "Reviewer"}
    assert "note:" not in capsys.readouterr().out


def test_warm_launch_never_targets_or_closes_existing_windows(tmp_path):
    fake = FakeTerminal(windows=[3, 4])        # the user's own windows
    ok, data = _create(tmp_path, fake, was_running=True)
    assert ok is True
    ttys, wids = _assert_three(data)
    assert all(t is None for t, _ in fake.do_scripts)
    assert not ({3, 4} & set(wids))
    assert 3 in fake.windows and 4 in fake.windows
    assert fake.closed == []
    assert not any("busy of selected tab" in c for c in fake.calls)


@pytest.mark.parametrize("windows,busy", [([7, 8], False), ([], False), ([7], True)])
def test_cold_launch_without_unambiguous_launch_window_creates_three(tmp_path, windows, busy):
    fake = FakeTerminal(windows=windows, busy=busy)
    ok, data = _create(tmp_path, fake, was_running=False)
    assert ok is True
    ttys, wids = _assert_three(data)
    assert all(t is None for t, _ in fake.do_scripts)
    for pre in windows:
        assert pre not in wids and pre in fake.windows
    assert fake.closed == []


def test_target_ignored_new_window_becomes_lead_and_launch_window_left_alone(tmp_path, capsys):
    fake = FakeTerminal(windows=[7], ignore_target=True)
    ok, data = _create(tmp_path, fake, was_running=False)
    assert ok is True
    ttys, wids = _assert_three(data)
    assert fake.do_scripts[0][0] == 7                # we did target it
    assert data["tabs"]["lead"]["window_id"] != 7    # but adopted the new one
    assert 7 in fake.windows and 7 not in fake.closed
    assert "note: Terminal.app opened a new window for the Lead" in capsys.readouterr().out


def test_unexpected_window_count_closes_created_and_writes_no_file(tmp_path, capsys):
    fake = FakeTerminal(windows=[3], extra_on_create=1)   # two windows per do script
    ok, data = _create(tmp_path, fake, was_running=True)
    assert ok is False and data is None
    out = capsys.readouterr().out
    assert "Error creating Terminal.app session" in out and "expected one new window" in out
    # everything we opened is closed again; the user's window survives
    assert fake.windows == [3]
    assert 3 not in fake.closed


def test_create_refuses_when_live_session_exists(tmp_path, capsys):
    fake = FakeTerminal(windows=[9])
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "terminal", "tabs": {"lead": {"session_id": FakeTerminal.tty_for(9)}}}))
    ok, _ = _create(tmp_path, fake, was_running=True)
    assert ok is False
    assert "Session file already exists" in capsys.readouterr().out
    assert fake.do_scripts == []


def test_create_replaces_stale_session_file(tmp_path, capsys):
    fake = FakeTerminal(windows=[9])
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "terminal", "tabs": {"lead": {"session_id": "/dev/ttys999"}}}))
    ok, data = _create(tmp_path, fake, was_running=True)
    assert ok is True and data["backend"] == "terminal"
    assert "Stale session file" in capsys.readouterr().out


def test_create_reports_launch_failure_with_automation_hint(tmp_path, capsys):
    with patch("tagteam.terminal.terminal_is_running", return_value=False), \
         patch("tagteam.terminal._ensure_terminal_ready",
               side_effect=RuntimeError("Terminal.app scripting did not become ready")):
        assert terminal.create_session(str(tmp_path)) is False
    out = capsys.readouterr().out
    assert "Terminal.app failed to launch" in out
    assert "Automation" in out and "--backend iterm2" in out


# --- per-tab operations -----------------------------------------------------

def test_write_text_targets_tab_by_tty_and_escapes():
    fake = FakeTerminal(windows=[5])
    tty = FakeTerminal.tty_for(5)
    with patch("tagteam.terminal._osascript", fake):
        assert terminal.write_text_to_session(tty, 'say "hi" \\ there') is True
        assert terminal.write_text_to_session("/dev/ttys777", "x") is False
    # two do scripts in one call: the escaped text, then the empty submit
    assert fake.tab_do_scripts == [(tty, '"say \\"hi\\" \\\\ there"'), (tty, '""')]
    text_script = fake.calls[0]
    assert text_script.index('do script "say') < text_script.index("delay ") < text_script.index('do script ""')
    # Phase 74c: the pause is the shared named gap, not a literal
    assert f"delay {SUBMIT_DELAY_S}\n" in text_script
    assert SUBMIT_DELAY_S == 0.5


def test_submit_sends_one_lone_newline_to_the_tab():
    fake = FakeTerminal(windows=[5])
    tty = FakeTerminal.tty_for(5)
    with patch("tagteam.terminal._osascript", fake):
        assert terminal.submit(tty) is True
        assert terminal.submit("/dev/ttys777") is False
    assert fake.tab_do_scripts == [(tty, '""')]
    with patch("tagteam.terminal._osascript", side_effect=RuntimeError("x")):
        assert terminal.submit(tty) is False


def test_get_session_contents_tail_and_validity():
    fake = FakeTerminal(windows=[5], contents={FakeTerminal.tty_for(5): "a\nb\nc\nd"})
    tty = FakeTerminal.tty_for(5)
    with patch("tagteam.terminal._osascript", fake):
        assert terminal.get_session_contents(tty, last_n_lines=2) == "c\nd"
        assert terminal.get_session_contents("/dev/ttys777") == ""
        assert terminal.session_id_is_valid(tty) is True
        assert terminal.session_id_is_valid("/dev/ttys777") is False


def test_get_session_contents_swallows_osascript_errors():
    with patch("tagteam.terminal._osascript", side_effect=RuntimeError("boom")):
        assert terminal.get_session_contents("/dev/ttys001") == ""
        assert terminal.session_id_is_valid("/dev/ttys001") is False
        assert terminal.write_text_to_session("/dev/ttys001", "x") is False


def test_any_session_alive_requires_running_terminal_and_live_tty():
    data = {"tabs": {"lead": {"session_id": FakeTerminal.tty_for(5)}}}
    fake = FakeTerminal(windows=[5])
    with patch("tagteam.terminal._osascript", fake), \
         patch("tagteam.terminal.terminal_is_running", return_value=False):
        assert terminal._any_session_alive(data) is False
    with patch("tagteam.terminal._osascript", fake), \
         patch("tagteam.terminal.terminal_is_running", return_value=True):
        assert terminal._any_session_alive(data) is True
        assert terminal._any_session_alive({"tabs": {"lead": {"session_id": "/dev/ttys9"}}}) is False


def test_kill_session_closes_recorded_ttys_only_and_removes_file(tmp_path, capsys):
    fake = FakeTerminal(windows=[5, 6, 7, 8])
    (tmp_path / ".handoff-session.json").write_text(json.dumps({
        "backend": "terminal",
        "tabs": {r: {"session_id": FakeTerminal.tty_for(w)} for r, w in
                 (("lead", 5), ("watcher", 6), ("reviewer", 7))},
    }))
    with patch("tagteam.terminal._osascript", fake), \
         patch("tagteam.terminal.subprocess.run") as run:
        assert terminal.kill_session(str(tmp_path)) is True
    # processes on each recorded tty are hung up before the close
    assert [c.args[0] for c in run.call_args_list] == [
        ["pkill", "-HUP", "-t", f"ttys{w:03d}"] for w in (5, 6, 7)]
    assert sorted(fake.closed) == [5, 6, 7]
    assert fake.windows == [8]
    assert not (tmp_path / ".handoff-session.json").exists()
    assert "Terminal.app session killed" in capsys.readouterr().out


def test_kill_session_without_file(tmp_path, capsys):
    assert terminal.kill_session(str(tmp_path)) is False
    assert "No session file found" in capsys.readouterr().out


def test_list_sessions_parses_rows():
    raw = "/dev/ttys001|Lead|12\n/dev/ttys002|zsh|13\n\nbad line\n"
    with patch("tagteam.terminal._osascript", return_value=raw):
        rows = terminal.list_sessions()
    assert rows == [
        {"unique_id": "/dev/ttys001", "tab_title": "Lead", "window_id": "12"},
        {"unique_id": "/dev/ttys002", "tab_title": "zsh", "window_id": "13"},
    ]
    with patch("tagteam.terminal._osascript", side_effect=RuntimeError("x")):
        assert terminal.list_sessions() == []


def test_get_session_id_reads_role(tmp_path):
    (tmp_path / ".handoff-session.json").write_text(json.dumps(
        {"backend": "terminal", "tabs": {"lead": {"session_id": "/dev/ttys004"}}}))
    assert terminal.get_session_id("lead", str(tmp_path)) == "/dev/ttys004"
    assert terminal.get_session_id("reviewer", str(tmp_path)) is None


def test_parse_id_list_handles_braces_and_negatives():
    assert terminal._parse_id_list("12, 15") == [12, 15]
    assert terminal._parse_id_list("{-100, 23, 800, 600}") == [-100, 23, 800, 600]
    assert terminal._parse_id_list("") == []


def test_ensure_ready_launches_and_polls():
    calls = []
    answers = iter([RuntimeError("not yet"), "1"])

    def fake_osa(script, timeout=None):
        calls.append(script)
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    with patch("tagteam.terminal.terminal_is_running", return_value=False), \
         patch("tagteam.terminal._launch_terminal_via_launchservices") as launch, \
         patch("tagteam.terminal._osascript", fake_osa), \
         patch("tagteam.terminal.time.sleep"):
        terminal._ensure_terminal_ready()
    launch.assert_called_once()
    assert calls == [terminal._TERMINAL_READY_PROBE] * 2


def test_ensure_ready_times_out():
    with patch("tagteam.terminal.terminal_is_running", return_value=True), \
         patch("tagteam.terminal._osascript", side_effect=RuntimeError("no")), \
         patch("tagteam.terminal.time.sleep"), \
         patch("tagteam.terminal.time.monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
        with pytest.raises(RuntimeError, match="did not become ready"):
            terminal._ensure_terminal_ready()


def test_launch_sends_commands_after_file_and_primes_when_ready(tmp_path, monkeypatch):
    """launch=True: agent commands, watcher command (mode terminal), then the
    prime once each agent's prompt is visible — file written first."""
    fake = FakeTerminal(windows=[7])
    order = []
    (tmp_path / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n")

    def fake_write(sid, text):
        order.append((sid, text, (tmp_path / ".handoff-session.json").exists()))
        return True

    monkeypatch.setattr("tagteam.session._read_launch_commands", lambda d: ("claude", "codex"))
    with patch("tagteam.terminal._osascript", fake), \
         patch("tagteam.terminal.terminal_is_running", return_value=False), \
         patch("tagteam.terminal._ensure_terminal_ready", return_value=None), \
         patch("tagteam.terminal.write_text_to_session", fake_write), \
         patch("tagteam.session.wait_for_agent_ready", return_value=True) as ready:
        assert terminal.create_session(str(tmp_path), launch=True) is True
    from tagteam.session import PRIME_MESSAGE
    assert all(existed for _, _, existed in order)
    texts = [t for _, t, _ in order]
    assert texts[0] == "claude" and texts[1] == "codex"
    assert texts[2].endswith("watch --mode terminal")
    assert texts[3:] == [PRIME_MESSAGE, PRIME_MESSAGE]
    assert ready.call_count == 2
