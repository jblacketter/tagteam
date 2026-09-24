"""Phase 73: the kit agents' read-only guard.

Three layers, each tested as it ships:
- the guard (``tagteam hook pre-tool-use``): exit code is the verdict;
- the plugin's validator (``plugin/hooks/validate_guard.py``): structural
  validation of a rewrite, independent of the CLI it checks;
- the wrapper — the EXACT command string in ``plugin/hooks/hooks.json``, run
  under ``sh -c`` with PATH arranged per case (real CLI, no CLI, an old CLI,
  CLIs that print hollow / truncated / misplaced output, no python3).
"""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tagteam import hook

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "plugin"
PREFIX = hook.READ_ONLY_EXPORT

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the plugin's hook wrapper is POSIX sh")


def _payload(agent="tagteam:verifier", command="echo hi", **extra_input):
    d = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": command, "description": "run it", **extra_input}}
    if agent is not None:
        d["agent_type"] = agent
        d["agent_id"] = "a1"
    return d


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------

class TestGuard:
    def test_a_kit_agent_is_rewritten_keeping_every_other_field(self):
        code, out, _ = hook.guard_decision(_payload(command="a && tagteam cycle add x", timeout=5))
        assert code == hook.GUARD_REWRITE
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse" and "permissionDecision" not in hso
        assert hso["updatedInput"] == {"command": PREFIX + "a && tagteam cycle add x", "description": "run it",
                                       "timeout": 5}

    def test_an_already_prefixed_command_is_unchanged(self):
        _, out, _ = hook.guard_decision(_payload(command=PREFIX + "ls"))
        assert out["hookSpecificOutput"]["updatedInput"]["command"] == PREFIX + "ls"

    @pytest.mark.parametrize("agent", [None, "general-purpose", "Explore", "tagteamx:verifier", ""])
    def test_not_a_kit_agent_is_exit_3_with_no_output(self, agent):
        assert hook.guard_decision(_payload(agent=agent)) == (hook.GUARD_NOT_KIT, None, "")

    def test_identity_is_the_top_level_field_not_text_in_the_command(self):
        p = _payload(agent=None, command='echo \'"agent_type": "tagteam:verifier"\'')
        assert hook.guard_decision(p)[0] == hook.GUARD_NOT_KIT

    def test_missing_identity_passes_unguarded_the_documented_unsupported_case(self):
        """A kit-shaped call from a Claude Code that doesn't send agent_type: the
        guard can't tell it from the main session. Doctor warns about such versions."""
        assert hook.guard_decision(_payload(agent=None, command="tagteam cycle add"))[0] == hook.GUARD_NOT_KIT

    @pytest.mark.parametrize("payload", [[], "x", {"agent_type": "tagteam:v"},
                                         {"agent_type": "tagteam:v", "tool_input": {"command": 3}}])
    def test_unusable_input_for_a_kit_agent_is_a_deny(self, payload):
        code, out, reason = hook.guard_decision(payload)
        assert code == hook.GUARD_DENY and out is None and reason

    def test_the_cli_entry(self):
        out, err = io.StringIO(), io.StringIO()
        assert hook.pre_tool_use(io.StringIO(json.dumps(_payload())), out, err) == 0
        assert json.loads(out.getvalue())["hookSpecificOutput"]["updatedInput"]["command"] == PREFIX + "echo hi"
        out, err = io.StringIO(), io.StringIO()
        assert hook.pre_tool_use(io.StringIO("{broken"), out, err) == 2 and "not valid JSON" in err.getvalue()
        assert out.getvalue() == ""

    def test_it_runs_under_read_only(self):
        env = dict(os.environ, TAGTEAM_READ_ONLY="1")
        r = subprocess.run([sys.executable, "-m", "tagteam", "hook", "pre-tool-use"], input=json.dumps(_payload()),
                           capture_output=True, text=True, env=env, cwd=REPO)
        assert r.returncode == 0 and PREFIX in r.stdout


# ---------------------------------------------------------------------------
# the plugin-side validator
# ---------------------------------------------------------------------------

def _validator():
    spec = importlib.util.spec_from_file_location("validate_guard", PLUGIN / "hooks" / "validate_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rewrite(command, **kw):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "updatedInput": {"command": command, "description": "run it", **kw}}})


class TestValidator:
    def test_the_guards_own_output_is_valid(self):
        v = _validator()
        orig = json.dumps(_payload())
        _, out, _ = hook.guard_decision(_payload())
        assert v.problem(json.dumps(out), orig) is None

    @pytest.mark.parametrize("guard_out, why", [
        ("{}", "PreToolUse"),
        (json.dumps({"hookSpecificOutput": {}}), "PreToolUse"),
        (_rewrite("echo hi"), "read-only export"),                                        # unprefixed
        # review r3, counterexample 1: truncated JSON holding both markers
        ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"export TAGTEAM_READ_ONLY=1; echo ok"',
         "one complete JSON object"),
        # review r3, counterexample 2: the prefix in the wrong field
        (json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {
            "command": "tagteam cycle add", "description": "export TAGTEAM_READ_ONLY=1; "}}}), "read-only export"),
        (_rewrite(PREFIX + "echo other"), "read-only export"),                            # a different command
        (json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                            "updatedInput": {"command": PREFIX + "echo hi"}}}), "dropped"),
        (_rewrite(PREFIX + "echo hi", extra=1), "dropped"),                               # a field added
        (json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                            "updatedInput": {"command": PREFIX + "echo hi", "description": "run it"}}}),
         "auto-allow"),
    ])
    def test_unusable_rewrites_are_rejected(self, guard_out, why):
        assert why in (_validator().problem(guard_out, json.dumps(_payload())) or "")


# ---------------------------------------------------------------------------
# the wrapper, exactly as shipped
# ---------------------------------------------------------------------------

def _shipped_command() -> str:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    cmds = [h["command"] for grp in hooks["hooks"]["PreToolUse"] for h in grp["hooks"]]
    assert len(cmds) == 1 and hooks["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    return cmds[0]


def _bin(tmp_path, name, *, tagteam=None, python3=True):
    """A PATH dir with only what the wrapper needs, plus the chosen `tagteam`."""
    d = tmp_path / name
    d.mkdir()
    for tool in ("sh", "cat", "dirname"):
        os.symlink(shutil.which(tool), d / tool)
    if python3:
        os.symlink(sys.executable, d / "python3")
    if tagteam == "real":
        (d / "tagteam").write_text(f'#!/bin/sh\nexec "{sys.executable}" -m tagteam "$@"\n')
    elif tagteam is not None:
        (d / "tagteam").write_text(tagteam)
    if tagteam is not None:
        (d / "tagteam").chmod(0o755)
    return d


def _fake(stdout="", rc=0, stderr=""):
    return (f"#!/bin/sh\ncat >/dev/null\nprintf '%s' '{stdout}'\nprintf '%s' '{stderr}' >&2\nexit {rc}\n")


def _run(payload, bindir, *, raw=None):
    env = {"PATH": str(bindir), "CLAUDE_PLUGIN_ROOT": str(PLUGIN), "PYTHONPATH": str(REPO), "HOME": os.environ.get("HOME", "")}
    data = raw if raw is not None else json.dumps(payload)
    return subprocess.run(["/bin/sh", "-c", _shipped_command()], input=data, capture_output=True, text=True,
                          env=env, cwd=REPO, timeout=60)


class TestShippedWrapper:
    def test_a_non_kit_call_passes_without_python_and_the_overhead_is_measured(self, tmp_path):
        b = _bin(tmp_path, "nocli", tagteam=None, python3=False)
        r = _run(_payload(agent=None, command="ls -la"), b)
        assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
        n = 200
        t0 = time.perf_counter()
        for _ in range(n):
            subprocess.run(["/bin/sh", "-c", _shipped_command()], input='{"tool_name":"Bash","tool_input":{"command":"ls"}}',
                           capture_output=True, text=True,
                           env={"PATH": str(b), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)})
        per_ms = (time.perf_counter() - t0) / n * 1000
        print(f"\n[phase 73] non-kit wrapper overhead: {per_ms:.2f} ms per call over {n} runs (incl. sh spawn)")

    def test_a_kit_call_with_the_real_cli_is_rewritten_and_validated(self, tmp_path):
        r = _run(_payload(command="tagteam cycle add x"), _bin(tmp_path, "real", tagteam="real"))
        assert r.returncode == 0, r.stderr
        hso = json.loads(r.stdout)["hookSpecificOutput"]
        assert hso["updatedInput"] == {"command": PREFIX + "tagteam cycle add x", "description": "run it"}
        assert "permissionDecision" not in hso

    def test_a_prefilter_false_positive_passes_untouched(self, tmp_path):
        """The text matches the prefilter (a kit identity nested in tool_input);
        the top-level identity is the main session, or another agent."""
        b = _bin(tmp_path, "real", tagteam="real")
        for agent in (None, "general-purpose"):
            p = _payload(agent=agent, command="echo x", note='{"agent_type": "tagteam:verifier"}')
            r = _run(p, b)
            assert (r.returncode, r.stdout) == (0, ""), (agent, r.stderr)

    def test_a_guard_deny_blocks_with_its_reason(self, tmp_path):
        r = _run({"agent_type": "tagteam:verifier", "tool_input": {"command": 3}}, _bin(tmp_path, "real", tagteam="real"))
        assert r.returncode == 2 and r.stdout == "" and "no command string" in r.stderr

    def test_no_cli_blocks(self, tmp_path):
        r = _run(_payload(), _bin(tmp_path, "nocli", tagteam=None))
        assert r.returncode == 2 and r.stdout == "" and "not on PATH" in r.stderr

    def test_an_old_cli_without_the_subcommand_blocks(self, tmp_path):
        old = _fake(rc=1, stderr="unknown hook: pre-tool-use")
        r = _run(_payload(), _bin(tmp_path, "old", tagteam=old))
        assert r.returncode == 2 and r.stdout == "" and "upgrade it" in r.stderr and "exit 1" in r.stderr

    @pytest.mark.parametrize("stdout", [
        "",                                                                              # exit 0, empty
        "not json at all",                                                               # invalid output
        "{}",
        '{"hookSpecificOutput": {}}',                                                    # hollow
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"echo hi","description":"run it"}}}',
        # review r3, counterexample 1: truncated
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"export TAGTEAM_READ_ONLY=1; echo hi"',
        # review r3, counterexample 2: prefix in the wrong field
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"echo hi","description":"export TAGTEAM_READ_ONLY=1; "}}}',
        # a field dropped
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"export TAGTEAM_READ_ONLY=1; echo hi"}}}',
        # auto-allow
        '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","updatedInput":{"command":"export TAGTEAM_READ_ONLY=1; echo hi","description":"run it"}}}',
    ])
    def test_exit_0_with_unusable_output_blocks_and_forwards_nothing(self, tmp_path, stdout):
        r = _run(_payload(), _bin(tmp_path, "bad", tagteam=_fake(stdout=stdout)))
        assert r.returncode == 2 and r.stdout == "", (stdout, r.stdout, r.stderr)

    def test_no_python3_blocks_a_kit_call(self, tmp_path):
        good = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                  "updatedInput": {"command": PREFIX + "echo hi", "description": "run it"}}})
        r = _run(_payload(), _bin(tmp_path, "nopy", tagteam=_fake(stdout=good), python3=False))
        assert r.returncode == 2 and r.stdout == "" and "python3" in r.stderr


# ---------------------------------------------------------------------------
# the agent files and the contract
# ---------------------------------------------------------------------------

def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        k, _, v = line.partition(":")
        meta[k.strip()] = v.strip()
    return meta, body


class TestTheKit:
    EXPECT = {"test-runner": ("haiku", True), "verifier": ("sonnet", True), "explore": ("haiku", False)}

    def test_three_agents_pinned(self):
        files = sorted(p.stem for p in (PLUGIN / "agents").glob("*.md"))
        assert files == sorted(self.EXPECT)
        for name, (model, bash) in self.EXPECT.items():
            meta, body = _frontmatter(PLUGIN / "agents" / f"{name}.md")
            assert meta["name"] == name and meta["model"] == model and meta["description"]
            tools = {t.strip() for t in meta["tools"].split(",")}
            deny = {t.strip() for t in meta["disallowedTools"].split(",")}
            assert not tools & {"Write", "Edit", "NotebookEdit"}
            assert {"Write", "Edit", "NotebookEdit"} <= deny
            assert ("Bash" in tools) is bash and (("Bash" in deny) is not bash)
            if bash:
                assert "Never fix" in body and "never run a tagteam command" in body and PREFIX.strip() in body
        assert "Never run the full suite" in _frontmatter(PLUGIN / "agents" / "test-runner.md")[1]
        assert "no Bash" in _frontmatter(PLUGIN / "agents" / "explore.md")[1]

    def test_the_contract_names_the_kit_and_where_enforcement_lives(self):
        skill = (PLUGIN / "skills" / "handoff" / "SKILL.md").read_text(encoding="utf-8")
        for a in ("tagteam:test-runner", "tagteam:verifier", "tagteam:explore"):
            assert a in skill
        assert "is a courtesy, not the enforcement" in skill and "tagteam doctor" in skill


class TestDoctorKit:
    def _plugin(self, tmp_path, agents=True, min_version="3.14.8"):
        root = tmp_path / "install"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"version": min_version, "tagteam": {"minVersion": min_version}}))
        if agents:
            (root / "agents").mkdir()
            for a in ("test-runner", "verifier", "explore"):
                (root / "agents" / f"{a}.md").write_text("x")
        return str(root)

    def test_states(self, tmp_path):
        from tagteam import diagnostics as dg
        found = {"state": "found", "reason": "user scope"}
        k = dg.observe_kit({"state": "missing"}, None, None, "3.14.8")
        assert k["state"] == "unavailable" and not k["warnings"]
        k = dg.observe_kit(found, self._plugin(tmp_path / "a", agents=False), "2.1.281", "3.14.8")
        assert k["state"] == "absent" and "update the plugin" in k["detail"]
        k = dg.observe_kit(found, self._plugin(tmp_path / "b"), dg.KIT_MIN_CLAUDE_CODE, "3.14.8")
        assert k["state"] == "found" and k["guard"] == "supported" and k["warnings"] == []
        k = dg.observe_kit(found, self._plugin(tmp_path / "c"), "2.0.1", "3.14.8")
        assert k["guard"] == "unguarded" and "don't delegate" in k["warnings"][0] and "explore" in k["warnings"][0]
        k = dg.observe_kit(found, self._plugin(tmp_path / "d"), None, "3.14.8")
        assert k["guard"] == "unknown" and k["warnings"]
        k = dg.observe_kit(found, self._plugin(tmp_path / "e", min_version="3.14.9"), dg.KIT_MIN_CLAUDE_CODE, "3.14.8")
        assert any("expects tagteam >= 3.14.9" in w for w in k["warnings"])
