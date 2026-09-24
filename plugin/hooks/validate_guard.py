"""Structural validation of the tagteam guard's rewrite (Phase 73).

Run by ``pre_tool_use.sh`` only for a payload whose text matched the kit
prefilter and whose guard (``tagteam hook pre-tool-use``) exited 0. It is
plugin code, deliberately NOT a subcommand of the CLI it checks: a broken or
mismatched CLI must not be trusted to validate itself.

stdin: the guard's stdout. env ``TAGTEAM_HOOK_INPUT``: the original hook
payload. Exit 0 and print the re-serialized object only if the rewrite is
exactly what the guard promises; exit 1 otherwise (the wrapper then blocks).
Standard library only.
"""
import json
import os
import sys

PREFIX = "export TAGTEAM_READ_ONLY=1; "


def problem(guard_out: str, original: str):
    """None when the rewrite is usable, else a one-line reason."""
    try:
        obj = json.loads(guard_out)
    except ValueError:
        return "the guard's output is not one complete JSON object"
    try:
        payload = json.loads(original)
    except ValueError:
        return "the original hook input is not JSON"
    if not isinstance(obj, dict) or not isinstance(payload, dict):
        return "not a JSON object"
    hso = obj.get("hookSpecificOutput")
    if not isinstance(hso, dict) or hso.get("hookEventName") != "PreToolUse":
        return "hookSpecificOutput.hookEventName is not PreToolUse"
    if hso.get("permissionDecision") == "allow":
        return "the rewrite must not auto-allow the command"
    new = hso.get("updatedInput")
    old = payload.get("tool_input")
    if not isinstance(new, dict) or not isinstance(old, dict):
        return "updatedInput (or the original tool_input) is not an object"
    cmd, orig = new.get("command"), old.get("command")
    if not isinstance(cmd, str) or not isinstance(orig, str):
        return "the command is not a string"
    want = orig if orig.startswith(PREFIX) else PREFIX + orig
    if cmd != want:
        return "updatedInput.command is not the original command behind the read-only export"
    if {k: v for k, v in new.items() if k != "command"} != {k: v for k, v in old.items() if k != "command"}:
        return "the rewrite changed or dropped another tool_input field"
    return None


def main() -> int:
    raw = sys.stdin.read()
    reason = problem(raw, os.environ.get("TAGTEAM_HOOK_INPUT", ""))
    if reason:
        print(f"tagteam: blocked — the read-only guard's rewrite is unusable: {reason}", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(raw)))      # re-serialized: never the guard's raw bytes
    return 0


if __name__ == "__main__":
    sys.exit(main())
