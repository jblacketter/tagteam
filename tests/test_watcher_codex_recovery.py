"""Phase 74c: the recovery CR for a message Codex left in its composer.

Fixtures under tests/fixtures/codex_tails/ are live captures from an iTerm2
tab (80 columns) running codex-cli 0.157.0 (one is a Claude Code pane), taken
with `iterm.get_session_contents(sid, 16)` — the watcher's own read — and not
edited by hand. Each composer state was produced by typing into the live
composer; the stuck positive by sending the real turn message with no gap
before the CR.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tagteam import watcher as watcher_mod
from tagteam.contract import STANDARD_TURN_COMMAND
from tagteam.watcher import (
    RECOVERY_CAPTURE_LINES,
    _matches_displayed_lines,
    codex_stuck_composer,
)

TAILS = Path(__file__).parent / "fixtures" / "codex_tails"
POSITIVE = "stuck_wrapped.txt"
NEGATIVES = [
    "submitted_history_working.txt",      # a submitted prompt + Working line
    "working_above_matching_composer.txt",  # Working above the exact command
    "busy_queue_hint_matching_composer.txt",  # streaming output, exact command
    "idle_placeholder_after_answer.txt",  # history + answer + empty composer
    "approval_dialog.txt",
    "edited_text.txt",                    # the command + " now"
    "unrelated_text.txt",
    "whitespace_edit_in_line.txt",        # "then act" -> "thenact"
    "incomplete_starts_in_composer.txt",  # a 6-line read: nothing above `›`
    "claude_code_pane.txt",               # the command typed into Claude Code
    "empty.txt",
]


def _tail(name: str) -> str:
    return (TAILS / name).read_text(encoding="utf-8")


def test_every_fixture_is_classified():
    assert sorted(p.name for p in TAILS.iterdir()) == sorted([POSITIVE] + NEGATIVES)


def test_the_stuck_wrapped_command_is_recognized():
    tail = _tail(POSITIVE)
    assert "Claude Code: /" in tail and "  tagteam:handoff)" in tail  # wraps mid-token
    assert codex_stuck_composer(tail, STANDARD_TURN_COMMAND) is True


@pytest.mark.parametrize("name", NEGATIVES)
def test_negative_captures_are_rejected(name):
    assert codex_stuck_composer(_tail(name), STANDARD_TURN_COMMAND) is False


def test_the_busy_veto_alone_rejects_a_working_line_above_a_matching_composer():
    # The live capture's last line is Codex's busy hint ("tab to queue
    # message"), which the footer rule already rejects. Derived variant (in
    # the test, not the fixture): drop that line so composer + footer match
    # exactly, and only the Working line above them remains to reject it.
    lines = _tail("working_above_matching_composer.txt").splitlines()
    assert "tab to queue message" in lines[-1]
    variant = lines[:-1]
    assert any("Working (" in line for line in variant)
    assert codex_stuck_composer("\n".join(variant), STANDARD_TURN_COMMAND) is False
    without_working = [line for line in variant if "Working (" not in line]
    assert codex_stuck_composer("\n".join(without_working), STANDARD_TURN_COMMAND) is True


def test_the_whitespace_edit_differs_from_the_positive_only_by_that_edit():
    edited = STANDARD_TURN_COMMAND.replace("then act", "thenact")
    assert codex_stuck_composer(_tail("whitespace_edit_in_line.txt"), edited) is True
    assert codex_stuck_composer(_tail("whitespace_edit_in_line.txt"), STANDARD_TURN_COMMAND) is False


def test_a_different_command_does_not_match_the_positive():
    assert codex_stuck_composer(_tail(POSITIVE), STANDARD_TURN_COMMAND + " now") is False
    assert codex_stuck_composer(_tail(POSITIVE), "Read the handoff contract") is False


@pytest.mark.parametrize("bad", [None, 42, MagicMock()])
def test_a_non_text_capture_is_rejected(bad):
    assert codex_stuck_composer(bad, STANDARD_TURN_COMMAND) is False


# --- the line-preserving matcher ---------------------------------------------

def test_a_wrap_at_a_dropped_space():
    assert _matches_displayed_lines(["then act", "on your turn"], "then act on your turn")


def test_a_wrap_mid_token():
    assert _matches_displayed_lines(["Claude Code: /", "tagteam:handoff)"],
                                    "Claude Code: /tagteam:handoff)")


def test_only_one_space_may_hide_at_a_wrap():
    assert not _matches_displayed_lines(["then act", "on your turn"], "then act  on your turn")


@pytest.mark.parametrize("pieces", [
    ["thenact on your turn"],      # a space removed inside a line
    ["then  act on your turn"],    # a space added inside a line
    ["then act on your tur"],      # a character missing
    ["then act on your turn!"],    # a character added
    ["then act", "on your turn", "x"],  # text left over after the command
])
def test_any_edit_inside_a_line_fails(pieces):
    assert not _matches_displayed_lines(pieces, "then act on your turn")


def test_the_command_must_be_consumed_whole():
    assert not _matches_displayed_lines(["then act"], "then act on your turn")


# --- send_tab_command: at most one recovery CR --------------------------------

def _driver(contents: str, submit_ok: bool = True):
    d = MagicMock()
    d.session_id_is_valid.return_value = True
    d.write_text_to_session.return_value = True
    d.get_session_contents.return_value = contents
    d.submit.return_value = submit_ok
    return d


def _send(driver) -> bool:
    with patch("tagteam.watcher.time.sleep"), \
         patch("tagteam.watcher.wait_for_idle_tab", return_value=True):
        return watcher_mod.send_tab_command(driver, "sid", STANDARD_TURN_COMMAND)


def test_the_positive_gets_exactly_one_recovery_cr():
    d = _driver(_tail(POSITIVE))
    assert _send(d) is True
    d.submit.assert_called_once_with("sid")
    d.write_text_to_session.assert_called_once_with("sid", STANDARD_TURN_COMMAND)
    d.get_session_contents.assert_called_once_with("sid", last_n_lines=RECOVERY_CAPTURE_LINES)


@pytest.mark.parametrize("name", NEGATIVES)
def test_no_recovery_cr_for_a_negative(name):
    d = _driver(_tail(name))
    assert _send(d) is True
    d.submit.assert_not_called()


def test_a_failed_recovery_cr_is_logged_and_not_retried(capsys):
    d = _driver(_tail(POSITIVE), submit_ok=False)
    assert _send(d) is True
    d.submit.assert_called_once_with("sid")
    assert "the extra Enter failed" in capsys.readouterr().out


def test_a_failing_read_sends_no_recovery_cr():
    d = _driver("")
    d.get_session_contents.side_effect = RuntimeError("osascript failed")
    assert _send(d) is True
    d.submit.assert_not_called()


def test_no_read_and_no_cr_when_the_write_failed():
    d = _driver(_tail(POSITIVE))
    d.write_text_to_session.return_value = False
    with patch("tagteam.watcher.time.sleep"), \
         patch("tagteam.watcher.wait_for_idle_tab", return_value=True):
        assert watcher_mod.send_tab_command(d, "sid", STANDARD_TURN_COMMAND,
                                            max_retries=2, retry_delay=0) is False
    d.submit.assert_not_called()


def test_the_recovery_is_logged_as_a_sent_event():
    d = _driver(_tail(POSITIVE))
    with patch("tagteam.watcher._log") as log:
        _send(d)
    kinds = [c.kwargs.get("kind") for c in log.call_args_list
             if "composer" in c.args[0]]
    assert kinds == ["sent"]
