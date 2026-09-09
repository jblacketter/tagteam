"""Phase 48: `tagteam setup` with the plugin — the remove path is gated on
content provenance; every other outcome keeps the project's files."""
from __future__ import annotations

from pathlib import Path

import pytest

from tagteam import setup as su
from tagteam import registry as registry_mod
from tests._plugin_env import REPO, fake_plugin, no_plugin, framework_files

PACKAGED = REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md"


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    p = tmp_path / "proj"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    return p


def skill_dir(p: Path) -> Path:
    return p / ".claude" / "skills" / "handoff"


class TestRemovePath:
    def test_known_vendored_skill_is_removed(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        su.main(str(project))
        out = capsys.readouterr().out
        assert not skill_dir(project).exists()
        assert "plugin: installed (user scope" in out
        assert "removed vendored handoff skill (" in out and "contract) — served by the plugin" in out
        assert (project / "templates" / "phase_plan.md").exists()   # everything else vendored as usual
        assert not su.needs_setup(str(project))

    def test_modified_skill_is_kept_and_not_overwritten(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        mine = PACKAGED.read_bytes() + b"\n# local rule\n"
        (skill_dir(project) / "SKILL.md").write_bytes(mine)
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").read_bytes() == mine
        assert "kept .claude/skills/handoff/: SKILL.md modified" in out

    def test_extra_file_is_kept(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        (skill_dir(project) / "extra.md").write_text("ours")
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "extra.md").exists()
        assert (skill_dir(project) / "SKILL.md").exists()
        assert "extra files present: extra.md" in out

    def test_empty_dir_left_alone(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        skill_dir(project).mkdir(parents=True)
        su.main(str(project))
        assert skill_dir(project).is_dir()
        assert "kept .claude/skills/handoff/: empty directory" in capsys.readouterr().out

    def test_absent_skill_is_not_vendored(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        su.main(str(project))
        assert not skill_dir(project).exists()
        assert "served by the plugin — nothing to vendor" in capsys.readouterr().out

    def test_plugin_not_installed_vendors_as_today(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        su.main(str(project))
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").read_bytes() == PACKAGED.read_bytes()
        assert "plugin: not installed (" in out

    def test_disabled_plugin_vendors(self, project, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch, enabled=False)
        su.main(str(project))
        assert (skill_dir(project) / "SKILL.md").exists()

    def test_no_plugin_flag_forces_vendoring(self, project, tmp_path, monkeypatch, capsys):
        fake_plugin(tmp_path, monkeypatch)
        su.main(str(project), no_plugin=True)
        out = capsys.readouterr().out
        assert (skill_dir(project) / "SKILL.md").exists()
        assert "plugin: not installed (--no-plugin)" in out

    def test_cli_no_plugin_flag(self, project, tmp_path, monkeypatch):
        import tagteam.cli as cli
        fake_plugin(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(project), "--no-plugin"])
        assert cli.main() == 0
        assert (skill_dir(project) / "SKILL.md").exists()

    def test_run_setup_no_plugin(self, project, tmp_path, monkeypatch):
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=False)
        # migrated project: complete without the flag …
        assert not su.needs_setup(str(project))
        # … but --no-plugin means the local skill is required, so setup runs and vendors it
        su.run_setup(str(project), no_plugin=True)
        assert (skill_dir(project) / "SKILL.md").exists()


class TestUpgradeSweep:
    def test_upgrade_removes_known_vendored_copies(self, project, tmp_path, monkeypatch, capsys):
        from tagteam.cli import upgrade_command
        fake_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        registry_mod.register_project(str(project))
        assert upgrade_command() == 0
        assert not skill_dir(project).exists()


class TestLegacyUserSkillNote:
    """Phase 49: setup reports candidate user-level skills once; upgrade once
    per run; nothing under the config dir is ever modified."""

    def _legacy(self, tmp_path, monkeypatch, names=("handoff-cycle", "handoff-plan")):
        cfg = tmp_path / "cfg"; (cfg / "skills").mkdir(parents=True)
        for n in names:
            (cfg / "skills" / n).mkdir(); (cfg / "skills" / n / "SKILL.md").write_text(f"legacy {n}")
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
        return cfg

    def _snapshot(self, cfg):
        return {str(p.relative_to(cfg)): (p.read_bytes() if p.is_file() else None)
                for p in sorted(cfg.rglob("*"))}

    def test_setup_reports_once_and_modifies_nothing(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        cfg = self._legacy(tmp_path, monkeypatch)
        before = self._snapshot(cfg)
        su.main(str(project))
        out = capsys.readouterr().out
        assert out.count("may conflict with the tagteam plugin") == 1
        assert str(cfg / "skills" / "handoff-cycle") in out and str(cfg / "skills" / "handoff-plan") in out
        assert "tagteam did not modify them" in out
        assert "rm " not in out and "rm -" not in out
        assert self._snapshot(cfg) == before

    def test_setup_silent_on_clean_config(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        cfg = tmp_path / "cfg"; (cfg / "skills" / "ux-design-guide").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
        su.main(str(project))
        assert "may conflict" not in capsys.readouterr().out

    def test_no_plugin_flag_skips_the_note(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        self._legacy(tmp_path, monkeypatch)
        su.main(str(project), no_plugin=True)
        assert "may conflict" not in capsys.readouterr().out

    def test_report_flag_off(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        self._legacy(tmp_path, monkeypatch)
        su.main(str(project), report_user_skills=False)
        assert "may conflict" not in capsys.readouterr().out

    def test_upgrade_reports_exactly_once_for_three_projects(self, tmp_path, monkeypatch, capsys):
        from tagteam.cli import upgrade_command
        home = tmp_path / "home"; home.mkdir()
        monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
        monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
        no_plugin(tmp_path, monkeypatch)
        cfg = self._legacy(tmp_path, monkeypatch)
        before = self._snapshot(cfg)
        for i in range(3):
            p = tmp_path / f"proj{i}"; p.mkdir()
            (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
            registry_mod.register_project(str(p))
        assert upgrade_command() == 0
        out = capsys.readouterr().out
        assert out.count("may conflict with the tagteam plugin") == 1
        # the note comes after the last per-project block, never inside one
        assert out.rindex("may conflict") > out.rindex("Project: ")
        assert out.rindex("may conflict") > out.rindex("Setup complete!")
        assert self._snapshot(cfg) == before

    def test_upgrade_silent_on_clean_config(self, tmp_path, monkeypatch, capsys):
        from tagteam.cli import upgrade_command
        home = tmp_path / "home"; home.mkdir()
        monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
        monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
        no_plugin(tmp_path, monkeypatch)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg-clean"))
        p = tmp_path / "proj"; p.mkdir(); registry_mod.register_project(str(p))
        assert upgrade_command() == 0
        assert "may conflict" not in capsys.readouterr().out

    def test_run_setup_already_complete_still_reports_once(self, project, tmp_path, monkeypatch, capsys):
        """quickstart rerun on a configured project: the early-return path
        must report exactly once and modify nothing (impl review r2)."""
        no_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        cfg = self._legacy(tmp_path, monkeypatch)
        before = self._snapshot(cfg)
        su.run_setup(str(project))
        out = capsys.readouterr().out
        assert "Framework files already present" in out
        assert out.count("may conflict with the tagteam plugin") == 1
        assert "tagteam did not modify them" in out and "rm " not in out
        assert self._snapshot(cfg) == before

    def test_run_setup_already_complete_no_plugin_flag_is_silent(self, project, tmp_path, monkeypatch, capsys):
        no_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        self._legacy(tmp_path, monkeypatch)
        su.run_setup(str(project), no_plugin=True)
        assert "may conflict" not in capsys.readouterr().out

    def test_quickstart_rerun_reports_once(self, project, tmp_path, monkeypatch, capsys):
        """The real quickstart entry point over an already-complete project."""
        from unittest.mock import patch
        from tagteam.cli import quickstart_command
        no_plugin(tmp_path, monkeypatch)
        framework_files(project, skill=True)
        cfg = self._legacy(tmp_path, monkeypatch)
        before = self._snapshot(cfg)
        with patch("tagteam.session.ensure_session", return_value="created"), \
                patch("tagteam.cli.needs_init", return_value=False):
            quickstart_command(["--dir", str(project), "--backend", "manual"])
        out = capsys.readouterr().out
        assert out.count("may conflict with the tagteam plugin") == 1
        assert self._snapshot(cfg) == before


def test_setup_copies_shipped_templates_without_rendering(project, monkeypatch, capsys):
    def unexpected_render(*args, **kwargs):
        raise AssertionError("shipped files must not render role variables")
    monkeypatch.setattr(su, "render_template", unexpected_render)
    su.main(str(project), no_plugin=True, report_user_skills=False)
    for source in (su.get_data_dir() / "templates").glob("*.md"):
        assert (project / "templates" / source.name).read_bytes() == source.read_bytes()
    output = capsys.readouterr().out
    assert "Using agent names from config" not in output
    assert "templates will have" not in output


def test_copy_md_file_keeps_explicit_variable_compatibility(tmp_path):
    source, target = tmp_path / "source.md", tmp_path / "target.md"
    source.write_text("Lead: {{lead}}")
    su.copy_md_file(source, target, {"lead": "Builder"})
    assert target.read_text() == "Lead: Builder"
