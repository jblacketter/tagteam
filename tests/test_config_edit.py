"""Phase 71b: safe tagteam.yaml edits (`tagteam/config_edit.py`)."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from tagteam import config_edit as ce
from tagteam import dualwrite

REALISTIC = """\
# Tagteam configuration — hand-written, keep my comments
agents:
  lead:
    name: claude      # the lead
  reviewer:
    name: 'codex'

# the gate
gatekeeper:
  # turn on when ready
  enabled: false  # off for now
  tests:
    command: "pytest -q *_test.py"

watcher:
  resend_minutes: 10   # minutes

empty_unrelated:
"""


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)
    monkeypatch.setattr(ce, "_TEST_HOOK", None)
    return tmp_path


def _write(p: Path, text: str) -> Path:
    f = p / "tagteam.yaml"
    f.write_bytes(text.encode("utf-8"))
    return f


def _changed_lines(old: str, new: str) -> tuple[list[str], list[str]]:
    import difflib
    rem, add = [], []
    for l in difflib.ndiff(old.splitlines(keepends=True), new.splitlines(keepends=True)):
        if l.startswith("- "):
            rem.append(l[2:])
        elif l.startswith("+ "):
            add.append(l[2:])
    return rem, add


# ---------------------------------------------------------------------------
# Criterion 1: exactly the target changes, every other byte identical
# ---------------------------------------------------------------------------

class TestTargetedEdits:
    def test_replace_keeps_indent_spacing_and_trailing_comment(self, proj):
        _write(proj, REALISTIC)
        ce.apply(proj, "gatekeeper.enabled", True)
        new = (proj / "tagteam.yaml").read_text()
        rem, add = _changed_lines(REALISTIC, new)
        assert rem == ["  enabled: false  # off for now\n"] and add == ["  enabled: true  # off for now\n"]

    def test_replace_an_integer(self, proj):
        _write(proj, REALISTIC)
        ce.apply(proj, "watcher.resend_minutes", 0)
        rem, add = _changed_lines(REALISTIC, (proj / "tagteam.yaml").read_text())
        assert rem == ["  resend_minutes: 10   # minutes\n"] and add == ["  resend_minutes: 0   # minutes\n"]

    def test_insert_an_absent_key_after_the_header(self, proj):
        _write(proj, REALISTIC)
        ce.apply(proj, "gatekeeper.on_submit", True)
        new = (proj / "tagteam.yaml").read_text()
        rem, add = _changed_lines(REALISTIC, new)
        assert rem == [] and add == ["  on_submit: true\n"]
        assert new.index("gatekeeper:\n  on_submit: true\n  # turn on when ready") > 0

    def test_append_an_absent_block(self, proj):
        _write(proj, REALISTIC)
        ce.apply(proj, "briefer.enabled", False)
        new = (proj / "tagteam.yaml").read_text()
        assert new == REALISTIC + "\nbriefer:\n  enabled: false\n"

    def test_crlf_and_no_trailing_newline_are_preserved(self, proj):
        text = REALISTIC.replace("\n", "\r\n").rstrip("\r\n")
        _write(proj, text)
        ce.apply(proj, "gatekeeper.enabled", True)
        new = (proj / "tagteam.yaml").read_bytes().decode()
        assert new == text.replace("enabled: false  # off for now", "enabled: true  # off for now")
        ce.apply(proj, "panel.enabled", False)
        new2 = (proj / "tagteam.yaml").read_bytes().decode()
        assert new2 == new + "\r\n\r\npanel:\r\n  enabled: false"          # still no trailing newline
        assert "\n" not in new2.replace("\r\n", "")

    @pytest.mark.parametrize("key, value", [
        ("gatekeeper.enabled", True), ("gatekeeper.on_submit", False), ("panel.enabled", False),
        ("briefer.enabled", False), ("watcher.resend_minutes", 42)])
    def test_every_safe_key_parses_to_old_plus_target(self, proj, key, value):
        for text in (REALISTIC, REALISTIC.replace("\n", "\r\n"), "agents:\n  lead:\n    name: a\n", ""):
            _write(proj, text)
            old = yaml.safe_load(text) or {}
            ce.apply(proj, key, value)
            new = yaml.safe_load((proj / "tagteam.yaml").read_text()) or {}
            block, leaf = key.split(".")
            exp = json.loads(json.dumps(old))
            exp[block] = dict(exp.get(block) or {}, **{leaf: value})
            assert new == exp, (key, text[:30])

    def test_file_mode_is_kept(self, proj):
        f = _write(proj, REALISTIC)
        os.chmod(f, 0o600)
        ce.apply(proj, "gatekeeper.enabled", True)
        assert (os.stat(f).st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# Criterion 9: absent / empty target blocks; unrelated empty blocks untouched
# ---------------------------------------------------------------------------

class TestAbsentAndEmptyBlocks:
    def test_empty_target_block(self, proj):
        text = "agents:\n  lead:\n    name: a\nwatcher:\nempty_unrelated:\n"
        _write(proj, text)
        ce.apply(proj, "watcher.resend_minutes", 5)
        new = (proj / "tagteam.yaml").read_text()
        assert new == "agents:\n  lead:\n    name: a\nwatcher:\n  resend_minutes: 5\nempty_unrelated:\n"
        assert yaml.safe_load(new)["empty_unrelated"] is None

    def test_empty_target_block_at_the_end_without_newline(self, proj):
        _write(proj, "agents:\n  lead:\n    name: a\nwatcher:")
        ce.apply(proj, "watcher.resend_minutes", 5)
        assert (proj / "tagteam.yaml").read_text() == "agents:\n  lead:\n    name: a\nwatcher:\n  resend_minutes: 5"


# ---------------------------------------------------------------------------
# Criterion 2: refusals — nothing written
# ---------------------------------------------------------------------------

class TestRefusals:
    @pytest.mark.parametrize("text, frag", [
        ("gatekeeper: {enabled: false}\n", "not a plain block mapping"),
        ("gatekeeper: off\n", "not a plain block mapping"),
        ("gatekeeper:\n  enabled: false\ngatekeeper:\n  scope: true\n", "appears 2 times"),
        ("gatekeeper:\n  enabled: false\n  enabled: true\n", "appears 2 times"),
        ("gatekeeper:\n  enabled: |\n    true\n", "not a plain one-line value"),
        ("gatekeeper:\n  enabled:\n    - x\n", "multi-line value"),
        ("gatekeeper:\n\tenabled: false\n", "does not parse"),     # YAML itself rejects tab indentation
        ("base: &b true\ngatekeeper:\n  enabled: *b\n", "anchors or aliases"),
        ("---\ngatekeeper:\n  enabled: false\n", "document markers"),
    ])
    def test_unusual_layouts_are_refused(self, proj, text, frag):
        f = _write(proj, text)
        before = f.read_bytes()
        with pytest.raises(ce.Refused) as e:
            ce.apply(proj, "gatekeeper.enabled", True)
        assert frag in str(e.value) and "by hand" in str(e.value)
        assert f.read_bytes() == before

    def test_glob_in_a_command_is_not_an_alias(self, proj):
        _write(proj, REALISTIC)
        ce.apply(proj, "gatekeeper.enabled", True)          # `*_test.py` sits in the same block

    def test_unknown_key_and_wrong_value(self, proj):
        _write(proj, REALISTIC)
        assert ce.config_command(["set", "agents.lead.name", "x"], project_root=proj) == 2
        assert ce.config_command(["set", "gatekeeper.enabled", "maybe"], project_root=proj) == 2
        assert ce.config_command(["set", "watcher.resend_minutes", "-1"], project_root=proj) == 2
        assert ce.config_command(["set", "watcher.resend_minutes", "1.5"], project_root=proj) == 2
        assert (proj / "tagteam.yaml").read_text() == REALISTIC

    def test_missing_and_symlinked_file(self, proj, tmp_path_factory):
        with pytest.raises(ce.Refused, match="tagteam init"):
            ce.apply(proj, "gatekeeper.enabled", True)
        assert not (proj / "tagteam.yaml").exists()
        real = tmp_path_factory.mktemp("elsewhere") / "t.yaml"
        real.write_text(REALISTIC)
        os.symlink(real, proj / "tagteam.yaml")
        with pytest.raises(ce.Refused, match="symlink"):
            ce.apply(proj, "gatekeeper.enabled", True)
        assert real.read_text() == REALISTIC

    def test_read_only(self, proj, monkeypatch):
        f = _write(proj, REALISTIC)
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        with pytest.raises(dualwrite.ReadOnlyError):
            ce.apply(proj, "gatekeeper.enabled", True)
        assert f.read_text() == REALISTIC
        # reads still work
        assert ce.config_command(["keys"], project_root=proj) == 0
        assert ce.config_command(["set", "gatekeeper.enabled", "true", "--preview"], project_root=proj) == 0
        from tagteam.cli import read_only_refusal
        assert read_only_refusal(["config", "keys"]) is None
        assert read_only_refusal(["config", "set", "gatekeeper.enabled", "true", "--preview"]) is None
        assert read_only_refusal(["config", "set", "gatekeeper.enabled", "true"]) is not None

    def test_changed_between_compute_and_write(self, proj, monkeypatch):
        f = _write(proj, REALISTIC)
        monkeypatch.setattr(ce, "_TEST_HOOK", lambda: f.write_text(REALISTIC + "# hand edit\n"))
        with pytest.raises(ce.Refused) as e:
            ce.apply(proj, "gatekeeper.enabled", True)
        assert e.value.kind == "stale" and f.read_text() == REALISTIC + "# hand edit\n"

    def test_an_enable_the_engine_would_not_honour_is_refused_with_its_reason(self, proj):
        f = _write(proj, "agents:\n  lead:\n    name: someone\n  reviewer:\n    name: other\n")
        with pytest.raises(ce.Refused) as e:
            ce.apply(proj, "briefer.enabled", True)
        assert "engine would still use escalation brief: OFF" in str(e.value) and "briefer" in str(e.value)
        with pytest.raises(ce.Refused) as e:
            ce.apply(proj, "panel.enabled", True)
        assert "reviewer panel: OFF" in str(e.value)
        assert "briefer" not in f.read_text() and "panel" not in f.read_text()


# ---------------------------------------------------------------------------
# Criteria 3 + 8: saved vs effective; off is never refused by the engine check
# ---------------------------------------------------------------------------

class TestSavedVsEffective:
    def test_a_disable_writes_even_when_the_gate_is_already_effectively_off(self, proj):
        text = "gatekeeper:\n  enabled: true\n  scope: invalid\n"
        f = _write(proj, text)
        rows = {r["key"]: r for r in ce.keys_rows(proj)}
        assert rows["gatekeeper.enabled"]["saved"] is True and rows["gatekeeper.enabled"]["effective"] is False
        p = ce.apply(proj, "gatekeeper.enabled", False)
        assert p.noop is False and "enabled: false" in f.read_text()
        # repair the other problem by hand: the gate stays OFF, as asked
        f.write_text(f.read_text().replace("scope: invalid", "scope: true"))
        from tagteam.gatekeeper import load_spec
        assert load_spec(proj).enabled is False

    def test_watcher_three_versus_fifteen(self, proj):
        f = _write(proj, "watcher:\n  resend_minutes: 3\n  bogus: 1\n")
        p = ce.apply(proj, "watcher.resend_minutes", 3)
        assert p.noop and any("15 min" in n and "bogus" in n for n in p.notes)
        with pytest.raises(ce.Refused, match="would still use re-send a stuck turn: 15 min"):
            ce.apply(proj, "watcher.resend_minutes", 5)
        f.write_text("watcher:\n  resend_minutes: 3\n")
        ce.apply(proj, "watcher.resend_minutes", 5)
        from tagteam.config import read_config, resolve_watcher
        assert resolve_watcher(read_config(f))[0] == 5

    def test_an_invalid_saved_value_is_written_as_a_real_false(self, proj):
        f = _write(proj, 'panel:\n  enabled: "true"\n')
        assert ce.saved(yaml.safe_load(f.read_text()), "panel.enabled")[0] == "invalid"
        ce.apply(proj, "panel.enabled", False)
        assert yaml.safe_load(f.read_text())["panel"]["enabled"] is False

    def test_off_in_a_block_with_other_problems(self, proj):
        f = _write(proj, "briefer:\n  enabled: true\n  provider: nonsense\n  timeout_minutes: -3\n")
        ce.apply(proj, "briefer.enabled", False)
        assert yaml.safe_load(f.read_text())["briefer"]["enabled"] is False

    def test_a_true_noop(self, proj):
        f = _write(proj, REALISTIC)
        before = f.stat().st_mtime_ns
        assert ce.apply(proj, "gatekeeper.enabled", False).noop
        assert f.stat().st_mtime_ns == before


# ---------------------------------------------------------------------------
# Criteria 5 + 10: previews write nothing; a write is bound to its preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_preview_and_keys_write_nothing(self, proj, capsys):
        f = _write(proj, REALISTIC)
        before = sorted(p.name for p in proj.iterdir())
        assert ce.config_command(["set", "gatekeeper.enabled", "true", "--preview"], project_root=proj) == 0
        out = capsys.readouterr().out
        assert "-  enabled: false  # off for now" in out and "+  enabled: true  # off for now" in out
        assert "engine: gate: ON" in out and "base: " + hashlib.sha256(REALISTIC.encode()).hexdigest() in out
        assert ce.config_command(["keys", "--json"], project_root=proj) == 0
        assert f.read_text() == REALISTIC and sorted(p.name for p in proj.iterdir()) == before

    def test_expect_binds_the_write_to_the_previewed_bytes(self, proj):
        f = _write(proj, REALISTIC)
        base = ce.preview(proj, "gatekeeper.enabled", True).base
        f.write_text(REALISTIC + "# changed after the preview\n")
        assert ce.config_command(["set", "gatekeeper.enabled", "true", "--expect", base], project_root=proj) == 1
        assert f.read_text() == REALISTIC + "# changed after the preview\n"
        base2 = ce.preview(proj, "gatekeeper.enabled", True).base
        assert ce.config_command(["set", "gatekeeper.enabled", "true", "--expect", base2], project_root=proj) == 0
        assert "enabled: true" in f.read_text()


# ---------------------------------------------------------------------------
# Criterion 11: serialized writers (threads and processes)
# ---------------------------------------------------------------------------

class TestSerializedWriters:
    def test_two_threads_both_edits_survive(self, proj, monkeypatch):
        f = _write(proj, REALISTIC)
        inside, release = threading.Event(), threading.Event()
        calls = []

        def hook():
            calls.append(threading.current_thread().name)
            if len(calls) == 1:
                inside.set()
                assert release.wait(10)
        monkeypatch.setattr(ce, "_TEST_HOOK", hook)
        errors = []

        def run(key, value):
            try:
                ce.apply(proj, key, value)
            except Exception as e:              # pragma: no cover - reported below
                errors.append(e)
        a = threading.Thread(target=run, args=("gatekeeper.enabled", True), name="A")
        a.start()
        assert inside.wait(10)
        b = threading.Thread(target=run, args=("watcher.resend_minutes", 7), name="B")
        b.start()
        time.sleep(0.5)
        assert b.is_alive() and calls == ["A"]            # B is blocked on the lock, not computing
        release.set()
        a.join(10); b.join(10)
        assert not errors, errors
        cfg = yaml.safe_load(f.read_text())
        assert cfg["gatekeeper"]["enabled"] is True and cfg["watcher"]["resend_minutes"] == 7

    def test_two_processes_both_edits_survive(self, proj):
        f = _write(proj, REALISTIC)
        with dualwrite.writer_lock(str(proj)):
            ce.apply(proj, "gatekeeper.enabled", True)       # reentrant inside the held lock
            child = subprocess.Popen(
                [sys.executable, "-m", "tagteam", "config", "set", "watcher.resend_minutes", "7"],
                cwd=str(proj), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={k: v for k, v in os.environ.items() if k != "TAGTEAM_READ_ONLY"})
            time.sleep(1.5)
            assert child.poll() is None                      # blocked on the flock
            assert "resend_minutes: 10" in f.read_text()
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        cfg = yaml.safe_load(f.read_text())
        assert cfg["gatekeeper"]["enabled"] is True and cfg["watcher"]["resend_minutes"] == 7


class TestBackstop:
    def test_a_locator_bug_is_refused_and_nothing_is_written(self, proj, monkeypatch):
        """Validation step 2: even a wrong edit_text cannot write a config that
        differs anywhere but the target."""
        f = _write(proj, REALISTIC)
        real = ce.edit_text
        monkeypatch.setattr(ce, "edit_text",
                            lambda t, k, v: real(t, k, v).replace("name: claude", "name: mallory"))
        with pytest.raises(ce.Refused, match="internal: the edit would change more than"):
            ce.apply(proj, "gatekeeper.enabled", True)
        assert f.read_text() == REALISTIC



class TestQuotedAndDuplicateSpellings:
    """impl r1 review: safe_load collapses duplicates and hides quoting; the
    parser's node tree does not."""

    @pytest.mark.parametrize("text", [
        '"gatekeeper": {enabled: false}\n',
        '"gatekeeper":\n  enabled: false\n',
        "'gatekeeper':\n  enabled: true\n",
        '"gatekeeper":\n  enabled: false\ngatekeeper:\n  scope: true\n',
        'gatekeeper:\n  scope: true\n"gatekeeper":\n  enabled: false\n',
        'gatekeeper:\n  "enabled": false\n  enabled: false\n',
        'gatekeeper:\n  "enabled": true\n',
    ])
    @pytest.mark.parametrize("value", [True, False])            # False covers the no-op spellings too
    def test_refused_with_bytes_unchanged(self, proj, text, value):
        f = _write(proj, text)
        with pytest.raises(ce.Refused) as e:
            ce.apply(proj, "gatekeeper.enabled", value)
        assert "by hand" in str(e.value)
        assert f.read_text() == text


class TestReadableDiff:
    def test_replacing_the_final_scalar_without_a_newline(self, proj):
        _write(proj, "gatekeeper:\n  enabled: false")
        d = ce.preview(proj, "gatekeeper.enabled", True).diff
        lines = d.splitlines()
        assert "-  enabled: false" in lines and "+  enabled: true" in lines
        assert lines.count("\\ No newline at end of file") == 2

    def test_appending_to_a_file_without_a_newline(self, proj):
        _write(proj, "agents:\n  x: 1")
        d = ce.preview(proj, "panel.enabled", False).diff
        lines = d.splitlines()
        assert lines[-5:] == ["+  x: 1", "+", "+panel:", "+  enabled: false", "\\ No newline at end of file"]
        assert "-  x: 1" in lines

    def test_an_ordinary_file_has_no_marker(self, proj):
        _write(proj, REALISTIC)
        assert "No newline" not in ce.preview(proj, "gatekeeper.enabled", True).diff
