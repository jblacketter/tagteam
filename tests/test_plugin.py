"""Phase 48: tagteam.plugin — detection, provenance, and the shipped plugin tree."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tagteam import plugin as pl
from tests._plugin_env import (REPO, PLUGIN_SRC, fake_plugin, fake_claude, no_plugin,
                               no_cli, install_tree)

PACKAGED = REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md"


class TestPluginTree:
    def test_plugin_skill_matches_packaged_copy(self):
        assert (PLUGIN_SRC / "skills" / "handoff" / "SKILL.md").read_bytes() == PACKAGED.read_bytes()

    def test_manifests_parse_and_agree(self):
        manifest = json.loads((PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        assert manifest["name"] == pl.PLUGIN_NAME
        assert market["name"] == pl.MARKETPLACE_NAME
        assert [p["name"] for p in market["plugins"]] == [pl.PLUGIN_NAME]
        assert market["plugins"][0]["source"] == "./plugin"
        assert manifest["tagteam"]["minVersion"]

    def test_plugin_version_matches_pyproject(self):
        import tomllib
        py = tomllib.load(open(REPO / "pyproject.toml", "rb"))["project"]["version"]
        manifest = json.loads((PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["version"] == py
        assert manifest["tagteam"]["minVersion"] == py

    def test_hooks_json_is_pinned_to_the_subcommand(self):
        hooks = json.loads((PLUGIN_SRC / "hooks" / "hooks.json").read_text())
        cmds = [h["command"] for grp in hooks["hooks"]["SessionStart"] for h in grp["hooks"]]
        assert len(cmds) == 1
        cmd = cmds[0]
        assert 'tagteam hook session-start --plugin-root "$CLAUDE_PLUGIN_ROOT"' in cmd
        assert cmd.startswith("command -v tagteam >/dev/null")
        assert cmd.endswith("|| true")
        # Phase 73: the kit agents' read-only guard (tests/test_hook_pre_tool_use.py runs it as shipped)
        assert set(hooks["hooks"]) == {"SessionStart", "PreToolUse"}
        pre = hooks["hooks"]["PreToolUse"]
        assert [g["matcher"] for g in pre] == ["Bash"]
        assert [h["command"] for g in pre for h in g["hooks"]] == ['sh "$CLAUDE_PLUGIN_ROOT/hooks/pre_tool_use.sh"']
        assert (PLUGIN_SRC / "hooks" / "pre_tool_use.sh").is_file()
        assert (PLUGIN_SRC / "hooks" / "validate_guard.py").is_file()

    def test_current_contract_hash_is_known(self):
        digest = hashlib.sha256(PACKAGED.read_bytes()).hexdigest()
        assert digest in pl.known_contract_hashes()

    def test_hash_file_is_current(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "contract_hashes.py"),
                            "--check", "--root", str(REPO)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


class TestPluginStatus:
    """Detection asks `claude plugin list --json` (effective state) and fails
    closed on every doubt."""

    def test_no_cli(self, tmp_path, monkeypatch):
        no_cli(monkeypatch)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "claude CLI not found" in st.reason

    def test_nothing_installed(self, tmp_path, monkeypatch):
        no_plugin(tmp_path, monkeypatch)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "not installed for" in st.reason

    def test_user_scope_enabled(self, tmp_path, monkeypatch):
        install = fake_plugin(tmp_path, monkeypatch)
        (tmp_path / "proj").mkdir()
        st = pl.plugin_status(tmp_path / "proj")
        assert st.installed and st.scope == "user" and st.install_path == install
        assert "per claude plugin list" in st.reason

    def test_effective_disabled(self, tmp_path, monkeypatch):
        """Managed policy or any settings layer disabling it shows up as
        enabled=false from the CLI — that is the only signal we trust."""
        fake_plugin(tmp_path, monkeypatch, enabled=False)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "not enabled" in st.reason

    def test_enabled_missing_is_not_enabled(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, enabled=None)
        assert not pl.plugin_status(tmp_path).installed

    @pytest.mark.parametrize("scope", ["project", "local"])
    def test_project_and_local_scope_matching(self, tmp_path, monkeypatch, scope):
        proj = tmp_path / "proj"; proj.mkdir()
        fake_plugin(tmp_path, monkeypatch, scope=scope, project_path=proj)
        st = pl.plugin_status(proj)
        assert st.installed and st.scope == scope

    @pytest.mark.parametrize("scope", ["project", "local"])
    def test_project_and_local_scope_other_path(self, tmp_path, monkeypatch, scope):
        proj = tmp_path / "proj"; proj.mkdir()
        other = tmp_path / "other"; other.mkdir()
        fake_plugin(tmp_path, monkeypatch, scope=scope, project_path=other)
        st = pl.plugin_status(proj)
        assert not st.installed and "not installed for" in st.reason

    def test_realpath_comparison(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"; proj.mkdir()
        link = tmp_path / "link"; link.symlink_to(proj)
        fake_plugin(tmp_path, monkeypatch, scope="project", project_path=link)
        assert pl.plugin_status(proj).installed

    def test_unsupported_scope(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, scope="managed")
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "unsupported scope" in st.reason

    def test_ambiguous_records(self, tmp_path, monkeypatch):
        extra = {"id": pl.PLUGIN_KEY, "scope": "user", "enabled": False, "installPath": "/x"}
        fake_plugin(tmp_path, monkeypatch, enabled=True, extra_records=[extra])
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "ambiguous" in st.reason

    def test_cli_nonzero_exit(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, exit_code=3)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "exited 3" in st.reason

    def test_cli_malformed_json(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, stdout="{not json")
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "invalid JSON" in st.reason

    def test_cli_not_an_array(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, stdout='{"plugins": []}')
        assert "not a JSON array" in pl.plugin_status(tmp_path).reason

    def test_cli_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pl, "PLUGIN_LIST_TIMEOUT_S", 0.2)
        fake_claude(tmp_path, monkeypatch, stdout="[]", sleep=2)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "timed out" in st.reason

    def test_missing_project_dir_fails_closed(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch)
        st = pl.plugin_status(tmp_path / "does-not-exist")
        assert not st.installed and "could not run" in st.reason

    def test_cli_not_executable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", str(tmp_path / "missing-claude"))
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "could not run" in st.reason

    def test_broken_install_missing_skill(self, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, broken=True)
        st = pl.plugin_status(tmp_path)
        assert not st.installed and "broken install" in st.reason

    def test_runs_with_project_as_cwd(self, tmp_path, monkeypatch):
        """project/local scope are resolved by the CLI against its cwd."""
        proj = tmp_path / "proj"; proj.mkdir()
        exe = fake_claude(tmp_path, monkeypatch, stdout="[]")
        exe.write_text("#!/bin/sh\npwd > " + str(tmp_path / "cwd.txt") + "\necho []\n")
        pl.plugin_status(proj)
        assert Path((tmp_path / "cwd.txt").read_text().strip()).resolve() == proj.resolve()

    def test_real_cli_shape_parses(self, tmp_path, monkeypatch):
        """The exact record shape `claude plugin list --json` printed on the
        arbiter's machine (2.1.251), with tagteam added."""
        install = install_tree(tmp_path)
        records = [
            {"id": "swift-lsp@claude-plugins-official", "version": "1.0.0", "scope": "user",
             "enabled": False, "installPath": "/x", "installedAt": "2025-12-23T02:08:06.479Z",
             "lastUpdated": "2025-12-23T02:08:06.479Z"},
            {"id": "frontend-design@claude-plugins-official", "version": "unknown",
             "scope": "project", "enabled": False, "installPath": "/y",
             "projectPath": "/Users/someone/projects/tokenbench"},
            {"id": pl.PLUGIN_KEY, "version": "3.10.0", "scope": "user", "enabled": True,
             "installPath": str(install), "installedAt": "x", "lastUpdated": "y"},
        ]
        fake_claude(tmp_path, monkeypatch, stdout=json.dumps(records))
        assert pl.plugin_status(tmp_path).installed


class TestHandoffCommand:
    def test_vendored_project_uses_slash_handoff(self, tmp_path):
        from tagteam.contract import handoff_command
        d = tmp_path / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
        assert handoff_command(tmp_path) == "/handoff"

    def test_migrated_project_uses_namespaced(self, tmp_path):
        from tagteam.contract import handoff_command
        assert handoff_command(tmp_path) == "/tagteam:handoff"

    def test_plugin_name_and_skill_dir_compose_the_command(self):
        from tagteam.contract import PLUGIN_SKILL_COMMAND
        manifest = json.loads((PLUGIN_SRC / ".claude-plugin" / "plugin.json").read_text())
        skill_dirs = [p.name for p in (PLUGIN_SRC / "skills").iterdir() if p.is_dir()]
        assert skill_dirs == ["handoff"]
        assert PLUGIN_SKILL_COMMAND == f"/{manifest['name']}:{skill_dirs[0]}"

    def test_contract_uses_the_namespaced_command_and_explains_fallbacks(self):
        text = PACKAGED.read_text()
        assert "NEXT: Tell [agent name] to run:  /tagteam:handoff" in text
        assert "`/handoff`" in text and "`tagteam contract`" in text
        assert "/handoff start" not in text.replace("/tagteam:handoff start", "")

    def test_standard_turn_command_names_both_routes(self):
        from tagteam.contract import STANDARD_TURN_COMMAND
        assert "tagteam contract" in STANDARD_TURN_COMMAND and "/tagteam:handoff" in STANDARD_TURN_COMMAND
        assert ".claude/skills" not in STANDARD_TURN_COMMAND

    def test_start_parser_accepts_both_names(self):
        from tagteam import headless as h
        assert h.parse_start_command("/tagteam:handoff start feat-x impl") == ("feat-x", "impl")
        assert h.parse_start_command("/handoff start feat-x") == ("feat-x", "plan")


class TestContractCommand:
    def test_prints_packaged_contract(self, tmp_path):
        r = subprocess.run([sys.executable, "-m", "tagteam", "contract"], cwd=str(tmp_path),
                           capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout == PACKAGED.read_text()

    def test_path(self, tmp_path):
        r = subprocess.run([sys.executable, "-m", "tagteam", "contract", "--path"], cwd=str(tmp_path),
                           capture_output=True, text=True)
        assert r.returncode == 0
        assert Path(r.stdout.strip()).resolve() == PACKAGED.resolve()


class TestRuntimeStringsAudit:
    """No runtime instruction may point an agent at the vendored path or an
    unconditional `/handoff`: a migrated project has neither."""

    RUNTIME = sorted((REPO / "tagteam").glob("*.py"))
    # modules that legitimately name the path: they define/resolve it
    PATH_OWNERS = {"contract.py", "headless.py", "plugin.py", "setup.py"}

    def test_no_bare_vendored_path_instruction(self):
        hits = []
        for f in self.RUNTIME:
            if f.name in self.PATH_OWNERS:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if ".claude/skills/handoff/SKILL.md" in line and not line.lstrip().startswith("#"):
                    hits.append(f"{f.name}:{n}: {line.strip()}")
        assert not hits, "\n".join(hits)

    def test_no_unconditional_slash_handoff_instruction(self):
        import re
        pat = re.compile(r"(run|type|paste|tell [^\"']+ to run)\s+/handoff\b")
        hits = []
        for f in self.RUNTIME:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line) and "handoff_command" not in line and not line.lstrip().startswith("#"):
                    hits.append(f"{f.name}:{n}: {line.strip()}")
        assert not hits, "\n".join(hits)

    def test_prime_message_names_both_routes(self):
        from tagteam.session import PRIME_MESSAGE
        from tagteam.contract import CONTRACT_HOWTO
        assert CONTRACT_HOWTO in PRIME_MESSAGE and ".claude/skills" not in PRIME_MESSAGE

    def test_lead_chat_header_is_project_aware(self, tmp_path):
        from tagteam.lead_chat import FIRST_TURN_HEADER
        from tagteam.contract import handoff_command
        migrated = FIRST_TURN_HEADER.format(root=tmp_path, name="x", state_line="s",
                                            handoff_cmd=handoff_command(tmp_path))
        assert "/tagteam:handoff start <phase>" in migrated and ".claude/skills" not in migrated
        d = tmp_path / ".claude" / "skills" / "handoff"; d.mkdir(parents=True); (d / "SKILL.md").write_text("x")
        vendored = FIRST_TURN_HEADER.format(root=tmp_path, name="x", state_line="s",
                                            handoff_cmd=handoff_command(tmp_path))
        assert "`/handoff start <phase>`" in vendored

    @pytest.mark.parametrize("reviewer", ["codex", "claude"])
    def test_gate_and_panel_next_lines_are_provider_neutral(self, tmp_path, reviewer):
        from io import StringIO
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from tagteam import gatekeeper, panel
        from tagteam.contract import CONTRACT_HOWTO

        spec = MagicMock(enabled=True, on_submit=True, problems=[])
        result = SimpleNamespace(status="pass", decision=None, event_key=None, reason="ok")
        outputs = []
        with patch.object(gatekeeper, "load_spec", return_value=spec), \
             patch.object(gatekeeper, "run_gate", return_value=result):
            out = StringIO()
            gatekeeper.on_submit_gate(tmp_path, "p", "impl", reviewer=reviewer,
                                     out=out, err=StringIO())
            assert f"tell {reviewer} to read" in out.getvalue()
            outputs.append(out.getvalue())
            out = StringIO()
            assert gatekeeper.gate_command(["run", "--phase", "p", "--type", "impl"],
                                           project_root=tmp_path, out=out) == 0
            outputs.append(out.getvalue())
        result = SimpleNamespace(status="fallback", content=None, event_key=None, reason="fallback")
        with patch.object(panel, "load_spec", return_value=spec), \
             patch.object(panel, "run_panel", return_value=result):
            out = StringIO()
            assert panel.panel_command(["run", "--phase", "p", "--type", "impl"],
                                       project_root=tmp_path, out=out) == 0
            outputs.append(out.getvalue())
        for text in outputs:
            assert CONTRACT_HOWTO in text
            assert "then act on their turn" in text
            assert "to run /" not in text



class TestProvenance:
    def _dir(self, tmp_path, content: bytes | None = PACKAGED.read_bytes()):
        d = tmp_path / ".claude" / "skills" / "handoff"
        d.mkdir(parents=True)
        if content is not None:
            (d / "SKILL.md").write_bytes(content)
        return d

    def test_known_contract_is_removable(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path))
        assert p.removable and p.version

    def test_modified_contract_is_kept(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path, PACKAGED.read_bytes() + b"\n# mine\n"))
        assert not p.removable and "modified" in p.reason

    def test_extra_file_is_kept(self, tmp_path):
        d = self._dir(tmp_path)
        (d / "notes.md").write_text("ours")
        p = pl.vendored_skill_provenance(d)
        assert not p.removable and "extra files present: notes.md" in p.reason

    def test_empty_dir_is_kept(self, tmp_path):
        p = pl.vendored_skill_provenance(self._dir(tmp_path, None))
        assert not p.removable and p.reason == "empty directory"

    def test_absent(self, tmp_path):
        p = pl.vendored_skill_provenance(tmp_path / "nope")
        assert not p.removable and p.reason == "absent"

    def test_symlinked_skill_is_kept(self, tmp_path):
        d = tmp_path / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
        (d / "SKILL.md").symlink_to(PACKAGED)
        assert not pl.vendored_skill_provenance(d).removable

    def test_empty_known_set_never_removes(self, tmp_path):
        assert not pl.vendored_skill_provenance(self._dir(tmp_path), known={}).removable


class TestClaudeConfigDir:
    def test_unset(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert pl.claude_config_dir() == Path.home() / ".claude"

    def test_empty_is_unset(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "   ")
        assert pl.claude_config_dir() == Path.home() / ".claude"

    def test_set_need_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
        assert pl.claude_config_dir() == tmp_path / "nope"

    def test_relative_and_tilde_become_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "cfg")
        assert pl.claude_config_dir() == tmp_path / "cfg"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/x-cfg")
        assert pl.claude_config_dir() == Path.home() / "x-cfg"

    def test_symlinked_config_dir_is_not_resolved(self, tmp_path, monkeypatch):
        real = tmp_path / "real"; real.mkdir()
        link = tmp_path / "link"; link.symlink_to(real)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(link))
        assert pl.claude_config_dir() == link


class TestLegacySkillCandidates:
    def _cfg(self, tmp_path, monkeypatch, names=(), files=(), broken=()):
        cfg = tmp_path / "cfg"; (cfg / "skills").mkdir(parents=True)
        for n in names:
            (cfg / "skills" / n).mkdir(); (cfg / "skills" / n / "SKILL.md").write_text("old")
        for n in broken:
            (cfg / "skills" / n).mkdir()                    # no SKILL.md
        for n in files:
            (cfg / "skills" / n).write_text("not a dir")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
        return cfg

    def test_none(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch, names=["ux-design-guide"])
        assert pl.legacy_handoff_skill_candidates() == []

    def test_no_skills_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
        assert pl.legacy_handoff_skill_candidates() == []

    def test_one_and_several_sorted(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch, names=["handoff-sync", "handoff-cycle", "other"])
        assert pl.legacy_handoff_skill_candidates() == [cfg / "skills" / "handoff-cycle",
                                                        cfg / "skills" / "handoff-sync"]

    def test_bare_handoff_counts(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch, names=["handoff"])
        assert pl.legacy_handoff_skill_candidates() == [cfg / "skills" / "handoff"]

    def test_without_skill_md_and_files_ignored(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch, broken=["handoff-foo"], files=["handoff-x"])
        assert pl.legacy_handoff_skill_candidates() == []

    def test_handoffish_but_not_prefix_ignored(self, tmp_path, monkeypatch):
        self._cfg(tmp_path, monkeypatch, names=["my-handoff", "handoffs"])
        assert pl.legacy_handoff_skill_candidates() == []

    def test_symlink_outside_config_reported_by_link_path(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        outside = tmp_path / "elsewhere" / "skill"; outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("old")
        (cfg / "skills" / "handoff-old").symlink_to(outside)
        got = pl.legacy_handoff_skill_candidates()
        assert got == [cfg / "skills" / "handoff-old"]
        note = pl.render_legacy_skill_note(got)
        assert str(cfg / "skills" / "handoff-old") in note
        assert str(outside) not in note

    def test_explicit_config_dir_argument(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch, names=["handoff-plan"])
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "unrelated"))
        assert pl.legacy_handoff_skill_candidates(cfg) == [cfg / "skills" / "handoff-plan"]

    def test_note_shape(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch, names=["handoff-a", "handoff-b"])
        note = pl.render_legacy_skill_note(pl.legacy_handoff_skill_candidates())
        lines = note.splitlines()
        assert lines[0].startswith("note: 2 user-level skills under ") and "/tagteam:handoff" in lines[0]
        assert lines[1:3] == [f"  {cfg / 'skills' / 'handoff-a'}", f"  {cfg / 'skills' / 'handoff-b'}"]
        assert lines[3] == "tagteam did not modify them. Review each; remove confirmed pre-plugin copies yourself."
        assert "rm " not in note and "rm -" not in note

    def test_empty_note(self):
        assert pl.render_legacy_skill_note([]) == ""


class TestShippedDocsAudit:
    """Phase 49: nothing tagteam ships or runs may mention the dead /handoff-* family."""

    def test_no_legacy_command_family(self):
        import re
        pat = re.compile(r"/handoff-[a-z]+(?![\w-]*\.md)")   # a command, not a doc filename
        hits = []
        for f in list((REPO / "tagteam" / "data").rglob("*.md")) + list((REPO / "tagteam").rglob("*.py")):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{f.relative_to(REPO)}:{n}: {line.strip()[:100]}")
        assert not hits, "\n".join(hits)

    # Phase 63: the family before /handoff-* — `/phase`, `/plan create`,
    # `/status` — which the seeded roadmap still named. Exactly two shipped
    # files keep them, both frozen provenance whose bytes retire a project's
    # old templates/roadmap.md; each must be tag-pinned (tests/_provenance.py).
    OLDER_FAMILY_ALLOWED = {
        "tagteam/data/templates/roadmap.md",
        "tagteam/data/history/v3.3.0/templates/roadmap.md",
    }

    @staticmethod
    def _older_family_hits(files, root):
        import re
        pat = re.compile(r"`/(?:phase|plan|status)\b[^`]*`")
        hits = []
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append((f.relative_to(root).as_posix(), n, line.strip()[:100]))
        return hits

    def test_no_older_command_family_outside_pinned_provenance(self, tmp_path):
        from tests import _provenance as prov
        hits = self._older_family_hits(list((REPO / "tagteam" / "data").rglob("*.md")), REPO)
        assert {rel for rel, _, _ in hits} == self.OLDER_FAMILY_ALLOWED, hits      # exactly these, no others
        assert self.OLDER_FAMILY_ALLOWED <= prov.pinned_relpaths()                   # exempt ⇒ pinned to a tag
        # the scan itself rejects the same text anywhere else
        stray = tmp_path / "tagteam" / "data" / "new.md"
        stray.parent.mkdir(parents=True)
        stray.write_text("1. Use `/plan create [phase]` to start planning\nsee /status/ page and `tagteam state`\n")
        assert [(rel, n) for rel, n, _ in self._older_family_hits([stray], tmp_path)] == [("tagteam/data/new.md", 1)]

    def test_handoff_family_audit_still_scans_the_exempt_files(self):
        import re
        pat = re.compile(r"/handoff-[a-z]+(?![\w-]*\.md)")
        for rel in self.OLDER_FAMILY_ALLOWED:
            assert (REPO / rel).is_file() and not pat.search((REPO / rel).read_text(encoding="utf-8")), rel

    def test_workflows_md_describes_the_current_surface(self):
        text = (REPO / "tagteam" / "data" / "workflows.md").read_text()
        for needle in ("/tagteam:handoff", "`/handoff`", "tagteam contract", "cycle add", "one-run", "gatekeeper"):
            assert needle in text, needle
