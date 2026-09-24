"""
CLI for Tagteam.

Usage:
    python -m tagteam init        - Initialize agent configuration
    python -m tagteam setup [dir] [--no-plugin] [--preview] [--accept PATH] [--force]
                                  - Bring framework files up to the package
    python -m tagteam hook session-start  - SessionStart hook body (plugin)
    python -m tagteam contract [--path]   - Print the handoff contract (for agents without the plugin)
    python -m tagteam migrate     - Migrate legacy projects to use config
    python -m tagteam watch       - Start the watcher daemon
    python -m tagteam tail        - Follow the in-flight headless turn log
    python -m tagteam state       - View/update orchestration state
    python -m tagteam session     - Manage orchestration sessions
    python -m tagteam serve       - Start the web dashboard server (--theme cockpit)
"""

from __future__ import annotations

import sys
from pathlib import Path

from tagteam.config import read_config


CONFIG_TEMPLATE = """# Tagteam Configuration
# Defines the two AI agents and their roles in the collaboration workflow.

agents:
  lead:
    name: {lead_name}
  reviewer:
    name: {reviewer_name}

# Optional blocks (see docs/how-tagteam-works.md):
#
# gatekeeper:              # deterministic pre-checks before each reviewer turn
#   enabled: true
#   on_submit: true        # run the gate from `tagteam cycle add` itself — no watcher needed;
#                          # the round's one full-suite run is then on the record
#   tests:
#     command: "python -m pytest -q"
#
# watcher:
#   resend_minutes: 15     # watchdog re-send of a still-'ready' turn (idle agent only; 0 = never)
"""

HANDOFF_EXPLAINER = """
How the handoff works:

  Lead (one AI agent) plans each phase and implements the approved plan.
  Reviewer (a second AI agent) reviews both the plan and the implementation.
  Arbiter (you, the human) breaks ties and approves phases.

Work progresses phase-by-phase. Each phase is listed in docs/roadmap.md and
goes through two review cycles: plan, then implementation. If the two agents
can't make progress in 10 rounds, control escalates to the human arbiter.

State is tracked in handoff-state.json (current turn) and
docs/handoffs/<phase>_<type>_rounds.jsonl plus <phase>_<type>_status.json
(per-cycle rounds). Either agent can pick up where the other left off at
any time.
"""

GETTING_STARTED = """
Getting Started
===============
Start a session with agents and watcher (run from project root):

  tagteam session start

If you are on Windows or another unsupported platform, use the manual backend:

  tagteam session start --backend manual
  tagteam watch --mode notify

Or use quickstart (runs setup + init + session with backend auto-detection):

  tagteam quickstart
"""


def prompt_input(
    prompt: str,
    valid_options: list[str] | None = None,
    lowercase: bool = True,
) -> str:
    """Get user input with optional validation."""
    while True:
        raw_value = input(prompt).strip()
        if not raw_value:
            print("  Please enter a value.")
            continue

        check_value = raw_value.lower()
        if valid_options and check_value not in valid_options:
            print(f"  Please enter one of: {', '.join(valid_options)}")
            continue

        return check_value if lowercase else raw_value


def write_config(target_dir: str, lead_name: str, reviewer_name: str) -> Path:
    """Write tagteam.yaml to target_dir. Non-interactive."""
    config_path = Path(target_dir) / "tagteam.yaml"
    config_content = CONFIG_TEMPLATE.format(
        lead_name=lead_name,
        reviewer_name=reviewer_name,
    )
    config_path.write_text(config_content, encoding="utf-8")
    return config_path


def needs_init(project_dir: str = ".") -> bool:
    """Check if agent configuration is needed."""
    return not (Path(project_dir) / "tagteam.yaml").exists()


def run_init(project_dir: str = ".", show_explainer: bool = False) -> bool:
    """Run interactive init if config is missing. Requires TTY.

    show_explainer=False by default so callers like quickstart can print the
    explainer themselves exactly once. Standalone CLI dispatch passes True.
    """
    if not needs_init(project_dir):
        print("Agent configuration already exists; skipping init.")
        return True

    if not sys.stdin.isatty():
        print("Error: No tagteam.yaml found and stdin is not interactive.")
        print("  Run 'tagteam init' interactively first.")
        return False

    import os

    original_dir = os.getcwd()
    try:
        os.chdir(project_dir)
        init_command(show_explainer=show_explainer)
    finally:
        os.chdir(original_dir)
    return True


def init_command(show_explainer: bool = True) -> int:
    """Interactive init command to create tagteam.yaml.

    Prompts for two agent names: lead first, reviewer second. No role prompt —
    order defines role.
    """
    config_path = Path("tagteam.yaml")

    print()
    print("Tagteam Setup")
    print("================")
    print("This framework coordinates work between two AI agents.")
    print()

    if config_path.exists():
        existing = read_config(config_path)
        if existing:
            agents = existing.get("agents", {})
            lead = agents.get("lead", {}).get("name", "unknown")
            reviewer = agents.get("reviewer", {}).get("name", "unknown")

            print("tagteam.yaml already exists with:")
            print(f"  Lead: {lead}")
            print(f"  Reviewer: {reviewer}")
        else:
            print("tagteam.yaml already exists but could not be parsed.")
            print("(File may be empty or malformed)")

        print()
        overwrite = prompt_input("Overwrite? (y/n): ", ["y", "n", "yes", "no"])
        if overwrite not in ["y", "yes"]:
            print("Aborted.")
            return 0
        print()

    print("Enter the names of your two AI agents (first is Lead, second is Reviewer).")
    print()

    lead_name = prompt_input("Lead agent name: ", lowercase=False)
    reviewer_name = prompt_input("Reviewer agent name: ", lowercase=False)
    print()

    write_config(".", lead_name, reviewer_name)

    print("Created tagteam.yaml")
    print(f"  Lead: {lead_name}")
    print(f"  Reviewer: {reviewer_name}")

    if show_explainer:
        print(HANDOFF_EXPLAINER)
    print(GETTING_STARTED)
    return 0


def setup_command(target_dir: str = ".", *, no_plugin: bool = False, **opts) -> int:
    """Bring the framework files in ``target_dir`` up to the package
    (Phase 52: ``--preview``, ``--accept PATH``, ``--force``)."""
    from tagteam.setup import main as setup_main

    return setup_main(target_dir, no_plugin=no_plugin, **opts)


_BACKEND_SURFACE = {
    "iterm2": "tab",
    "tmux": "pane",
    "terminal": "window",
    "manual": "terminal",
}


def _print_priming_box(lead_name: str, reviewer_name: str, surface: str) -> None:
    """Print a boxed 'SESSION READY' message with backend-appropriate terminology."""
    prime_body = (
        "Read tagteam.yaml, project instructions and docs/workflows.md, then read the handoff contract: "
        "/tagteam:handoff in Claude Code (/handoff if this project "
        "vendors the skill); other agents: `tagteam contract`"
    )
    lines = [
        "SESSION READY",
        "",
        f"In the Lead {surface}, tell {lead_name}:",
        f'  "{prime_body}"',
        "",
        f"In the Reviewer {surface}, tell {reviewer_name} the same.",
    ]
    width = max(len(line) for line in lines) + 4
    print("╔" + "═" * (width - 2) + "╗")
    for line in lines:
        print("║ " + line.ljust(width - 4) + " ║")
    print("╚" + "═" * (width - 2) + "╝")


def quickstart_command(args: list[str]) -> int:
    """Run setup + init + session start in one command."""
    from tagteam.session import SUPPORTED_BACKENDS, default_backend, ensure_session
    from tagteam.setup import run_setup

    project_dir = "."
    backend = None
    i = 0
    while i < len(args):
        if args[i] == "--dir" and i + 1 < len(args):
            project_dir = args[i + 1]
            i += 2
        elif args[i] == "--backend" and i + 1 < len(args):
            backend = args[i + 1]
            i += 2
        else:
            i += 1

    if backend is not None and backend not in SUPPORTED_BACKENDS:
        print(f"Invalid backend: {backend}. Use 'iterm2', 'tmux', 'terminal', or 'manual'.")
        return 1

    project_dir = str(Path(project_dir).resolve())

    print("Tagteam - Quick Start")
    print("========================")
    print(f"Project: {project_dir}")
    print()

    print("[1/3] Framework setup...")
    run_setup(project_dir)
    print()

    print("[2/3] Agent configuration...")
    if not run_init(project_dir, show_explainer=False):
        return 1
    print()

    print("[3/3] Starting session...")
    outcome = ensure_session(project_dir, backend, launch=True)
    if outcome == "error":
        return 1

    effective_backend = backend or default_backend()
    surface = _BACKEND_SURFACE.get(effective_backend, "terminal")

    config = read_config(Path(project_dir) / "tagteam.yaml") or {}
    agents = config.get("agents", {})
    lead_name = agents.get("lead", {}).get("name", "Lead")
    reviewer_name = agents.get("reviewer", {}).get("name", "Reviewer")

    from tagteam.onboarding import describe_roles
    print(describe_roles(project_dir))
    print(HANDOFF_EXPLAINER)

    if outcome == "exists":
        print("Session already running. Switch to it to continue.")
        return 0

    _print_priming_box(lead_name, reviewer_name, surface)
    return 0


def upgrade_command(args: list[str] | None = None) -> int:
    """Migrate every registered project to the installed package (Phase 52):
    framework-owned paths are refreshed, custom ones kept and reported with
    the `tagteam setup DIR --accept PATH` line to run. ``--preview`` writes
    nothing — not to the projects, not to the registry (a registered
    directory that is missing is skipped with a note, not pruned). Exit 1 if
    any project raised or had a refused path."""
    from tagteam.registry import get_registered_projects, read_registry_raw
    from tagteam.setup import main as setup_main

    args = list(args or [])
    preview = "--preview" in args or "--dry-run" in args
    unknown = [a for a in args if a not in ("--preview", "--dry-run")]
    if unknown:
        print(f"upgrade: unknown option {unknown[0]}")
        print("usage: tagteam upgrade [--preview]")
        return 2

    missing: list[str] = []
    if preview:
        raw = read_registry_raw()
        projects = [p for p in raw if Path(p).is_dir()]
        missing = [p for p in raw if p not in projects]
    else:
        projects = get_registered_projects()

    for p in missing:
        print(f"note: registered project not found, skipped: {p}")
    if missing:
        print()

    if not projects:
        print("No registered projects found.")
        print()
        print("Projects are registered automatically when you run 'tagteam setup'.")
        print("Run 'tagteam setup <dir>' in each project directory first.")
        return 0

    verb = "Previewing" if preview else "Upgrading"
    print(f"{verb} {len(projects)} registered project(s)...")
    print()

    failed = []
    refused = []
    for project_dir in projects:
        print("=" * 60)
        print(f"Project: {project_dir}")
        print("=" * 60)
        try:
            if setup_main(project_dir, report_user_skills=False, preview=preview) != 0:
                refused.append(project_dir)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(project_dir)
        print()

    # Phase 49: one aggregate note for the whole run, never per project
    from tagteam.setup import report_legacy_user_skills
    if report_legacy_user_skills():
        print()

    if failed or refused:
        if failed:
            print(f"Completed with {len(failed)} error(s):")
            for project_dir in failed:
                print(f"  - {project_dir}")
        if refused:
            print(f"{len(refused)} project(s) with refused paths (see their reports):")
            for project_dir in refused:
                print(f"  - {project_dir}")
        return 1

    if preview:
        print(f"Previewed {len(projects)} project(s); nothing written.")
    else:
        print(f"All {len(projects)} project(s) upgraded successfully.")
    return 0


HELP_TEXT = """\
Tagteam

Usage: tagteam <command>

Quick start (from project root):
  tagteam quickstart

  This runs setup, agent configuration, and session start in one command.
  The session backend is auto-detected unless you pass --backend.

Commands:
  quickstart    Setup + init + session start in one command
  init          Create tagteam.yaml configuration interactively
  setup [dir]   Bring framework files up to the package: refreshes only what tagteam
                wrote, keeps custom files (--preview, --accept PATH, --force, --no-plugin)
  session       Manage orchestration session (start/kill/attach)
  watch         Start the watcher daemon for automated orchestration
                (--mode headless spawns each turn as a fresh agent process)
                watch status: is it running, when it last looked, its last dispatch
                watch log [-n N] [--json]: what the watcher did, newest last
  tail          Follow the in-flight headless turn log (or show the last one)
  pause         Hold dispatch in every watcher mode (marker file)
  resume        Clear the pause; the watcher re-dispatches the owed turn once
  cancel-turn   Kill the in-flight headless turn (recorded as 'cancelled', then paused)
  interject     Leave an arbiter note for the next turn (--to lead|reviewer, --list, --retire)
  usage         Per-turn token usage for this project (--by role|cycle|model|kind, --json)
  report        What a phase took: rounds, bounces, gate and turn time, usage coverage (--phase P, --json)
  bench         Review bench: replay recorded rounds at reviewer cells (select | run [--yes] | table)
  rollback      Print (or with --yes run) the revert recipe for a given version
  brief         Show the escalation decision brief for the current event (--list, --generate)
  gate          Gatekeeper pre-checks: check (lead pre-flight) | run | status | list
  panel         Reviewer panel: run | status | list | lenses | preview --lens L
  rule          Rule on an escalation: approve | request-changes | answer (--to lead|reviewer)
  state         View or update the orchestration state file
  roadmap       Roadmap phases, DAG queue, check | graph | ready | resume, worktree | worktrees (3.4)
  cycle         Manage cycle documents (init, add, status, rounds [--tail N], render)
  serve         Start the web dashboard server (--theme cockpit for the arbiter cockpit;
                default is the Saloon; --host, --max-sse; `serve.theme` in tagteam.yaml)
  lead          Talk to the lead agent from the terminal: `tagteam lead "message"`
                (same engine as the cockpit's Lead panel; --new / --conversation ID / --list)
  hub           One surface over every registered project (Needs you / Waiting / Quiet,
                burn, shared window; each cockpit mounted at /p/<id>/); --list for text
  registry      list | unregister PATH — the projects `tagteam setup` registered
  tui           Launch the Handoff Saloon terminal UI
  migrate       Migrate legacy projects to use tagteam.yaml
  upgrade       Migrate every registered project to the installed package (--preview)
  --version     Version, and the directory this tagteam was imported from (-V, version)
  doctor [dir]  Read-only report: legacy workflow findings, per-role executables, contract
                entry points, instruction sources, tool config names (--json)

Advanced setup (individual steps, from project root):
  tagteam setup
  tagteam init
  tagteam session start

Manual workflow fallback:
  tagteam session start --backend manual
  tagteam watch --mode notify

Headless mode (opt-in; no terminals to drive, works on Windows):
  tagteam watch --mode headless
  tagteam tail

Arbiter controls (any mode):
  tagteam pause --reason "reviewing by hand"    tagteam resume
  tagteam interject "prefer the smaller diff"   tagteam cancel-turn
  tagteam usage
Standing orders (every turn; `stop` is enforced, notes are advisory):
  tagteam orders stop roadmap [--run]           tagteam orders add "hold the PR for my approval"
Safe tagteam.yaml edits (comments kept; refused unless the engine would honour them):
  tagteam config keys                           tagteam config set gatekeeper.enabled true --preview
Escalations (opt-in briefer: `briefer: {enabled: true}` in tagteam.yaml):
  tagteam brief                                 tagteam rule approve --content "..."
Gatekeeper (opt-in: `gatekeeper: {enabled: true, tests: {command: "..."}}`; `on_submit: true` gates from `cycle add`):
  tagteam gate check [--skip-tests]             tagteam gate status
Reviewer panel (opt-in: `panel: {enabled: true}`; 2–3 lens reviews merged into one reviewer entry):
  tagteam panel lenses                          tagteam panel status
"""


# Phase 50: what a read-only helper may run. Under `TAGTEAM_READ_ONLY` every
# other invocation is refused BEFORE dispatch — so a command that mutates the
# tree through a path the low-level guards do not cover is still stopped.
# The table lists READS (the small, safe set): a new command is refused by
# default until it is classified here; `tests/test_readonly.py` pins that
# every dispatched command is classified.
_HELP = {"-h", "--help", "help"}
# Phase 64. Commands are lower-cased before they are looked at, so `-V` is `-v`.
_VERSION = {"--version", "-v", "version"}


def _no_flag(*flags: str):
    return lambda rest: not (set(rest) & set(flags))


def _sub_in(*subs: str):
    return lambda rest: bool(rest) and rest[0] in subs


READ_ONLY_COMMANDS: dict[str, "callable"] = {
    "cycle": _sub_in("status", "rounds"),
    "state": lambda rest: not rest or (rest[0] == "diagnose" and "--clean" not in rest),
    "gate": _sub_in("status", "list"),
    "panel": _sub_in("status", "lenses", "list"),
    "roadmap": _sub_in("queue", "phases", "check", "graph", "ready"),
    "interject": lambda rest: "--list" in rest,
    "brief": _no_flag("--generate"),
    "hub": _sub_in("list"),
    "registry": _sub_in("list"),
    "usage": lambda rest: True,
    "contract": lambda rest: True,
    "tail": lambda rest: True,
    "hook": lambda rest: True,
    "doctor": lambda rest: True,
    "report": lambda rest: True,
    "watch": _sub_in("status", "log"),   # Phase 67: the heartbeat / event-log reads only
    "orders": lambda rest: not rest or rest == ["--json"] or rest[0] in ("-h", "--help", "help"),  # Phase 70
    # Phase 71b: `config keys` and `config set … --preview` read; `config set` without it writes
    "config": lambda rest: bool(rest) and (rest[0] == "keys" or (rest[0] == "set" and "--preview" in rest)),
}
# Never a helper's business: parents, humans and installers only. Refused with
# any arguments — `--help` included (see `read_only_refusal`).
READ_ONLY_REFUSED = ("quickstart", "init", "setup", "migrate", "pause", "resume", "cancel-turn",
                     "rollback", "rule", "session", "serve", "lead", "tui", "upgrade", "bench")


def read_only_refusal(argv: list[str]) -> str | None:
    """Detail line when `argv` (command + rest) is not a read invocation."""
    if not argv or argv[0].lower() in _HELP | _VERSION:
        return None          # top-level `tagteam --help` / `--version` print and exit
    command, rest = argv[0].lower(), argv[1:]
    # No command-level help exception: not every subcommand consumes `--help`
    # (e.g. `setup --help` would take it as the target directory). Help for a
    # refused command is refused too — fail closed.
    allowed = READ_ONLY_COMMANDS.get(command)
    if allowed is not None and allowed(rest):
        return None
    shown = " ".join([command] + rest[:1])
    return (f"`tagteam {shown}` is not a read command; a read-only helper may run: "
            + ", ".join(f"{c} {'/'.join(v)}" if isinstance(v, tuple) else c
                        for c, v in _read_only_summary()))


def _read_only_summary() -> list[tuple[str, tuple[str, ...] | None]]:
    return [("cycle", ("status", "rounds")), ("state", ("diagnose",)), ("gate", ("status", "list")),
            ("panel", ("status", "lenses", "list")), ("roadmap", ("queue", "phases", "check", "graph", "ready")),
            ("interject --list", None), ("brief", None), ("hub list", None),
            ("registry list", None), ("usage", None), ("contract", None), ("tail", None), ("hook", None),
            ("doctor", None), ("report", None), ("watch", ("status", "log")), ("orders [--json]", None),
            ("config keys", None), ("config set … --preview", None)]


def main() -> int:
    """Main CLI entry point.

    Phase 50: under `TAGTEAM_READ_ONLY` only the invocations in
    `READ_ONLY_COMMANDS` are dispatched; anything else — and any write refused
    deeper in the package — surfaces here as one message, exit 2.
    """
    from tagteam.dualwrite import ReadOnlyError, read_only
    from tagteam.participants import ParticipantMismatch
    try:
        if read_only():
            detail = read_only_refusal(sys.argv[1:])
            if detail is not None:
                raise ReadOnlyError(detail)
        return _dispatch()
    except (ReadOnlyError, ParticipantMismatch) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _dispatch() -> int:
    """Dispatch `sys.argv` to the subcommand (see `main`)."""
    if len(sys.argv) < 2:
        print(HELP_TEXT)
        return 1

    command = sys.argv[1].lower()

    if command == "quickstart":
        return quickstart_command(sys.argv[2:])
    if command == "init":
        return init_command()
    if command == "setup":
        from tagteam.setup import parse_setup_args
        try:
            target, opts = parse_setup_args(sys.argv[2:])
        except ValueError as e:
            print(f"setup: {e}")
            print("usage: tagteam setup [dir] [--no-plugin] [--preview] [--accept PATH]... [--force]")
            return 2
        return setup_command(target, **opts)
    if command == "hook":
        from tagteam.hook import hook_command
        return hook_command(sys.argv[2:])
    if command == "contract":
        from tagteam.contract import contract_command
        return contract_command(sys.argv[2:])
    if command == "migrate":
        from tagteam.migrate import migrate_command

        return migrate_command(sys.argv[2:])
    if command == "watch":
        from tagteam.watcher import watch_command

        return watch_command(sys.argv[2:])
    if command == "tail":
        from tagteam.headless import tail_command

        return tail_command(sys.argv[2:])
    if command == "pause":
        from tagteam.controls import pause_command

        return pause_command(sys.argv[2:])
    if command == "resume":
        from tagteam.controls import resume_command

        return resume_command(sys.argv[2:])
    if command == "cancel-turn":
        from tagteam.controls import cancel_turn_command

        return cancel_turn_command(sys.argv[2:])
    if command == "interject":
        from tagteam.controls import interject_command

        return interject_command(sys.argv[2:])
    if command == "orders":
        from tagteam.orders import orders_command

        return orders_command(sys.argv[2:])
    if command == "config":
        from tagteam.config_edit import config_command

        return config_command(sys.argv[2:])
    if command == "usage":
        from tagteam.usage import usage_command

        return usage_command(sys.argv[2:])
    if command == "report":
        from tagteam.report import report_command

        return report_command(sys.argv[2:])
    if command == "bench":
        from tagteam.bench import bench_command

        return bench_command(sys.argv[2:])
    if command == "rollback":
        from tagteam.controls import rollback_command

        return rollback_command(sys.argv[2:])
    if command == "brief":
        from tagteam.briefer import brief_command

        return brief_command(sys.argv[2:])
    if command == "gate":
        from tagteam.gatekeeper import gate_command

        return gate_command(sys.argv[2:])
    if command == "panel":
        from tagteam.panel import panel_command

        return panel_command(sys.argv[2:])
    if command == "rule":
        from tagteam.controls import rule_command

        return rule_command(sys.argv[2:])
    if command == "roadmap":
        from tagteam.roadmap import roadmap_command

        return roadmap_command(sys.argv[2:])
    if command == "cycle":
        from tagteam.cycle import cycle_command

        return cycle_command(sys.argv[2:])
    if command == "state":
        from tagteam.state import state_command

        return state_command(sys.argv[2:])
    if command == "session":
        from tagteam.session import session_command

        return session_command(sys.argv[2:])
    if command == "serve":
        from tagteam.server import serve_command

        return serve_command(sys.argv[2:])
    if command == "lead":
        from tagteam.lead_chat import lead_command

        return lead_command(sys.argv[2:])
    if command == "hub":
        from tagteam.hub import hub_command

        return hub_command(sys.argv[2:])
    if command == "registry":
        from tagteam.hub import registry_command

        return registry_command(sys.argv[2:])
    if command == "tui":
        try:
            from tagteam.tui import tui_command
        except ImportError:
            print("The TUI requires the 'textual' package.")
            print("Install it with: pip install tagteam[tui]")
            return 1
        return tui_command(sys.argv[2:])
    if command == "upgrade":
        return upgrade_command(sys.argv[2:])
    if command == "doctor":
        from tagteam.diagnostics import doctor_command

        return doctor_command(sys.argv[2:])
    if command in ["-h", "--help", "help"]:
        print(HELP_TEXT)
        return 0
    if command in _VERSION:
        import tagteam
        print(f"tagteam {tagteam.__version__}")
        print(f"  {Path(tagteam.__file__).resolve().parent}")     # which copy is this
        return 0

    print(f"Unknown command: {command}")
    print("Run 'tagteam --help' for usage.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
