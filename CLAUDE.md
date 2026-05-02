# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Tagteam is a Python CLI that orchestrates structured handoffs between two AI agents (a Lead and a Reviewer) with a human Arbiter. Work proceeds phase-by-phase from `docs/roadmap.md`; each phase has a `plan` cycle and an `impl` cycle, capped at 10 rounds before auto-escalation. The package is installed by users into their own project directories — most of the code paths assume "the user's project," not this repo.

This repo is **also** a tagteam-managed project (it has its own `tagteam.yaml`, `handoff-state.json`, and `docs/handoffs/`), so beware: running cycle/state CLI commands here mutates this repo's own handoff state.

## Common commands

```bash
# Tests
pytest                            # full suite
pytest tests/test_cycle.py        # single file
pytest tests/test_cycle.py::test_x # single test
./scripts/project-helper.sh test  # convenience wrapper

# Local install for development
pip install -e .
pip install -e '.[tui]'           # with the textual TUI

# Run the CLI from source (no install required)
python -m tagteam <command>
```

There is no configured linter or formatter in `pyproject.toml`; `.ruff_cache/` exists but ruff is not part of the build. Don't add lint/format steps unless asked.

## Release flow

`pyproject.toml` is the version source of truth. The `Publish to PyPI` workflow (`.github/workflows/publish.yml`) triggers on `v*` tag pushes and **fails the build if the tag doesn't match `pyproject.toml`**. So a release is: bump version in `pyproject.toml` → commit → `git tag vX.Y.Z` → `git push --tags`. Do not push a tag without bumping first.

## Architecture: how the pieces fit

The CLI dispatches to subcommand modules from `tagteam/cli.py`. The interesting modules are:

- **`state.py`** — atomic read/write of `handoff-state.json` (whose turn, what command, current phase/type/round). Owns project-root resolution: `_resolve_project_root()` walks up from cwd looking for the nearest `tagteam.yaml` *before* falling back to `git rev-parse`. This walk-up rule exists specifically to prevent a nested git repo from shadowing the outer tagteam project (Issue #1 in `docs/handoff-cycle-issues-2026-04-24.md`). All cycle/state writes go through this resolver — don't reintroduce raw `Path.cwd()` writes.
- **`cycle.py`** — append-only JSONL rounds + a small status JSON, one pair per cycle at `docs/handoffs/{phase}_{type}_{rounds.jsonl,status.json}`. Defines the action vocabulary (`SUBMIT_FOR_REVIEW`, `REQUEST_CHANGES`, `APPROVE`, `ESCALATE`, `NEED_HUMAN`, `AMEND`) and the state-transition table. `STALE_ROUND_LIMIT = 10` triggers auto-escalation when the lead re-submits identical content. Includes scope-diff/baseline logic for impl-review audits — `_TAGTEAM_ARTIFACT_FILES` / `_TAGTEAM_ARTIFACT_PREFIXES` exclude bookkeeping files written *after* baseline capture.
- **`watcher.py`** — polling daemon that reads `handoff-state.json` and triggers the next agent (macOS notifications or `tmux send-keys`). Detects "busy" terminals via screen-scrape patterns to avoid interrupting in-flight work.
- **`session.py` + `iterm.py`** — multi-terminal session management. Backends: `iterm2` (macOS, three tabs), `tmux` (three panes), `manual` (prints commands). `default_backend()` auto-detects.
- **`server.py`** — Flask-free hand-rolled HTTP server for the web dashboard (the "Saloon"); static assets live in `tagteam/data/web/`.
- **`tui/`** — optional Textual-based TUI (gated behind `pip install tagteam[tui]`).
- **`setup.py` (the module, not packaging)** — `tagteam setup` copies framework files from `tagteam/data/` into a target project: `.claude/skills/handoff/SKILL.md`, `templates/`, `docs/checklists/`, sample `docs/roadmap.md`, etc. `needs_setup()` checks a fixed set of marker files to keep setup idempotent.
- **`registry.py`** — tracks which projects ran `tagteam setup`, used by `tagteam upgrade` to re-copy framework files after a `pip install -U`.

The handoff workflow itself is defined in `tagteam/data/.claude/skills/handoff/SKILL.md` (also installed at `.claude/skills/handoff/SKILL.md` in this repo). That file is the **contract** agents follow: status banner format, action commands, NEXT-COMMAND box, AMEND semantics. Changes to cycle states or CLI flags need to be reflected there too.

## Conventions worth knowing

- `tagteam/data/` is shipped as package data (see `[tool.setuptools.package-data]`). Adding new template/skill/checklist files requires the matching glob in `pyproject.toml` or they won't reach installed users.
- The CLI prints copious user-facing prose; treat it as part of the UX, not noise. `HANDOFF_EXPLAINER`, `GETTING_STARTED`, and the `_print_priming_box` boxed banner in `cli.py` are intentional.
- Tests are plain pytest, no fixtures package, no conftest tricks. New modules should get a sibling `tests/test_<module>.py`.
- Templates use simple `{variable}` substitution via `templates.py:render_template` — not Jinja.

## What lives in `docs/`

`docs/roadmap.md`, `docs/decision_log.md`, `docs/phases/`, `docs/handoffs/`, `docs/escalations/` are this repo's *own* tagteam working set — they document tagteam's development using tagteam. `docs/tagteam-2.0-proposal.md` is the active forward-looking design; `docs/handoff-cycle-issues-2026-04-24.md` enumerates known issues that drove recent fixes (project-root resolution, scope-diff baseline, AMEND action).
