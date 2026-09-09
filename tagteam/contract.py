"""Phase 48: the handoff contract — where it lives and how an agent is told
to read it.

The contract text is one document, shipped twice as the same bytes: as the
plugin skill (Claude Code invokes it as ``/tagteam:handoff``) and as package
data (``tagteam contract`` prints it; headless prompts embed it). A project
that still vendors a copy at ``.claude/skills/handoff/SKILL.md`` invokes it
as ``/handoff``; :func:`handoff_command` picks the right name for a project.
"""
from __future__ import annotations

import sys
from pathlib import Path

SKILL_RELPATH = Path(".claude") / "skills" / "handoff" / "SKILL.md"
PACKAGED_SKILL_PATH = Path(__file__).parent / "data" / SKILL_RELPATH

PLUGIN_SKILL_COMMAND = "/tagteam:handoff"   # plugin skills are namespaced by plugin name
LOCAL_SKILL_COMMAND = "/handoff"            # a project-local (vendored) skill

# How any agent gets at the contract: Claude Code has the slash command;
# anything with a shell (Codex) reads the same bytes with `tagteam contract`.
# Every runtime instruction that names the contract is built from this one
# phrase — never from a file path, which a migrated project no longer has.
CONTRACT_HOWTO = "the handoff contract (`tagteam contract`; in Claude Code: /tagteam:handoff)"

# The standard "act on your turn" command written by cycle add/init.
STANDARD_TURN_COMMAND = (
    f"Read {CONTRACT_HOWTO} and handoff-state.json, then act on your turn"
)


def handoff_command(project_root: str | Path) -> str:
    """The slash command that invokes the contract in ``project_root``:
    ``/handoff`` while the project vendors the skill (works with or without
    the plugin), ``/tagteam:handoff`` once only the plugin serves it."""
    if (Path(project_root) / SKILL_RELPATH).is_file():
        return LOCAL_SKILL_COMMAND
    return PLUGIN_SKILL_COMMAND


def terminal_turn_message(command: str) -> str:
    """Translate workflow slash requests into text accepted by either CLI.

    State retains its structured start command for headless verification, but
    terminal input must not depend on a provider's installed slash commands.
    """
    if command.strip() in (LOCAL_SKILL_COMMAND, PLUGIN_SKILL_COMMAND):
        return STANDARD_TURN_COMMAND
    for prefix in (LOCAL_SKILL_COMMAND, PLUGIN_SKILL_COMMAND):
        if command.startswith(prefix + " start "):
            return (
                f"Read {CONTRACT_HOWTO}, tagteam.yaml, and handoff-state.json. "
                f"Follow the contract's workflow request: {command}. "
                "For an impl request, implement the approved plan before "
                "opening the implementation review cycle."
            )
    return command


def contract_text() -> str:
    return PACKAGED_SKILL_PATH.read_text(encoding="utf-8")


def contract_command(args: list[str]) -> int:
    """``tagteam contract`` — print the packaged handoff contract (``--path``
    prints where it is). For agents without Claude Code's plugin skills."""
    if args and args[0] in ("-h", "--help"):
        print("usage: tagteam contract [--path]")
        return 0
    if "--path" in args:
        print(PACKAGED_SKILL_PATH)
        return 0
    try:
        sys.stdout.write(contract_text())
    except OSError as e:
        print(f"contract: cannot read {PACKAGED_SKILL_PATH}: {e}", file=sys.stderr)
        return 1
    return 0
