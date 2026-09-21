# Phase 65: Hardening: shadow installs, quiet upgrades, clean test runs

## Status
- [x] Planning: approved round 1 (2026-09-20) at `79de8a1`
- [x] Implementation: branch `phase/hardening-shadow-installs-quiet-upgrades-clean-test-runs`
- [x] Implementation Review: approved round 2 (2026-09-20) at `df0db7f`; gate 2,142 passed, 5 skipped. Round 1: two reviewer findings in `installs.py` (duplicate `Version` headers counted only when valid; METADATA read without the guarded reader).
- [x] Complete: PR #49 merged 2026-09-20 (rebase).

## Closeout
```
Phase report: hardening-shadow-installs-quiet-upgrades-clean-test-runs — plan approved r1 · impl approved r2
  plan   1 round · 0 change requests · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 15m 24s
  time   start→approve 26m 41s · implementation before first submit 5m 19s
         lead 2m 33s (1 span, 2 unknown) · reviewer 3m 24s (3 spans) · gate 15m 25s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 6 · no token data 0 · unmatched 4 · unknown 2
```
Criterion 5: after both full-suite gate runs `git status --short --ignored`
showed no `build/`, `dist/` or `tagteam.egg-info/` (the reviewer confirmed the
same). The round-1 fixes added `tagteam/safe_read.py` — doctor's guarded
bounded reader moved verbatim so `installs` shares it without importing
`diagnostics`.

**On release (3.14.3):** no project files change; note that `tagteam doctor` /
`tagteam state` now report a tagteam in the project's `.venv`, and that an
upgrade which changes no framework file no longer rewrites
`tagteam-manifest.json`.

## Summary
Four small, independent fixes, all observed on 2026-09-20 and logged in
`docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md` (issues 1, 5, 4,
10). The arbiter asked for them as one hardening phase. None changes the
handoff contract.

1. **A project venv's tagteam shadows the CLI and nothing says so.** Six venvs
   under `~/projects` carried tagteam 0.5.0 – 3.13.0 while the CLI on PATH was
   current; one (`northstar-test-automation`, 3.12.0) still does. Anything run
   through such a venv (`.venv/bin/tagteam`, `python -m tagteam`, a watcher
   started from it) is the old code — for a pre-3.14 copy that means `setup`
   re-creates the retired `templates/` and `docs/checklists/`. It was found
   with a `find`, not by tagteam.
2. **Every version bump rewrites every project's manifest.** The 3.14.1 sweep
   rewrote `tagteam-manifest.json` in all 41 projects; for a release that
   changes no framework file the only difference is the top-level `"tagteam"`
   stamp (`_same_manifest()` ignores `written_at` but not the version). Each
   release therefore dirties every tracked manifest for nothing.
3. **The suite dirties the checkout.** `tests/test_upgrade_smoke.py`'s
   `wheel_venv` fixture runs `pip wheel` on the repo itself, which leaves
   `build/` and `tagteam.egg-info/` behind on every full run. A stale
   `egg-info` is the documented cause of misleading failures; they were
   cleared by hand nine times on 2026-09-20.
4. **A flake that cannot explain itself.** `test_watcher_lock.py::
   test_start_watcher_reports_refusal_as_already_running` failed once in a
   gate run (a spawned watcher had not taken its lock after 20 s) and passed
   every other time. `_start_watch()` already pipes the child's output — and
   nothing ever reads it, so the cause could not be established.

## Scope
**In:**

1. **Shadow installs** (`tagteam/diagnostics.py`, `tagteam/framework.py`).
   - New `observe_installs(root) -> list[dict]`: for `<root>/.venv` and
     `<root>/venv`, lstat-only like the rest of doctor (no subprocess, no
     import, never follows a symlinked venv directory — a symlinked one is
     reported as `not scanned: symlink`): find
     `lib/python*/site-packages/tagteam-*.dist-info` (and Windows
     `Lib/site-packages`), take the version from its `METADATA` `Version:`
     line, and mark it `editable` when a `__editable__*tagteam*` file sits
     beside it (an editable link runs whatever tree it points at; since Phase
     64 its label is not the truth, so it is reported as `editable`, never as a
     mismatch).
   - Skipped when that venv is the interpreter running doctor
     (`Path(sys.prefix).resolve()` equals it): that is not a shadow, it is us.
   - `doctor` gets a section `Other tagteam installs` and one finding per
     mismatching, non-editable copy: severity `warn`, rule `shadow-install`,
     e.g. `.venv has tagteam 3.12.0; running 3.14.2 — .venv/bin/tagteam and
     python -m tagteam from that venv run the older code; upgrade or remove
     it`. Same-version copies and editable links are listed, not warned.
     `--json` gains an additive `installs` key; `SCHEMA` stays 1.
   - `tagteam state`'s `Framework:` line appends ` · .venv: tagteam 3.12.0
     (differs from the running 3.14.2)` when there is a mismatch — one clause,
     nothing when clean.
   - Read-only by construction; allowed under `TAGTEAM_READ_ONLY` already
     (`doctor`, `state`).
2. **A no-change upgrade writes nothing** (`tagteam/framework.py`).
   `_same_manifest()` also ignores the top-level `"tagteam"` stamp, so the
   manifest is rewritten only when a `files` entry changes. The top-level
   value then means "the last tagteam that changed something here", which is
   what `written_at` beside it already means. Per-file `"tagteam"` values —
   the provenance `_classify()` reads — are untouched. `tagteam state` /
   `doctor` print it as they do today.
3. **`wheel_venv` builds from a copy** (`tests/test_upgrade_smoke.py`). The
   fixture copies the tree to its temp dir (ignoring `.git`, `.venv`,
   `build`, `dist`, `*.egg-info`, `.tagteam`, caches) and runs `pip wheel` on
   the copy — uncommitted work is still what gets built. It records whether
   `build/` / `tagteam.egg-info/` existed in the repo beforehand and asserts
   it created neither.
4. **The watcher-lock tests dump the child's output when a wait fails**
   (`tests/test_watcher_lock.py`). A helper used by the waits that watch a
   spawned watcher: on timeout it terminates the child, reads what the pipe
   holds, and fails with the child's exit code and output in the message. No
   timeout is changed and no test is loosened — this makes the next
   occurrence diagnosable, it does not claim to fix it.
5. **Docs:** `CLAUDE.md` (manifest stamp meaning; suite no longer dirties the
   checkout), `tagteam/data/workflows.md` doctor section (shadow installs),
   README doctor line, issues 1, 4, 5 marked fixed and issue 10 marked
   "diagnosable".

**Out:**
- Scanning anything but `<root>/.venv` and `<root>/venv` (no `$VIRTUAL_ENV`,
  no walking the tree, no pyenv / uv-tool inventory). The project's own venv
  is where the incident was.
- Upgrading or removing a shadow install. Report only — the owner decides.
- Fixing the flake itself; its cause is unknown.
- Re-stamping existing manifests. They keep whatever they say.

## Technical Approach
- `observe_installs()` reuses doctor's `_dir_chain` / bounded-read helpers;
  `METADATA` is read with `read_bounded` (small cap), version by
  `^Version:\s*(\S+)`. Malformed or unreadable → listed as `version unknown`,
  no warning (not sure ≠ mismatch).
- `Report.installs`; `counts` includes the shadow warnings because they are
  appended to `findings` with `path` = the dist-info directory, `line` 0.
- `framework.version_line()` takes the mismatch clause from a small shared
  helper so `state` and `doctor` cannot disagree.
- `_same_manifest`: strip `written_at` **and** `tagteam`.
- Tests: `tests/test_diagnostics.py` (fake venv trees: mismatch, same
  version, editable, symlinked venv, malformed METADATA, the running venv
  skipped, Windows layout, `--json` key, `state` clause);
  `tests/test_framework.py` (package version bumped, nothing else changed →
  second run `unchanged`, bytes identical; a real file change still rewrites
  and restamps); smoke + watcher tests as above.

## Files
- `tagteam/diagnostics.py`, `tagteam/framework.py`, `tagteam/state.py` (only if
  the clause cannot live in `version_line`)
- `tests/test_diagnostics.py`, `tests/test_framework.py`,
  `tests/test_upgrade_smoke.py`, `tests/test_watcher_lock.py`
- `CLAUDE.md`, `README.md`, `tagteam/data/workflows.md`, `docs/workflows.md`,
  `docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`, `docs/roadmap.md`

## Success Criteria
1. Fake `.venv` with `tagteam-3.12.0.dist-info` → doctor `warn
   shadow-install`, the section lists it, `counts.warn` ≥ 1, `--json` has
   `installs`; `tagteam state` shows the clause. Same version → listed, no
   warn, no clause. Editable marker → `editable`, no warn. Symlinked `.venv`
   → `not scanned`, nothing read through the link. Malformed `METADATA` →
   `version unknown`, no warn. The venv that is `sys.prefix` → not reported.
2. Doctor stays read-only: tree snapshot identical before/after, and the
   whole thing runs under `TAGTEAM_READ_ONLY=1`.
3. Real check, read-only: `tagteam doctor` in
   `~/projects/northstar/northstar-test-automation` reports its 3.12.0 copy;
   in bugalizer (3.14.1 vs running) whatever is true at the time — both pasted
   into the impl submission.
4. Manifest: setup at package `1.0.0`, then package `2.0.0` with identical
   sources → `Manifest: … unchanged`, file bytes identical, exit 0; with one
   source changed → rewritten, top-level and that entry say `2.0.0`.
   Existing Phase 52/61/62 manifest tests pass unchanged or with their
   expectation updated where they asserted the old restamp — each such change
   listed in the submission.
5. After a full suite run the checkout has no `build/` and no
   `tagteam.egg-info/` it did not have before (asserted in the fixture;
   confirmed by `git status --ignored` after the gate run, reported).
6. A watcher-lock wait that fails prints the child's exit code and output
   (proved with a child that exits immediately); passing tests unchanged in
   behaviour and timing.
7. Gate: full suite green via `on_submit`.

## Implementation notes
- **Deviation from the Technical Approach, smaller than planned:** shadow
  installs are **not** appended to `Report.findings`. That list is the
  "Legacy workflow findings" section and feeds `setup`'s one-line legacy
  summary (`legacy_findings()` / `summary_line()`); a venv copy is neither. They
  live in `Report.installs`, print under their own `Other tagteam installs`
  heading, and `Report.counts["warn"]` adds the `differs` rows — so the
  `findings: N warn` line and `--json` `counts` include them as planned.
- The observer is its own module, `tagteam/installs.py`, importing nothing
  from tagteam but `__version__` — no cycle between `diagnostics` and
  `framework` (plan-approval note).
- Plan-approval notes as built: every level (`lib`, `python*`,
  `site-packages`, the dist-info, `METADATA`) is lstat-checked before it is
  listed or read, with tests for a symlink at each; duplicate / missing /
  body-only / blank `Version:` → `unknown`, never a mismatch; wording is
  "different code", never "older" (a newer copy is reported the same way); the
  manifest stamp is documented as "the version that last changed this
  manifest"; the no-new-artifacts assertion sits in a `finally` so it runs on
  a failed or skipped build; the child dump is bounded (TERM → 5 s → KILL →
  5 s, `communicate(timeout=5)`) and says whether the child had already
  exited, was terminated, or had to be killed.

## Real check (criterion 3) — read-only, 2026-09-20, running 3.14.2
| Project | `doctor` | `state` Framework clause |
|---|---|---|
| northstar-test-automation | `warn .venv/lib/python3.12/site-packages/tagteam-3.12.0.dist-info — tagteam 3.12.0; running 3.14.2 …` | `· .venv: tagteam 3.12.0 (differs from the running 3.14.2)` |
| bugalizer, superdash | same line for their `tagteam-3.14.1.dist-info` | `· .venv: tagteam 3.14.1 (differs …)` |

A patch-level difference is reported too. That is deliberate: the check says
"different code", it does not rank versions.
