# Phase 51: Role-neutral readiness and onboarding

## Status
- [x] Planning: approved round 3 (2026-09-09)
- [x] Approved
- [x] Implementation: phase-51-role-neutrality
- [x] Implementation Review: approved round 2 (2026-09-09), gate passed at 06a9eda
- [x] Complete: PR #33 merged (2026-09-09); 3.12.0 tag pending arbiter decision

## Roles
Read current assignments from `tagteam.yaml`: initially codex lead, claude
reviewer, human arbiter. Historical attribution stays unchanged.

## Summary
Make either supported provider usable in either role through the existing
workflow. This phase delivers priority 1 of the Northstar recommendations;
Phase 52 delivers safe migration, and Phase 53 diagnostics and capability/context
visibility. Depends on Phase 50 (released in 3.11.0).

Source: `docs/role-neutrality-recommendations-2026-09-09.md`, approved in Northstar plan round 2, SHA256 `388fe0ba21d610a6d8fb6459af522cb5cbd3ccacdb8e5f7fc36cd07f7d61ff98`.

## Scope
Canonical-contract readiness, neutral reusable instructions, resolved onboarding,
and participant guards at cycle writes and watcher dispatch. Preserve provider
adapters, custom display names, explicit launch commands, and cycle semantics.

Migration manifests/preview/apply and installed-distribution smoke coverage move
to Phase 52. Extended legacy diagnostics and capability reporting move to 53.
No downstream migration, credentials/memory transfer, paid model calls, new
provider integrations, session identity subsystem, or iTerm startup fix here.
Phase 51 alone is not the minimum safe downstream upgrade: wait for Phase 52.

## Evidence and concrete audit targets
At source HEAD `8175db7`, config helpers and template substitution already support
both assignments. `setup.needs_setup` still requires a Claude skill or plugin.
`session.py` hardcodes tmux titles CLAUDE (Lead) and CODEX (Reviewer).
`watcher._dispatch` uses names cached on the watcher, while cycle status records
participants independently of current configuration. Headless context injection
already selects the other provider's instruction file and reports truncation.

Audit config defaults, priming/status, terminal labels, watcher/headless routing,
lead conversation, panel/briefer, and UI for actual provider-to-role assumptions.
List concrete fixes in the implementation submission; keep legitimate provider
adapters. Missing configuration must not silently launch a default provider.

## Technical Approach

### 1. Canonical readiness and shared instructions
Use readable canonical contract availability plus existing framework markers for
readiness. Claude installation/plugin availability is not a readiness condition.
Preserve plugin and `--no-plugin` vendored entry points as supported adapters.
Keep packaged/plugin contract parity; do not fork a Codex-specific contract.

Use installed `docs/workflows.md` as the shared reference, updating its shipped
source `tagteam/data/workflows.md`; maintain `docs/how-tagteam-works.md` as needed.
Reusable shipped templates describe Lead and Reviewer and direct agents to read
current configuration instead of freezing provider assignments. Do not rewrite
historical plans or cycle attribution.

Create minimal AGENTS.md/CLAUDE.md pointers only if absent, pointing to
`docs/workflows.md` and `tagteam contract`. Never edit an existing instruction
file. Preserve existing headless context fallback, unreadable-file behavior,
and truncation notices; manual priming explicitly directs agents to project
instructions and the shared reference. Do not run blanket setup/upgrade on this
repository to refresh its files; safe artifact migration is Phase 52.

### 2. Resolved onboarding
Reuse config helpers to show project root, lead/reviewer names, launch commands,
and handoff entry point through existing status/priming surfaces. Shell entry is
`tagteam contract`, with Claude plugin/vendored alternatives explained. Preserve
custom names/commands and report unsupported provider inference without silently
substituting one. Fix tmux titles and other confirmed role assumptions.
No new diagnostic command or capability probing in this phase.

### 3. Participant checks at two boundaries
Put shared validation in cycle init/add paths before mutation and in
`watcher._dispatch` before gate/panel/agent dispatch. Compare current validated
config with the active cycle's recorded lead/reviewer. Cycle init also checks
explicit participants against config and must not bypass an unfinished-cycle
mismatch by opening a different phase. Recheck within the existing writer lock
before committing a cycle change; retain Phase 50 read-only enforcement.

Watcher dispatch re-reads configuration instead of trusting startup names. If
an unfinished cycle disagrees, refuse dispatch and ordinary submissions, report
both assignments, and leave state/history unchanged. Unknown participant identity
is reported, not invented. Read/status and human recovery paths remain available;
automated dispatch with unverifiable active-cycle identity is refused.

Supported recovery: restore the recorded assignment, finish the cycle, stop the
old agent session/watcher, change config, recreate the session, and start new
work. No in-place participant migration. Explicit human abort/state recovery
remains available, but do not document a nonexistent cycle-abort command or
claim that changing top-level state alone closes the canonical cycle.
Completed cycles retain recorded names and permit new cycles with new roles.

Test both watcher dispatch and underlying cycle APIs, not just CLI argument
parsing. Trace other launch paths during implementation: do not claim they all
route through watcher dispatch without evidence. Direct interactive launches
remain user-managed in this phase; the cycle write guard prevents conflicting
submissions, not filesystem edits or external actions. Document that limit and
manual session recreation requirement. If an independent automated cycle-turn
launch bypasses these boundaries, report it in review and propose the smallest
necessary shared guard call instead of silently expanding the subsystem.

Session/pane identity tracking is deferred without a reproduced failure. This
phase makes no stale-pane safety guarantee; it does not kill or duplicate running
processes. Existing turn-slot locking remains in force.

## Files to Create/Modify
- `tagteam/config.py`, `setup.py`, `templates.py`, `cli.py`: canonical readiness,
  role resolution, absent-only instruction pointers, onboarding.
- `tagteam/cycle.py`, `watcher.py`, small shared validation helper if useful:
  participant guards and dispatch-time configuration refresh.
- `tagteam/session.py` and confirmed role-dependent label/prompt call sites.
- `tagteam/data/templates/`, `tagteam/data/workflows.md`, instruction pointer
  templates, package-data declarations, README/how-tagteam-works documentation.
- Packaged/plugin contract only where needed, preserving parity.
- Config/setup/templates/session/headless/cycle/watcher tests and focused guard
  tests. Preserve existing unrelated local changes.
- Release notes and coordinated version bump using existing release tooling.

## Success Criteria and Verification
1. Both assignments complete mocked plan and impl cycles: submit, changes
   requested, resubmit, approve, with correct participants and updated_by.
2. Fresh setup works without Claude installed. Plugin, vendored, and shell entry
   points expose the same contract. Existing custom instructions survive;
   absent instruction pointers are created once. Custom display names and launch
   commands survive. Titles and priming reflect configured roles.
3. A role switch after completion produces correct new prompts and attribution.
   A mismatch during an active cycle refuses cycle init/add and watcher dispatch
   without state/history mutation, including a watcher started before the edit.
   Reads and documented human recovery remain usable. Test missing identity too.
4. Both providers retain AGENTS-only, CLAUDE-only, both, absent, unreadable and
   truncated context behavior; manual priming identifies project instructions.
5. Mocked dispatch tests cover relevant gate bounce/pass, panel/briefer selection,
   interjections, pause/resume, roadmap advancement and read-only helpers under
   both assignments. Confirm no mismatch launches a gate/panel/agent through
   watcher dispatch. No repeated full-suite runs or paid providers.
6. Focused tests while implementing; the on_submit gate's run is the recorded
   full-suite run. Preflight `tagteam gate check --skip-tests`, then submit once
   with the gate result tied to the implementation commit. Restore the tracked
   gatekeeper/briefer settings lost during config regeneration, preserving roles:
   briefer enabled; gate enabled/on_submit; test command
   `.venv/bin/python -m pytest -q -p no:cacheprovider`; timeout 15 minutes.
7. Release notes state scope, role-switch procedure, manual-session limitations,
   and dependency on Phase 52 before downstream cleanup. Coordinate version
   metadata through existing `scripts/release.py` flow; publication follows merge
   separately. Wheel/sdist smoke checks belong to Phase 52's existing
   `scripts/upgrade_smoke.py` and `tests/test_upgrade_smoke.py` harness.

Plan revisions require document checks, not the full suite.

## Follow-on scope retained for review
Phase 52: one setup/upgrade migration path; per-project dry-run; manifest of
installed hashes/version; current-package render matches for configured/swapped
names as pre-manifest evidence; reuse vendored provenance; unknown content kept
with per-path acceptance; preimage checks; git-based recovery, dirty/non-git
refusal unless explicitly forced; idempotent retry; extend existing distribution
smoke harness. No historical hash-mining or backup subsystem. Exact apply/force
semantics and preservation of untracked content require Phase 52 plan review.

Phase 53: report-only recognized legacy workflow diagnostics and bounded
capability/context visibility, retaining Phase 49's user-level preservation rule.
No full plans for those phases until their own cycles.

## Risks and Review Focus
Verify guard placement without trapping recovery, and distinguish workflow
submission protection from arbitrary agent writes. Keep provider adapters intact.
The existing setup overwrite behavior is not solved by this phase: do not treat
readiness improvements as safe migration. No human clarification is needed now.


## Implementation notes
- Reproduced during this cycle: the watcher completion notice sent a Claude
  slash command to Codex, which rejected it before the agent saw the message.
  Terminal start/turn messages now translate to plain text while preserving
  structured state commands for headless verification. Plan completion explicitly
  requests implementation.
- Fixed hardcoded tmux Claude/Codex titles and missing-role provider defaults.
  Status/priming displays configured names/commands; reusable templates defer to
  current configuration; shell contract readiness and absent-only pointers work
  without Claude installed.
- Guard calls were also necessary at HeadlessEngine.run_owed_turn and
  lead_chat.start_turn: those are independently callable automated launch paths.
  They reuse the same participant check; a headless engine whose startup agent
  configuration changed requires restart rather than launching its cached adapter.
- Existing direct Python cycle callers without tagteam.yaml retain their explicit
  participant API. Configured projects enforce matching roles. Checks use the
  Phase 50 read-only DB connection, falling back to legacy files; a pre-existing
  WAL index may change, but no workflow history is written on refusal.
- Human rulings bypass the participant guard through the existing internal ruling
  path, so a mismatch cannot trap arbiter recovery. Ordinary init/add/AMEND are
  checked before mutation and again inside the writer lock.
- Pre-existing deleted vendored skill, local docs/workflows.md edits and iTerm
  issue note are preserved; no blanket setup/upgrade was run on this repository.
- Version metadata prepared as 3.12.0 using scripts/release.py. No publication or
  downstream installation. Phase 52 remains required before downstream migration.


## Closeout (2026-09-09)
Reviewer approved implementation round 2. Recorded gate at `06a9eda`: 1,785
passed, 5 skipped; scope and plan-document checks passed. Closeout changes are
status/documentation and recorded cycle artifacts; no repeat full-suite run.

Per the arbiter, include the implementation cycle files, deleted local vendored
handoff skill, refreshed local `docs/workflows.md`, and the existing iTerm startup
issue note in the branch/PR. These supersede the earlier note that those local
files remained outside the implementation commit. The iTerm issue remains open;
including its note does not claim a fix.

The review's four non-blocking nits remain recorded in the impl round-2 approval
for a separate arbiter decision. No tag, publication, or downstream upgrade is
part of this closeout. Phase 52 remains required before downstream migration.
