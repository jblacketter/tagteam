# Project Roadmap

## Overview
Tagteam - A collaboration framework enabling structured, multi-phase AI-to-AI collaboration with human oversight.

**Tech Stack:** Python 3.10+, YAML configuration, Markdown templates, Textual (TUI)

**Workflow:** Lead / Reviewer with Human Arbiter

## Phases
<!-- Phases 67–74: the cockpit arc agreed with the arbiter on 2026-09-20. Goal: the cockpit replaces the three
iTerm2 tabs (lead | watcher | reviewer) — lead lane left, reviewer lane right, the watcher behind a turn bar at
the top. Each phase is engine + CLI first, cockpit surface second. tagteam stays a standalone package; superdash
(a separate project) consumes it (Phase 74). -->
### Phase 74: Versioned read API for other dashboards
- **Status:** Not started — arbiter decision 2026-09-20: tagteam stays here and is a package superdash uses; the cockpit is the first step towards that larger UI, not a part of it.
- **Description:** The hub and cockpit read endpoints (`/api/hub`, `/api/now`, `/api/roadmap`, `/api/watcher/events`, …) are consumed today only by tagteam's own pages, so nothing promises their shape. Declare the read surface another dashboard may rely on: an `api_version` in `/api/hub/info` and `/api/cockpit/info`, a documented list of stable endpoints and fields, and a test that pins them. No new data, no Aegis- or superdash-specific glue.
- **Depends on:** Phase 69

### Phase 73: Subagent kit
- **Status:** Not started — proposed in `docs/research/2026-09-14-better-tagteam/` (02, steps 3–4), never scheduled. Reopens the question Phase 57 deferred, from the need side.
- **Description:** Ship `plugin/agents/` with a small kit of cheap, tool-restricted helpers the lead delegates to — `test-runner` (focused runs, returns a digest instead of raw output), `verifier` (no write tools), `explore` — all run with `TAGTEAM_READ_ONLY=1`. The top model orchestrates and analyses; helpers absorb verbose output. Claude-side only (Codex has no equivalent; reviewer savings come from gate / panel / effort). Savings are measured with Phase 55's usage data before and after, in tokens and window state, never dollars.
- **Depends on:** Phase 72

### Phase 72: Jobs: background tasks and ci-watch
- **Status:** Not started
- **Description:** Deterministic first, cheap model second, top model last. A `tagteam job` concept: a recorded background task with a status, a log and a short result delivered to the lead — first job `ci-watch` (poll a GitHub Actions run / a PyPI version with `gh`, no model; on 2026-09-20 the lead polled five release runs by hand in the top model). A model is involved only to summarise a failure. The cockpit shows a Jobs strip.
- **Depends on:** Phase 68

### Phase 71: Cockpit Rules tab
- **Status:** Not started
- **Description:** A Rules tab that shows, in plain language, what governs this project's runs: the effective `tagteam.yaml` (gate, panel, briefer, resend minutes, round cap) and the standing orders of Phase 70. Rules the engine enforces and orders the agents are merely told are kept visually apart. Read-only first; then editing of a small safe set through the CLI with a dry-run diff (targeted line edits — a PyYAML rewrite would drop the file's comments). Presets with a recommended default, plus one free-text box.
- **Depends on:** Phase 70, Phase 68

### Phase 70: Standing orders
- **Status:** ✅ Approved — plan approved round 3, impl approved round 3 (2026-09-23); gate: 2,354 passed, 5 skipped at `c8e5640`. PR #56 open. See `docs/phases/standing-orders.md`.
- **Description:** The arbiter's run-level instructions live in chat today ("go to the end of all phases unless you have a question", "commit at phase end but hold the PR for my approval" / "open the PR when you are done"). Make them durable: per-project standing orders with a per-run override, delivered to both agents in every turn (headless prompt, `cycle rounds`), like an interjection that is not consumed. Two kinds, kept distinct: **enforced** — when the run stops for the arbiter (after each phase · at the end of the roadmap · only on questions), a switch over the existing single-phase / full-roadmap machinery; **advisory** — git and PR conduct, which tagteam delivers but cannot enforce (it runs no git). Engine + CLI only.

### Phase 69: Cockpit roadmap board
- **Status:** Not started
- **Description:** The cockpit shows only the next phase (the Start card). Add `GET /api/roadmap` over `parse_roadmap()` / `ready_phases()` / `roadmap check` and a Roadmap tab: Done · In progress · Up next, with Up next split into ready and blocked-by-dependency and `roadmap check` problems shown inline. Start lives on a ready phase; the separate Start card goes away. Tabs are regrouped by intent: Now · Roadmap · Rules · History (rounds + diff) · Usage.
- **Depends on:** Phase 68

### Phase 68c: Wait-child test waits for its child
- **Status:** ✅ Complete — impl approved round 1 (2026-09-21); gate: 2,275 passed, 5 skipped at `638558e`. PR #54 merged 2026-09-21 (rebase). See `docs/phases/wait-child-test-waits-for-its-child.md`.
- **Description:** Issue 11. `test_wait_child_terminates_and_reports_a_child_still_running` failed once in the 3.14.7 release-tree suite (load average ~48) and passed on the rerun. It gives a spawned interpreter 0.5 s and then expects its output in the failure dump; a starved interpreter has not printed yet. The child now signals — with a marker file written after its flush — that it has printed, and the clock starts then. Test only; no product code.

### Phase 68b: Cockpit lanes read as terminals
- **Status:** ✅ Complete — plan approved round 2, impl approved round 3 (2026-09-21); gate: 2,295 passed, 5 skipped at `15eefdf`. PR #55 merged 2026-09-21 (rebase). See `docs/phases/cockpit-lanes-read-as-terminals.md`. Split out of Phase 68 so each half is reviewable.
- **Description:** The lead lane is chat bubbles, the reviewer lane a card with a ~16-line log box: two looks, neither the terminal tab it replaces. Both lanes become one terminal-style stream per turn — the rendered turn log (`→ Bash: …` / `← …`), live for the running turn, newest turn open and earlier ones collapsed — scoped to the current cycle in **both** lanes (the lead lane has no cycle filter today, so it shows Phase 52 cards under the current cycle's header: the open backlog item). Gate and panel runs leave the reviewer's lane for a strip between the lanes, so a `BOUNCED` no longer reads as the reviewer's. The lead lane keeps the only composer; a chat turn renders in the same stream style. Also found while planning: the Needs-you "process disappeared — Cancel turn" card and the lanes' `gone` state still test `pid_alive === false`, which is true for the whole of every pre-check and the start of every turn — they move to `inflight.liveness` (Phase 68), and the lanes and Needs-you take their story from `headline` so a stalled watcher is not contradicted on the same page.
- **Depends on:** Phase 68

### Phase 68: Cockpit turn bar and watcher drawer
- **Status:** ✅ Complete — plan approved round 2, impl approved round 3 (2026-09-21); gate: 2,275 passed, 5 skipped at `1132e1f`. PR #53 merged 2026-09-21 (rebase). See `docs/phases/cockpit-turn-bar-and-watcher-drawer.md`.
- **Description:** The Now strip is up to seven pills of equal weight; `watcher: on` has nothing behind it, and the heartbeat and event history that Phase 67 put in `/api/now` and `/api/watcher/events` are read by nothing. One dominant sentence says who has the ball ("codex is reviewing · round 2 · 1m02s", "Waiting on you", "Stalled: codex is owed a turn — the watcher last looked 6m ago", "Waiting on codex — the watcher is off"), derived server-side so the CLI and other dashboards can say the same thing; selecting it opens the watcher drawer: what the watcher is, when it last looked, and what it did. When the cockpit cannot run turns itself it says why.
- **Depends on:** Phase 68a

### Phase 68a: Headless watcher hands an approved plan to the lead
- **Status:** ✅ Complete — plan approved round 1, impl approved round 1 (2026-09-20); gate: 2,219 passed, 5 skipped at `b01183f`. PR #52 merged 2026-09-21 (rebase). See `docs/phases/headless-watcher-hands-an-approved-plan-to-the-lead.md`.
- **Description:** Found by running a real cycle through the cockpit (2026-09-20, scratch project, headless): when a plan is approved the watcher logs `Sending completion notice to claude...` and, in headless mode, sends nothing — `_handle_done` has branches for tab, tmux and notify modes only. In iTerm2 that notice is what makes the lead implement; headless, the loop stops and the cockpit waits for a Start click. In single-phase mode a headless watcher now does what full-roadmap mode already does at that point: it makes the `start <phase> impl` turn the lead's owed turn, which the engine already knows how to run and verify. Also fixes a Phase 67 defect the same trial exposed: the heartbeat is written only before a tick, so `watch status` reads STALE for up to one poll interval after every long turn.
- **Depends on:** Phase 67

### Phase 67: Watcher event log and heartbeat
- **Status:** ✅ Complete — plan approved round 2, impl approved round 4 (2026-09-20); gate: 2,202 passed, 5 skipped at `b0ee363`. PR #51 merged 2026-09-20 (rebase). After the merge the arbiter restarted the iTerm2 watcher: `tagteam watch status` shows it running with a fresh heartbeat and `watch log` holds its start-up lines; the remaining manual check — a real dispatched round compared against the tab — happens on the next cycle. Released in 3.14.5. See `docs/phases/watcher-event-log-and-heartbeat.md`.
- **Description:** The watcher narrates everything it does (116 `_log()` calls: whose turn, sent / failed, paused, resumed, watchdog re-send, gate, panel, done) to its own stdout and nowhere else — visible in an iTerm2 tab, invisible to the cockpit and lost when the tab closes. Nothing records when it last looked at the state. Add a bounded per-project event log and a heartbeat, written by every watcher mode, with `tagteam watch status` / `tagteam watch log` and two read endpoints. No UI.

### Phase 66: Roadmap dependency problems name their line
- **Status:** ✅ Complete — plan approved round 1, impl approved round 1 (2026-09-20); gate: 2,148 passed, 5 skipped at `6295abf`. PR #50 merged 2026-09-20. See `docs/phases/roadmap-dependency-problems-name-their-line.md`.
- **Description:** `roadmap check` reports `unknown dependency '…'` and `depends on itself` without a location; on 2026-09-20 two projects (bugalizer, designwing) each needed a grep to find one bad `Depends on:` line. `RoadmapPhase` gains `line` and `dep_lines`, `parse_roadmap()` fills them, and `validate_graph()` appends `(line N)` when the line is known — hand-built phases keep today's exact strings. Identity and cycle problems are unchanged.
- **Depends on:** Phase 63

### Phase 65: Hardening: shadow installs, quiet upgrades, clean test runs
- **Status:** ✅ Complete — plan approved round 1, impl approved round 2 (2026-09-20); gate: 2,142 passed, 5 skipped at `df0db7f`. PR #49 merged 2026-09-20. See `docs/phases/hardening-shadow-installs-quiet-upgrades-clean-test-runs.md`.
- **Description:** Four small fixes from the 2026-09-20 issues log. (1) `doctor` and `tagteam state` report a tagteam installed in the project's own `.venv` / `venv` whose version differs from the running one — six such copies had gone unnoticed and one pre-3.14 copy can still re-create the retired `templates/`. (2) `_same_manifest()` ignores the top-level version stamp, so a release that changes no framework file no longer rewrites the manifest in every project. (3) The `wheel_venv` test fixture builds from a copy, so the suite stops leaving `build/` and `tagteam.egg-info/` in the checkout. (4) The watcher-lock tests print the spawned watcher's output when a wait times out, so the flake seen once in a gate run can explain itself next time.
- **Depends on:** Phase 64

### Phase 64: Version from the source tree
- **Status:** ✅ Complete — plan approved round 1, impl approved round 3 (2026-09-20); gate: 2,112 passed, 5 skipped at `57088cf`. Branch `phase/version-from-the-source-tree`, rebased onto `main` after PR #47 merged (tree byte-identical to the gated `57088cf`); PR #48 merged 2026-09-20. See `docs/phases/version-from-the-source-tree.md`.
- **Description:** `tagteam.__version__` comes from `importlib.metadata`, which an editable install writes once and never updates: on 2026-09-20 the arbiter's CLI ran 3.14.0 code while reporting 3.13.0 (a sweep would have stamped 41 manifests wrongly), this repo's `.venv` reported 3.12.0 (two environmental `upgrade_smoke` failures, twice misdiagnosed), rankr's link reported 0.5.0. `__version__` now prefers the `pyproject.toml` beside the package when it declares `name = "tagteam"` — true only for a source tree — and falls back to metadata exactly as before for every wheel install. Adds `tagteam --version` (version + the directory it was imported from).
- **Depends on:** Phase 63

### Phase 63: Roadmap placeholders are not phases
- **Status:** ✅ Complete — plan approved round 3, impl approved round 1 (2026-09-20); gate: 2,074 passed, 5 skipped at `c35e793`. PR #47 merged 2026-09-20. See `docs/phases/roadmap-placeholders-are-not-phases.md`.
- **Description:** The roadmap `tagteam setup` seeds fails `tagteam roadmap check` (`duplicate slug 'name'`, `name: depends on itself`): its three `### Phase N: [Name]` headings share one slug. 9 of 41 registered projects still carry it. A heading whose whole title is a bracketed placeholder stops being a phase — skipped by the parser and identity validation, reported on `roadmap check`'s warning channel, and a placeholder-only roadmap is `ok: 0 phase(s)` — so existing projects become valid unedited. Seeds move to `data/seeds/` so that `data/templates/` (Phase 61/62 retired-path provenance) can be frozen and pinned; the seed's dead `/phase` / `/plan` / `/status` commands are replaced and the shipped-docs audit learns them.
- **Depends on:** Phase 62

### Phase 62: Framework provenance from earlier releases
- **Status:** ✅ Complete — plan approved round 1, impl approved round 2 (2026-09-20; round 1 was a gate bounce); gate: 2,058 passed, 5 skipped at `386261b`. PR #46 merged 2026-09-20. Release 3.14.1 and the registered-project sweep follow. See `docs/phases/framework-provenance-from-earlier-releases.md`.
- **Description:** Phase 61's retirement proves too little on real projects: `tagteam upgrade --preview` over the 41 registered projects (3.14.0) retires 184 files and keeps 296 templates plus 36 `docs/workflows.md` as "custom" — yet every one is a byte-exact copy of an earlier release's file (templates: the v0.3.0–v3.11.0 source rendered with the project's agent names, which `setup` baked in until 3.12.0; workflows: verbatim v3.10.0 / v3.11.0). Ship those 13 earlier sources (~31 KB, a closed set) as `tagteam/data/history/<tag>/…` and let `_classify()` match them verbatim or rendered for the configured / swapped names → `framework`, reconstructible: retired paths are removed, `docs/workflows.md` is refreshed. Also closes the Phase 61 review note: `_prune_retired_dirs()` reports a directory it could not remove.
- **Depends on:** Phase 61

### Phase 61: Framework files: retire what nothing reads
- **Status:** ✅ Complete — plan approved round 2, impl approved round 1 (2026-09-20); gate: 2,044 passed, 5 skipped at `ca36dc7`. PR #45 merged 2026-09-20. **On release:** the release body must note that `setup`/`upgrade` stop installing `templates/` and `docs/checklists/` and remove unmodified copies; then run the registered-project sweep. See `docs/phases/framework-files-retire-what-nothing-reads.md`.
- **Description:** `tagteam setup` installs 13 framework files; nothing reads 12 of them (`templates/*.md`, `docs/checklists/*.md` — the latter only by `bench`, already guarded), and deleting them does not stick because `absent → create` restores them on the next `setup`/`upgrade`. Reported from the `linkedin-articles` project on 3.13.0 (`docs/tagteam-issue-framework-files-no-opt-out-2026-09-20.md`). The managed set shrinks to `docs/workflows.md` (+ the vendored skill); the 12 paths become *retired*: a copy whose bytes tagteam provably wrote is removed, a modified copy is kept and reported with its `--accept` line, emptied directories are `rmdir`'d (never recursive). Bench falls back to the package checklist; `needs_setup()` stops keying on the retired directories. No `framework.skip` key. Docs updated to match.
- **Depends on:** Phase 52

### Phase 60: Roadmap parser: decimals, deployed, no silent drops
- **Status:** ✅ Complete — plan approved round 1 (2026-09-15); impl approved round 1 (2026-09-16); gate: 2,030 passed, 5 skipped at `3f23845`. Branch `phase/roadmap-parser-decimals-deployed-no-silent-drops`; PR merge pending. **On release:** the release body must note that decimal-numbered phases previously omitted can now enter `roadmap ready`/`queue`. See `docs/phases/roadmap-parser-decimals-deployed-no-silent-drops.md`.
- **Description:** Three fixes in `tagteam/roadmap.py`, from defects found running tagteam against Liminal. (1) `### Phase 9.1:` matches neither `_PHASE_HEADING_RE` nor `_PHASE_HEADING_LENIENT_RE`, so a decimal-numbered phase is invisible to parsing *and* to identity validation — Liminal has 28 headings, parses 26, silent for ~2 months. Direct sequel to Phase 59, which fixed letters and ruled dotted suffixes out as "speculation". (2) `_TERMINAL_STATUS_WORDS` lacks `deployed`, so a deployed phase never leaves `roadmap ready`. (3) The class defect: a heading that looks like a phase and fails to parse is dropped silently — new non-fatal `warn:` channel in `roadmap check` (separate from `problems`, which are fatal via `check_graph`) so the next unrecognized shape is loud instead. Ships as a plain bug fix; newly-visible phases are a release-note behavior change. See `docs/phases/roadmap-parser-decimals-deployed-no-silent-drops.md`.
- **Depends on:** Phase 59

### Phase 59: Roadmap phase-number suffixes
- **Status:** ✅ Complete — plan approved round 1 (2026-09-15); impl gate: 2,006 passed, 5 skipped at `9a9b0cc`; impl review skipped, closed by arbiter decision and merged at `8e12e8b`. See `docs/phases/roadmap-phase-number-suffixes.md`.
- **Description:** `### Phase 58a:` never matches `_PHASE_HEADING_RE` (`(\d+):` rejects the `a`), so a suffixed phase is not parsed at all — this repo's roadmap has 59 headings and parses 58, and bugalizer's valid `- **Depends on:** Phase 5b` fails as an unknown dependency. Carry an optional single-letter suffix through heading parsing, reference resolution and duplicate detection, keyed as `(number, suffix)` so `Phase 5` and `Phase 5b` stay distinct and neither is reported as a duplicate of the other. Parser only; no renumbering, no ordering change. See `docs/phases/roadmap-phase-number-suffixes.md`.

### Phase 58a: Watcher SIGTERM test in event mode
- **Status:** ✅ Complete — impl approved round 1 (2026-09-15); gate: 1,995 passed, 5 skipped at `c75d52f`; PR #42 CI (with watchdog): 2,007 passed, 4 skipped. See `docs/phases/watcher-sigterm-test-event-mode.md`.
- **Description:** CI (with `watchdog`) failed a Phase 58 test that asserted the poll loop's `Watcher stopped.` log line; the event loop exits cleanly without it. Assert the clean exit (code, pidfile, lock) instead.
- **Depends on:** Phase 58

### Phase 58: Cockpit session lifecycle
- **Status:** ✅ Complete — plan approved round 3, impl approved round 1 (2026-09-15); gate: 1,995 passed, 5 skipped at `97309f7`. Branch `phase/cockpit-session-lifecycle`; PR merge pending. See `docs/phases/cockpit-session-lifecycle.md`.
- **Description:** One watcher per project (a lifetime per-user lock in `tagteam watch`, refusal naming the running pid); SIGTERM stops a watcher cleanly (pidfile removed, in-flight turn killed via the existing interrupt path); Ctrl+C on `tagteam serve` stops the watchers that cockpit started and leaves others alone; the reviewer lane shows only the current cycle, blank by default, with a "Show last session" toggle, and verdict chips keyed by cycle. From the 2026-09-15 backlog entries below.

### Phase 57: Model policy by activity kind
- **Status:** Deferred (2026-09-15) — waits on a concrete need, not on Phase 56. Arbiter decision after reviewing the bench's cost: the 6×2 bench run (~13M input tokens, proxy estimate) was not run. Reopen when one of these is true: (1) the reviewer role moves to Claude, (2) panel lenses run on Claude, or (3) headless turns are used routinely enough that model choice costs real window (recent phases recorded no usage rows: turns ran interactively). At that point, run `tagteam bench` on rounds with exact snapshots first, and let its table decide the policy.
- **Description:** A `models:` table in `tagteam.yaml` keyed by activity kind (`lead.plan`, `lead.impl`, `reviewer.review`, `panel.<lens>`, `briefer`), every entry optional, merged into the role's validated headless args at spawn; `tagteam state` prints the resolved policy. Source: `docs/research/2026-09-14-better-tagteam/02-agents-and-models.html` step 3.
- **Depends on:** Phase 56

### Phase 56: Review bench
- **Status:** ✅ Complete — plan approved round 2, impl approved round 2 (2026-09-14); gate: 1,976 passed, 5 skipped at `211a1b3`. PR #40 merged 2026-09-15. The first bench run is deferred with Phase 57; snapshots accumulate from every submission meanwhile. See `docs/phases/review-bench.md`.
- **Description:** Replay recorded reviewer rounds at reviewer cells (provider × model × effort) in isolated replay repositories under a verdict-file contract that never writes the cycle; tabulate verdict agreement with the recorded reviewer, tokens and seconds. Adds exact round snapshots (a git tree pinned under `refs/tagteam/snapshots/` at every lead submission, no model). Dry-run by default, turn cap, resumable. Claude cells only in v1; no manual labelling (waits for `tagteam grade`); planted-defect control is a follow-up. Source: `docs/research/2026-09-14-better-tagteam/`.
- **Depends on:** Phase 55

### Phase 54: Role Neutrality Polish
- **Status:** ✅ Complete — plan approved round 1, impl approved round 2 (2026-09-09); gate: 1,789 passed, 5 skipped at `8d854b3`. Separate cleanup PR pending merge; version remains 3.12.0, unpublished. See `docs/phases/role-neutrality-polish.md`.
- **Depends on:** Phase 51

### Phase 51: Role Neutrality
- **Status:** ✅ Complete — plan approved round 3, impl approved round 2 (2026-09-09); gate: 1,785 passed, 5 skipped at `06a9eda`. PR #33 merged; **3.12.0** tagging pending arbiter decision. See `docs/phases/role-neutrality.md`.
- **Depends on:** Phase 50
- **Description:** Provider-independent readiness, shared onboarding, neutral instructions and participant guards at cycle writes/watcher dispatch. First upstream stage; downstream migration waits for Phase 52.

### Phase 52: Safe framework migration
- **Status:** ✅ Complete — plan approved round 2 (2026-09-14), impl approved round 5 (2026-09-15); gate: 1,841 passed, 5 skipped at `15b15bd`. Branch `phase/safe-framework-migration`; PR merge pending. See `docs/phases/safe-framework-migration.md`.
- **Description:** Manifest/provenance-aware setup and upgrade through one migration engine: per-project `--preview`, refresh only content provably written by tagteam, keep and report everything else (`--accept PATH` per path), preimage checks, git-based recovery with dirty/non-git refusal unless `--force`, idempotent retry, wheel-installed smoke coverage. No historical hash-mining, no backup subsystem.
- **Depends on:** Phase 51

### Phase 53: Legacy diagnostics and capability visibility
- **Status:** ✅ Complete — plan approved round 2, impl approved round 2 (2026-09-15); gate: 1,894 passed, 5 skipped at `1d3a35f`. Branch `phase/legacy-diagnostics`; PR merge pending. See `docs/phases/legacy-diagnostics.md`.
- **Description:** Report-only workflow artifact diagnostics and bounded context/capability visibility: a read-only `tagteam doctor` (recognized legacy skills/instructions with evidence and remediation; per-role executables, contract entry points, instruction sources, tool configuration names, protection notes) plus a one-line pointer from `setup`/`upgrade`. Nothing is edited, deleted, probed or read from secrets.
- **Depends on:** Phase 52

### Phase 55: Measure the loop
- **Status:** ✅ Complete — plan approved round 3, impl approved round 3 (2026-09-14); gate: 1,938 passed, 5 skipped at `a43b370`. Branch `phase/measure-the-loop`; PR merge pending. See `docs/phases/measure-the-loop.md`.
- **Description:** Read-only measurement from data tagteam already stores: `tagteam report --phase P` (rounds, change requests, gate bounces and minutes, per-role elapsed turn time, start-to-approve, tokens where headless turns recorded them), `tagteam usage --by model|kind`, per-model token capture from Claude `modelUsage` (schema v10), and no dollar figures in any human-facing view. Source: `docs/research/2026-09-14-better-tagteam/`. `tagteam grade` is a separate later phase; no reading of user-level transcripts.

### Phase 50: Read-only Mode
- **Status:** ✅ Complete — impl approved round 4 (2026-09-04; plan approved round 3); PR #32 merged; released as **3.11.0**. See `docs/phases/read-only-mode.md`
- **Description:** The one-cycle-writing-call rule is enforced by prose, not by the CLI: panel lenses get a `TAGTEAM_PANEL_LENS` env var nobody reads (the panel only *detects* a stray write after the fact), and the `codex-brief` / verifier agents have Bash and honor-system "never write" rules. Add `TAGTEAM_READ_ONLY=1`, enforced at the two write chokepoints (`dualwrite.writer_lock` and the `db` writer functions) before anything touches disk, surfaced by the CLI as one refusal line with exit 2. Panel lens children get it; headless turns do not. Contract gains a "read-only helpers" paragraph. Prerequisite for ever shipping reviewer agents in the plugin (deferred from Phase 48).
- **Depends on:** Phase 39

### Phase 49: Legacy Skill Drift
- **Status:** ✅ Complete — plan approved round 3, impl approved round 3 (2026-08-29); PR #29 merged; released as **3.10.1** (tag `v3.10.1`, PyPI 2026-08-29). See `docs/phases/legacy-skill-drift.md`
- **Description:** After Phase 48, superseded pre-plugin artifacts still describe the old contract: ten user-level `~/.claude/skills/handoff-*` skills competed with `tagteam:handoff` in every project (removed by hand on the arbiter's machine), the shipped `data/workflows.md` documents the dead `/handoff-*` command family into every project, and `server.py` still emits `/handoff-cycle`. Retire the dead family from what tagteam ships; **detect and report** superseded user-level skills from `setup`/`upgrade` rather than delete them (a project tool never writes to `~/.claude/` — provenance rule from Phase 48). Ruled: detect-and-report only; name matches are *candidates* (no provenance), reported by resolved path with no shell command; `upgrade` reports once per run.
- **Depends on:** Phase 48

### Phase 48: Plugin Distribution
- **Status:** ✅ Complete — plan approved round 3, impl approved round 5 (2026-08-29); PR #28 merged; released as **3.10.0** (tag `v3.10.0`, PyPI 2026-08-29). See `docs/phases/plugin-distribution.md`
- **Description:** `tagteam setup` vendors the handoff contract into every project, and the copies have forked — 58 on the arbiter's machine, 6 live projects running a 155-line contract with no mention of the gatekeeper, the one-run rule, AMEND, interjections, GATE_BOUNCE or the panel. Ship the Claude-facing contract as an installable plugin (skill + SessionStart hook), keep the engine in the package, and change `setup` from vendoring to removing the vendored copy once the plugin is present.
- **Key Deliverables:**
  - `plugin/` tree (`.claude-plugin/plugin.json`, `skills/handoff/SKILL.md`, `hooks/hooks.json`) + repo-root `marketplace.json`; plugin and packaged contract copies pinned byte-identical by test; public command **`/tagteam:handoff`** (arbiter, impl r2), `/handoff` where vendored, `tagteam contract` for agents without plugins (Codex)
  - `headless.py` contract resolution: explicit → project-local → packaged; prompt header names the source
  - `setup.py` fail-closed plugin detection (installed-and-enabled for *this* project, via `claude plugin list --json`) + hash-gated remove path (content provenance from git history, never pathname)
  - Plugin-aware `needs_setup()` (local skill *or* installed-and-enabled plugin) so `session start --launch` and `worktree` seeding don't rerun or roll back on a migrated project
  - `tagteam hook session-start`: cycle banner + version-skew warning, silent exit 0 on every failure — the hook body and the skew emitter
  - `scripts/release.py` bumps `plugin.json`, regenerates the vendored-hash file, refuses on contract-copy mismatch; publish guard extended
- **Seam:** if only Claude reads it → plugin; if the CLI reads it or a human edits it after seeding → package. The contract is read by both, so it exists in both, pinned identical.
- **Decided:** plugin ships from this repo (lockstep versioning with `pyproject.toml`)
- **Ruled in plan review (r1):** v1 = B; migrate `bugalizer`, `northstar/ns-wip-tests`, `harmonic`; skip three archived snapshots; `archive/handoff-test` gets a contract-version note (no fixture consumes it)
- **Deferred:** `codex-brief` / reviewer agents (own phase)

### Phase 47: Reviewer Context Parity
- **Status:** ✅ Complete — impl approved round 2 (2026-08-29; no plan cycle by the arbiter's instruction — implementation reviewed directly; r1 fix: non-UTF-8 context file degrades to None). Merged 2026-08-29 via PR #27; released as **3.9.0** (tag `v3.9.0`, PyPI 2026-08-29).
- **Description:** The headless turn prompt carried the contract, the state and the round tail — and nothing about the project under review. Adds a provider-aware `PROJECT CONTEXT` block (the context file the provider's own CLI does *not* auto-load) and, for impl cycles, a `CHANGE SURFACE` block built from the existing `compute_scope_diff`, so the reviewer is handed the baseline sha and the attributable path list instead of guessing which diff to read.
- **Key Deliverables:**
  - `select_context_file` / `read_project_context` / `render_project_context` in `headless.py`
  - `collect_change_surface` / `render_change_surface` reusing `cycle.compute_scope_diff`
  - Two optional kwargs on `compose_prompt`; both blocks degrade to absent, never fail a turn
  - 19 tests in `tests/test_headless.py`
- See `docs/phases/reviewer-context-parity.md`

### Phase 1: Configurable Agents Init
- **Status:** Complete
- **Description:** Create interactive init command for configuring AI agents and their roles
- **Key Deliverables:**
  - Interactive `python -m tagteam init` command
  - `tagteam.yaml` config file generation
  - Skills updated to read config at runtime
  - Getting started documentation

### Phase 2: Review Cycle Automation
- **Status:** Complete
- **Description:** Automate the back-and-forth review process with a single cycle document
- **Key Deliverables:**
  - `/handoff-cycle` skill for automated review cycles
  - Single cycle document format with status tracking
  - Auto-escalation after 5 rounds
  - Human input pause/resume capability

### Phase 3: Automated Agent Orchestration
- **Status:** Complete
- **Description:** File-based state machine and watcher daemon for automated turn-taking between agents
- **Key Deliverables:**
  - `handoff-state.json` state file with atomic read/write
  - `python -m tagteam watch` watcher daemon (notify + tmux modes)
  - `python -m tagteam state` CLI for viewing/updating state
  - `python -m tagteam session` tmux session management
  - `/handoff-cycle` skill updated with state file integration

### Phase 4: TUI Consolidation
- **Status:** Complete
- **Description:** Consolidate the gamerfy TUI into tagteam as a subpackage. Adds `python -m tagteam tui` command with `--dir` flag, first-time user setup via TUI dialogue, and sound effects.
- **Key Deliverables:**
  - `tagteam/tui/` subpackage with ASCII saloon scene, dialogue system, map widget
  - `python -m tagteam tui [--dir PATH]` CLI subcommand
  - First-time user flow with project scaffolding via TUI
  - `pip install tagteam[tui]` optional dependency
  - Sound effects bundled in package

### Phase 5: Template Variable Substitution
- **Status:** Complete
- **Description:** Templates automatically use configured agent names
- **Key Deliverables:**
  - `tagteam/templates.py` module with `render_template()` and `get_template_variables()`
  - Variable substitution in 8 templates (`{{lead}}`, `{{reviewer}}`)
  - `setup.py` reads config and substitutes variables when copying templates
  - Generated docs reflect config when `setup` runs after `init`

### Phase 6: Migration & Advanced Features
- **Status:** Complete
- **Description:** Migration tooling for legacy projects, centralized config parsing with validation
- **Key Deliverables:**
  - `python -m tagteam migrate` command with auto-detection and backups
  - Centralized `tagteam/config.py` module (read, validate, get_agent_names)
  - Forward-compatible `model_patterns` schema field with overlap validation
  - Unit tests for config and migration modules

### Phase 7: Unified Command (Command Drift Fix)
- **Status:** Complete
- **Description:** Replace 10 skill files (~30+ subcommands) with a single `/handoff` command that auto-detects role and state. Fixes agent drift in long context windows.
- **Key Deliverables:**
  - Single `/handoff` skill file (<150 lines)
  - State-driven auto-dispatch (reads role + state, does the right thing)
  - Mandatory NEXT COMMAND output box on every response
  - Deprecation notices on old skill files
  - 3 commands total: `/handoff`, `/handoff start [phase]`, `/handoff status`

### Phase 8: Orchestration Fix
- **Status:** Complete
- **Description:** Fix the watcher daemon and tmux send-keys integration so agents automatically pick up tasks when it's their turn
- **Key Deliverables:**
  - `_log()` helper with `flush=True` for visible watcher output in tmux panes
  - Escape x3 + C-c input clearing (C-u doesn't work in TUI agents)
  - C-m submit instead of Enter (reliable across Claude Code and Codex)
  - Agent idle detection via `capture-pane` (waits for prompt before sending)
  - Universal text command instead of `/handoff` for cross-agent compatibility
  - Directory-based skill format (`handoff/SKILL.md` with YAML frontmatter)
  - `setup.py` copies directory-based skills alongside flat `.md` files
  - `session.py` creates 3-column layout with mouse mode and pane labels
  - `--dir` flag for `session start` to set working directory

### Phase 9: Dashboard & TUI Polish
- **Status:** Complete
- **Description:** Fix TUI bugs (GAMERFY naming, silent poll failures, status bar overflow), extract shared parser for TUI and web dashboard, improve web dashboard with escalation choices, phase map, and structured round display, split 46KB HTML into 3 files, add unit test coverage
- **Key Deliverables:**
  - Shared `tagteam/parser.py` used by both TUI and web dashboard
  - `GAMERFY_SOUND` → `HANDOFF_SOUND` rename with backward-compat fallback
  - TUI state poller: failure logging + `[STALE]` indicator
  - Status bar: action truncation (25 chars), `Round N` display (no `/5`)
  - Web dashboard split: `index.html` + `styles.css` + `app.js`
  - Web dashboard: escalation choice buttons, phase map, structured rounds
  - 39 new unit tests across 3 test files (parser, state_watcher, review_dialogue)

### Phase 10: Web Dashboard Redesign
- **Status:** Complete
- **Description:** Redesign from ASCII art to pixel art sprites with modern responsive layout, RPG dialogue system with typewriter effects, and JavaScript conversation engine
- **Key Deliverables:**
  - SVG pixel art sprites (Mayor, Rabbit, Clock, Saloon backdrop) via `sprites.js`
  - JavaScript dialogue engine with typewriter effect, portraits, conversation trees
  - Full-width banner + responsive card grid layout
  - CSS animations: pendulum swing, cuckoo pop-out, mayor glow pulse
  - Character reactions to state changes (color shifts)

### Phase 11: The Saloon — Interactive Character-Driven Setup & Monitoring
- **Status:** Complete
- **Description:** Transform the dashboard into a full saloon experience with three independently clickable characters (Mayor, Bartender, Watcher). First-time setup becomes a guided multi-character conversation. The new Watcher character provides agent monitoring and daemon control.
- **Key Deliverables:**
  - New Watcher character (pixel art sprite + portrait + dialogue)
  - All three characters independently clickable with domain-specific menus
  - Guided multi-character setup flow (Mayor → Bartender → Watcher)
  - Character glow system to guide users between characters
  - Watcher monitoring API (daemon status, tmux session control, log tails)
  - Setup state persistence (resume mid-flow)
- **Phase Plan:** `docs/phases/saloon-interactive-setup.md`

### Phase 12: Cycle Storage & CLI (Performance)
- **Status:** Complete
- **Description:** Replace markdown-based cycle documents with append-only JSONL rounds + JSON status files, updated via CLI commands. Eliminates repeated read/modify/write of markdown from the active handoff loop.
- **Key Deliverables:**
  - `tagteam/cycle.py` module (JSONL/JSON storage, CLI commands, centralized discovery)
  - CLI commands: `cycle init`, `cycle add`, `cycle status`, `cycle rounds`, `cycle render`
  - Format dispatcher in `parser.py` (JSONL first, legacy markdown fallback)
  - All consumers updated: `server.py`, `app.js`, `handoff_reader.py`, `review_replay.py`
  - SKILL.md updated to use CLI commands instead of manual file edits
  - Synthesized markdown view via `cycle render` for human readability
  - 56 unit tests across `test_cycle.py` and `test_parser.py`
- **Phase Plan:** `docs/phases/cycle-storage-cli.md`

### Phase 13: Unified State Command (Performance)
- **Status:** Complete
- **Description:** Merge `cycle add` and `state set` into a single command via `--updated-by` flag, cutting agent tool calls per handoff turn from 2 to 1.
- **Key Deliverables:**
  - `--updated-by` flag on `cycle init` and `cycle add` — auto-updates `handoff-state.json`
  - `_STATE_TRANSITIONS` table and `_update_handoff_state()` helper in `cycle.py`
  - Round-5 auto-escalation preserved in unified command path
  - SKILL.md updated to single-command flow for all agent actions
  - Regression test for round-5 escalation behavior

### Phase 14: Sharing Readiness
- **Status:** Complete
- **Description:** Make tagteam ready for wider sharing — simplify README (remove manual setup, promote automated), mark Saloon as WIP, improve watcher robustness (seq-based change detection, re-send watchdog, retry loop), add `session start --launch` for auto-starting agents
- **Key Deliverables:**
  - README restructured: single automated workflow, one-line manual mention, Saloon marked WIP
  - `config.py`: `get_launch_commands()` helper, `command` field validation, no-PyYAML fallback
  - `session.py` / `iterm.py`: `--launch` flag auto-starts agents and watcher
  - `watcher.py`: seq-based change detection, 5-min re-send watchdog for stuck `ready` states
  - Tests for new config helpers and launch behavior
- **Phase Plan:** `docs/phases/sharing-readiness.md`

### Phase 15: Onboarding Polish
- **Status:** Complete
- **Description:** Simplify the first-run experience by unifying `init` + `setup` + `session start --launch` into a single streamlined flow. Reduce the number of commands a new user needs to go from install to running their first handoff.
- **Key Deliverables:**
  - `quickstart` command: setup + init + session in one command
  - `ensure_session()` with idempotent behavior (tmux auto-attach, iTerm skip)
  - `needs_setup()` 3-point check, `run_init()` with TTY guard
  - README, HELP_TEXT, GETTING_STARTED all updated
  - 21 new tests
- **Phase Plan:** `docs/phases/onboarding-polish.md`

### Phase 16: Stale Handoff Diagnostics
- **Status:** Complete
- **Description:** Build structured logging and diagnostics for debugging stale handoffs.
- **Key Deliverables:**
  - `state diagnose` command with 7 diagnostic checks
  - Enriched history entries (phase, round, updated_by)
  - Seq mismatch side-channel logging (`handoff-diagnostics.jsonl`)
  - `--check-agents` for agent responsiveness via session discovery
  - History anomaly detection (oscillation, repeated escalations)
  - 19 new tests
- **Phase Plan:** `docs/phases/stale-handoff-diagnostics.md`

### Phase 17: Test Coverage & Isolation
- **Status:** Complete
- **Description:** Fix pre-existing test failures, add TUI test isolation.
- **Key Deliverables:**
  - Fixed `detect_agent_names()` — sorted glob, found-flags prevent overwrite
  - TUI tests skip gracefully via `pytest.importorskip("textual")`
  - Added `[tool.pytest.ini_options]` to pyproject.toml
  - Full suite: 228 passed, 3 skipped, 0 failed
- **Phase Plan:** `docs/phases/test-coverage-isolation.md`

### Phase 18: Saloon Production Ready
- **Status:** Complete
- **Description:** Polish the web dashboard to production quality.
- **Key Deliverables:**
  - User-visible error banner for failed API calls (app.js)
  - Exponential backoff polling (2s → 30s max, auto-recovery)
  - State POST validation — rejects unknown fields (server.py)
  - WIP banner removed from README
- **Phase Plan:** `docs/phases/saloon-production-ready.md`

### Phase 19: Public Onboarding
- **Status:** Complete
- **Description:** Make tagteam ready to share with the world. Simpler init prompts, shared handoff explainer in CLI + README, iTerm2 cold-launch fix, README trimmed to a single backend-neutral Quick Start using the `tagteam` console script, prominent post-quickstart priming box.
- **Key Deliverables:**
  - `init` prompts simplified to 2 questions (lead name, reviewer name) — no role prompt
  - `HANDOFF_EXPLAINER` printed once per quickstart path via `show_explainer` plumbing
  - `_ensure_iterm_running()` launches iTerm2 from a fully-quit state; window-count guard prevents duplicate windows
  - README rewritten with "How it works" section and backend-neutral Quick Start
  - `python -m tagteam` replaced with `tagteam` throughout docs
  - Backend-aware priming box (tab/pane/terminal) at end of quickstart
- **Phase Plan:** `docs/phases/public-onboarding.md`

### Phase 20: Tail-only reads (token efficiency) — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Track `last_round_seen` per agent so `tagteam cycle rounds` returns only new rounds since last read. With SQLite this is `WHERE round > ?` — trivially supported by the Phase 28 schema, no separate phase needed.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase A

### Phase 21: Round summary field (token efficiency) — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Writer emits a short `summary` on every round. Already present as a nullable column in the Phase 28 schema (`rounds.summary`).
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase B

### Phase 22: Structured round schema — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Native columns in the Phase 28 `rounds` table replace JSON-in-JSON. Decision/blockers/unresolved_threads/resolved should be added as columns when the production port lands.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase C

### Phase 23: Per-round files (optional, defer if 20–22 suffice)
- **Status:** Deferred (2026-05-03) — superseded by `--tail N` follow-up. See `docs/phases/per-round-files-experiment-findings.md` for the experiment writeup.
- **Description:** Original scope was to split each round into its own file. Token experiment on the rankr corpus (cl100k_base) showed savings come entirely from query shape, not storage shape — Phase 28's SQLite store already covers the storage side. Recommended follow-up: a `tagteam cycle rounds --tail N` flag (small CLI change). Per-round file splitting itself is not worth the complexity.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase D

### Phase 24: Event-driven watcher (optional polish)
- **Status:** Complete (2026-05-03) — shipped via `polish-pack-watcher-tokens-adopt`.
- **What shipped:**
  - `_StateProcessor` extracted from `watch()` so polling and event triggers share one processing path.
  - `tagteam/watcher_events.py` wrapping `watchdog.observers.Observer`, subscribed to `on_modified` + `on_created` + `on_moved` (atomic-rename of `.handoff-state.tmp` → `handoff-state.json` surfaces as a move on most backends).
  - 30s heartbeat as safety net for missed events on broken filesystems / NFS.
  - Runtime-failure fallback: if the observer raises (e.g. macOS FSEvents `SystemError: Cannot start fsevents stream`, or inotify `OSError(ENOSPC)`), the watcher logs the reason and drops back to poll mode instead of exiting.
  - `--poll` flag forces legacy polling for debugging.
  - `[event]` extras group in `pyproject.toml` (`pip install tagteam[event]` to enable; base install stays poll-only).
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase E; `docs/phases/polish-pack-watcher-tokens-adopt.md`

### Phase 25: Drift / out-of-sync audit — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** Drift between `handoff-state.json` / `_status.json` / `_rounds.jsonl` is impossible by construction with a single SQLite store: `state` is a singleton row, cycle status is derived from the round log. The audit phase becomes a one-time migration check rather than ongoing work.
- **Source:** `docs/tagteam-2.0-proposal.md` §8 Phase F

### Phase 26: Workspace cleanup — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** `.tagteam/tagteam.db` *is* the workspace cleanup. Runtime state collapses to one gitignored file. The auto-rendered markdown export (`docs/handoffs/<phase>_<type>.md` written on every DB write, byte-identical to today's `tagteam cycle render` output per the spike) preserves git-visible audit history.

### Phase 27: Cycle health & stale-state detection — ABSORBED BY PHASE 28
- **Status:** Absorbed — see Phase 28 / `docs/phases/sqlite-spike-findings.md`
- **Description:** The user-facing health surface (`tagteam state health [--stale-days N]`) reduces to a few SELECT queries over the Phase 28 schema. Should ship as part of the production port, not as a separate phase — the queries are trivial once the DB exists.

### Phase 28: SQLite as canonical runtime store
- **Status:** Complete (2026-05-03) — shipped in 0.6.0. Step A (dual-write), Step B (auto-export + `migrate --to-step-b`), and Stage 2 (DB-backed runtime readers) all merged; Step B activated on this repo post-Stage-2. See `docs/phases/sqlite-spike-findings.md` for the original go/no-go writeup.
- **Description:** Move runtime state (handoff state, cycle status, rounds, diagnostics) from a constellation of JSON/JSONL files to a single SQLite database at `.tagteam/tagteam.db`. Auto-render a synthesized markdown view to `docs/handoffs/<phase>_<type>.md` on every write so PR-reviewable conversation history is preserved. Eliminates by construction the multi-file drift class of bugs that motivated Phases 25 and 27, absorbs Phases 20/21/22/26 as well.
- **Why this is its own phase, not just an implementation detail of 26:** It changes the *canonical* data store, not just its location. The 2.0 proposal stayed file-based by default; this phase is the explicit revisit, scoped to runtime state only (not the round log's role as audit artifact — the markdown render covers that).
- **Schema sketch:**
  - `cycles(id, phase, type, lead, reviewer, state, ready_for, created_at, closed_at)`
  - `rounds(cycle_id, round, role, action, content, summary, decision, blockers_json, unresolved_json, resolved, updated_by, ts)` — collapses Phases 21 and 22 into native columns
  - `state(singleton, turn, status, phase, type, round, run_mode, roadmap_queue, roadmap_index, command, updated_by, ts)`
  - `diagnostics(ts, kind, payload_json)`
- **Migration plan:**
  - Stage 1: `tagteam migrate --to-sqlite` builds `.tagteam/tagteam.db` from existing files; old files remain
  - Stage 2: Dual-write release — write to both files and DB, but read from DB. One release cycle of soak.
  - Stage 3: DB-only — files become opt-in export via `tagteam cycle render`
- **What this absorbs if it lands:**
  - Phase 20 (tail reads) — `WHERE round > last_seen`
  - Phase 21 (summary field) — a column
  - Phase 22 (structured round schema) — native columns
  - Phase 25 (drift audit) — mostly obsolete; drift impossible by construction
  - Phase 26 (workspace cleanup) — `.tagteam/tagteam.db` is the cleanup
  - Phase 27 (cycle health) — collapses to a few SELECT queries
- **What it does NOT absorb:** Phase 23 (per-round files — different concern), Phase 24 (event-driven watcher — orthogonal).
- **Pre-commit experiment:** Before scheduling Stages 2–3, run a small spike that builds the schema, ports `cycle add`/`cycle rounds` against it, and measures (a) write latency on a realistic round burst, (b) read latency for `cycle rounds` against a 100-round cycle, (c) the size of the diff between auto-rendered markdown and current `cycle render` output. Decision criterion: if the spike doesn't surface a blocking issue and the markdown render is byte-identical or trivially aligned, proceed. If the spike reveals real friction, fall back to executing 20–27 incrementally.
- **Open questions:**
  - Does the auto-rendered markdown cover the full set of git-visible properties Jack actually relies on (PR review, `git blame`, archaeology against historical commits)? Worth asking explicitly during the experiment.
  - How are schema migrations versioned and rolled forward? (Probably: `PRAGMA user_version` + migration scripts in `tagteam/migrations/`.)
  - Concurrent access between watcher daemon and CLI commands — WAL mode should handle it, but worth load-testing.

### Phase 29: Watcher mode auto-detection
- **Status:** Complete (2026-05-03).
- **Motivation:** During the first live two-agent loop on this repo, the watcher detected the turn flip but only posted a notification — Codex's tab sat idle until manually relayed. Original assumption was that iTerm2 send-keys infrastructure didn't exist. Investigation revealed it ALL existed: `iterm.write_text_to_session`, `watcher.send_iterm_command`, `is_agent_idle_iterm`, `wait_for_idle_iterm`, and `tagteam watch --mode iterm2` were all wired up. The actual gap was UX: `tagteam watch` defaulted to `mode=notify`, and `--mode iterm2` only worked when `.handoff-session.json` existed (populated by `session start --launch`, NOT by manually opening tabs).
- **What shipped:**
  - `_auto_detect_mode(project_dir)` in `tagteam/watcher.py`: returns `iterm2` if both lead+reviewer iterm session IDs exist in `.handoff-session.json`, `tmux` if the default tmux session exists, else `notify` with a helpful pointer to `tagteam session start --launch`.
  - `tagteam watch` CLI: when `--mode` is not explicitly given, calls `_auto_detect_mode` and logs the picked mode + reason on startup.
  - 6 new tests in `tests/test_watcher_auto_detect.py` covering each mode-detection branch + graceful fallback when `session_exists()` raises.
- **Final scope:** ~50 lines + tests, one session. Smaller than the original 50–80 estimate because the underlying send-keys plumbing was already done.
- **Followup discovered, not done:** if you manually open iTerm tabs (instead of letting `session start --launch` create them), there's no way to register them as the watcher's lead/reviewer panes after-the-fact. → Shipped as Phase 30 below.

### Phase 30: Session adopt for manually-opened iTerm tabs
- **Status:** Complete (2026-05-03) — shipped via `polish-pack-watcher-tokens-adopt`.
- **What shipped:**
  - `tagteam session adopt --lead <unique-id> [--reviewer <id>] [--watcher <id>] [--force]` writes `.handoff-session.json` in the same `{"backend": "iterm2", "tabs": {role: {"session_id": id}}}` schema that `session start --launch` produces, so all existing consumers (`get_session_id`, `_any_session_alive`, watcher auto-detect, `state diagnose`, server log-tail) work unchanged.
  - `tagteam session list-iterm` lists currently-open iTerm2 sessions so users can discover the unique IDs to pass to `adopt`.
  - Reuses the existing `iterm.session_id_is_valid()` helper for liveness checking — no parallel validator.
  - 11 new tests including a `test_watcher_auto_detect_picks_iterm2_after_adopt` regression guard that closes the Phase 29 → adopt loop.
- **Origin:** Phase 29 followup discovered while wiring up the live two-agent loop on this repo.
- **Phase Plan:** `docs/phases/polish-pack-watcher-tokens-adopt.md` (Sub-phase C)

### Phase 31: Headless Turn Engine (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-14 (round 3; all review turns ran headless). Release 0.8.0: `pyproject.toml` bumped; tag push follows a green Windows CI run.
- **Description:** Opt-in `tagteam watch --mode headless`: on turn flip, the orchestrator spawns the owed agent via its signed-in CLI (`claude -p --output-format stream-json` / `codex exec --json`) with a bounded context (skill contract + state + round tail + command) on stdin, streams structured events to `.tagteam/turns/<stem>.events.jsonl` and a human log `<stem>.log`, verifies the agent wrote the *expected* round (or new cycle for `/handoff start …`), and records per-turn token usage in the additive `usage` table (schema v3). Includes `cycle rounds --tail N`, `tagteam tail`, `agents.<role>.headless.{provider,executable,args}` config with structural argv validation, and failure handling (timeout / nonzero exit / no round → diagnostics + `.tagteam/headless-paused.json` + notification; no retries). Never auto-detected; flag-off runtime behavior unchanged from 0.7.1.
- **Also fixed:** `dualwrite.py` imported `fcntl` at module top — every cycle write failed on Windows since Phase 28; now uses `msvcrt.locking` on Windows. New `.github/workflows/tests.yml` runs the suite on ubuntu + windows.
- **Plan:** `docs/phases/headless-turn-engine-30-arc.md` (approved round 3). **Findings:** `docs/phases/headless-turn-engine-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 31

### Phase 32: Orchestration Controls & Usage Surfacing (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 3; every plan and impl review turn ran headless). Release 0.9.0 via PR from `phase-32-orchestration-controls`; tag after merge + green CI.
- **Description:** `tagteam pause`/`resume` (marker honored by every watcher mode; resume re-dispatches the owed turn once), `tagteam cancel-turn` (binds the recorded child PID to the recorded turn via same-source creation identities + parent pid before signalling; engine records outcome `cancelled`), `tagteam interject` (additive `interjections` table with provenance, `--to lead|reviewer`, cycle-scoped eligibility, exact-id delivery stamping on `ok`, `--list/--retire`; surfaced in headless prompts and on `cycle rounds`/`render`), `tagteam usage` (per-turn lines + by-role/by-cycle/totals, `--json`), cross-platform `notify()` (osascript / PowerShell toast → `msg` / notify-send; `TAGTEAM_NO_NOTIFY`), `--turn-retries N` gated on a content-sensitive recursive repo fingerprint AND a handoff fingerprint (fail closed on UNSUPPORTED), per-role `headless.timeout_minutes`, `tagteam rollback X.Y.Z [--yes]`. Schema v4 additive.
- **Plan:** `docs/phases/orchestration-controls-usage-surfacing-30-arc.md` (approved round 7; every review turn headless). **Findings:** `docs/phases/orchestration-controls-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 32

### Phase 33: Escalation Briefer (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 4; plan 6 rounds; all review turns headless). Release 0.10.0 via PR from `phase-33-escalation-briefer`; tag after merge + green CI.
- **Description:** On `escalated`/`needs-human` (canonical cycle status), the watcher runs ONE headless briefer turn per escalation event — event identity is a repair-safe key from the triggering entry; a pre-spawn claim row (schema v5 `briefs`, partial unique indexes) gives at-most-once automatic attempts and one running attempt per event across kinds — that writes `docs/escalations/<phase>_<type>_r<N>_<event>-a<attempt>.md` (Positions / Crux / Evidence / Recommendation / Rulings) under a hard 60k-char prompt budget. `tagteam brief` (current-event scoped, `--list`, `--generate`), `tagteam rule approve|request-changes|answer` (dedicated `add_ruling` path bypassing the stale gate; `answer` = interjection + `rearm`), grouped rounds gain `entries`/`rulings`, repair now preserves `usage`/`interjections`/`briefs`. **Opt-in** (`briefer.enabled: true`; absent = 0.9.0 behavior; §2 hard constraint overrides §4's "opt-out" wording).
- **Plan:** `docs/phases/escalation-briefer-30-arc.md` (approved round 6). **Findings:** `docs/phases/escalation-briefer-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 33

### Phase 34: Arbiter Cockpit (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-15 (round 3; plan 3 rounds incl. a UX-design round). Release 0.11.0 via PR from `phase-34-arbiter-cockpit`; tag after merge + green CI.
- **Description:** `tagteam serve --theme cockpit` (or `serve.theme: cockpit`) serves a plain-JS cockpit organised by the arbiter's questions: a **Now** strip (state, owed turn + age, in-flight + tail drawer, hold, watcher, connection mode), **Needs you** (typed cards with one action each: escalation + brief + Approve/Request changes, question + Answer, hold + Resume, missing brief + Generate, stale in-flight/no watcher), and **Watch** tabs (Feed via SSE, per-file scope Diff, Usage with churn curve + rate-limit signal, Notes). Backend: `ThreadingHTTPServer`, per-run POST token + Origin check (cockpit mode only), read endpoints (`/api/now`, extended `/api/rounds`, `/api/interjections`, `/api/briefs`, `/api/brief/<id>|current`, `/api/usage`, `/api/scope-diff/<cycle>`, `/api/tail`), SSE `/api/events` (1 s sampler, heartbeat, `--max-sse` cap), action endpoints wrapping the Phase 32/33 command functions (`by = web:<user>`), `cycle.compute_scope_diff` extracted (CLI byte-identical), schema v6 `rate_limits` (latest Claude `rate_limit_event`, repair-preserved). **Bare `tagteam serve` is 0.10.0-identical** (Saloon, bind all, no token, new endpoints 404); the Saloon is served at `/?theme=saloon` in cockpit mode with a token-aware `tagteamFetch()`.
- **Plan:** `docs/phases/arbiter-cockpit-30-arc.md` (approved round 3). **Findings:** `docs/phases/arbiter-cockpit-findings.md`.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 34

### Phase 35: Cross-Project Hub (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-16 (round 2; plan 2 rounds, UX flow designed with the ux-design-guide skill). Release 0.12.0 via PR from `phase-35-cross-project-hub`; tag after merge + green CI.
- **Description:** `tagteam hub` — one read-only surface over every registered project: Needs you → Waiting (stale / abandoned? with the CLI hint) → Quiet, burn across projects and the shared subscription window (newest row per provider/kind), each project's cockpit mounted at `/p/<id>/` through a shared `CockpitRouter` seam (server-injected base-aware HTML), hub SSE over file signals + one shared process scan per tick, `tagteam hub --list [--json]`, `tagteam registry list|unregister`. Never migrates a project DB (`mode=ro`), never rewrites the registry (`read_registry_raw`).
- **Plan:** `docs/phases/cross-project-hub-30-arc.md` (approved round 2). **Findings:** `docs/phases/cross-project-hub-findings.md`.
- **Description:** `tagteam hub` — one surface over every registered project: waiting turns, pending escalations, stale cycles, aggregate token burn against the shared subscription pool.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 35

### Phase 36: Visual Story & Portfolio Feed (3.0 arc)
- **Status:** ✅ Complete — impl approved 2026-08-16 (round 3; plan 6 rounds, flow designed first with the ux-design-guide skill). Release **3.0.0** via PR from `phase-36-visual-story`; tag after merge + green CI. Docs, media, scripts and tests, plus one disclosed text-only cockpit fix (the churn chart's false round-10 threshold removed); behavior otherwise identical to 0.12.0.
- **Plan:** `docs/phases/visual-story-portfolio-feed-30-arc.md`. **Findings:** `docs/phases/visual-story-portfolio-feed-findings.md`.
- **Description:** README restructured around a visual narrative (mermaid flowcharts of the planning/review processes and 3.0 architecture), standalone SVG diagram exports in `docs/media/` shaped for the portfolio site's featured-app pages, and an outside-reader `docs/showcase.md` with real soak numbers. Portfolio repo itself is out of scope.
- **Source:** `docs/tagteam-3.0-proposal.md` §4 Phase 36

### Phase 37: Cockpit Launchpad & Lead Conversation (3.1)
- **Status:** ✅ Complete — impl approved 2026-08-16 (round 4; plan 5 rounds, flow designed first with the ux-design-guide skill from a user walk-through). Release **3.1.0** via PR from `phase-37-cockpit-launchpad`; tag after merge + green CI.
- **Findings:** `docs/phases/cockpit-launchpad-lead-conversation-findings.md`
- **Description:** bare `tagteam serve` opens the cockpit (Saloon behind `--theme saloon`), honest banner, port-collision refusal; a **Start** card that launches the interactive session or the headless watcher and hands the lead its first turn; a **Lead** panel — talk to the lead agent from the cockpit through its own CLI (resumable, streamed, transcript on disk, same permissions and lead lock as headless turns) so brainstorm → plan → handoff → follow-up → close-out all happen where the arbiter already is; `tagteam lead` CLI twin; hub rows link to Start.
- **Plan:** `docs/phases/cockpit-launchpad-lead-conversation-31.md`
- **Source:** arbiter walk-through 2026-08-16 (idle cockpit dead-ends; two servers shadowing on one port; "I want to talk to my lead and start one from here").

### Phase 38: Gatekeeper Pre-checks (3.2)
- **Status:** ✅ Complete — plan approved round 5, impl approved round 2 (2026-08-16); PR #16 merged; released as **3.2.0** (tag `v3.2.0`, PyPI 2026-08-16).
- **Description:** a deterministic, model-free gate between the lead's `SUBMIT_FOR_REVIEW` and the reviewer's turn: run the project's configured test command, the existing scope-diff audit and a plan-doc check; **PASS** attaches the report to the round so the reviewer starts with the facts, **BOUNCE** hands the turn straight back to the lead with the failing output so no reviewer turn is spent on a submission that doesn't build. Runs in the watcher (headless and interactive), at-most-once per submission via a claim row (briefer pattern), bounded by `max_bounces` so it can never trap a lead; `tagteam gate check|run|status|list`; cockpit feed shows gate items. Opt-in behind `gatekeeper.enabled` — flag-off is byte-identical to 3.1.1.
- **Plan:** `docs/phases/gatekeeper-pre-checks.md`
- **Source:** 3.0 proposal §4 candidate; promoted 2026-08-16 as the first post-arc phase (best value-to-risk of the four candidates).

### Phase 39: Reviewer Panels (3.3)
- **Depends on:** Phase 38
- **Status:** ✅ Complete — plan approved round 3, impl approved round 3 (2026-08-16); PR #17 merged; released as **3.3.0** (tag `v3.3.0`, PyPI 2026-08-16).
- **Description:** opt-in panel that takes the reviewer's turn as 2–3 independent lens reviews (correctness / scope / verification by default; each a fresh reviewer process with a lens brief, writing a structured verdict) merged deterministically into exactly one reviewer entry — one `REQUEST_CHANGES` with findings grouped by lens, or `APPROVE` only when every lens approves; any lens failure with no objection → fallback to the ordinary reviewer turn (never a partial approval). Runs after the gate at the watcher's reviewer seam in every mode; at-most-once per submission (`panels` claim row, schema v9, shared `_claim_satellite` with the gate); `tagteam panel run|status|list|lenses|preview`. No cockpit work.
- **Plan:** `docs/phases/reviewer-panels.md`

### Phase 40: Roadmap as a DAG (3.4)
- **Depends on:** Phase 35, Phase 39
- **Status:** ✅ Complete — plan approved round 3, impl approved round 2 (2026-08-16); PR #18 merged; released as **3.4.0** (tag `v3.4.0`, PyPI 2026-08-16).
- **Description:** phases gain an optional `- **Depends on:** …` line and tagteam treats the roadmap as a directed acyclic graph: `roadmap queue` becomes a stable topological order (byte-identical for edge-free roadmaps), `roadmap check|graph|ready` validate and show the graph, full-roadmap mode never starts a blocked phase (pause reason + `roadmap resume`), and `tagteam roadmap worktree <phase>` gives each independent ready phase its own git worktree — a separate tagteam project root with its own state, watcher, gate and panel — so independent phases can run in parallel; `roadmap worktrees` lists them, `--remove` cleans up merged ones. No cockpit work.
- **Plan:** `docs/phases/roadmap-dag.md`
- **Source:** 3.0 proposal §4 candidate "Roadmap as DAG (`depends_on`, parallel phases in worktrees)"; promoted 2026-08-16.

### Phase 41: Review-loop efficiency (3.5)
- **Depends on:** Phase 38
- **Status:** ✅ Complete — plan approved round 3, impl approved round 2 (2026-08-16); PR #19 merged; released as **3.5.0** (tag `v3.5.0`, PyPI 2026-08-16).
- **Description:** one full-suite run and zero spurious prompts per review round: `gatekeeper.on_submit` runs the gate synchronously from the lead's `cycle add SUBMIT_FOR_REVIEW` (works without a watcher; `gate check --skip-tests` is the cheap pre-flight); the watcher's watchdog re-sends only on positive idle, at `watcher.resend_minutes` (default 15), at most twice; verification-budget contract in the SKILL (lead reports the suite once, reviewer does not re-run it); `scripts/release.py X.Y.Z` bumps pyproject/CITATION/uv.lock.
- **Plan:** `docs/phases/review-loop-efficiency.md`
- **Source:** measured 2026-08-16 — four suite runs per impl round, 5-minute watchdog nudges to both agents.

### Phase 42: Terminal.app backend (3.6)
- **Depends on:** Phase 41
- **Status:** ✅ Complete — plan approved round 2, impl approved round 1 (2026-08-17); PR #20 merged; released as **3.6.0** (tag `v3.6.0`, PyPI 2026-08-17).
- **Description:** a fourth session backend, `terminal`, drives macOS Terminal.app through AppleScript like `iterm2` drives iTerm2: `session start --backend terminal --launch` opens three windows (Lead/Watcher/Reviewer), launches and primes the agents, writes `.handoff-session.json` (`backend: terminal`, tab identity = tty); `tagteam watch --mode terminal` (auto-detected from the file) sends commands with the same idle/retry/watchdog discipline; `session kill|adopt|list-terminal`, `state` health and the dashboard log tail work for it. Opt-in; the only default change is that a Mac with neither iTerm2 nor tmux falls through to `terminal` instead of `manual`. iTerm2/tmux paths byte-identical. No cockpit work.
- **Plan:** `docs/phases/terminal-app-backend.md`
- **Source:** backlog item "Terminal.app backend (macOS, optional)"; promoted 2026-08-17.

### Phase 43: Cockpit hardening — a legible cycle (3.7)
- **Depends on:** Phase 42
- **Status:** ✅ Complete — plan approved round 1, impl approved round 2 (2026-08-17); PR #21 merged; released as **3.7.0** (tag `v3.7.0`, PyPI 2026-08-17).
- **Description:** make a running handoff unmistakable in the cockpit: a **Cycle** region at the top of Watch (Lead and Reviewer lanes, the turn token on the owed side, the running process named by kind — cycle turn rN · lead conversation · gate · panel · briefer) over a persistent **Activity** log of every agent turn (both roles' cycle turns, gates, panel lenses, briefer, lead-conversation turns) that streams the running one and keeps finished ones on screen with a named outcome (`finished · cancelled · failed · timed out · process gone · orphaned`) and a log link; **Start** acknowledges within one refresh from the pending `launches` row; the Lead panel keeps a finished turn's streamed lines under a disclosure instead of discarding them; the Now strip names the in-flight kind and the last outcome; `events_signature` covers conversation turns, launches and log growth. Read-only over existing records — no schema bump, no CLI change; the tail drawer is folded into the activity rows.
- **Plan:** `docs/phases/cockpit-hardening.md`
- **Source:** `docs/cockpit-issues.md` (2026-08-16: a running handoff is not legible as a handoff); backlog item "Cockpit hardening & UX"; promoted 2026-08-17. The saloon rethink is **not** folded in — it is Phase 44 (see the plan's scoping call).

### Phase 45: Cockpit lanes — Lead | Reviewer, each in its own pane (3.8)
- **Depends on:** Phase 43
- **Status:** ✅ Complete — plan approved round 2, impl approved round 2 (2026-08-17); PR #24 merged; released as **3.8.0** (tag `v3.8.0`, PyPI 2026-08-17).
- **Description:** replace the shared newest-first Activity log with two panes in the order the loop runs — `<lead> — lead` | `<reviewer> — reviewer` — each a timeline newest at the foot: the lead pane is the chat plus the lead's cycle turns as cards between the messages (composer at the foot); the reviewer pane holds each round's pre-check → review → verdict, streaming in place; the working pane pulses; the token sits between them; tabs shrink to Rounds · Diff · Usage · Notes with the flat "all activity" list as a disclosure. Front-end only; no schema/CLI change.
- **Plan:** `docs/phases/cockpit-lanes.md`
- **Source:** `docs/cockpit-issues.md` 2026-08-17 (later): "awkward to scroll up … Lead (Claude) and Reviewer (Codex) should be clearly labeled … since we start with the lead, it's unexpected to have the reviewer up above."

### Phase 46: Pause visibility — a held pause marker is announced where it matters (3.8.2)
- **Status:** ✅ Complete — impl approved round 2 (2026-08-22; no plan cycle by the arbiter's instruction — implementation reviewed directly); PR #26 merged; released as **3.8.2** (tag `v3.8.2`, PyPI 2026-08-22).
- **Description:** a `tagteam pause` marker is unbounded and was visible only in the watcher's own log; a four-day-old pause silently held the next cycle's first turn on the aegis project (3.7.0). Now `cycle init` / `cycle add` / `state set` print `note: watcher dispatch is PAUSED (<age> ago, by <who>): <reason>` whenever the write hands a turn over; the watcher log is aged; `cycle status` / `state` show `dispatch:`; `pause` records the state it was set on; SKILL.md tells the lead what to do. The marker is deliberately not auto-expired.
- **Plan:** `docs/phases/stale-pause-visibility.md`
- **Source:** `docs/tagteam-issue-stale-pause-marker-2026-08-22.md`

### Phase 44: Saloon rethink — archetype cast & theme packs (3.9)
- **Depends on:** Phase 45
- **Status:** Not started — promoted 2026-08-17 from the backlog; brainstorm in `docs/saloon-rethink.md`. Plan to be written when Phase 43 ships (it consumes Phase 43's per-role turn state and outcome vocabulary for the state → picture table).
- **Description:** recast the fun theme around the loop's real roles (Host, Lead, Reviewer, Turn-keeper, Round clock, You-as-Arbiter) instead of feature mascots; three-beat first-run flow (init agents → start watcher → hand the user the kickoff message); every element bound to real state. Make the engine theme-driven so up to five settings can be trialed (saloon revised, alien spaceship, pirate ship, mission control, restaurant kitchen). Cockpit remains the serious surface; theme is a skin.

## Backlog

### 3.0 candidate later phases (unscheduled)
- **Status:** Not started — deliberately kept out of the 3.0 arc to protect focus; see `docs/tagteam-3.0-proposal.md` §4 "Candidate later phases"
- ~~Gatekeeper pre-checks~~ → promoted to Phase 38 (2026-08-16)
- ~~Reviewer panels~~ → promoted to Phase 39 (2026-08-16)
- ~~Roadmap as DAG (`depends_on`, parallel phases in worktrees)~~ → promoted to Phase 40 (2026-08-16)
- Thin MCP server (revisit if the headless path proves insufficient for non-Claude agents)

### Reviewer wake delivery (possible one-off, unscheduled)
- **Status:** Observed once on 2026-08-30 (Codex not woken when the turn flipped to reviewer; the human had to nudge it). Arbiter ruling 2026-09-03: not reproduced, treat as a possible one-off, no fix scheduled. Evidence and suggested diagnostics in `docs/tagteam-issue-reviewer-wake-delivery-2026-08-30.md` — if it recurs, promote to a phase from that note.

### iTerm2 backend: injected `cd` eaten by shell-startup input (reproduced, unscheduled)
- **Status:** Reproduced 2026-09-04 on sonicgrid (CLI 3.11.0). `create_session` writes `cd <project_dir>` into each tab before the shell reaches its prompt; anything that reads the tty during startup (here the oh-my-zsh `[Y/n]` update prompt) swallows the first character, the tab stays in `~`, and `claude` launches with the home-directory trust prompt. State layer was correct throughout — it presents as "handoff defaulting to my user root". Fix belongs in the backend: gate the `cd` on a shell-prompt readiness poll (reuse `wait_for_agent_ready`), then verify `session.path` before launching agents. Evidence, screen capture, and suggested fix in `docs/tagteam-issue-iterm-lead-tab-cd-dropped-2026-09-04.md`. Dotfile workaround (`zstyle ':omz:update' mode reminder`) covers only oh-my-zsh.

### Cockpit stop should stop its watcher (for later, unscheduled)
- **Status:** ✅ Done in Phase 58 (`docs/phases/cockpit-session-lifecycle.md`). Arbiter request 2026-09-15. Stopping a cockpit session leaves its headless watcher running (`tagteam watch --mode headless --pidfile`, launched from the uv-tool install). Starting a CLI/iTerm2 session afterwards puts **two watchers** on one project, racing for the turn slot. Observed on this repo during Phase 52 impl round 4: `tagteam resume` woke the stale headless watcher, which started the gate; stopping it mid-run and the iTerm2 watcher's restart left a forced `GATE_PASS` with no test result, and the round had to be resubmitted. Wanted: stopping a cockpit session stops (and reaps) the watcher it started, and `.tagteam/watcher.json` does not outlive it. Worth considering alongside: a watcher refusing to start (or warning loudly) when another live watcher already owns the project.

### New session starts with a fresh reviewer view (for later, unscheduled)
- **Status:** ✅ Done in Phase 58 (`docs/phases/cockpit-session-lifecycle.md`). Arbiter request 2026-09-15. When a new session starts, the reviewer screen still shows the previous session's status, so it reads as if that work is current. Wanted: the reviewer view starts fresh by default, or offers a clear button. History stays on disk (cycle records, turn logs); this is about what the screen shows at session start.

### ~~Lead lane: cycle-turn cards from earlier cycles~~ → done in Phase 68b
- **Status:** ✅ Done in Phase 68b (`docs/phases/cockpit-lanes-read-as-terminals.md`): the lead lane is scoped to the current cycle exactly like the reviewer's, with its own "Show last session". Found during Phase 58 (2026-09-15). The lead lane's cycle-turn cards (`LLANE` in `cockpit.js`) come from the same project-wide `/api/activity` list the reviewer lane used and are not scoped to the current cycle, so earlier cycles' lead turns stay visible as if current. Phase 58 scoped only the reviewer lane (arbiter request). The same `cycleKey` / "Show last session" pattern would apply.

### ~~Retired-directory prune: report a directory left in place~~ → done in Phase 62
- **Status:** ✅ Done in Phase 62 (`docs/phases/framework-provenance-from-earlier-releases.md`): not-empty stays silent, any other `rmdir` failure prints `kept <dir>/ — directory left in place (<why>)`. Original note: `framework._prune_retired_dirs()` swallows `OSError` from `os.rmdir`, so a retired directory that could not be removed (not empty, or a permission failure) is left without a word; the Phase 61 plan promised a "directory left in place (<why>)" note like `_handover()`'s. Reviewer's non-blocking note at impl approval (2026-09-20). Small: record `(dir, reason)` on the plan and print one report line; distinguish "not empty" (expected, arguably silent) from a permission failure (worth saying).

### Reviewer agents in the plugin (deferred from Phase 48)
- **Status:** Deferred 2026-09-03 by arbiter ruling; depends on Phase 50 (read-only mode). Ship `codex-brief` (the submission drafter) in the plugin only after Phase 50 gives the CLI an enforced read-only mode, and extend Phase 49's user-level conflict report to agents. `doc-drift` is generic, not tagteam-specific — leave it out of the plugin. Before scheduling: check whether the user-level `codex-brief` briefs are actually what gets submitted or get rewritten; if rewritten, the agent is not earning its keep.

### Licensing & attribution decision
- **Status:** Done 2026-08-16 — **relicensed MIT → Apache-2.0** (`LICENSE` replaced, `NOTICE` added, `pyproject.toml` license + classifier, `CITATION.cff`, README License section). First Apache-2.0 release is **3.1.1** (2026-08-16; docs/license/CI only, no behavior change from 3.1.0); 3.1.0 and earlier stay MIT on PyPI (nothing published can be retracted). Not run through a handoff cycle — mechanical, no code paths touched.
- **History:** 2026-08-15 interim decision was "stay open, keep MIT" with `CITATION.cff` + README link-back; that was explicitly a placeholder pending a stronger-attribution license. Apache-2.0 chosen for the express patent grant, the "state your changes" clause on modified files, the NOTICE-file attribution that must travel with redistributions, and the trademark clause — all while staying permissive so internal adoption (the reputation path) isn't deterred.
- **Context:** Repo was already public and MIT-licensed, and every release is on PyPI as an sdist, so going private would have hidden only *future* work. Public provenance (commit history, release dates, handoff logs) plus a stronger attribution license is the protection against uncredited copying, and public proof is the whole point of the portfolio plan.
- **Options considered and rejected:** AGPL/GPL (deters commercial forks but also the internal adoption that builds reputation); private or private-shared (kills portfolio value without retracting published versions).
- **Follow-up (done 2026-08-17):** `CITATION.cff` `version` / `date-released` are bumped by `scripts/release.py` alongside `pyproject.toml` (Phase 41), and `.github/workflows/publish.yml` now fails the tag build if `CITATION.cff` disagrees with the tag, same as the existing `pyproject.toml` guard. No handoff cycle — mechanical.

### CI: take the Windows job out of the per-PR gate
- **Status:** Done 2026-08-16 — `.github/workflows/tests.yml` now runs only `pytest (ubuntu-latest)` on push/PR; the Windows job is a separate `pytest-windows` job that runs only via **Run workflow → `windows` = true** (`workflow_dispatch` input). No branch protection existed, so nothing else to unrequire. **We still want Windows back in the gate later** — when Windows becomes a priority, drop the `if:` on `pytest-windows` (or fold it back into a matrix) and run it before any release that touches procs / headless / watcher pidfile / port-lease code.
- **Motivation:** `pytest (windows-latest)` takes ~7–9 min per PR vs ~2 min for Ubuntu and Windows is not a current priority. Options: run it on `workflow_dispatch` / nightly / on `v*` tags only, or keep it non-required. Verify Windows separately once macOS/Ubuntu are green. Keep the Windows code paths (procs, headless, watcher pidfile, port-lease fallback) — this is about per-PR cost, not support.

### ~~Cockpit hardening & UX~~ → promoted to Phase 43 (2026-08-17)
- Evidence stays in `docs/cockpit-issues.md` (running list). The saloon rethink was promoted separately as Phase 44 rather than folded in — see `docs/phases/cockpit-hardening.md` (scoping call).

### ~~Saloon rethink — archetype cast & theme packs~~ → promoted to Phase 44 (2026-08-17)
- Brainstorm in `docs/saloon-rethink.md`; depends on Phase 43.

### ~~Terminal.app backend (macOS, optional)~~ → promoted to Phase 42 (2026-08-17)
- See `docs/phases/terminal-app-backend.md`.

## Decision Log
See `docs/decision_log.md`

## Getting Started
1. Use `/tagteam:handoff status` to see the current phase and whose turn it is
2. Use `/tagteam:handoff start [phase]` to open a phase's plan cycle
3. Use `tagteam roadmap ready` to list the phases that can start now
