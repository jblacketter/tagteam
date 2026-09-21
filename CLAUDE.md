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

Since Phase 64 `tagteam.__version__` reads the `pyproject.toml` beside the package when it declares `name = "tagteam"` (a source tree / editable install) and falls back to `importlib.metadata` otherwise (every wheel install). An editable install's dist-info is frozen at install time, so `uv tool list` / `pip list` may still show an old number after a bump — that is cosmetic now: what tagteam reports and stamps into manifests follows the tree. `tagteam --version` prints the version and the directory it was imported from. `tagteam/installs.py` (Phase 65, no other tagteam imports — `diagnostics` and `framework` both use it) reports a tagteam installed in a project's own `.venv`/`venv`; `doctor` warns and `tagteam state` adds a clause when its version differs from the running one.

## Architecture: how the pieces fit

The CLI dispatches to subcommand modules from `tagteam/cli.py`. The interesting modules are:

- **`state.py`** — atomic read/write of `handoff-state.json` (whose turn, what command, current phase/type/round). Owns project-root resolution: `_resolve_project_root()` walks up from cwd looking for the nearest `tagteam.yaml` *before* falling back to `git rev-parse`. This walk-up rule exists specifically to prevent a nested git repo from shadowing the outer tagteam project (Issue #1 in `docs/handoff-cycle-issues-2026-04-24.md`). All cycle/state writes go through this resolver — don't reintroduce raw `Path.cwd()` writes.
- **`cycle.py`** — append-only JSONL rounds + a small status JSON, one pair per cycle at `docs/handoffs/{phase}_{type}_{rounds.jsonl,status.json}`. Defines the action vocabulary (`SUBMIT_FOR_REVIEW`, `REQUEST_CHANGES`, `APPROVE`, `ESCALATE`, `NEED_HUMAN`, `AMEND`) and the state-transition table. `STALE_ROUND_LIMIT = 10` triggers auto-escalation when the lead re-submits identical content. Includes scope-diff/baseline logic for impl-review audits — `_TAGTEAM_ARTIFACT_FILES` / `_TAGTEAM_ARTIFACT_PREFIXES` exclude bookkeeping files written *after* baseline capture.
- **`watcher.py`** — polling daemon that reads `handoff-state.json` and triggers the next agent (macOS notifications or `tmux send-keys`). Detects "busy" terminals via screen-scrape patterns to avoid interrupting in-flight work.
- **`session.py` + `iterm.py`** — multi-terminal session management. Backends: `iterm2` (macOS, three tabs), `tmux` (three panes), `manual` (prints commands). `default_backend()` auto-detects.
- **`server.py`** — Flask-free hand-rolled HTTP server for the web dashboard (the "Saloon"); static assets live in `tagteam/data/web/`.
- **`tui/`** — optional Textual-based TUI (gated behind `pip install tagteam[tui]`).
- **`setup.py` (the module, not packaging)** — `tagteam setup` brings the framework files from `tagteam/data/` (`docs/workflows.md`, plus `.claude/skills/handoff/SKILL.md` when the plugin is absent) up to the package and seeds `docs/roadmap.md` etc. when absent. `needs_setup()` keys on `docs/workflows.md` to keep setup idempotent. Since Phase 52 it is a thin caller of **`framework.py`**: every managed path is classified against the package and the project's committed `tagteam-manifest.json`; only provably tagteam-written content is refreshed, custom content is kept and reported (`--preview`, `--accept PATH`, `--force`). Since Phase 61 `templates/*.md` and `docs/checklists/*.md` are *retired*: never created, and removed from a project when provably tagteam's (`_retired_sources()`); their package copies stay in `tagteam/data/` as seed sources, bench's checklist fallback and provenance evidence — don't delete them. Since Phase 62 `tagteam/data/history/<last-tag>/…` holds the 13 earlier sources (through 3.12.0) that `_classify()` matches verbatim or rendered for the configured / swapped names — 12 as text, and the pre-plugin `v3.10.0/workflows.md` as a `.sha256` digest only, because its text is all dead `/handoff-*` commands and `TestShippedDocsAudit` forbids shipping those; it is frozen provenance pinned to git tags by `test_history_table_is_what_git_history_says` — never edit it, and never add a version ≥ 3.13.0 (manifests cover those). Since Phase 63 `tagteam/data/templates/` and `tagteam/data/checklists/` are frozen too (pinned to `v3.14.1` by `test_retired_path_sources_are_frozen_at_the_tag`): the once-only seeds come from `tagteam/data/seeds/` — change a seed there, never in `templates/`. In `roadmap.py` a heading whose whole title is a bracketed placeholder (`### Phase 1: [Name]`, as seeded) is not a phase: skipped by `parse_roadmap()` and `validate_identities()`, reported as a `warn:` by `roadmap check`. `migrate.py` is the *older* legacy-config migration, not this.
- **`registry.py`** — tracks which projects ran `tagteam setup`, used by `tagteam upgrade` to run the same migration over every project after a `pip install -U`.

The handoff workflow itself is defined in `tagteam/data/.claude/skills/handoff/SKILL.md` (also installed at `.claude/skills/handoff/SKILL.md` in this repo). That file is the **contract** agents follow: status banner format, action commands, NEXT-COMMAND box, AMEND semantics. Changes to cycle states or CLI flags need to be reflected there too.

## Conventions worth knowing

- `tagteam/data/` is shipped as package data (see `[tool.setuptools.package-data]`). Adding new template/skill/checklist files requires the matching glob in `pyproject.toml` or they won't reach installed users.
- The CLI prints copious user-facing prose; treat it as part of the UX, not noise. `HANDOFF_EXPLAINER`, `GETTING_STARTED`, and the `_print_priming_box` boxed banner in `cli.py` are intentional.
- Since Phase 65 the full suite leaves the checkout clean: the wheel-build fixture in `tests/test_upgrade_smoke.py` builds from a copy of the tree and asserts it created no `build/` / `tagteam.egg-info/`. A manifest's top-level `tagteam` value is "the version that last changed this manifest" (`_same_manifest()` ignores it, like `written_at`), so a release that changes no framework file rewrites no project's manifest; provenance is per file.
- Tests are plain pytest, no fixtures package, no conftest tricks. New modules should get a sibling `tests/test_<module>.py`.
- Templates use simple `{variable}` substitution via `templates.py:render_template` — not Jinja.

## What lives in `docs/`

`docs/roadmap.md`, `docs/decision_log.md`, `docs/phases/`, `docs/handoffs/`, `docs/escalations/` are this repo's *own* tagteam working set — they document tagteam's development using tagteam. `docs/tagteam-2.0-proposal.md` is the active forward-looking design; `docs/handoff-cycle-issues-2026-04-24.md` enumerates known issues that drove recent fixes (project-root resolution, scope-diff baseline, AMEND action).
