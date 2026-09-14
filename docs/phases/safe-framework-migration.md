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
`docs/checklists/*.md`, `docs/workflows.md`, the vendored handoff skill
`.claude/skills/handoff/SKILL.md`, and the legacy flat
`.claude/skills/handoff-*.md` / `handoff.md` files (removal candidates).
Seed-only files (`docs/roadmap.md`, `docs/decision_log.md`, `AGENTS.md`,
`CLAUDE.md`) are created when absent and never touched otherwise, exactly as
now, and are not in the manifest.

Each managed path is classified against the current package source:
- `unsupported` → the path or a parent component has a filesystem shape the
  engine does not handle (see "Filesystem boundaries") → refuse, always.
- `absent` → create.
- `current` → byte-identical to the package → no write; adopted into the
  manifest (see "Manifest").
- `framework` → sha256 equals the manifest entry (what tagteam last wrote), or,
  pre-manifest, equals the current package file rendered for the configured
  names, the swapped names, or no names. Shipped templates carry no
  placeholders since Phase 54, so today that check degenerates to byte
  equality; the renderer is used so the rule stays true if placeholders return.
  → refresh.
- `custom` → anything else → keep. Listed in the report with the reason;
  overwritten (or, for removal candidates, deleted) only by `--accept PATH`.
No older template blobs are recognised. An old project's rendered
"Lead: claude" templates classify as `custom`; the report says how to accept
them. That is the deliberate cost of no hash-mining.

### Handoff skill and legacy flat skills
Round 1 claimed the Phase 48 skill logic could be reused unchanged. That is
wrong: `setup._sync_handoff_skill` gates only the plugin-driven *removal* on
provenance; its vendoring branch (plugin absent, or `--no-plugin`) does an
unconditional `rmtree` + `copytree` of `.claude/skills/handoff/`. This phase
reuses the Phase 48 **classifier** (`plugin.vendored_skill_provenance`: exactly
one entry, a regular `SKILL.md`, sha256 in the known vendored-contract set) and
replaces both copy/delete behaviours with the engine's rules:

- **Vendoring (plugin absent or `--no-plugin`).** The managed path is
  `.claude/skills/handoff/SKILL.md`, classified like any other file: known
  contract hash or manifest hash → `framework` → refresh in place; absent →
  create (directory created if needed); anything else → `custom` → kept.
  Extra entries in the directory are never deleted or overwritten under any
  flag; they are listed in the report as `keep`. A directory that is a symlink
  or not a plain directory is `unsupported`. `--accept
  .claude/skills/handoff/SKILL.md` overwrites that one file only.
- **Handover to the plugin (plugin installed).** Removal stays exactly the
  Phase 48 rule: the directory is deleted only when `vendored_skill_provenance`
  says `removable` (sole entry is a known contract). Otherwise it is kept and
  reported. There is no `--accept` for directory removal; the user removes a
  customised skill directory by hand, as today.
- **Legacy flat files `handoff-*.md` / `handoff.md`.** Tagteam has no hashes
  for these (the contract hash file is contract-only and hash-mining is out of
  scope), so a name match is a candidate, not proof of ownership: each is
  classified `custom` and **kept** by default, listed in the report as
  `keep (legacy flat skill; delete with --accept PATH)`. Deletion requires
  `--accept PATH` per file (authorisation), and only then the recoverability
  check (tracked-and-clean, else refused unless `--force`). Recoverability
  never substitutes for authorisation: a tracked-clean user-authored
  `handoff-notes.md` is not deleted without `--accept`. This is a deliberate
  change from today's unconditional removal.
- **User-level skill note** (`report_legacy_user_skills`, Phase 49) is
  report-only and unchanged.

### Manifest
`tagteam-manifest.json` at the project root, beside `tagteam.yaml`, committed
(`.tagteam/` is gitignored, so it cannot hold the record). Schema v1:

```
{"schema": 1, "tagteam": "<package version of the run that last wrote this file>",
 "written_at": "<iso>",
 "files": {"<relpath>": {"sha256": "<hex>", "source": "<data-relpath>",
                         "tagteam": "<package version whose bytes these are>"}}}
```

The top-level `tagteam` is the last migration's package version; the
per-entry `tagteam` is the version that produced those bytes (this is the
per-artifact version Scope promises). The two differ after a partial apply.

**What an entry means.** An entry exists for a path exactly while the bytes on
disk are verified package output. Entries are (re)recorded for paths that
were `created` or `refreshed` this run, and for paths classified `current`
(bootstrap adoption: the bytes equal the current package render, so recording
them as framework-owned states a verified fact and makes the next package
version refresh them instead of calling them `custom`). Entries are never
recorded for `custom` bytes; a stale entry whose hash no longer matches the
file is dropped, and the report says `modified since tagteam <ver> wrote it`.
A refused path's entry is left as it was. Removal candidates never get
entries; a deleted path's entry, if any, is dropped.

**When it is written.** The engine computes the manifest the tree *should*
have after this run and writes it once, at the end, only if it differs from
the file on disk (ignoring `written_at`). So: a current-package pre-manifest
project gets exactly one write, the manifest; a second run computes the same
projection and writes nothing; a run where every path was refused still
adopts `current` paths. If the process dies between file writes and the
manifest write, the next run classifies the refreshed files `current` and
adopts them, so retry converges without ever recording custom bytes.

Absent or unparseable manifest means "pre-manifest": no entries are trusted,
nothing is deleted on its account.

### Filesystem boundaries
The project root is `Path(target).resolve()` once; every managed path is a
lexical relpath under it. Before classification and again immediately before
each write or delete, the engine `lstat`s every component from the root down:
- Each parent component must be an existing plain directory (not a symlink)
  or absent (to be created with `mkdir`, never through a link).
- The path itself must be absent or a regular file that is not a symlink.
- Anything else (symlink to a file or directory, directory where a file is
  expected, socket/FIFO/device, unreadable) is `unsupported` → `refuse:
  unsupported filesystem shape (<what>)`. No flag lifts this, not `--accept`,
  not `--force`; the report tells the user what to fix by hand. The engine
  never follows a link and never deletes directory contents.

Preimage check = the triple (existence, type, sha256) captured at
classification must be identical at write time, for creates, overwrites, and
deletes alike. Creates use exclusive open (`O_CREAT|O_EXCL`), so a file that
appeared since classification makes the create fail and be refused rather
than clobbered. Overwrites write a temp file in the same real directory and
`rename` over the target only after the re-check passes. Deletes re-check
then `unlink`. This narrows the check-to-act window to the syscall pair; it
does not close it, and the plan says so rather than claiming atomicity it
cannot have. The skill directory is checked the same way, with its entries
enumerated by `lstat`.

### Apply, refusal, recovery
Apply runs classification, then applies per path in report order; a preimage
or shape failure refuses that path alone and the rest proceed. Exit 1 if any
path was refused. Per-path order of checks for a `custom` path:
authorisation (`--accept PATH` present) → filesystem shape → recoverability →
preimage → act. A path that fails at any step is reported with that reason.

Recovery is git. Rules, applied per path:
- `absent` creates and `framework` refreshes need no recoverability: nothing
  the user authored is lost.
- `custom` overwrites and legacy flat-skill deletions (both only with
  `--accept PATH`) require the path to be git-tracked and clean in a git
  repository, so `git checkout -- PATH` restores it. Otherwise the path is
  refused with the reason (`not a git repository`, `untracked`,
  `uncommitted changes`).
- `--force` lifts the recoverability refusal only; it never widens what counts
  as `framework`, never grants acceptance, never lifts a shape refusal, and is
  printed in the report.
`--accept` is a `setup [dir]` flag only, repeatable; `upgrade` refreshes
`framework` paths across every registered project and reports each project's
`custom` paths with the exact `tagteam setup DIR --accept PATH` line to run.

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

### Unit fixtures (`tests/test_migrate.py`, in-process, fast)
Skill and legacy files:
- customised `SKILL.md`, plugin absent → kept; same with `--no-plugin`;
  with `--accept .claude/skills/handoff/SKILL.md` and tracked-clean → refreshed.
- known-contract `SKILL.md` plus an extra file in the directory, plugin
  absent → `SKILL.md` refreshed, extra file untouched and reported `keep`;
  plugin installed → directory kept (not `removable`), reported.
- tracked-clean user-authored `handoff-notes.md` → kept without `--accept`;
  deleted with `--accept`; untracked with `--accept` → refused; untracked with
  `--accept --force` → deleted.
Manifest:
- pre-manifest, current-package tree → only the manifest written, entries for
  every managed path with per-entry version = package version.
- bootstrap at version A, then package B with changed templates → those paths
  `framework`, refreshed, entries updated to B; untouched paths keep A.
- one customised path among framework paths → apply refreshes the rest,
  manifest has no entry for the custom path, second run writes nothing.
- manifest entry present but file edited since → `custom`, entry dropped,
  report names the version it was written at.
- simulated crash after file writes before manifest write → next run adopts
  and converges; manifest matches the tree.
Filesystem shapes (each refused with `unsupported filesystem shape`, under
`--accept` and `--force` too, tree unchanged):
- managed path is a symlink to a file with identical bytes.
- `templates/` is a symlink to a directory outside the project.
- managed path is a directory; skill directory is a symlink.
- file created at an `absent` path between classification and apply →
  exclusive create fails, path refused, existing file intact.
- file replaced by a symlink with identical target bytes between
  classification and apply → type re-check refuses.
- delete target replaced between classification and apply → refused.

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
  writes only the manifest, adopting every managed path; with old rendered
  templates: every one classifies `custom`, is kept, and is reported with an
  accept line.
- `--accept PATH` overwrites a tracked-clean custom path and refuses an
  untracked, dirty, or non-git one until `--force`.
- Customised vendored `SKILL.md` and extra skill-directory files survive
  vendoring (plugin absent and `--no-plugin`) without `--accept`.
- Legacy flat `handoff-*.md` files are kept unless individually accepted, even
  when tracked and clean.
- Preimage mismatch (existence, type, or hash) between classification and
  write or delete refuses that one path; symlinks and non-regular files at
  any component are refused under every flag.
- Second run on any migrated project: zero bytes changed, exit 0. Manifest
  entries never record custom bytes.
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
- Legacy flat skills are no longer removed automatically; projects that still
  carry one get a report line and an accept command instead. Flagged in
  release notes.
- Bootstrap adoption trusts byte-equality with the current package, never a
  manifest it did not write; an attacker-controlled manifest cannot make the
  engine overwrite custom bytes, since `framework` via manifest still requires
  the on-disk hash to match the entry.
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
