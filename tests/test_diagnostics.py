"""Phase 53: `tagteam doctor` — legacy workflow findings, capability and
context visibility, the output boundary, and read-only guarantees.

Temp projects with a fake HOME / CLAUDE_CONFIG_DIR and a controlled PATH;
the plugin is reached through the Phase 48 fake `claude` CLI.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from tagteam import cli
from tagteam import diagnostics as dg
from tagteam import plugin as pl
from tagteam import registry as registry_mod
from tagteam import setup as su
from tests._plugin_env import fake_claude, fake_plugin, no_cli


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    cfg = home / ".claude"; cfg.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home / ".tagteam")
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / ".tagteam" / "projects.json")
    bindir = tmp_path / "bin"; bindir.mkdir()
    # The fake `claude` script needs sleep/cat; no system dir holds claude or codex.
    monkeypatch.setenv("PATH", os.pathsep.join([str(bindir), "/bin", "/usr/bin"]))
    no_cli(monkeypatch)
    proj = tmp_path / "proj"; proj.mkdir()
    return type("Env", (), {"home": home, "cfg": cfg, "bin": bindir, "proj": proj, "tmp": tmp_path})


def config(proj: Path, lead: str = "claude", reviewer: str = "codex", extra: str = "") -> None:
    (proj / "tagteam.yaml").write_text(
        extra or f"agents:\n  lead:\n    name: {lead}\n  reviewer:\n    name: {reviewer}\n")


def exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def write(proj: Path, rel: str, text: str) -> Path:
    p = proj / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def rules(findings, path=None):
    return sorted((f["rule"], f["severity"]) for f in findings if path is None or f["path"] == path)


def role(rep, name):
    return next(r for r in rep.roles if r["role"] == name)


def tree(*roots: Path) -> dict:
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            st = os.lstat(p)
            key = str(p)
            if p.is_symlink():
                out[key] = ("link", os.readlink(p))
            elif p.is_dir():
                out[key] = ("dir", st.st_mtime_ns)
            else:
                out[key] = (p.read_bytes(), st.st_mtime_ns)
        out[str(root)] = ("root", os.lstat(root).st_mtime_ns)
    return out


NORTHSTAR_PLAN = """---
name: plan
---
# Skill: /plan

Create or update phase plans for the project. Claude is always the lead for planning.
"""
NORTHSTAR_REVIEW = "# Review\n\nWhen done, run `/handoff-cycle` to hand back.\n"


# ---------------------------------------------------------------------------
# Legacy workflow findings
# ---------------------------------------------------------------------------

class TestLegacyFindings:
    def _northstar(self, proj):
        write(proj, ".claude/skills/plan/SKILL.md", NORTHSTAR_PLAN)
        write(proj, ".claude/skills/review/SKILL.md", NORTHSTAR_REVIEW)

    def test_contradicting_fixed_role_and_retired_command_warn(self, env):
        config(env.proj, lead="codex", reviewer="claude")
        self._northstar(env.proj)
        rep = dg.build_report(env.proj)
        assert rules(rep.findings, ".claude/skills/plan/SKILL.md") == [
            ("fixed-role", "warn"), ("legacy-skill-shape", "warn")]
        assert rules(rep.findings, ".claude/skills/review/SKILL.md") == [
            ("legacy-skill-shape", "warn"), ("retired-command", "warn")]
        fixed = next(f for f in rep.findings if f["rule"] == "fixed-role")
        assert fixed["line"] == 6 and "always the lead" in fixed["excerpt"]
        assert fixed["provenance"] == dg.PROVENANCE
        assert "lead: codex" in fixed["remediation"]

    def test_matching_fixed_role_is_info_retired_still_warn(self, env):
        config(env.proj, lead="claude", reviewer="codex")
        self._northstar(env.proj)
        rep = dg.build_report(env.proj)
        assert rules(rep.findings, ".claude/skills/plan/SKILL.md") == [
            ("fixed-role", "info"), ("legacy-skill-shape", "info")]
        assert ("retired-command", "warn") in rules(rep.findings, ".claude/skills/review/SKILL.md")
        assert rep.counts == {"warn": 2, "info": 2}

    def test_provider_mentions_and_skill_name_alone_are_not_findings(self, env):
        config(env.proj)
        write(env.proj, ".claude/skills/plan/SKILL.md",
              "# plan\nAsk Claude or Codex to sketch options; the lead decides.\n"
              "See /tagteam:handoff and .claude/skills/handoff-cycle.md history.\n")
        write(env.proj, ".claude/commands/review.md", "Codex reviews diffs. Claude writes code.\n")
        assert dg.build_report(env.proj).findings == []

    def test_instruction_file_line_number_and_dedupe(self, env):
        config(env.proj, lead="codex", reviewer="claude")
        write(env.proj, "CLAUDE.md", "# Rules\n\nintro\nThis project uses a Claude (Lead) / Codex (Reviewer) flow.\n")
        f = dg.build_report(env.proj).findings
        assert [(x["path"], x["rule"], x["severity"], x["line"]) for x in f] == [
            ("CLAUDE.md", "fixed-role", "warn", 4)]

    def test_custom_display_name_role_identity_uses_provider(self, env):
        config(env.proj, extra="agents:\n  lead:\n    name: Architect\n    command: claude --x\n"
                               "  reviewer:\n    name: Critic\n    command: codex\n")
        write(env.proj, "AGENTS.md", "Claude is the lead.\nCodex is the lead.\n")
        f = dg.build_report(env.proj).findings
        assert [(x["line"], x["severity"]) for x in f] == [(1, "info"), (2, "warn")]

    def test_managed_paths_are_framework_section_only(self, env):
        config(env.proj)
        write(env.proj, ".claude/skills/handoff/SKILL.md", "run /handoff-cycle\n")
        write(env.proj, "templates/phase_plan.md", "Claude is always the lead. /handoff-plan\n")
        write(env.proj, ".claude/skills/handoff-cycle.md", "run /handoff-cycle\n")
        rep = dg.build_report(env.proj)
        assert rep.findings == []
        paths = {i["path"] for i in rep.framework["items"]}
        assert {"templates/phase_plan.md", ".claude/skills/handoff/SKILL.md",
                ".claude/skills/handoff-cycle.md"} <= paths

    def test_retired_paths_show_only_while_a_copy_is_on_disk(self, env):
        """Phase 61: a project with no templates/ has none in the framework
        section; an untouched leftover shows as `retire`, an edited one as `keep`."""
        config(env.proj)
        assert not [i for i in dg.build_report(env.proj).framework["items"]
                    if i["path"].startswith(("templates/", "docs/checklists/"))]
        data = su.get_data_dir()
        write(env.proj, "templates/cycle.md", (data / "templates" / "cycle.md").read_text(encoding="utf-8"))
        write(env.proj, "docs/checklists/code_review.md", "ours\n")
        by = {i["path"]: i["action"] for i in dg.build_report(env.proj).framework["items"]}
        assert by["templates/cycle.md"] == "retire" and by["docs/checklists/code_review.md"] == "keep"
        assert "templates/feedback.md" not in by
        assert (env.proj / "templates" / "cycle.md").exists()        # doctor is read-only

    def test_unsupported_shapes_and_oversize(self, env):
        config(env.proj)
        outside = write(env.tmp, "outside.md", "Codex is always the lead. /handoff-plan\n")
        (env.proj / ".claude" / "skills").mkdir(parents=True)
        (env.proj / ".claude" / "skills" / "linked.md").symlink_to(outside)
        (env.proj / ".claude" / "skills" / "dirlink").symlink_to(env.tmp)
        (env.proj / ".claude" / "commands" / "odd.md").mkdir(parents=True)
        big = "x" * (dg.MARKDOWN_LIMIT - 100) + "\n/handoff-sync\n" + "tail /handoff-plan\n" * 10
        write(env.proj, ".claude/commands/big.md", big)
        (env.proj / "AGENTS.md").symlink_to(outside)
        rep = dg.build_report(env.proj)
        notes = {n["path"]: n["detail"] for n in rep.notes}
        assert "symlink" in notes[".claude/skills/linked.md"]
        assert "symlink" in notes[".claude/skills/dirlink"]
        assert "is a directory" in notes[".claude/commands/odd.md"]
        assert "symlink" in notes["AGENTS.md"]
        assert notes[".claude/commands/big.md"].startswith("truncated scan")
        assert {f["path"] for f in rep.findings} == {".claude/commands/big.md"}    # nothing via links
        assert rep.findings[0]["line"] == 2

    def test_symlinked_skills_directory_not_scanned(self, env):
        config(env.proj)
        d = env.tmp / "elsewhere" / "plan"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text(NORTHSTAR_PLAN)
        (env.proj / ".claude").mkdir()
        (env.proj / ".claude" / "skills").symlink_to(env.tmp / "elsewhere")
        rep = dg.build_report(env.proj)
        assert rep.findings == []
        assert any(n["path"] == ".claude/skills" and "symlink" in n["detail"] for n in rep.notes)

    def test_user_level_candidates_by_path_never_read(self, env, monkeypatch):
        config(env.proj)
        d = env.cfg / "skills" / "handoff-cycle"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text("Codex is always the lead. /handoff-cycle SENTINEL-U\n")
        opened = []
        real = dg.read_bounded
        monkeypatch.setattr(dg, "read_bounded", lambda root, rel, *a, **k: (opened.append((root, rel)),
                                                                            real(root, rel, *a, **k))[1])
        rep = dg.build_report(env.proj)
        assert rep.user_level == [str(d)] and rep.findings == []
        assert all(Path(root) == env.proj.resolve() for root, _ in opened)
        assert "SENTINEL-U" not in dg.format_report(rep)


# ---------------------------------------------------------------------------
# Roles: desktop and headless
# ---------------------------------------------------------------------------

class TestRoles:
    def test_executables_found_and_missing(self, env):
        config(env.proj)
        claude = exe(env.bin / "claude")
        rep = dg.build_report(env.proj)
        lead, rev = role(rep, "lead"), role(rep, "reviewer")
        assert (lead["desktop"]["state"], lead["desktop"]["path"]) == ("found", str(claude))
        assert rev["desktop"]["state"] == "missing" and rev["headless"]["state"] == "missing"

    @pytest.mark.parametrize("files, claude_injects, codex_injects", [
        (("AGENTS.md",), "AGENTS.md", None),
        (("CLAUDE.md",), None, "CLAUDE.md"),
        (("AGENTS.md", "CLAUDE.md"), "AGENTS.md", "CLAUDE.md"),
        ((), None, None),
    ])
    @pytest.mark.parametrize("lead, reviewer", [("claude", "codex"), ("codex", "claude")])
    def test_instruction_sources_both_assignments(self, env, files, claude_injects, codex_injects,
                                                  lead, reviewer):
        config(env.proj, lead=lead, reviewer=reviewer)
        for f in files:
            write(env.proj, f, f"# {f}\nproject rules\n")
        rep = dg.build_report(env.proj)
        by = {r["name"]: r for r in rep.roles}
        assert by["claude"]["desktop"]["autoloads"]["file"] == "CLAUDE.md"
        assert by["codex"]["desktop"]["autoloads"]["file"] == "AGENTS.md"
        assert by["claude"]["desktop"]["autoloads"]["state"] == ("found" if "CLAUDE.md" in files else "missing")
        assert by["claude"]["headless"]["injects"]["file"] == claude_injects
        assert by["codex"]["headless"]["injects"]["file"] == codex_injects
        states = {i["file"]: i["state"] for i in rep.instructions}
        assert states == {n: ("found" if n in files else "missing") for n in dg.INSTRUCTION_FILES}

    def test_injection_over_limit_truncates(self, env):
        from tagteam.headless import PROJECT_CONTEXT_MAX_CHARS
        config(env.proj)
        write(env.proj, "AGENTS.md", "a" * (PROJECT_CONTEXT_MAX_CHARS + 5))
        inj = role(dg.build_report(env.proj), "lead")["headless"]["injects"]
        assert inj["state"] == "injected" and inj["truncates"] is True
        assert "truncated at" in dg.format_report(dg.build_report(env.proj))

    def test_unknown_provider(self, env):
        config(env.proj, lead="Gemini", reviewer="codex")
        lead = role(dg.build_report(env.proj), "lead")
        assert lead["desktop"]["autoloads"]["state"] == "unknown"
        assert lead["headless"]["provider"] is None and lead["headless"]["source"] == "unresolved"
        assert lead["headless"]["state"] == "unknown"
        assert lead["headless"]["injects"]["state"] == "unknown"

    def test_explicit_unknown_headless_provider_still_described(self, env):
        config(env.proj, extra="agents:\n  lead:\n    name: claude\n    headless: {provider: gemini}\n"
                               "  reviewer:\n    name: codex\n")
        lead = role(dg.build_report(env.proj), "lead")
        assert lead["headless"]["provider"] == "gemini" and lead["headless"]["source"] == "explicit"
        assert lead["headless"]["state"] == "unknown" and lead["headless"]["reason"] == "unknown provider"

    def test_desktop_and_headless_are_separate_modes(self, env, capsys):
        fake_codex = exe(env.tmp / "opt" / "fake" / "codex")
        claude = exe(env.bin / "claude")
        config(env.proj, extra=(
            "agents:\n"
            "  lead:\n    name: Architect\n    command: claude --model x\n"
            f"    headless:\n      provider: codex\n      executable: {fake_codex}\n"
            "  reviewer:\n    name: codex\n"))
        write(env.proj, "AGENTS.md", "agents\n")
        write(env.proj, "CLAUDE.md", "claude\n")
        lead = role(dg.build_report(env.proj), "lead")
        assert lead["name"] == "Architect"
        assert lead["desktop"]["executable"] == "claude" and lead["desktop"]["path"] == str(claude)
        assert lead["desktop"]["autoloads"]["file"] == "CLAUDE.md"
        h = lead["headless"]
        assert (h["provider"], h["source"], h["executable"], h["state"], h["path"]) == (
            "codex", "explicit", str(fake_codex), "found", str(fake_codex))
        assert h["injects"]["file"] == "CLAUDE.md"         # codex auto-loads AGENTS.md itself
        assert dg.doctor_command([str(env.proj), "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        jl = next(r for r in data["roles"] if r["role"] == "lead")
        assert set(jl) == {"role", "name", "desktop", "headless"}
        assert jl["desktop"]["executable"] == "claude" and jl["headless"]["provider"] == "codex"

    def test_name_only_and_command_inferred_sources(self, env):
        config(env.proj, extra="agents:\n  lead:\n    name: claude\n"
                               "  reviewer:\n    name: Critic\n    command: codex --x\n")
        rep = dg.build_report(env.proj)
        assert role(rep, "lead")["headless"]["source"] == "inferred from name"
        assert role(rep, "lead")["desktop"]["executable"] == "claude"
        assert role(rep, "reviewer")["headless"]["source"] == "inferred from command"
        assert role(rep, "reviewer")["headless"]["provider"] == "codex"

    def test_unconfigured_roles(self, env, capsys):
        rep = dg.build_report(env.proj)
        assert rep.roles == [] and "not configured" in dg.format_report(rep)


# ---------------------------------------------------------------------------
# Output boundary and config shapes
# ---------------------------------------------------------------------------

def _all_output(proj, capsys) -> str:
    capsys.readouterr()
    assert dg.doctor_command([str(proj)]) == 0
    assert dg.doctor_command([str(proj), "--json"]) == 0
    return capsys.readouterr().out


class TestOutputBoundary:
    def test_no_argument_or_secret_is_emitted(self, env, capsys):
        exe(env.bin / "claude"); exe(env.bin / "codex")
        config(env.proj, extra=(
            "agents:\n"
            "  lead:\n    name: claude\n    command: claude --api-key SENTINEL-1\n"
            "    headless:\n      args: [--token, SENTINEL-3]\n"
            "  reviewer:\n    name: codex\n    command: FOO=SENTINEL-2 codex\n"))
        write(env.proj, ".mcp.json", json.dumps({"mcpServers": {"datadog": {
            "command": "npx", "args": ["SENTINEL-6"], "env": {"DD_API_KEY": "SENTINEL-4"},
            "headers": {"Authorization": "SENTINEL-5"}}}}))
        out = _all_output(env.proj, capsys)
        assert "SENTINEL" not in out
        assert "datadog" in out
        rep = dg.build_report(env.proj)
        assert role(rep, "reviewer")["desktop"]["state"] == "unknown"
        assert role(rep, "reviewer")["desktop"]["reason"] == "launch command not parsed"
        assert role(rep, "lead")["desktop"]["executable"] == "claude"

    def test_unsplittable_command_echoes_nothing(self, env):
        config(env.proj, extra="agents:\n  lead:\n    name: claude\n    command: \"claude 'SENTINEL-7\"\n"
                               "  reviewer:\n    name: codex\n")
        rep = dg.build_report(env.proj)
        assert role(rep, "lead")["desktop"]["state"] == "unknown"
        assert "SENTINEL" not in dg.format_report(rep)

    @pytest.mark.parametrize("rel", [".mcp.json", ".claude/settings.json"])
    def test_symlinked_config_to_home_secret_never_opened(self, env, capsys, rel):
        config(env.proj)
        secret = env.home / "creds.json"
        secret.write_text(json.dumps({"mcpServers": {"SENTINEL-H": {}},
                                      "hooks": {"SENTINEL-H": [{"matcher": "SENTINEL-H"}]}}))
        p = env.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(secret)
        out = _all_output(env.proj, capsys)
        assert "SENTINEL-H" not in out
        rep = dg.build_report(env.proj)
        entry = rep.tools["mcp"] if rel == ".mcp.json" else rep.tools["claude_settings"][0]
        assert entry["state"] == "unsupported" and "symlink" in entry["detail"]

    def test_symlinked_claude_parent(self, env, capsys):
        config(env.proj)
        real = env.home / "dotclaude"; real.mkdir()
        (real / "settings.json").write_text(json.dumps({"hooks": {"SENTINEL-P": []}}))
        (env.proj / ".claude").symlink_to(real)
        out = _all_output(env.proj, capsys)
        assert "SENTINEL-P" not in out
        for s in dg.build_report(env.proj).tools["claude_settings"]:
            assert s["state"] == "unsupported" and ".claude is a symlink" in s["detail"]

    def test_directory_fifo_oversize_malformed_wrong_type(self, env, capsys):
        config(env.proj)
        (env.proj / ".mcp.json").mkdir()
        (env.proj / ".claude").mkdir()
        os.mkfifo(env.proj / ".claude" / "settings.json")          # opening it would block
        write(env.proj, ".claude/settings.local.json", "{not json")
        rep = dg.build_report(env.proj)
        assert rep.tools["mcp"]["state"] == "unsupported" and "directory" in rep.tools["mcp"]["detail"]
        s, local = rep.tools["claude_settings"]
        assert s["state"] == "unsupported" and "not a regular file" in s["detail"]
        assert local["state"] == "unknown" and "malformed" in local["detail"]
        os.rmdir(env.proj / ".mcp.json")
        write(env.proj, ".mcp.json", json.dumps({"mcpServers": {"x": {"env": {"K": "v" * dg.JSON_LIMIT}}}}))
        assert dg.build_report(env.proj).tools["mcp"]["state"] == "skipped"
        write(env.proj, ".mcp.json", json.dumps(["SENTINEL-L"]))
        write(env.proj, ".claude/settings.local.json", json.dumps({"hooks": ["x"]}))
        rep = dg.build_report(env.proj)
        assert rep.tools["mcp"]["state"] == "unknown"
        assert rep.tools["claude_settings"][1]["state"] == "unknown"
        assert "SENTINEL-L" not in _all_output(env.proj, capsys)

    def test_evidence_is_the_match_not_the_line(self, env, capsys):
        config(env.proj)                                  # claude lead, codex reviewer
        write(env.proj, "AGENTS.md",
              "Use /handoff-plan --api-key SENTINEL_ARG for planning.\n"
              "Codex is the lead; export TOKEN=SENTINEL_ROLE before starting.\n"
              "ai_handoff SENTINEL_PKG\n")
        write(env.proj, ".claude/skills/plan/SKILL.md",
              "Claude (Lead) with --secret SENTINEL_SKILL and /handoff-cycle SENTINEL_SKILL2\n")
        rep = dg.build_report(env.proj)
        got = sorted((f["path"], f["line"], f["rule"], f["excerpt"]) for f in rep.findings)
        assert got == [
            (".claude/skills/plan/SKILL.md", 0, "legacy-skill-shape", "skill name 'plan' with 2 content hit(s)"),
            (".claude/skills/plan/SKILL.md", 1, "fixed-role", "Claude (Lead)"),
            (".claude/skills/plan/SKILL.md", 1, "retired-command", "/handoff-cycle"),
            ("AGENTS.md", 1, "retired-command", "/handoff-plan"),
            ("AGENTS.md", 2, "fixed-role", "Codex is the lead"),
            ("AGENTS.md", 3, "retired-command", "ai_handoff"),
        ]
        assert "SENTINEL" not in _all_output(env.proj, capsys)

    def test_hook_names_and_scope_note(self, env):
        config(env.proj, lead="codex", reviewer="claude")
        write(env.proj, ".claude/settings.json", json.dumps({"hooks": {
            "PreToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "SENTINEL-C"}]}]}}))
        rep = dg.build_report(env.proj)
        assert rep.tools["claude_settings"][0]["hooks"] == ["PreToolUse[Edit|Write]"]
        note = rep.protections[0]
        assert "apply to Claude processes only" in note and "do not bind codex (codex)" in note
        assert "SENTINEL-C" not in dg.format_report(rep)

    def test_no_hook_note_without_hooks(self, env):
        config(env.proj)
        assert not any("hooks" in n for n in dg.build_report(env.proj).protections)


class TestRoleConfigRead:
    """tagteam.yaml goes through the bounded reader too (impl review r1)."""

    def _no_unbounded_config_reads(self, monkeypatch):
        from tagteam import config as config_mod
        from tagteam import framework as fw

        def refuse(*a, **k):
            raise AssertionError("read_config must not be called by doctor")
        monkeypatch.setattr(config_mod, "read_config", refuse)
        monkeypatch.setattr(fw, "read_config", refuse)

    def test_symlinked_config_not_followed(self, env, capsys, monkeypatch):
        outside = write(env.tmp, "outside.yaml",
                        "agents:\n  lead: {name: SENTINEL_OUTSIDE}\n  reviewer: {name: codex}\n")
        (env.proj / "tagteam.yaml").symlink_to(outside)
        write(env.proj, "AGENTS.md", "Use /handoff-plan.\n")
        self._no_unbounded_config_reads(monkeypatch)
        rep = dg.build_report(env.proj)
        assert rep.roles == []
        assert rep.notes[0] == {"path": "tagteam.yaml",
                                "detail": "not read: unsupported filesystem shape (tagteam.yaml is a symlink)"}
        assert "tagteam.yaml not read: unsupported filesystem shape" in dg.format_report(rep)
        assert "SENTINEL_OUTSIDE" not in _all_output(env.proj, capsys)
        assert [f["rule"] for f in dg.legacy_findings(env.proj)] == ["retired-command"]

    @pytest.mark.parametrize("content, detail", [
        ("agents:\n  lead: {name: claude}\n  reviewer: {name: codex}\n# " + "x" * dg.JSON_LIMIT + "\n",
         "not read: over size limit (64 KB)"),
        ("agents: [unclosed\n", "not read: malformed tagteam.yaml"),
        ("- just\n- a list\n", "not read: malformed tagteam.yaml"),
    ])
    def test_oversized_and_malformed_config(self, env, monkeypatch, content, detail):
        write(env.proj, "tagteam.yaml", content)
        write(env.proj, "CLAUDE.md", "Codex is the lead.\n")
        self._no_unbounded_config_reads(monkeypatch)
        rep = dg.build_report(env.proj)
        assert rep.roles == [] and rep.notes[0] == {"path": "tagteam.yaml", "detail": detail}
        assert [(f["rule"], f["severity"]) for f in dg.legacy_findings(env.proj)] == [("fixed-role", "info")]

    def test_fifo_config_does_not_block(self, env):
        import subprocess
        os.mkfifo(env.proj / "tagteam.yaml")
        write(env.proj, "AGENTS.md", "Use /handoff-plan.\n")
        run_env = {**os.environ, "TAGTEAM_CLAUDE_BIN": ""}
        repo = Path(__file__).resolve().parents[1]
        for code in (f"from tagteam import diagnostics as d; import sys; sys.exit(d.doctor_command([{str(env.proj)!r}]))",
                     f"from tagteam import diagnostics as d; print(len(d.legacy_findings({str(env.proj)!r})))"):
            r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=run_env,
                               capture_output=True, text=True, timeout=30)
            assert r.returncode == 0, r.stderr
        doctor = subprocess.run([sys.executable, "-m", "tagteam", "doctor", str(env.proj)], cwd=repo,
                                env=run_env, capture_output=True, text=True, timeout=30)
        assert doctor.returncode == 0
        assert "tagteam.yaml not read: unsupported filesystem shape (tagteam.yaml is not a regular file)" \
            in doctor.stdout


# ---------------------------------------------------------------------------
# Plugin availability vs plugin_status
# ---------------------------------------------------------------------------

class TestPluginAvailability:
    def _both(self, proj):
        return dg.plugin_availability(proj), pl.plugin_status(proj)

    def test_missing_cli_is_unknown(self, env):
        a, s = self._both(env.proj)
        assert a == {"state": "unknown", "reason": "claude CLI not found"}
        assert s.installed is False

    def test_timeout_is_unknown(self, env, monkeypatch):
        monkeypatch.setattr(pl, "PLUGIN_LIST_TIMEOUT_S", 0.2)
        fake_claude(env.tmp, monkeypatch, stdout="[]", sleep=2)
        a, s = self._both(env.proj)
        assert a["state"] == "unknown" and "timed out" in a["reason"] and s.installed is False

    @pytest.mark.parametrize("stdout, code, why", [("not json", 0, "claude plugin list"),
                                                   ("[]", 3, "exited 3"),
                                                   ('{"a": 1}', 0, "not a JSON array")])
    def test_failed_discovery_is_unknown(self, env, monkeypatch, stdout, code, why):
        fake_claude(env.tmp, monkeypatch, stdout=stdout, exit_code=code)
        a, s = self._both(env.proj)
        assert a["state"] == "unknown" and why in a["reason"] and s.installed is False

    def test_confirmed_absent(self, env, monkeypatch):
        fake_claude(env.tmp, monkeypatch, stdout="[]")
        a, s = self._both(env.proj)
        assert a["state"] == "missing" and s.installed is False

    def test_disabled_broken_found(self, env, monkeypatch):
        fake_plugin(env.tmp, monkeypatch, enabled=False)
        a, s = self._both(env.proj)
        assert a["state"] == "configured, disabled" and s.installed is False
        (env.tmp / "b").mkdir()
        fake_plugin(env.tmp / "b", monkeypatch, broken=True)
        a, s = self._both(env.proj)
        assert a["state"] == "configured, broken" and s.installed is False
        (env.tmp / "c").mkdir()
        fake_plugin(env.tmp / "c", monkeypatch)
        a, s = self._both(env.proj)
        assert a == {"state": "found", "reason": "user scope"} and s.installed is True

    def test_report_does_not_say_not_installed_when_unknown(self, env):
        config(env.proj)
        text = dg.format_report(dg.build_report(env.proj))
        assert "plugin    unknown (claude CLI not found)" in text
        assert "not installed" not in text


# ---------------------------------------------------------------------------
# Command, read-only, setup/upgrade pointer
# ---------------------------------------------------------------------------

class TestCommand:
    def test_exit_codes(self, env, capsys):
        assert dg.doctor_command([str(env.tmp / "nope")]) == 2
        f = write(env.tmp, "file", "x")
        assert dg.doctor_command([str(f)]) == 2
        assert dg.doctor_command(["--bogus"]) == 2
        assert dg.doctor_command([str(env.proj), str(env.proj)]) == 2

    def test_json_matches_text_counts(self, env, capsys):
        config(env.proj, lead="codex", reviewer="claude")
        write(env.proj, ".claude/skills/plan/SKILL.md", NORTHSTAR_PLAN)
        write(env.proj, "AGENTS.md", "Codex is the lead.\n")
        capsys.readouterr()
        assert dg.doctor_command([str(env.proj), "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["schema"] == 1
        assert data["counts"] == {"warn": 2, "info": 1}
        assert dg.doctor_command([str(env.proj)]) == 0
        assert capsys.readouterr().out.rstrip().endswith("findings: 2 warn, 1 info")

    def test_read_only_tree_and_home_unchanged(self, env, capsys, monkeypatch):
        config(env.proj)
        write(env.proj, ".claude/skills/plan/SKILL.md", NORTHSTAR_PLAN)
        write(env.proj, ".mcp.json", json.dumps({"mcpServers": {"a": {}}}))
        d = env.cfg / "skills" / "handoff-x"; d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
        fake_plugin(env.tmp, monkeypatch)
        before = tree(env.proj, env.home)
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        monkeypatch.setattr(sys, "argv", ["tagteam", "doctor", str(env.proj)])
        assert cli.main() == 0
        monkeypatch.setattr(sys, "argv", ["tagteam", "doctor", str(env.proj), "--json"])
        assert cli.main() == 0
        assert tree(env.proj, env.home) == before
        assert not (env.proj / ".tagteam").exists()

    def test_setup_and_upgrade_preview_unchanged(self, env, capsys, monkeypatch):
        config(env.proj)
        write(env.proj, ".claude/skills/review/SKILL.md", NORTHSTAR_REVIEW)
        registry_mod.register_project(str(env.proj))
        before = tree(env.proj, env.home)
        assert su.main(str(env.proj), report_user_skills=False, preview=True) == 0
        assert cli.upgrade_command(["--preview"]) == 0
        assert tree(env.proj, env.home) == before


class TestSetupPointer:
    def test_pointer_with_findings_exit_code_unchanged(self, env, capsys):
        config(env.proj)
        capsys.readouterr()
        clean = su.main(str(env.proj), report_user_skills=False, preview=True)
        out = capsys.readouterr().out
        assert "legacy workflow finding" not in out
        write(env.proj, ".claude/skills/review/SKILL.md", NORTHSTAR_REVIEW)
        assert su.main(str(env.proj), report_user_skills=False, preview=True) == clean
        out = capsys.readouterr().out
        assert (f"note: 2 legacy workflow findings (2 warn) — run: tagteam doctor {env.proj.resolve()}"
                in out)

    def test_pointer_on_apply_and_per_project_upgrade(self, env, capsys):
        config(env.proj)
        write(env.proj, "CLAUDE.md", "Use /handoff-plan to start.\n")
        other = env.tmp / "other"; other.mkdir()
        config(other)
        capsys.readouterr()
        assert su.main(str(env.proj), report_user_skills=False) == 0
        assert "note: 1 legacy workflow finding (1 warn)" in capsys.readouterr().out
        assert su.main(str(other), report_user_skills=False) == 0
        assert "legacy workflow finding" not in capsys.readouterr().out
        assert cli.upgrade_command([]) == 0
        out = capsys.readouterr().out
        assert out.count("legacy workflow finding") == 1
        section = out.split(f"Project: {env.proj.resolve()}")[1].split("Project:")[0]
        assert "legacy workflow finding" in section

    def test_scan_failure_never_breaks_setup(self, env, capsys, monkeypatch):
        config(env.proj)
        def boom(_):
            raise RuntimeError("x")
        monkeypatch.setattr(dg, "legacy_findings", boom)
        assert su.main(str(env.proj), report_user_skills=False, preview=True) == 0
        assert "note: legacy workflow scan failed (RuntimeError)" in capsys.readouterr().out
