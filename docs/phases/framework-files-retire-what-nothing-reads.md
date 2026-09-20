# Phase 61: Framework files: retire what nothing reads

## Status
- [ ] Planning
- [ ] Implementation: branch `phase/framework-files-retire-what-nothing-reads`
- [ ] Implementation Review
- [ ] Complete

## Summary
`tagteam setup` installs 13 framework files into every project. Twelve of them
are read by nothing:

| Path | Files | Read by |
|---|---|---|
| `docs/workflows.md` | 1 | agents — the seeded `CLAUDE.md` / `AGENTS.md` point at it |
| `templates/*.md` | 10 | nothing. No `tagteam/*.py` reads a project's `templates/`; the contract, `README.md` workflow text and `cli.py` never mention them. `SEED_FILES` reads `roadmap.md` / `decision_log.md` from the **package's** `data/templates/`, not the project's copy. Four (`handoff_plan.md`, `handoff_impl.md`, `feedback.md`, `sync_state.md`) describe the pre-cycle manual flow that `cycle add` replaced |
| `docs/checklists/*.md` | 2 | `bench.py:599` only, guarded by `is_file()` |

Evidence and the original report: `docs/tagteam-issue-framework-files-no-opt-out-2026-09-20.md`
(written from the `linkedin-articles` project on 3.13.0). Its observation holds:
deleting the files does not stick, because `framework._sources()` lists them
unconditionally and `absent → create` puts them back on the next
`setup` / `upgrade`.

**Where this plan departs from that report.** The report recommends an opt-in
`framework.skip:` key in `tagteam.yaml` and explicitly rejects deletion. Arbiter
direction (2026-09-20) is the opposite: every new project needs the same manual
cleanup, so the fix is for `upgrade` to address the stale files and start
fresh — not a key that must be added to each of ~40 registered projects and
that still leaves the files on disk. The report's own closing section
("whether the ten templates should ship at all … if most of them go, the
`skip` key … may not be worth building") is the option taken here. No
`framework.skip` key is built.

## Scope
**In:**

1. **The managed set shrinks.** `_sources()` returns `docs/workflows.md` only
   (the vendored skill is unchanged). `templates` and `docs/checklists` leave
   `SEED_DIRS`. A fresh `tagteam setup` creates neither directory and none of
   the 12 files.
2. **Retired paths.** A new `_retired_sources()` lists the 12 paths with their
   package files, which **stay in `tagteam/data/`** — `data/templates/roadmap.md`
   and `decision_log.md` are still seed sources, `data/checklists/*` becomes the
   bench fallback (item 5), and all 12 remain the provenance evidence that lets
   a pre-manifest project's copy be recognised as tagteam's bytes. Each retired
   path is classified by the existing `_classify()` and gets a new `kind`
   (`retired`) and one of:

   | Classification | Action | Report line |
   |---|---|---|
   | absent | none | silent |
   | `current` / `framework` (bytes provably tagteam's: package match, manifest hash, rendered variant) | `retire` → unlink | `retired  templates/cycle.md — no longer installed; written by tagteam 3.13.0` |
   | `custom` | `keep` | `keep     templates/cycle.md — no longer managed; modified since tagteam 3.13.0 wrote it; delete with: tagteam setup DIR --accept templates/cycle.md` |
   | `unsupported` | `keep` (not `refuse` — tagteam no longer wants to write here, so a symlink at a retired path is not an error and must not fail `upgrade`) | `keep     … — no longer managed; <shape detail>; never touched` |

   `--accept PATH` on a custom retired path deletes it under the existing
   recoverability rule (tracked and clean, or `--force`), exactly as it does
   for a legacy flat skill today.
3. **Why unlinking without a git check is safe here.** A `retire` fires only
   when the on-disk sha256 equals bytes tagteam can prove it wrote, and those
   bytes are still in the installed package — nothing of the owner's is lost,
   tracked or not. This is the `_handover()` precedent (a provably-vendored
   `SKILL.md` is already removed without a recoverability check). The preimage
   re-check before every unlink applies unchanged. After the files, `rmdir` on
   `templates/` and `docs/checklists/` — never recursive; a directory with
   anything else in it stays, with the same "directory left in place" note
   `_handover()` uses.
4. **Manifest.** `projected_manifest()` writes no entry for a retired path
   (removed → dropped; custom → no entry, already the rule). After one run the
   manifest lists `docs/workflows.md` (+ vendored skill) only. A second run is a
   byte-identical no-op — the existing idempotence property, with retired-custom
   `keep` lines being the only repeated output (same as legacy flat skills).
5. **Bench falls back to the package checklist.** `bench.py` uses the
   project's `docs/checklists/<type>_review.md` when present (a project that
   customised it keeps its version), else the package's
   `data/checklists/<type>_review.md`. Bench prompts do not silently lose the
   checklist when the project copy is retired.
6. **`needs_setup()`** stops keying on `templates/` and `docs/checklists/`
   (it would report "setup needed" forever after a retire). New marker:
   `docs/workflows.md` is a file. Contract-text check unchanged.
7. **Documentation** (arbiter ask: fix what this touches):
   - `tagteam/data/workflows.md` § "Framework files and upgrades" and
     § "Files tagteam writes": the managed set, the `retired` / `keep` lines.
   - `README.md` "Safe migration" paragraph and the `--accept templates/cycle.md`
     example (line 306) — pick an example path that is still managed.
   - `CLAUDE.md` `setup.py` bullet (lists `templates/`, `docs/checklists/` as
     what setup brings up) and the `tagteam/data/` conventions bullet.
   - `docs/roadmap.md` "Getting Started" still names `/handoff-phase`,
     `/handoff-plan`, `/handoff-status` — pre-plugin commands that no longer
     exist. Replace with `/tagteam:handoff`.
   - Commit the issue report as the phase's evidence.
   - Release note line for the next version: *"`setup`/`upgrade` no longer
     install `templates/` or `docs/checklists/`, and remove unmodified copies
     they wrote; modified copies are kept."*
8. **This repo.** Its own `templates/` (10) and `docs/checklists/` (2) are
   tracked; running the new `setup` here retires whichever are unmodified. Done
   as the last implementation step, output pasted into the impl submission.
   This repo's `docs/workflows.md` is also behind `data/workflows.md` (it lacks
   the "Framework files and upgrades" section); the same run refreshes or
   reports it — whichever the classifier says, recorded as observed.

**Out:**
- The `framework.skip` key, per-path globs, project-type detection — see
  Summary.
- Deleting the eight now-unseeded templates from `tagteam/data/`. They are the
  provenance evidence for pre-manifest projects; removing them turns every such
  copy into `custom` and the cleanup stops working. Revisit after a release or
  two, once the sweep has run everywhere.
- `migrate.py`'s legacy `templates/` backup — untouched; it runs before setup
  on pre-yaml projects and only copies.
- Running the 40-project `upgrade` sweep. That is a release chore after merge
  (`scripts/upgrade_smoke.py` first), not part of the cycle.
- A separate `tagteam cleanup` command. The arbiter allowed one; it is not
  needed, because the retire rides on the plan/preview/apply machinery that
  already exists and `--preview` already shows it before anything is touched.

## Technical Approach
All in `tagteam/framework.py` unless noted.

- `_sources()` → workflows only. New `_retired_sources(data_dir)` yields the
  same `(rel, package file, source_rel)` triples for `templates/*.md` and
  `checklists/*.md`.
- `build_plan()`: after the managed files, build `Item(kind="retired")` per
  retired path, `_classify()` it, then map classification → action per the
  table (`retire` / `keep` / `none`; `accept_set` membership on a custom one →
  `remove`, which already flows through the recoverability block and
  `matched`). `retire` joins the write-action sets used by the unsupported-root
  and `_ensure_root` refusal paths. `_DONE["retire"] = "retired"`;
  `Item.done` includes `retired`.
- `_act()`: `retire` → `os.unlink`, same preimage `_recheck`. New
  `_prune_retired_dirs(plan)` after the item loop: `os.rmdir` each of
  `templates`, `docs/checklists` iff `observe_dir` says plain dir **and** at
  least one item under it was retired/removed this run; `OSError` (not empty)
  is swallowed into a note. Never touches a directory this run did not empty.
- `projected_manifest()`: `retired` kind is skipped entirely.
- `format_report()`: one `retired` / `keep` line per path, per the table. No
  summary count — the per-path lines are the evidence trail, and N ≤ 12.
- `setup.py`: `needs_setup()` marker change. `bench.py`: package fallback via
  the same `data_dir` resolution `setup.py` uses.
- `diagnostics.py`, `session.py`, `worktree.py` call `build_plan` /
  `needs_setup`: read each call site; expected change is none beyond counts in
  doctor's `current N` line (13 → 1), which tests will pin.

## Files
- `tagteam/framework.py`, `tagteam/setup.py`, `tagteam/bench.py`
- `tests/test_framework.py`, `tests/test_setup.py`, `tests/test_bench*.py`;
  expected fallout in `tests/test_quickstart.py`, `test_onboarding.py`,
  `test_diagnostics.py`, `test_worktree.py`, `test_upgrade_smoke.py`,
  `tests/_plugin_env.py` wherever they assert `templates/` exists after setup
- `tagteam/data/workflows.md`, `README.md`, `CLAUDE.md`, `docs/roadmap.md`
- `docs/tagteam-issue-framework-files-no-opt-out-2026-09-20.md` (committed as-is)
- This repo's `templates/`, `docs/checklists/`, `docs/workflows.md`,
  `tagteam-manifest.json` (result of item 8)

## Success Criteria
Each is a test in `tests/test_framework.py` unless noted.

1. Fresh directory: `setup` creates no `templates/`, no `docs/checklists/`;
   manifest lists `docs/workflows.md` only; `--preview` on it mentions neither.
2. Project set up by 3.13.0 (13 files + manifest, untouched): one run retires
   all 12, removes both directories, rewrites the manifest without them; second
   run writes nothing and prints no `retired`/`keep` line.
3. Same, with `templates/cycle.md` edited: 11 retired, `cycle.md` kept with the
   `--accept` hint, `templates/` left in place, `docs/checklists/` removed.
   `--accept templates/cycle.md` untracked → refused (not recoverable);
   tracked+clean → removed and `templates/` removed.
4. Pre-manifest project whose files equal the package bytes: retired (package
   match is the provenance). Pre-manifest with different bytes: kept.
5. `templates/` holding an owner's extra file (`templates/mine.md`): tagteam's
   files retired, `mine.md` and the directory untouched.
6. `templates` as a symlink, and `templates/cycle.md` as a symlink: `keep`,
   nothing unlinked through the link, exit status not a refusal.
7. Preimage race: file changed between `build_plan` and `apply` → that path
   `refused: changed since classification`, others retired.
8. `--preview` writes and deletes nothing (tree hash before == after).
9. `needs_setup()` is `False` on a retired project (`tests/test_setup.py`).
10. Bench prompt contains the package checklist when the project has none, and
    the project's when it has one (`tests/test_bench*.py`).
11. Item 8's output on this repo is in the impl submission.
12. Gate: full suite green via `on_submit`.

## Open question for the arbiter
**Automatic or flagged?** This plan retires on every plain `setup` / `upgrade`,
with `--preview` as the look-before. The alternative is `--retire` (nothing is
removed unless asked; a plain run prints `retire available: 12 file(s)`), which
costs one more sweep per release and leaves the files in every project whose
owner never passes it. The plan takes automatic because the deletion is limited
to bytes tagteam can prove it wrote and still ships. Say so if you want the flag.
