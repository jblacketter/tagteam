"""Shared pytest configuration.

Phase 32 made every watcher mode honor `.tagteam/headless-paused.json`.
Older watcher tests construct `_StateProcessor(project_dir=".")`, i.e. the
checkout itself — so an operator hold on *this* repository (routine during
headless dogfood, or in a reviewer's sandbox) would leak into the suite.
Isolate: a processor rooted at "." ignores the real repo's marker under
test; tests that exercise pause/resume use an explicit fixture project.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_cwd_pause_marker(monkeypatch):
    from tagteam import watcher

    real = watcher._StateProcessor._pause_info

    def isolated(self):
        if self.project_dir in (".", ""):
            return None
        return real(self)

    monkeypatch.setattr(watcher._StateProcessor, "_pause_info", isolated)





@pytest.fixture(autouse=True)
def _isolated_port_leases(tmp_path, monkeypatch):
    """Phase 37: `tagteam serve` / `hub` claim a port lease under
    ~/.tagteam/ports/ — tests must never touch the real one."""
    monkeypatch.setenv("TAGTEAM_PORT_LEASE_DIR", str(tmp_path / "_port_leases"))
    yield


@pytest.fixture(autouse=True)
def _isolated_watcher_locks(tmp_path, monkeypatch):
    """Phase 58: the one-watcher lock lives under ~/.tagteam/watchers/ — tests
    must never touch the real one. Outside tmp_path so project snapshots stay clean."""
    monkeypatch.setenv("TAGTEAM_WATCHER_LOCK_DIR", str(tmp_path.parent / (tmp_path.name + "_watcher_locks")))
    yield


@pytest.fixture(autouse=True)
def _no_real_claude_cli(monkeypatch):
    """Phase 48: plugin detection shells out to `claude plugin list --json`.
    The suite must never ask the developer's real CLI — an empty override
    means "no CLI" (→ plugin not installed); tests that want a plugin point
    TAGTEAM_CLAUDE_BIN at a fake (tests/_plugin_env.py)."""
    monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", "")
