# Phase 52: Safe Framework Migration

## Status
- [x] Planning
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
`tagteam setup` and `tagteam upgrade` still overwrite every managed file
(`templates/*.md`, `docs/checklists/*.md`, `docs/workflows.md`) on every run,
with no record of what tagteam wrote and no way to preview or recover. Phase 51
shipped role-neutral templates but explicitly deferred the safe path to bring
existing projects onto them. This phase makes setup/upgrade a
provenance-aware migration: preview per project, refresh only content that is
provably tagteam's, keep everything else and say so, record what was written,
and make a second run a no-op. Depends on Phase 51 (merged, 3.12.0 candidate).

Source of scope: the "follow-on scope retained for review" section of
`docs/phases/role-neutrality.md` and priority 2 of
`docs/role-neutrality-recommendations-2026-09-09.md`. Roles come from
`tagteam.yaml`.

## Scope
In:
1. One migration engine (`tagteam/migrate.py`) used by both `setup [dir]` and
   `upgrade`; `setup.main` becomes a thin caller. Same classification, same
   report, same manifest either way.
2. A per-project manifest of installed framework artifacts: path, sha256,
   package version that wrote it.
3. `--preview` (dry run) on both commands; per-path `--accept PATH` on `setup`;
   `--force` to override the git-recoverability refusal.
4. Preimage checks before every overwrite, git-based recovery rules, idempotent
   retry.
5. Installed-distribution smoke coverage: extend `scripts/upgrade_smoke.py` and
   `tests/test_upgrade_smoke.py` to build the wheel, install it into a throwaway
   venv, and run preview/apply/retry on migration fixtures.
6. Version visibility: preview and `tagteam state` distinguish package version,
   manifest version, and plugin status so a package update never implies the
   project's artifacts moved.

Out: historical hash-mining of old template blobs (the vendored-contract hash
file stays contract-only), any backup/snapshot subsystem (git is the recovery
path), three-way merge, Phase 53 legacy-workflow diagnostics and capability
reporting, downstream migration of the arbiter's projects, release/tag.

## Technical Approach

### Managed set and classification
Managed paths are exactly what setup writes today: `templates/*.md`,
`docs/checklists/*.md`, `docs/workflows.md`, plus the vendored handoff skill
(already provenance-gated by Phase 48; that logic is reused unchanged) and the
legacy flat `.claude/skills/handoff-*.md` / `handoff.md` removals. Seed-only
files (`docs/roadmap.md`, `docs/decision_log.md`, `AGENTS.md`, `CLAUDE.md`)
are created when absent and never touched otherwise, exactly as now, and are
not in the manifest.

Each managed path is classified against the current package source:
- `absent` → create.
- `current` → byte-identical to the package → no write.
- `framework` → sha256 equals the manifest entry (what tagteam last wrote), or,
  pre-manifest, equals the current package file rendered for the configured
  names, the swapped names, or no names. Shipped templates carry no
  placeholders since Phase 54, so today that check degenerates to byte
  equality; the renderer is used so the rule stays true if placeholders return.
  → refresh.
- `custom` → anything else, including symlinks and non-regular files → keep.
  Listed in the report with the reason; overwritten only by `--accept PATH`.
No older template blobs are recognised. An old project's rendered
"Lead: claude" templates classify as `custom`; the report says how to accept
them. That is the deliberate cost of no hash-mining.

### Manifest
`tagteam-manifest.json` at the project root, beside `tagteam.yaml`, committed
(`.tagteam/` is gitignored, so it cannot hold the record). Schema v1:
`{"schema": 1, "tagteam": "<version>", "written_at": "<iso>", "files":
{"<relpath>": {"sha256": "<hex>", "source": "<data-relpath>"}}}`. Written
only when at least one managed path changed, so a no-op run leaves the tree
untouched. Absent or unparseable manifest means "pre-manifest": no entries are
trusted, nothing is deleted on its account.

### Apply, refusal, recovery
Apply runs classification, then for each pending write re-reads the file and
compares its sha256 to the hash observed during classification (preimage
check); a mismatch refuses that path and reports it. Writes are per-file
atomic (temp + rename in the same directory).

Recovery is git. Rules, applied per path:
- `absent` creates and `framework` refreshes need no recoverability: nothing
  the user authored is lost.
- `custom` overwrites (`--accept`) and legacy flat-skill deletions require the
  path to be git-tracked and clean in a git repository, so `git checkout --
  PATH` restores it. Otherwise the path is refused with the reason
  (`not a git repository`, `untracked`, `uncommitted changes`).
- `--force` lifts the recoverability refusal only; it never widens what counts
  as `framework`, never touches `custom` paths that were not accepted, and is
  printed in the report.
`--accept` is a `setup [dir]` flag only; `upgrade` refreshes `framework` paths
across every registered project and reports each project's `custom` paths with
the exact `tagteam setup DIR --accept PATH` line to run.

### Preview and idempotency
`--preview` prints the same report apply would print, prefixed per path with
`create` / `refresh` / `keep` / `accept` / `remove` / `refuse`, plus the header
`package X · manifest Y (written D) or none · plugin: <status>`, and writes
nothing. Exit code is 0 for preview; for apply, 0 when every pending path was
written, 1 when any path was refused. A second apply on an unchanged tree
classifies everything `current`, writes nothing, and does not rewrite the
manifest (verified by the harness diffing the tree before and after).

`needs_setup` / `run_setup` (used by `quickstart`, `session start --launch`,
`worktree`) keep their contract: a project that already has framework files is
skipped. Migration of an existing project is always an explicit `setup` or
`upgrade`.

### Distribution smoke
`scripts/upgrade_smoke.py` gains the manifest in `MANAGED_FILES` and a
`--preview` passthrough so the isolated helper can run a dry run. A new test
in `tests/test_upgrade_smoke.py` builds the wheel from the checkout
(`pip wheel --no-deps`), installs it into a fresh venv with `--no-index`, and
drives the harness through fixture projects: old Claude-lead rendered
scaffolding, a customised checklist, plugin present / absent, no `claude`
executable. Asserts: preview writes nothing; apply refreshes only `framework`
paths; the custom path survives with a report line; a second apply is a
byte-identical no-op; cycle history, roadmap and decision log are untouched.
Skipped with an explicit environmental reason when venv creation or pip is
unavailable, never silently.

## Files
- New: `tagteam/migrate.py` (classify, manifest, plan, apply, report),
  `tests/test_migrate.py`.
- Modified: `tagteam/setup.py` (thin caller; flags), `tagteam/cli.py`
  (`upgrade --preview`, help text, `state` version lines),
  `tagteam/plugin.py` only if a hash helper needs sharing,
  `scripts/upgrade_smoke.py`, `tests/test_upgrade_smoke.py`,
  `tests/test_setup.py`, `tagteam/data/workflows.md` (managed files and
  migration section), `README.md`, `docs/roadmap.md`, this plan.
- Not modified: `tagteam/data/vendored_contract_hashes.json`,
  `scripts/contract_hashes.py`, `docs/handoffs/`, any downstream project.

## Success Criteria and Verification
- Fresh project: `setup` creates everything, writes the manifest, registers the
  project; output and `needs_setup` unchanged from today.
- Existing pre-manifest project with untouched current-package files: apply
  writes only the manifest; with old rendered templates: every one classifies
  `custom`, is kept, and is reported with an accept line.
- `--accept PATH` overwrites a tracked-clean custom path and refuses an
  untracked, dirty, or non-git one until `--force`.
- Preimage mismatch between classification and write refuses that one path.
- Second run on any migrated project: zero bytes changed, exit 0.
- `upgrade` across two registered projects refreshes `framework` paths in both
  and never accepts anything.
- Wheel-installed smoke passes on the fixtures above; existing smoke tests
  still pass.
- Focused tests while working; the on_submit gate supplies the recorded full
  suite for the impl submission. Plan revisions need document checks only.

## Risks and Review Focus
- Default `setup` on an existing project stops overwriting. Any workflow that
  relied on "rerun setup to reset a template" now needs `--accept`. Flagged in
  release notes.
- The manifest is a new committed root file in every project; review the name
  and location before implementation, since renaming later is a migration.
- Classifying old rendered templates as `custom` is correct but means the
  arbiter's four downstream projects will need per-path acceptance on first
  migration. Preview makes that a read-then-decide step, not a surprise.
- Exact `--force` semantics and preservation of untracked content are the two
  points Phase 51 asked this review to settle; both are stated above.
- The wheel-build test is the slowest test in the suite; it must skip cleanly
  offline rather than fail the gate.

No human clarification is required for plan review.
