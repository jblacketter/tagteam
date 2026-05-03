"""Tests for `tagteam session adopt` and `tagteam session list-iterm`.

Adopt writes the EXACT same .handoff-session.json schema that
session start --launch produces (`{"backend": "iterm2", "tabs": {role:
{"session_id": id}}}`), so all existing consumers (watcher
auto-detect, iterm.get_session_id, _any_session_alive, server log-tail)
work unchanged after adopt. The schema regression is covered explicitly.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tagteam.session import _adopt_command, _list_iterm_command


def _adopt(args, project_dir):
    return _adopt_command(args + ["--dir", str(project_dir)], "iterm2")


def test_adopt_writes_correct_schema(tmp_path):
    """Schema MUST match what session start --launch writes
    (iterm.py:226-234), or downstream consumers break."""
    with patch("tagteam.iterm.session_id_is_valid", return_value=True):
        rc = _adopt(["--lead", "L1", "--reviewer", "R1", "--watcher", "W1"],
                    tmp_path)
    assert rc == 0
    payload = json.loads((tmp_path / ".handoff-session.json").read_text())
    assert payload == {
        "backend": "iterm2",
        "tabs": {
            "lead": {"session_id": "L1"},
            "watcher": {"session_id": "W1"},
            "reviewer": {"session_id": "R1"},
        },
    }


def test_adopt_lead_only_omits_optional_roles(tmp_path):
    with patch("tagteam.iterm.session_id_is_valid", return_value=True):
        rc = _adopt(["--lead", "L1"], tmp_path)
    assert rc == 0
    payload = json.loads((tmp_path / ".handoff-session.json").read_text())
    assert payload["tabs"] == {"lead": {"session_id": "L1"}}


def test_adopt_requires_lead(tmp_path, capsys):
    rc = _adopt(["--reviewer", "R1"], tmp_path)
    assert rc == 1
    assert "--lead is required" in capsys.readouterr().out


def test_adopt_validates_live_session(tmp_path, capsys):
    with patch("tagteam.iterm.session_id_is_valid", return_value=False):
        rc = _adopt(["--lead", "DEADBEEF"], tmp_path)
    assert rc == 1
    assert "not a live iTerm2 session" in capsys.readouterr().out


def test_adopt_refuses_to_overwrite_without_force(tmp_path, capsys):
    (tmp_path / ".handoff-session.json").write_text("{}")
    with patch("tagteam.iterm.session_id_is_valid", return_value=True):
        rc = _adopt(["--lead", "L1"], tmp_path)
    assert rc == 1
    assert "--force" in capsys.readouterr().out


def test_adopt_force_overwrites_existing(tmp_path):
    (tmp_path / ".handoff-session.json").write_text('{"backend":"old"}')
    with patch("tagteam.iterm.session_id_is_valid", return_value=True):
        rc = _adopt(["--lead", "L1", "--force"], tmp_path)
    assert rc == 0
    payload = json.loads((tmp_path / ".handoff-session.json").read_text())
    assert payload["backend"] == "iterm2"


def test_adopt_rejects_non_iterm2_backend(capsys):
    rc = _adopt_command(["--lead", "L1"], "tmux")
    assert rc == 1
    assert "iterm2 backend" in capsys.readouterr().out


def test_adopt_unknown_arg_errors(tmp_path, capsys):
    rc = _adopt(["--lead", "L1", "--bogus"], tmp_path)
    assert rc == 1
    assert "Unknown arg" in capsys.readouterr().out


# --- list-iterm ---

def test_list_iterm_prints_sessions(capsys):
    fake = [
        {"unique_id": "ABC", "tab_title": "Lead", "window_id": "1"},
        {"unique_id": "DEF", "tab_title": "Reviewer", "window_id": "1"},
    ]
    with patch("tagteam.iterm.list_iterm_sessions", return_value=fake):
        rc = _list_iterm_command()
    out = capsys.readouterr().out
    assert rc == 0
    assert "ABC" in out and "DEF" in out
    assert "Lead" in out and "Reviewer" in out


def test_list_iterm_empty_returns_error(capsys):
    with patch("tagteam.iterm.list_iterm_sessions", return_value=[]):
        rc = _list_iterm_command()
    assert rc == 1
    assert "No iTerm2 sessions" in capsys.readouterr().out


# --- watcher auto-detect smoke test (closes the regression loop) ---

def test_watcher_auto_detect_picks_iterm2_after_adopt(tmp_path):
    """End-to-end: after `session adopt --lead X --reviewer Y`,
    `_auto_detect_mode` (Phase 29) returns 'iterm2'. This is the
    regression guard for the schema mismatch caught in plan review."""
    from tagteam.watcher import _auto_detect_mode

    with patch("tagteam.iterm.session_id_is_valid", return_value=True):
        _adopt(["--lead", "L1", "--reviewer", "R1"], tmp_path)

    mode, reason = _auto_detect_mode(str(tmp_path))
    assert mode == "iterm2"
    assert "iterm2 session IDs found" in reason
