"""Phase 48 test helper: a fake `claude` CLI whose `plugin list --json`
prints the records a test wants. Injected through TAGTEAM_CLAUDE_BIN so
tests exercise the real reader (subprocess, cwd, parsing, scope rules)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from tagteam.plugin import PLUGIN_KEY

REPO = Path(__file__).resolve().parents[1]
PLUGIN_SRC = REPO / "plugin"


def install_tree(tmp_path: Path, *, broken: bool = False) -> Path:
    """A copy of this repo's plugin tree, as Claude Code would cache it."""
    install = tmp_path / "claude" / "plugins" / "cache" / "tagteam" / "tagteam" / "9.9.9"
    (install / "skills" / "handoff").mkdir(parents=True, exist_ok=True)
    if not broken:
        (install / "skills" / "handoff" / "SKILL.md").write_bytes(
            (PLUGIN_SRC / "skills" / "handoff" / "SKILL.md").read_bytes())
    (install / ".claude-plugin").mkdir(exist_ok=True)
    (install / ".claude-plugin" / "plugin.json").write_bytes(
        (PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_bytes())
    return install


def fake_claude(tmp_path: Path, monkeypatch, *, stdout: str, exit_code: int = 0,
                sleep: float = 0.0) -> Path:
    """Write a fake `claude` executable and point TAGTEAM_CLAUDE_BIN at it."""
    bin_dir = tmp_path / "fakebin"; bin_dir.mkdir(exist_ok=True)
    payload = bin_dir / "plugin-list.json"
    payload.write_text(stdout, encoding="utf-8")
    exe = bin_dir / "claude"
    exe.write_text("#!/bin/sh\n"
                   f"[ \"$1\" = plugin ] && [ \"$2\" = list ] || exit 64\n"
                   f"sleep {sleep}\n"
                   f"cat {payload}\n"
                   f"exit {exit_code}\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", str(exe))
    return exe


def fake_plugin(tmp_path: Path, monkeypatch, *, scope: str = "user",
                project_path: Path | None = None, enabled: bool | None = True,
                broken: bool = False, extra_records: list | None = None,
                exit_code: int = 0, stdout: str | None = None) -> Path:
    """The tagteam plugin as `claude plugin list --json` would report it.
    Returns the install path."""
    install = install_tree(tmp_path, broken=broken)
    rec = {"id": PLUGIN_KEY, "version": "9.9.9", "scope": scope, "enabled": enabled,
           "installPath": str(install)}
    if scope in ("project", "local"):
        rec["projectPath"] = str(project_path)
    records = [{"id": "other@somewhere", "scope": "user", "enabled": True,
                "installPath": str(tmp_path / "nope")}, rec] + (extra_records or [])
    fake_claude(tmp_path, monkeypatch, stdout=json.dumps(records) if stdout is None else stdout,
                exit_code=exit_code)
    return install


def no_plugin(tmp_path: Path, monkeypatch) -> None:
    """A CLI that lists other plugins only."""
    fake_claude(tmp_path, monkeypatch, stdout=json.dumps(
        [{"id": "other@somewhere", "scope": "user", "enabled": True, "installPath": "/x"}]))


def no_cli(monkeypatch) -> None:
    monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", "")


def framework_files(project: Path, *, skill: bool = True) -> None:
    """docs/workflows.md (+ optionally the vendored skill) so needs_setup's
    other requirements are met. Phase 61: templates/ and docs/checklists/
    are retired and no longer mark a project as set up."""
    (project / "docs").mkdir(parents=True, exist_ok=True)
    (project / "docs" / "workflows.md").write_text("w")
    if skill:
        d = project / ".claude" / "skills" / "handoff"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_bytes(
            (REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md").read_bytes())
