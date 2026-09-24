"""Phase 48: ``tagteam hook session-start`` — the body of the plugin's
SessionStart hook. Phase 73: ``tagteam hook pre-tool-use`` — the read-only
guard for the plugin's kit agents (see ``pre_tool_use``).

Prints one status line when the cwd is a tagteam project with a readable
``handoff-state.json``, plus a version-skew warning when the plugin declares a
minimum tagteam version the installed CLI does not meet. In **every** other
case it prints nothing and exits 0: a session start must never fail, and a
non-tagteam project with the plugin installed must see nothing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MIN_VERSION_KEY = "minVersion"   # under plugin.json["tagteam"]


def _semver(s) -> tuple[int, ...] | None:
    if not isinstance(s, str):
        return None
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", s.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def banner_line(project_dir: Path) -> str | None:
    """The one-line banner, or None when there is nothing safe to say."""
    if not (project_dir / "tagteam.yaml").is_file():
        return None
    state_path = project_dir / "handoff-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    phase, ctype, rnd = state.get("phase"), state.get("type"), state.get("round")
    turn, status = state.get("turn"), state.get("status")
    if not isinstance(phase, str) or not isinstance(ctype, str) \
            or not isinstance(rnd, int) or not isinstance(status, str):
        return None
    if turn is not None and not isinstance(turn, str):
        return None
    return (f"tagteam: phase {phase} | type {ctype} | round {rnd} | "
            f"turn {turn or '—'} | status {status}")


def skew_warning(plugin_root: Path | None, installed: str) -> str | None:
    if plugin_root is None:
        return None
    try:
        manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    section = manifest.get("tagteam")
    minimum = section.get(MIN_VERSION_KEY) if isinstance(section, dict) else None
    min_t, inst_t = _semver(minimum), _semver(installed)
    if min_t is None or inst_t is None or inst_t >= min_t:
        return None
    return (f"warning: plugin {manifest.get('version', '?')} expects tagteam >= "
            f"{minimum}, installed {installed} — run: uv tool upgrade tagteam")


def session_start(argv: list[str], *, cwd: Path | None = None, out=None) -> int:
    out = out or sys.stdout
    plugin_root: Path | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--plugin-root" and i + 1 < len(argv):
            plugin_root = Path(argv[i + 1]) if argv[i + 1] else None
            i += 2
        else:
            i += 1   # unknown args are ignored, never fatal
    try:
        project = cwd or Path.cwd()
        line = banner_line(project)
        if line is None:
            return 0
        from tagteam import __version__
        print(line, file=out)
        warn = skew_warning(plugin_root, __version__)
        if warn:
            print(warn, file=out)
    except Exception:  # noqa: BLE001 — a hook must never fail a session start
        pass
    return 0


# ---------------------------------------------------------------------------
# Phase 73: the kit agents' read-only guard
# ---------------------------------------------------------------------------

KIT_PREFIX = "tagteam:"                        # plugin-scoped agent names: tagteam:test-runner, …
READ_ONLY_EXPORT = "export TAGTEAM_READ_ONLY=1; "

# The verdict is the exit code, so the plugin's shell wrapper can act on it
# without parsing JSON (it validates a rewrite separately, plugin-side):
GUARD_REWRITE = 0      # kit agent: stdout is the rewrite
GUARD_DENY = 2         # unusable input for a payload the wrapper matched
GUARD_NOT_KIT = 3      # verified: the top-level identity is not a kit agent — pass untouched


def guard_decision(payload) -> tuple[int, dict | None, str]:
    """(exit code, hook output or None, reason). Pure: no I/O."""
    if not isinstance(payload, dict):
        return GUARD_DENY, None, "tagteam guard: the hook input is not a JSON object"
    agent = payload.get("agent_type")
    if not (isinstance(agent, str) and agent.startswith(KIT_PREFIX)):
        return GUARD_NOT_KIT, None, ""
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return GUARD_DENY, None, f"tagteam guard: {agent}: the Bash input has no command string"
    new_input = dict(tool_input)                 # every other field kept as it was
    if not command.startswith(READ_ONLY_EXPORT):
        new_input["command"] = READ_ONLY_EXPORT + command
    # No permissionDecision: normal Bash permission evaluation still applies.
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": new_input}}
    return GUARD_REWRITE, out, ""


def pre_tool_use(stdin=None, out=None, err=None) -> int:
    """``tagteam hook pre-tool-use``: read the PreToolUse payload on stdin.

    A kit agent's (top-level ``agent_type`` ``tagteam:*``) Bash command is
    rewritten to start with ``export TAGTEAM_READ_ONLY=1; `` (exit 0, the
    rewrite on stdout); a non-kit caller exits 3 with no output; unusable
    input exits 2 with the reason on stderr. The plugin's wrapper blocks on
    anything it can't positively verify, including an older CLI without
    this subcommand (exit 1)."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        payload = json.loads(stdin.read())
    except (ValueError, UnicodeDecodeError, OSError):
        print("tagteam guard: the hook input is not valid JSON", file=err)
        return GUARD_DENY
    code, output, reason = guard_decision(payload)
    if output is not None:
        print(json.dumps(output), file=out)
    if reason:
        print(reason, file=err)
    return code


def hook_command(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help"):
        print("usage: tagteam hook session-start [--plugin-root DIR] | pre-tool-use")
        return 0 if args else 1
    if args[0] == "session-start":
        return session_start(args[1:])
    if args[0] == "pre-tool-use":
        return pre_tool_use()
    print(f"unknown hook: {args[0]}", file=sys.stderr)
    return 1
