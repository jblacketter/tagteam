"""Tests for the headless turn engine (Phase 31).

Unit tests cover argv construction/validation, start-command parsing,
prompt composition, event rendering, usage parsing, and transition
verification. End-to-end tests drive `HeadlessEngine.run_owed_turn`
against a fake agent CLI (`tests/fixtures/fake_agent.py`) installed into
a temp PATH dir as `claude`/`codex` — a shell script on POSIX, a `.cmd`
shim on Windows — so the real `shutil.which`/PATHEXT resolution path is
exercised on both.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tagteam import db, headless as h
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod
from tagteam.config import read_config, validate_config, get_headless_spec

REPO = Path(__file__).resolve().parents[1]
FAKE = REPO / "tests" / "fixtures" / "fake_agent.py"
FIXTURES = REPO / "tests" / "fixtures" / "headless"
SKILL_SRC = REPO / "tagteam" / "data" / ".claude" / "skills" / "handoff" / "SKILL.md"

STD_CMD = h.STANDARD_TURN_COMMAND


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    """A minimal tagteam project (config + skill contract), cwd-resolved."""
    (tmp_path / "tagteam.yaml").write_text(
        "agents:\n"
        "  lead:\n    name: Claude\n"
        "  reviewer:\n    name: Codex\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    skill = tmp_path / h.SKILL_RELPATH
    skill.parent.mkdir(parents=True)
    skill.write_text(SKILL_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(state_mod, "_cached_project_root", None, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _install_fake(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    for name in ("claude", "codex"):
        if sys.platform == "win32":
            (bin_dir / f"{name}.cmd").write_text(
                "@echo off\r\n"
                f"set FAKE_AGENT_FLAVOR={name}\r\n"
                f"\"{py}\" \"{FAKE}\" %*\r\n",
                encoding="utf-8",
            )
        else:
            p = bin_dir / name
            p.write_text(
                "#!/bin/sh\n"
                f"FAKE_AGENT_FLAVOR={name} exec \"{py}\" \"{FAKE}\" \"$@\"\n",
                encoding="utf-8",
            )
            p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_path(tmp_path, monkeypatch):
    """Put fake `claude`/`codex` shims first on PATH."""
    bin_dir = tmp_path / "fakebin"
    _install_fake(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_AGENT_MODE", "ok")
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.15")
    monkeypatch.delenv("FAKE_AGENT_CAPTURE", raising=False)
    monkeypatch.delenv("FAKE_AGENT_PIDFILE", raising=False)
    # Make sure the fake's `python -m tagteam` finds this checkout.
    monkeypatch.setenv("PYTHONPATH", str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    return bin_dir


def _engine(project: Path, **kw) -> h.HeadlessEngine:
    cfg = read_config(project / "tagteam.yaml")
    logs: list[str] = []
    notes: list[tuple[str, str]] = []
    eng = h.HeadlessEngine(project, cfg, lead_name="Claude", reviewer_name="Codex",
                           log=logs.append, notify=lambda t, m: notes.append((t, m)),
                           **kw)
    eng._test_logs = logs      # type: ignore[attr-defined]
    eng._test_notes = notes    # type: ignore[attr-defined]
    errors = eng.validate()
    assert errors == [], errors
    return eng


def _init_cycle(project: Path, phase="feat-x", ctype="plan"):
    cycle_mod.init_cycle(phase, ctype, "Claude", "Codex", "initial", str(project),
                         updated_by="Claude")
    return state_mod.read_state(str(project))


def _usage_rows(project: Path):
    conn = db.connect(project_dir=str(project))
    try:
        return db.get_usage(conn)
    finally:
        conn.close()


def _diag_kinds(project: Path):
    conn = db.connect(project_dir=str(project))
    try:
        return [r[0] for r in conn.execute("SELECT kind FROM diagnostics ORDER BY id")]
    finally:
        conn.close()


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # zombie check: on POSIX a killed-but-unreaped grandchild still answers
    # kill(0); try waitpid non-blocking to reap if it's ours, else look at
    # /proc when available.
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False
    except ChildProcessError:
        pass
    return True


# ---------------------------------------------------------------------------
# argv construction / validation
# ---------------------------------------------------------------------------

class TestBuildArgv:
    @pytest.mark.parametrize("adapter", [h.CLAUDE, h.CODEX], ids=["claude", "codex"])
    @pytest.mark.parametrize("bad", [
        ["fix the tests"], ["-"], ["--"], ["--output-format=json"],
        ["--output-format", "json"], ["-C", "dir"], ["-Cdir"], ["--cd=dir"],
        ["--print"], ["-p"], ["--model", "--"], ["--model", "-"], ["--model"],
        ["--model", "opus", "extra positional"], ["--json"], ["--verbose"],
        ["--not-a-real-flag"], [""],
    ])
    def test_rejected_forms(self, adapter, bad):
        with pytest.raises(h.HeadlessConfigError) as ei:
            h.validate_user_args(adapter, bad)
        # The offending token is named in the message.
        assert any(tok in str(ei.value) for tok in bad if tok) or bad == [""]

    def test_args_must_be_list_of_strings(self):
        with pytest.raises(h.HeadlessConfigError):
            h.validate_user_args(h.CLAUDE, "--model opus")  # type: ignore[arg-type]
        with pytest.raises(h.HeadlessConfigError):
            h.validate_user_args(h.CLAUDE, ["--model", 3])  # type: ignore[list-item]

    def test_claude_defaults_and_ordering(self):
        argv = h.build_argv(h.CLAUDE, "/x/claude", ["--model", "opus"], "/proj")
        assert argv[:5] == ["/x/claude", "-p", "--output-format", "stream-json", "--verbose"]
        assert "--permission-mode" in argv and "acceptEdits" in argv
        assert "--allowedTools" in argv
        assert argv[-2:] == ["--model", "opus"]
        assert "-" not in argv  # claude reads stdin with -p and no positional

    def test_claude_permission_override_drops_default(self):
        argv = h.build_argv(h.CLAUDE, "claude", ["--permission-mode", "bypassPermissions"], "/p")
        assert argv.count("--permission-mode") == 1
        assert "acceptEdits" not in argv
        assert "bypassPermissions" in argv
        argv2 = h.build_argv(h.CLAUDE, "claude", ["--dangerously-skip-permissions"], "/p")
        assert "--permission-mode" not in argv2

    def test_claude_variadic_tools_override(self):
        argv = h.build_argv(h.CLAUDE, "claude", ["--allowedTools", "Bash(git *)", "Read", "--model", "x"], "/p")
        i = argv.index("--allowedTools")
        assert argv[i + 1:i + 3] == ["Bash(git *)", "Read"]
        assert argv.count("--allowedTools") == 1
        assert "Glob" not in argv  # default tools family dropped

    def test_codex_defaults_ordering_and_marker_last(self):
        argv = h.build_argv(h.CODEX, "/x/codex", [], "/proj")
        assert argv[:5] == ["/x/codex", "exec", "--json", "-C", "/proj"]
        assert argv[-2:] == ["--skip-git-repo-check", "-"]
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert argv[argv.index("-c") + 1] == "approval_policy=never"

    def test_codex_overrides(self):
        argv = h.build_argv(h.CODEX, "codex",
                            ["-c", "approval_policy=untrusted", "--sandbox", "danger-full-access",
                             "--model", "o3"], "/proj")
        assert "approval_policy=never" not in argv
        assert "workspace-write" not in argv
        assert argv[-1] == "-"
        assert argv[-2] == "--skip-git-repo-check"
        # user args land before the tail
        assert argv.index("o3") < argv.index("--skip-git-repo-check")

    def test_codex_equals_and_attached_short_forms_normalized(self):
        argv = h.build_argv(h.CODEX, "codex", ["--model=o3", "-mgpt"], "/p")
        assert ["--model", "o3", "-m", "gpt"] == argv[argv.index("--model"):argv.index("--model") + 4]

    def test_reserved_family_checked_on_normalized_name(self):
        for bad in (["--output-format=stream-json"], ["--output-format"]):
            with pytest.raises(h.HeadlessConfigError, match="reserved"):
                h.validate_user_args(h.CLAUDE, bad)
        for bad in (["--cd=/x"], ["-C/x"], ["--json"]):
            with pytest.raises(h.HeadlessConfigError, match="reserved"):
                h.validate_user_args(h.CODEX, bad)


class TestProviderAndExecutable:
    def test_inference_order(self):
        cfg = {"agents": {"lead": {"name": "Zed", "command": "/opt/bin/codex --foo",
                                   "headless": {"provider": "claude"}},
                          "reviewer": {"name": "Reviewer", "command": "codex-next -x"}}}
        assert get_headless_spec(cfg, "lead")["provider"] == "claude"     # explicit wins
        assert get_headless_spec(cfg, "reviewer")["provider"] == "codex"  # command basename
        cfg2 = {"agents": {"lead": {"name": "Claude"}, "reviewer": {"name": "Gemini"}}}
        assert get_headless_spec(cfg2, "lead")["provider"] == "claude"    # name
        assert get_headless_spec(cfg2, "reviewer")["provider"] is None    # uninferable
        # explicit-but-unknown provider is reported, never inferred around
        cfg3 = {"agents": {"lead": {"name": "Claude", "headless": {"provider": "gemini"}},
                           "reviewer": {"name": "Codex"}}}
        assert get_headless_spec(cfg3, "lead")["provider"] == "gemini"
        # non-list args are passed through raw so startup can reject them
        cfg4 = {"agents": {"lead": {"name": "Claude", "headless": {"args": "--model opus"}},
                           "reviewer": {"name": "Codex"}}}
        assert get_headless_spec(cfg4, "lead")["args"] == "--model opus"

    def test_validate_config_rejects_bad_headless(self):
        base = {"agents": {"lead": {"name": "A"}, "reviewer": {"name": "B"}}}
        base["agents"]["lead"]["headless"] = {"provider": "gemini"}
        assert any("provider" in e for e in validate_config(base))
        base["agents"]["lead"]["headless"] = {"args": "--model opus"}
        assert any("list of strings" in e for e in validate_config(base))
        base["agents"]["lead"]["headless"] = {"args": ["--model", 3]}
        assert any("only strings" in e for e in validate_config(base))
        base["agents"]["lead"]["headless"] = {"bogus": 1}
        assert any("unknown keys" in e for e in validate_config(base))
        base["agents"]["lead"]["headless"] = {"provider": "claude", "args": ["--model", "x"],
                                              "executable": "/x/claude"}
        assert validate_config(base) == []

    def test_resolve_executable_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(h.HeadlessConfigError, match="not found on PATH"):
            h.resolve_executable("claude", None)
        with pytest.raises(h.HeadlessConfigError, match="does not exist"):
            h.resolve_executable("claude", str(tmp_path / "nope" / "claude"))
        with pytest.raises(h.HeadlessConfigError, match="not found on PATH"):
            h.resolve_executable("claude", "some-missing-name")
        with pytest.raises(h.HeadlessConfigError, match="not a file"):
            h.resolve_executable("claude", str(tmp_path))  # a directory
        with pytest.raises(h.HeadlessConfigError, match="must be a string"):
            h.resolve_executable("claude", 42)  # type: ignore[arg-type]
        if sys.platform != "win32":
            f = tmp_path / "notexec"
            f.write_text("#!/bin/sh\n"); f.chmod(0o644)
            with pytest.raises(h.HeadlessConfigError, match="not executable"):
                h.resolve_executable("claude", str(f))

    def test_engine_validate_reports_errors(self, project, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        eng = h.HeadlessEngine(project, read_config(project / "tagteam.yaml"),
                               lead_name="Claude", reviewer_name="Codex", log=lambda m: None)
        errors = eng.validate()
        assert len(errors) == 2 and all("not found on PATH" in e for e in errors)

    def test_engine_validate_reserved_args_and_unknown_provider(self, project, fake_path):
        (project / "tagteam.yaml").write_text(
            "agents:\n"
            "  lead:\n    name: Claude\n    headless:\n      args: ['--output-format', 'json']\n"
            "  reviewer:\n    name: Gemini\n", encoding="utf-8")
        eng = h.HeadlessEngine(project, read_config(project / "tagteam.yaml"),
                               lead_name="Claude", reviewer_name="Gemini", log=lambda m: None)
        errors = eng.validate()
        assert any("reserved" in e for e in errors)
        assert any("cannot determine headless provider" in e for e in errors)

    @pytest.mark.parametrize("headless_yaml,needle", [
        ("      args: \"--model opus\"\n", "list of strings"),
        ("      provider: gemini\n", "gemini"),
        ("      executable: 42\n", "executable"),
        ("      bogus: 1\n", "unknown keys"),
    ])
    def test_engine_and_watch_reject_invalid_headless_config(
            self, project, fake_path, headless_yaml, needle):
        """Reviewer finding (impl r1): invalid headless config must fail at
        startup even though the executables resolve and the name would
        infer a valid provider."""
        (project / "tagteam.yaml").write_text(
            "agents:\n"
            "  lead:\n    name: Claude\n    headless:\n" + headless_yaml +
            "  reviewer:\n    name: Codex\n", encoding="utf-8")
        eng = h.HeadlessEngine(project, read_config(project / "tagteam.yaml"),
                               lead_name="Claude", reviewer_name="Codex", log=lambda m: None)
        errors = eng.validate()
        assert errors and any(needle in e for e in errors), errors
        assert "lead" not in eng.roles  # nothing was launched/configured for lead
        from tagteam import watcher
        assert watcher.watch_command(["--mode", "headless"]) == 1

    def test_documented_status_vocabulary_matches_db(self):
        """The plan's usage.status vocabulary must stay aligned with
        db.USAGE_STATUSES (reviewer request, impl r2)."""
        import re
        plan = (REPO / "docs" / "phases" / "headless-turn-engine-30-arc.md").read_text()
        m = re.search(r"status\s+TEXT NOT NULL,\s+--\s*([a-z_ |]+)", plan)
        assert m, "status vocabulary comment not found in plan schema block"
        documented = {t.strip() for t in m.group(1).split("|")}
        assert documented == set(db.USAGE_STATUSES)
        assert documented == {h.OUTCOME_OK, h.OUTCOME_TIMEOUT, h.OUTCOME_NONZERO,
                              h.OUTCOME_NO_ROUND, h.OUTCOME_SPAWN_FAILED,
                              h.OUTCOME_CANCELLED}

    @pytest.mark.parametrize("headless_yaml,needle", [
        ('      args: "--model opus"\n', "list of strings"),
        ("      provider: gemini\n", "gemini"),
        ("      bogus: 1\n", "unknown keys"),
    ])
    def test_fallback_parser_startup_rejects_invalid_headless(
            self, project, fake_path, monkeypatch, headless_yaml, needle):
        """Reviewer finding (impl r2): the no-PyYAML fallback parser must not
        coerce invalid headless config into something that launches."""
        from tagteam import config as config_mod
        monkeypatch.setattr(config_mod, "HAS_YAML", False)
        (project / "tagteam.yaml").write_text(
            "agents:\n"
            "  lead:\n    name: Claude\n    headless:\n" + headless_yaml +
            "  reviewer:\n    name: Codex\n", encoding="utf-8")
        cfg = read_config(project / "tagteam.yaml")
        assert cfg is not None and "headless" in cfg["agents"]["lead"]
        assert any(needle in e for e in validate_config(cfg)), validate_config(cfg)
        eng = h.HeadlessEngine(project, cfg, lead_name="Claude", reviewer_name="Codex",
                               log=lambda m: None)
        errors = eng.validate()
        assert errors and any(needle in e for e in errors), errors
        from tagteam import watcher
        assert watcher.watch_command(["--mode", "headless"]) == 1

    def test_fallback_parser_accepts_valid_headless(self, monkeypatch):
        from tagteam import config as config_mod
        cfg = config_mod._read_config_fallback(
            "agents:\n  lead:\n    name: Claude\n    headless:\n"
            "      provider: claude\n      executable: /x/claude\n"
            "      args:\n        - --model\n        - opus\n"
            "  reviewer:\n    name: Codex\n    headless:\n      args: [\"-m\", \"o3\"]\n")
        assert cfg["agents"]["lead"]["headless"] == {
            "provider": "claude", "executable": "/x/claude", "args": ["--model", "opus"]}
        assert cfg["agents"]["reviewer"]["headless"] == {"args": ["-m", "o3"]}
        assert validate_config(cfg) == []

    def test_engine_validate_uses_which_through_shims(self, project, fake_path):
        eng = _engine(project)
        assert eng.roles["lead"].provider == "claude"
        assert eng.roles["reviewer"].provider == "codex"
        assert Path(eng.roles["lead"].executable).parent == fake_path


# ---------------------------------------------------------------------------
# start-command parsing / prompt composition
# ---------------------------------------------------------------------------

class TestStartCommandAndPrompt:
    @pytest.mark.parametrize("cmd,expected", [
        ("/handoff start feat-x", ("feat-x", "plan")),
        ("/handoff start feat-x impl", ("feat-x", "impl")),
        ("  /handoff start a1-b2   impl ", ("a1-b2", "impl")),
        ("/handoff start Feat-X", None),
        ("/handoff start feat_x", None),
        ("/handoff start feat-x --roadmap", None),
        ("/handoff start --roadmap feat-x", None),
        ("/handoff start", None),
        ("/handoff", None),
        (STD_CMD, None),
        ("please /handoff start feat-x", None),
        (None, None),
    ])
    def test_parse_start_command(self, cmd, expected):
        assert h.parse_start_command(cmd) == expected

    def test_boundary_clause_only_for_impl_start(self):
        base = dict(role="lead", agent_name="Claude", project_root="/p",
                    skill_text="CONTRACT", tail_entries=[{"round": 1}], tail_n=3)
        p_impl = h.compose_prompt(state={"command": "/handoff start feat-x impl"}, **base)
        assert "plan-approved boundary" in p_impl
        assert "docs/phases/feat-x.md" in p_impl
        p_plan = h.compose_prompt(state={"command": "/handoff start feat-x"}, **base)
        assert "plan-approved boundary" not in p_plan
        p_std = h.compose_prompt(state={"command": STD_CMD}, **base)
        assert "plan-approved boundary" not in p_std
        for p in (p_impl, p_plan, p_std):
            assert "=== COMMAND ===" in p and "CONTRACT" in p
            assert '--updated-by "Claude"' in p
            assert '{"round": 1}' in p


# ---------------------------------------------------------------------------
# event rendering + usage parsing against real captured fixtures
# ---------------------------------------------------------------------------

class TestParsers:
    def test_claude_usage_from_fixture(self):
        lines = (FIXTURES / "claude_stream.jsonl").read_text().splitlines()
        u = h.parse_usage("claude", lines)
        assert u["model"] == "claude-fable-5"
        assert u["session_id"] and u["input_tokens"] == 6 and u["output_tokens"] == 219
        assert u["cache_read_tokens"] == 74274 and u["cache_write_tokens"] == 10966
        assert u["cost_usd"] == pytest.approx(0.304604) and u["num_turns"] == 3
        # Phase 55: per-model split, token fields only
        assert json.loads(u["model_usage_json"]) == {"claude-fable-5": {
            "input_tokens": 6, "output_tokens": 219, "cache_read_tokens": 74274, "cache_write_tokens": 10966}}

    def test_codex_usage_from_fixture(self):
        lines = (FIXTURES / "codex_stream.jsonl").read_text().splitlines()
        u = h.parse_usage("codex", lines)
        assert u["session_id"] == "01a003f7-41c6-7ef2-87d2-2a7b6ad17b88"
        assert u["input_tokens"] == 30583 and u["output_tokens"] == 125
        assert u["cache_read_tokens"] == 26112 and u["num_turns"] == 1
        assert u["model"] is None
        assert "model_usage_json" not in u

    def test_sanitize_model_usage(self):
        two = {"claude-fable-5-1": {"inputTokens": 610, "outputTokens": 29464, "cacheReadInputTokens": 2102926,
                                    "cacheCreationInputTokens": 118832, "costUSD": 4.28, "contextWindow": 1000000},
               "claude-haiku-4-5-20251001": {"inputTokens": 18616, "outputTokens": 12, "costUSD": 0.01}}
        clean = h.sanitize_model_usage(two)
        assert clean == {"claude-fable-5-1": {"input_tokens": 610, "output_tokens": 29464,
                                              "cache_read_tokens": 2102926, "cache_write_tokens": 118832},
                         "claude-haiku-4-5-20251001": {"input_tokens": 18616, "output_tokens": 12}}
        assert "cost" not in h.model_usage_json(two).lower()
        # wrong types / oversized names / bools / negatives dropped; nothing left → None
        assert h.sanitize_model_usage({"x" * 129: {"inputTokens": 1}, "ok": "nope",
                                       "b": {"inputTokens": True, "outputTokens": -1, "cacheReadInputTokens": "9"}}) is None
        assert h.sanitize_model_usage([1, 2]) is None and h.sanitize_model_usage({}) is None
        too_many = {f"m{i}": {"inputTokens": 1} for i in range(h.MODEL_USAGE_MAX_MODELS + 1)}
        assert h.sanitize_model_usage(too_many) is None and h.model_usage_json(too_many) is None
        # a malformed modelUsage never costs the turn its usage row
        lines = [json.dumps({"type": "result", "usage": {"input_tokens": 1}, "modelUsage": "garbage"})]
        u = h.parse_usage("claude", lines)
        assert u["input_tokens"] == 1 and u["model_usage_json"] is None

    def test_usage_missing_or_garbage(self):
        assert h.parse_usage("claude", []) is None
        assert h.parse_usage("claude", ["not json", "{\"type\": \"assistant\"}"]) is None
        assert h.parse_usage("codex", ["{\"type\": \"thread.started\"}"]) is None

    def test_render_events(self):
        c_lines = (FIXTURES / "claude_stream.jsonl").read_text().splitlines()
        rendered = [h.render_event("claude", l) for l in c_lines]
        text = "\n".join(r for r in rendered if r)
        assert "session 492b9dec" in text and "→ Bash: echo probe-ok" in text
        assert "[claude] result success" in text
        x_lines = (FIXTURES / "codex_stream.jsonl").read_text().splitlines()
        text = "\n".join(r for r in (h.render_event("codex", l) for l in x_lines) if r)
        assert "$ /bin/zsh -lc" in text and "exit 0: probe-ok" in text
        assert "[codex] turn completed" in text
        assert h.render_event("claude", "") is None
        assert h.render_event("claude", "plain text").startswith("[claude] plain text")


# ---------------------------------------------------------------------------
# verification (direct)
# ---------------------------------------------------------------------------

class TestVerifyTransition:
    def test_ordinary_lead_turn_expects_next_round(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix",
                            str(project), updated_by="Codex")
        st = state_mod.read_state(str(project))
        assert st["turn"] == "lead" and st["round"] == 1
        ident = h.snapshot_identity(project, st)
        assert (ident.target_phase, ident.target_type, ident.target_round) == ("feat-x", "plan", 2)
        assert not ident.is_start
        ok, why = h.verify_transition(project, ident, "Claude")
        assert not ok and "no lead entry" in why
        cycle_mod.add_round("feat-x", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "done",
                            str(project), updated_by="Claude")
        ok, why = h.verify_transition(project, ident, "Claude")
        assert ok, why

    def test_unrelated_seq_advance_is_not_ok(self, project):
        st = _init_cycle(project)  # turn reviewer, round 1
        ident = h.snapshot_identity(project, st)
        state_mod.update_state({"command": "poked by a human"}, str(project))
        assert state_mod.read_state(str(project))["seq"] > st["seq"]
        ok, why = h.verify_transition(project, ident, "Codex")
        assert not ok and "no reviewer entry" in why

    def test_wrong_round_and_wrong_cycle_and_amend(self, project):
        st = _init_cycle(project)  # reviewer owed at round 1
        ident = h.snapshot_identity(project, st)
        # wrong cycle: an entry lands in another phase
        _init_cycle(project, phase="other")
        ok, _ = h.verify_transition(project, ident, "Codex")
        assert not ok
        # wrong round: reviewer entry at round 5
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 5, "x",
                            str(project), updated_by="Codex")
        ok, why = h.verify_transition(project, ident, "Codex")
        assert not ok and "round 1" in why
        # AMEND where a SUBMIT is owed (lead turn) — write directly to the DB
        st2 = state_mod.read_state(str(project))
        st2 = dict(st2, turn="lead", phase="feat-x", type="plan", round=1)
        ident2 = h.snapshot_identity(project, st2)
        conn = db.connect(project_dir=str(project))
        try:
            cid = conn.execute("SELECT id FROM cycles WHERE phase='feat-x' AND type='plan'").fetchone()[0]
            db.add_round(conn, cid, 2, "lead", "AMEND", "amend", "2026-01-01T00:00:00+00:00",
                         updated_by="Claude")
            conn.commit()
        finally:
            conn.close()
        ok, why = h.verify_transition(project, ident2, "Claude")
        assert not ok

    def test_entry_present_but_state_mismatch(self, project):
        st = _init_cycle(project)
        ident = h.snapshot_identity(project, st)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        # A concurrent human write flips state to a different phase
        state_mod.update_state({"phase": "elsewhere"}, str(project))
        ok, why = h.verify_transition(project, ident, "Codex")
        assert not ok and "state mismatch" in why

    def test_matching_entry_but_tampered_cycle_status_is_not_ok(self, project):
        """Reviewer finding (impl r1): a matching action entry with a stale
        or corrupt per-cycle status must not verify."""
        st = _init_cycle(project)  # reviewer owed at round 1
        ident = h.snapshot_identity(project, st)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "r",
                            str(project), updated_by="Codex")
        ok, why = h.verify_transition(project, ident, "Codex")
        assert ok, why
        conn = db.connect(project_dir=str(project))
        try:
            # ready_for wrong for REQUEST_CHANGES
            conn.execute("UPDATE cycles SET ready_for='reviewer' WHERE phase='feat-x'")
            conn.commit()
            ok, why = h.verify_transition(project, ident, "Codex")
            assert not ok and "cycle status" in why
            conn.execute("UPDATE cycles SET ready_for='lead', state='approved' WHERE phase='feat-x'")
            conn.commit()
            ok, why = h.verify_transition(project, ident, "Codex")
            assert not ok and "cycle status" in why
            # restore state, break round
            conn.execute("UPDATE cycles SET state='in-progress', round=7 WHERE phase='feat-x'")
            conn.commit()
            ok, why = h.verify_transition(project, ident, "Codex")
            assert not ok and "round" in why
        finally:
            conn.close()

    def test_lead_submit_requires_ready_for_reviewer(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "fix",
                            str(project), updated_by="Codex")
        st = state_mod.read_state(str(project))
        ident = h.snapshot_identity(project, st)
        cycle_mod.add_round("feat-x", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "done",
                            str(project), updated_by="Claude")
        assert h.verify_transition(project, ident, "Claude")[0]
        conn = db.connect(project_dir=str(project))
        try:
            conn.execute("UPDATE cycles SET ready_for='lead' WHERE phase='feat-x'"); conn.commit()
        finally:
            conn.close()
        ok, why = h.verify_transition(project, ident, "Claude")
        assert not ok and "expected one of" in why

    def test_start_targets_cross_phase_and_plan_to_impl(self, project):
        _init_cycle(project, phase="phase-a")
        cycle_mod.add_round("phase-a", "plan", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        # plan → impl start while state.type is still plan
        state_mod.update_state({"turn": "lead", "status": "ready",
                                "command": "/handoff start phase-a impl"}, str(project))
        st = state_mod.read_state(str(project))
        ident = h.snapshot_identity(project, st)
        assert ident.is_start and (ident.target_phase, ident.target_type, ident.target_round) == \
            ("phase-a", "impl", 1)
        ok, why = h.verify_transition(project, ident, "Claude")
        assert not ok and "was not created" in why
        cycle_mod.init_cycle("phase-a", "impl", "Claude", "Codex", "impl", str(project),
                             updated_by="Claude")
        ok, why = h.verify_transition(project, ident, "Claude")
        assert ok, why
        # cross-phase start while state still names the previous phase
        cycle_mod.add_round("phase-a", "impl", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        state_mod.update_state({"turn": "lead", "status": "ready",
                                "command": "/handoff start phase-b"}, str(project))
        st = state_mod.read_state(str(project))
        assert st["phase"] == "phase-a"
        ident = h.snapshot_identity(project, st)
        assert (ident.target_phase, ident.target_type, ident.target_round) == ("phase-b", "plan", 1)
        cycle_mod.init_cycle("phase-b", "plan", "Claude", "Codex", "plan b", str(project),
                             updated_by="Claude")
        ok, why = h.verify_transition(project, ident, "Claude")
        assert ok, why

    def test_malformed_start_command_is_ordinary_turn(self, project):
        _init_cycle(project, phase="phase-a")
        cycle_mod.add_round("phase-a", "plan", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        state_mod.update_state({"turn": "lead", "status": "ready",
                                "command": "/handoff start phase-c --roadmap"}, str(project))
        st = state_mod.read_state(str(project))
        ident = h.snapshot_identity(project, st)
        assert not ident.is_start and ident.target_phase == "phase-a"
        # An agent that "helpfully" inits phase-c is not a match.
        cycle_mod.init_cycle("phase-c", "plan", "Claude", "Codex", "c", str(project),
                             updated_by="Claude")
        ok, _ = h.verify_transition(project, ident, "Claude")
        assert not ok


# ---------------------------------------------------------------------------
# end-to-end with the fake agent
# ---------------------------------------------------------------------------

class TestEngineE2E:
    def test_reviewer_then_lead_turn_ok(self, project, fake_path, monkeypatch, tmp_path):
        cap = tmp_path / "cap.json"
        monkeypatch.setenv("FAKE_AGENT_CAPTURE", str(cap))
        st = _init_cycle(project)                      # reviewer owed (codex fake)
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", res.reason
        captured = json.loads(cap.read_text())
        assert captured["argv"][-1] == "-"                    # codex marker last
        assert "=== COMMAND ===" in captured["prompt"]
        assert Path(captured["cwd"]).resolve() == project.resolve()
        st2 = state_mod.read_state(str(project))
        assert st2["turn"] == "lead" and st2["updated_by"] == "Codex"
        rows = _usage_rows(project)
        assert len(rows) == 1 and rows[0]["status"] == "ok"
        assert rows[0]["provider"] == "codex" and rows[0]["input_tokens"] == 11
        assert rows[0]["output_tokens"] == 22 and rows[0]["cache_read_tokens"] == 33
        assert rows[0]["role"] == "reviewer" and rows[0]["round"] == 1
        # per-turn files exist and inflight pointer is gone
        assert Path(res.log_path).exists() and Path(res.events_path).exists()
        assert h.read_inflight(project) is None
        assert eng.paused() is None
        # lead turn (claude fake) — expects round 2
        res2 = eng.run_owed_turn(st2)
        assert res2.outcome == "ok", res2.reason
        st3 = state_mod.read_state(str(project))
        assert st3["turn"] == "reviewer" and st3["round"] == 2 and st3["updated_by"] == "Claude"
        rows = _usage_rows(project)
        assert [r["provider"] for r in rows] == ["codex", "claude"]
        assert rows[1]["model"] == "fake-model" and rows[1]["cost_usd"] == pytest.approx(0.01)
        # log content is human-readable, events file is structured
        log = Path(res2.log_path).read_text()
        assert "[claude] session fake-sess" in log and "[tagteam] outcome ok" in log
        for line in Path(res2.events_path).read_text().splitlines():
            json.loads(line)

    def test_no_round_pauses_and_blocks_dispatch(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "no_round")
        st = _init_cycle(project)
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "no_round" and "no reviewer entry" in res.reason
        pause = eng.paused()
        assert pause and pause["outcome"] == "no_round" and pause["log_path"] == res.log_path
        assert "headless_turn_failed" in _diag_kinds(project)
        assert _usage_rows(project)[-1]["status"] == "no_round"
        assert eng._test_notes and "no_round" in eng._test_notes[-1][1]
        # paused → no dispatch, even after "restart"
        assert eng.run_owed_turn(st) is None
        eng2 = _engine(project)
        assert eng2.run_owed_turn(st) is None
        assert len(_usage_rows(project)) == 1
        # clearing the marker resumes
        assert h.clear_pause(project)
        monkeypatch.setenv("FAKE_AGENT_MODE", "ok")
        assert eng2.run_owed_turn(st).outcome == "ok"

    def test_nonzero_exit(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "nonzero")
        st = _init_cycle(project)
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "nonzero_exit" and res.exit_code == 3
        assert eng.paused()["outcome"] == "nonzero_exit"
        assert _usage_rows(project)[-1]["status"] == "nonzero_exit"
        assert "[stderr] fatal: fake failure" in Path(res.log_path).read_text()

    def test_timeout_kills_process_tree(self, project, fake_path, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_AGENT_MODE", "grandchild_hang")
        pidfile = tmp_path / "grandchild.pid"
        monkeypatch.setenv("FAKE_AGENT_PIDFILE", str(pidfile))
        st = _init_cycle(project)
        eng = _engine(project, timeout_minutes=0.2)  # 12 s (Windows CI python startup is slow)
        t0 = time.monotonic()
        res = eng.run_owed_turn(st)
        assert res.outcome == "timeout"
        assert time.monotonic() - t0 < 60
        assert eng.paused()["outcome"] == "timeout"
        assert _usage_rows(project)[-1]["status"] == "timeout"
        assert pidfile.exists()
        gpid = int(pidfile.read_text())
        deadline = time.monotonic() + 10
        while _pid_alive(gpid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _pid_alive(gpid), "grandchild survived the process-tree kill"

    def test_log_grows_before_exit_and_stderr_separated(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "stderr_noise")
        monkeypatch.setenv("FAKE_AGENT_SLEEP", "0.4")
        st = _init_cycle(project)
        eng = _engine(project)
        sizes: list[tuple[int, bool]] = []  # (log size, inflight present)
        pids: list = []
        stop = threading.Event()

        def sampler():
            d = h.turns_dir(project)
            while not stop.is_set():
                logs = [f for f in d.glob("*.log")] if d.exists() else []
                inflight = h.read_inflight(project)
                if logs:
                    sizes.append((logs[0].stat().st_size, inflight is not None))
                if inflight is not None:
                    pid = inflight.get("pid")
                    pids.append((pid, _pid_alive(pid) if isinstance(pid, int) else None))
                time.sleep(0.05)

        th = threading.Thread(target=sampler, daemon=True); th.start()
        res = eng.run_owed_turn(st)
        stop.set(); th.join(2)
        assert res.outcome == "ok", res.reason
        inflight_sizes = sorted({s for s, inflight in sizes if inflight})
        assert len(inflight_sizes) >= 2, f"log did not grow while in flight: {sizes}"
        # Reviewer finding (impl r1): inflight.json carries the live PID.
        live = [p for p, alive in pids if isinstance(p, int)]
        assert live, f"inflight.json never had an integer pid: {pids}"
        assert all(p == live[0] for p in live)
        assert any(alive for p, alive in pids if isinstance(p, int)), \
            f"pid was never observed alive while in flight: {pids}"
        events = Path(res.events_path).read_text()
        log = Path(res.log_path).read_text()
        assert "warning: something noisy" not in events
        assert "[stderr] warning: something noisy" in log
        assert _usage_rows(project)[-1]["input_tokens"] == 11  # stderr didn't break parsing

    def test_malformed_usage_does_not_fail_turn(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "malformed")
        st = _init_cycle(project)
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", res.reason
        row = _usage_rows(project)[-1]
        assert row["status"] == "ok" and row["input_tokens"] is None
        assert "headless_usage_unparsed" in _diag_kinds(project)
        assert eng.paused() is None

    def test_wrong_round_is_no_round(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "wrong_round")
        st = _init_cycle(project)
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "no_round" and "round 1" in res.reason

    def test_impl_start_boundary_e2e(self, project, fake_path):
        _init_cycle(project, phase="phase-a")
        cycle_mod.add_round("phase-a", "plan", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        state_mod.update_state({"turn": "lead", "status": "ready",
                                "command": "/handoff start phase-a impl"}, str(project))
        st = state_mod.read_state(str(project))
        eng = _engine(project)
        res = eng.run_owed_turn(st)
        assert res.outcome == "ok", res.reason
        st2 = state_mod.read_state(str(project))
        assert st2["type"] == "impl" and st2["round"] == 1 and st2["turn"] == "reviewer"
        # Phase 55: stored identity is the owed state (plan r1); the target is the impl entry it produced
        row = _usage_rows(project)[-1]
        assert (row["phase"], row["type"], row["round"], row["role"]) == ("phase-a", "plan", 1, "lead")
        assert (row["target_phase"], row["target_type"], row["target_round"]) == ("phase-a", "impl", 1)

    def test_impl_start_unchanged_state_is_no_round(self, project, fake_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGENT_MODE", "no_round")
        _init_cycle(project, phase="phase-a")
        cycle_mod.add_round("phase-a", "plan", "reviewer", "APPROVE", 1, "ok",
                            str(project), updated_by="Codex")
        state_mod.update_state({"turn": "lead", "status": "ready",
                                "command": "/handoff start phase-a impl"}, str(project))
        st = state_mod.read_state(str(project))
        res = _engine(project).run_owed_turn(st)
        assert res.outcome == "no_round" and "was not created" in res.reason

    def test_ctrl_c_kills_child_and_clears_inflight(self, project, fake_path, monkeypatch, tmp_path):
        monkeypatch.setenv("FAKE_AGENT_MODE", "grandchild_hang")
        pidfile = tmp_path / "gc.pid"
        monkeypatch.setenv("FAKE_AGENT_PIDFILE", str(pidfile))
        st = _init_cycle(project)
        eng = _engine(project, timeout_minutes=5)
        real_wait = h.subprocess.Popen.wait
        seen = {}

        def interrupting_wait(self_, timeout=None):
            args0 = str((getattr(self_, "args", None) or [""])[0])
            if "pid" in seen or not args0.startswith(str(fake_path)):
                # later calls (kill-tree cleanup) and unrelated subprocesses
                # (e.g. `ps` inside procs.identity) behave normally
                return real_wait(self_, timeout=timeout)
            # let the fake start its grandchild, then simulate Ctrl-C
            deadline = time.monotonic() + 20
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            seen["pid"] = self_.pid
            raise KeyboardInterrupt
        monkeypatch.setattr(h.subprocess.Popen, "wait", interrupting_wait)
        with pytest.raises(KeyboardInterrupt):
            eng.run_owed_turn(st)
        monkeypatch.setattr(h.subprocess.Popen, "wait", real_wait)
        assert h.read_inflight(project) is None
        gpid = int(pidfile.read_text())
        deadline = time.monotonic() + 10
        while (_pid_alive(gpid) or _pid_alive(seen["pid"])) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _pid_alive(seen["pid"]) and not _pid_alive(gpid)

    def test_spawn_failure_is_recorded_and_pauses(self, project, fake_path, monkeypatch):
        """Reviewer finding (impl r1): an OSError from Popen must become a
        recorded, paused failure — usage row, diagnostic, marker, notify."""
        st = _init_cycle(project)
        eng = _engine(project)
        real_popen = h.subprocess.Popen

        def boom(*a, **k):
            raise PermissionError(13, "Permission denied", a[0][0])
        monkeypatch.setattr(h.subprocess, "Popen", boom)
        res = eng.run_owed_turn(st)
        monkeypatch.setattr(h.subprocess, "Popen", real_popen)
        assert res.outcome == "spawn_failed" and "Permission denied" in res.reason
        assert res.exit_code is None
        assert eng.paused()["outcome"] == "spawn_failed"
        assert _usage_rows(project)[-1]["status"] == "spawn_failed"
        assert "headless_turn_failed" in _diag_kinds(project)
        assert eng._test_notes and "spawn_failed" in eng._test_notes[-1][1]
        assert h.read_inflight(project) is None
        assert "spawn failed" in Path(res.log_path).read_text()

    def test_prune_keeps_newest(self, project):
        d = h.turns_dir(project); d.mkdir(parents=True)
        for i in range(60):
            (d / f"p_plan_r1_lead_{i:04d}.log").write_text("x")
            (d / f"p_plan_r1_lead_{i:04d}.events.jsonl").write_text("{}")
            os.utime(d / f"p_plan_r1_lead_{i:04d}.log", (i, i))
            os.utime(d / f"p_plan_r1_lead_{i:04d}.events.jsonl", (i, i))
        (d / h.INFLIGHT_NAME).write_text("{}")
        removed = h.prune_turn_logs(project, keep=50)
        assert removed == 20
        assert (d / h.INFLIGHT_NAME).exists()
        assert not (d / "p_plan_r1_lead_0000.log").exists()
        assert (d / "p_plan_r1_lead_0059.log").exists()


# ---------------------------------------------------------------------------
# watcher integration
# ---------------------------------------------------------------------------

class TestWatcherIntegration:
    def _proc(self, engine, project_dir=None, **kw):
        from tagteam.watcher import _StateProcessor
        return _StateProcessor(
            mode="headless", lead_name="Claude", reviewer_name="Codex",
            lead_pane="x", reviewer_pane="y", lead_session_id=None,
            reviewer_session_id=None, confirm=False, timeout_minutes=30,
            project_dir=str(project_dir or "."), max_retries=1, retry_delay=0,
            pre_send_delay=0, engine=engine, **kw)

    def _state(self, seq, **extra):
        s = {"seq": seq, "status": "ready", "turn": "reviewer", "command": STD_CMD,
             "phase": "p", "type": "plan", "round": 1, "updated_at": f"t{seq}"}
        s.update(extra)
        return s

    def test_ready_dispatches_to_engine_and_watchdog_never_resends(self, tmp_path):
        # Isolated project dir: a real pause marker in the repo (e.g. an
        # operator hold during dogfood) must not leak into this test.
        from unittest.mock import MagicMock
        eng = MagicMock()
        eng.paused.return_value = None
        p = self._proc(eng, project_dir=tmp_path)
        p.tick(self._state(1))
        assert eng.run_owed_turn.call_count == 1
        # Simulate the resend window elapsing on the same seq
        p.last_ready_send_time = time.time() - 10_000
        p.tick(self._state(1))
        assert eng.run_owed_turn.call_count == 1  # no watchdog re-send in headless
        # New seq → dispatch again
        p.tick(self._state(2))
        assert eng.run_owed_turn.call_count == 2

    def test_done_in_headless_does_not_send(self, tmp_path):
        from unittest.mock import MagicMock, patch
        eng = MagicMock()
        p = self._proc(eng, project_dir=tmp_path)
        with patch("tagteam.watcher.send_iterm_command") as si, \
             patch("tagteam.watcher.send_tmux_keys") as stk, \
             patch("tagteam.watcher.notify_macos"):
            p.tick(self._state(1, status="ready"))
            p.tick(self._state(2, status="done", result="approved"))
        si.assert_not_called(); stk.assert_not_called()

    def test_auto_detect_never_returns_headless(self, tmp_path, monkeypatch):
        from tagteam.watcher import _auto_detect_mode
        monkeypatch.chdir(tmp_path)
        mode, _ = _auto_detect_mode(str(tmp_path))
        assert mode != "headless"

    def test_watch_command_rejects_bad_mode_and_accepts_headless_flags(self, project, fake_path):
        from tagteam import watcher
        assert watcher.watch_command(["--mode", "bogus"]) == 1
        # headless startup with unresolvable CLIs fails fast (exit 1)
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Gemini\n")
        rc = watcher.watch_command(["--mode", "headless", "--turn-timeout", "5",
                                    "--tail-rounds", "2"])
        assert rc == 1

    def test_build_processor_headless_paused_marker_logged(self, project, fake_path):
        from tagteam import watcher
        h.write_pause(project, {"reason": "earlier failure", "log_path": "x"})
        logs = []
        orig = watcher._log
        watcher._log = logs.append
        try:
            proc = watcher._build_processor(
                mode="headless", lead_pane="a", reviewer_pane="b", confirm=False,
                timeout_minutes=30, project_dir=str(project), max_retries=1,
                retry_delay=0, pre_send_delay=0, turn_timeout_minutes=7, tail_rounds=2)
            assert proc is not None and proc.engine is not None
            assert proc.engine.timeout_s == 7 * 60 and proc.engine.tail_n == 2
            watcher._log_startup_banner(proc, 10)
        finally:
            watcher._log = orig
        assert any("PAUSED" in l for l in logs)


# ---------------------------------------------------------------------------
# tagteam tail
# ---------------------------------------------------------------------------

class TestTailCommand:
    def test_no_logs(self, project, capsys):
        assert h.tail_command([], project_root=project) == 1
        assert "No headless turn logs" in capsys.readouterr().out

    def test_last_log_no_follow(self, project, capsys):
        d = h.turns_dir(project); d.mkdir(parents=True)
        (d / "a.log").write_text("\n".join(f"line{i}" for i in range(100)))
        assert h.tail_command(["--lines", "3", "--no-follow"], project_root=project) == 0
        out = capsys.readouterr().out
        assert "line99" in out and "line96" not in out

    def test_follow_until_inflight_clears(self, project, capsys):
        d = h.turns_dir(project); d.mkdir(parents=True)
        log = d / "t.log"; ev = d / "t.events.jsonl"
        log.write_text("start\n"); ev.write_text("")
        h.inflight_path(project).write_text(json.dumps({
            "log_path": str(log), "events_path": str(ev), "provider": "claude",
            "agent": "Claude", "role": "lead", "phase": "p", "type": "plan", "round": 1}))

        def writer():
            time.sleep(0.3)
            with open(log, "a") as f:
                f.write("more\n")
            time.sleep(0.3)
            with open(log, "a") as f:
                f.write("[tagteam] outcome ok: done\n")
            h.inflight_path(project).unlink()

        th = threading.Thread(target=writer); th.start()
        rc = h.tail_command([], project_root=project, poll_interval=0.05, max_follow_s=10)
        th.join()
        assert rc == 0
        out = capsys.readouterr().out
        assert "following claude turn" in out and "more" in out and "outcome ok" in out

    def test_bad_args(self, project, capsys):
        assert h.tail_command(["--wat"], project_root=project) == 1
        assert h.tail_command(["--help"], project_root=project) == 0


# ---------------------------------------------------------------------------
# cycle rounds --tail
# ---------------------------------------------------------------------------

class TestCycleRoundsTail:
    def test_tail_flag(self, project, capsys):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "r1",
                            str(project), updated_by="Codex")
        cycle_mod.add_round("feat-x", "plan", "lead", "SUBMIT_FOR_REVIEW", 2, "l2",
                            str(project), updated_by="Claude")
        cycle_mod.add_round("feat-x", "plan", "lead", "AMEND", 2, "amend",
                            str(project), updated_by="Claude")
        rc = cycle_mod.cycle_command(["rounds", "--phase", "feat-x", "--type", "plan"])
        full = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
        assert rc == 0 and len(full) >= 2
        rc = cycle_mod.cycle_command(["rounds", "--phase", "feat-x", "--type", "plan",
                                      "--tail", "1"])
        tail1 = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
        assert rc == 0 and len(tail1) == 1 and tail1[0] == full[-1]
        rc = cycle_mod.cycle_command(["rounds", "--phase", "feat-x", "--type", "plan",
                                      "--tail", "99"])
        big = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
        assert rc == 0 and big == full
        for bad in ("0", "-1", "x"):
            rc = cycle_mod.cycle_command(["rounds", "--phase", "feat-x", "--type", "plan",
                                          "--tail", bad])
            assert rc == 1
            assert "--tail must be" in capsys.readouterr().out
        assert cycle_mod.tail_rounds("feat-x", "plan", 1, str(project)) == [full[-1]]
        with pytest.raises(ValueError):
            cycle_mod.tail_rounds("feat-x", "plan", 0, str(project))


# ---------------------------------------------------------------------------
# schema v3 / usage table
# ---------------------------------------------------------------------------

class TestSchemaV3:
    def test_fresh_and_v2_migrate_to_current(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert c.execute("SELECT name FROM sqlite_master WHERE name='usage'").fetchone()
        c.close()
        # simulate a v2 DB
        p2 = tmp_path / "v2" / ".tagteam" / "tagteam.db"
        p2.parent.mkdir(parents=True)
        import sqlite3
        raw = sqlite3.connect(p2)
        raw.executescript(db._SCHEMA_V1)  # V1 DDL already carries ready_for_present
        raw.execute("PRAGMA user_version = 2"); raw.commit(); raw.close()
        c = db.connect(project_dir=str(tmp_path / "v2"))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert c.execute("SELECT name FROM sqlite_master WHERE name='usage'").fetchone()
        c.close()

    def test_newer_user_version_tolerated(self, tmp_path):
        """Additive-only guard: code must not raise on a DB written by a
        newer release (this is the *regression guard*, not the downgrade
        proof — see the plan's release checklist)."""
        c = db.connect(project_dir=str(tmp_path))
        c.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}"); c.commit(); c.close()
        c = db.connect(project_dir=str(tmp_path))
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION + 1
        c.close()

    def test_add_and_get_usage(self, tmp_path):
        c = db.connect(project_dir=str(tmp_path))
        rid = db.add_usage(c, ts="2026-01-01T00:00:00+00:00", phase="p", type="plan",
                           round=1, role="lead", agent="Claude", provider="claude",
                           status="ok", input_tokens=1, output_tokens=2)
        assert rid == 1
        db.add_usage(c, ts="2026-01-01T00:00:01+00:00", phase="q", type="impl",
                     round=2, role="reviewer", status="timeout")
        rows = db.get_usage(c)
        assert [r["phase"] for r in rows] == ["p", "q"]
        assert db.get_usage(c, phase="q")[0]["status"] == "timeout"
        assert db.get_usage(c, phase="p", cycle_type="plan")[0]["input_tokens"] == 1
        db.add_usage(c, ts="x", status="spawn_failed")
        with pytest.raises(ValueError):
            db.add_usage(c, ts="x", status="weird")
        with pytest.raises(ValueError):
            db.add_usage(c, ts="x", status="ok", bogus=1)
        with pytest.raises(ValueError):
            db.add_usage(c, status="ok")
        c.close()


# ---------------------------------------------------------------------------
# Phase 34: rate-limit signal capture (schema v6 `rate_limits`)
# ---------------------------------------------------------------------------

class TestRateLimitCapture:
    def test_parse_from_recorded_fixture(self):
        lines = (FIXTURES / "claude_stream.jsonl").read_text().splitlines()
        sig = h.parse_rate_limits("claude", lines)
        assert len(sig) == 1
        s = sig[0]
        assert s["kind"] == "five_hour" and s["status"] == "allowed"
        assert s["resets_at"] == "2026-08-15T09:10:00+00:00"       # 1786785000 → ISO UTC
        assert s["payload"]["rateLimitType"] == "five_hour"
        assert h.parse_rate_limits("codex", lines) == []              # no equivalent
        assert h.parse_rate_limits("claude", ["garbage", '{"type": "assistant"}']) == []
        # latest per kind wins
        two = ['{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1,"rateLimitType":"five_hour"}}',
               '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":2,"rateLimitType":"five_hour"}}',
               '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","rateLimitType":"seven_day"}}']
        got = {s["kind"]: s for s in h.parse_rate_limits("claude", two)}
        assert got["five_hour"]["status"] == "rejected" and got["seven_day"]["resets_at"] is None

    def test_engine_records_latest_signal(self, project, fake_path, monkeypatch):
        st = _init_cycle(project)
        eng = _engine(project)
        res = eng.run_owed_turn(st)                    # codex reviewer: no signal
        assert res.outcome == "ok", res.reason
        conn = db.connect(project_dir=str(project))
        try:
            assert db.latest_rate_limits(conn) == []
        finally:
            conn.close()
        st2 = state_mod.read_state(str(project))
        res2 = eng.run_owed_turn(st2)                  # claude lead: fake emits five_hour
        assert res2.outcome == "ok", res2.reason
        conn = db.connect(project_dir=str(project))
        try:
            rows = db.latest_rate_limits(conn)
        finally:
            conn.close()
        assert len(rows) == 1 and rows[0]["provider"] == "claude" and rows[0]["kind"] == "five_hour"
        assert rows[0]["status"] == "allowed" and rows[0]["resets_at"].startswith("2026-08-15T")
        assert h.record_rate_limits(project, "codex", []) == 0

    def test_record_never_raises_without_db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")))
        line = '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1,"rateLimitType":"five_hour"}}'
        assert h.record_rate_limits(tmp_path, "claude", [line], log=lambda m: None) == 0


# --- Phase 47: reviewer context parity -------------------------------------

class TestSkillResolution:
    """Phase 48: explicit → project-local → packaged; plugin cache never."""

    CFG = {"agents": {"lead": {"name": "A", "headless": {"provider": "claude"}},
                      "reviewer": {"name": "B", "headless": {"provider": "codex"}}}}

    def test_project_local_wins_when_present(self, tmp_path):
        skill = tmp_path / h.SKILL_RELPATH
        skill.parent.mkdir(parents=True); skill.write_text("local")
        path, source = h.resolve_skill_path(tmp_path)
        assert path == skill and source == "project"

    def test_packaged_when_absent(self, tmp_path):
        path, source = h.resolve_skill_path(tmp_path)
        assert path == h.PACKAGED_SKILL_PATH and source == "packaged"
        assert path.is_file()

    def test_explicit_always(self, tmp_path):
        skill = tmp_path / h.SKILL_RELPATH
        skill.parent.mkdir(parents=True); skill.write_text("local")
        path, source = h.resolve_skill_path(tmp_path, tmp_path / "elsewhere.md")
        assert path == tmp_path / "elsewhere.md" and source == "explicit"

    def test_validate_passes_without_project_skill(self, tmp_path, monkeypatch):
        monkeypatch.setattr(h, "resolve_executable", lambda p, e: "/bin/true")
        t = h.HeadlessEngine(tmp_path, self.CFG, lead_name="A", reviewer_name="B")
        assert t.skill_source == "packaged"
        assert not [e for e in t.validate() if "skill contract" in e]

    def test_validate_reports_missing_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(h, "resolve_executable", lambda p, e: "/bin/true")
        t = h.HeadlessEngine(tmp_path, self.CFG, lead_name="A", reviewer_name="B",
                           skill_path=tmp_path / "nope.md")
        assert any("skill contract not found" in e and "(explicit)" in e for e in t.validate())

    def test_header_names_the_source(self):
        base = dict(role="reviewer", agent_name="B", project_root="/p",
                    state={"phase": "x", "type": "plan"}, skill_text="S",
                    tail_entries=[], tail_n=1)
        assert "=== HANDOFF CONTRACT (packaged) ===" in h.compose_prompt(**base, skill_source="packaged")
        assert "=== HANDOFF CONTRACT (project) ===" in h.compose_prompt(**base)


class TestProjectContextInjection:
    """The context file a provider does NOT auto-load is the one worth sending."""

    def _root(self, tmp_path, names):
        for n in names:
            (tmp_path / n).write_text(f"# {n}\nconventions for this project\n",
                                      encoding="utf-8")
        return tmp_path

    def test_codex_gets_claude_md_when_only_claude_md_exists(self, tmp_path):
        # The 40-repo case: CLAUDE.md present, no AGENTS.md. Codex would
        # otherwise see nothing about the project at all.
        root = self._root(tmp_path, ["CLAUDE.md"])
        assert h.select_context_file(root, "codex").name == "CLAUDE.md"

    def test_codex_skips_agents_md_it_already_autoloads(self, tmp_path):
        root = self._root(tmp_path, ["AGENTS.md"])
        assert h.select_context_file(root, "codex") is None

    def test_codex_prefers_claude_md_when_both_exist(self, tmp_path):
        root = self._root(tmp_path, ["AGENTS.md", "CLAUDE.md"])
        assert h.select_context_file(root, "codex").name == "CLAUDE.md"

    def test_claude_skips_claude_md_it_already_autoloads(self, tmp_path):
        root = self._root(tmp_path, ["CLAUDE.md"])
        assert h.select_context_file(root, "claude") is None

    def test_claude_gets_agents_md(self, tmp_path):
        root = self._root(tmp_path, ["AGENTS.md"])
        assert h.select_context_file(root, "claude").name == "AGENTS.md"

    def test_no_context_files_at_all(self, tmp_path):
        assert h.select_context_file(tmp_path, "codex") is None
        assert h.read_project_context(tmp_path, "codex") is None

    def test_empty_file_is_not_injected(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("   \n", encoding="utf-8")
        assert h.read_project_context(tmp_path, "codex") is None

    def test_invalid_utf8_context_degrades_to_none(self, tmp_path):
        # A non-UTF-8 CLAUDE.md must not abort the turn (impl review r1).
        (tmp_path / "CLAUDE.md").write_bytes(b"\xff\xfe")
        assert h.read_project_context(tmp_path, "codex") is None

    def test_oversized_context_is_truncated(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("x" * 40000, encoding="utf-8")
        source, text = h.read_project_context(tmp_path, "codex")
        assert source == "CLAUDE.md"
        assert len(text) < 40000
        assert "truncated" in text

    def test_render_is_empty_without_context(self):
        assert h.render_project_context(None) == ""

    def test_render_names_its_source(self):
        out = h.render_project_context(("AGENTS.md", "body text"))
        assert "AGENTS.md" in out
        assert "body text" in out
        assert out.startswith(h.PROJECT_CONTEXT_HEADER)


class TestChangeSurface:
    """The reviewer is told which diff to read, not left to guess."""

    def test_plan_cycles_have_no_change_surface(self, tmp_path):
        assert h.collect_change_surface("feat-x", "plan", tmp_path) is None

    def test_missing_phase_is_skipped(self, tmp_path):
        assert h.collect_change_surface("", "impl", tmp_path) is None

    def test_unavailable_scope_diff_does_not_raise(self, tmp_path):
        # No cycle exists here — ScopeDiffError must degrade to None.
        assert h.collect_change_surface("nope", "impl", tmp_path) is None

    def test_render_is_empty_without_scope(self):
        assert h.render_change_surface(None) == ""

    def test_render_lists_paths_and_baseline(self):
        out = h.render_change_surface({"paths": ["a.py", "b.py"],
                                       "diff_base": "abc123"})
        assert "abc123" in out
        assert "a.py" in out and "b.py" in out
        assert "2 file(s) changed" in out

    def test_render_caps_long_path_lists(self):
        paths = [f"f{i}.py" for i in range(200)]
        out = h.render_change_surface({"paths": paths, "diff_base": "abc123"})
        assert "and 140 more" in out
        assert "f199.py" not in out

    def test_render_flags_an_empty_surface(self):
        out = h.render_change_surface({"paths": [], "diff_base": "abc123"})
        assert "No files are attributable" in out


class TestComposePromptWithNewBlocks:
    """Both blocks are optional; the prompt is unchanged when they are absent."""

    BASE = dict(role="reviewer", agent_name="codex", project_root="/tmp/p",
                skill_text="CONTRACT", tail_entries=[], tail_n=3)

    def test_absent_blocks_leave_prompt_clean(self):
        out = h.compose_prompt(state={"command": STD_CMD}, **self.BASE)
        assert h.PROJECT_CONTEXT_HEADER not in out
        assert h.CHANGE_SURFACE_HEADER not in out

    def test_blocks_appear_before_the_contract(self):
        out = h.compose_prompt(state={"command": STD_CMD},
                               project_context=("CLAUDE.md", "project rules"),
                               change_surface={"paths": ["x.py"],
                                               "diff_base": "sha1"},
                               **self.BASE)
        assert out.index(h.PROJECT_CONTEXT_HEADER) < out.index("=== HANDOFF CONTRACT")
        assert out.index(h.CHANGE_SURFACE_HEADER) < out.index("=== HANDOFF CONTRACT")
        assert "project rules" in out
        assert "x.py" in out
