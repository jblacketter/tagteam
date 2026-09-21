# Issues seen during the 3.14.0 / 3.14.1 releases, the registry sweep and the project-venv upgrades

Logged 2026-09-20 by the lead session (claude) at the arbiter's request. Each
item says what was observed and how; suggestions are marked as such. Nothing
here is scheduled.

## What was done
- 3.14.0 (Phase 61) and 3.14.1 (Phase 62) released; registry sweep run on
  3.14.1: 41 projects, 480 files retired, 40 `docs/workflows.md` refreshed,
  0 kept, 0 refused; second run a no-op.
- Project venvs carrying their own tagteam, upgraded to 3.14.1 and verified by
  importing from `/tmp` (code version, metadata version, module path) and by
  `python -m tagteam setup --preview` through each venv (`current 1 file(s)`,
  manifest unchanged):

  | Project | Before | How |
  |---|---|---|
  | superdash | 3.13.0 | `uv pip install --python .venv/bin/python tagteam==3.14.1` (venv has no pip) |
  | bugalizer | 3.12.0 | `uv lock --upgrade-package tagteam` + `uv pip install …` (venv has no pip) |
  | github-profile | 3.7.0 | `.venv/bin/python -m pip install tagteam==3.14.1` |
  | jobs/scratch | 0.7.1 | same |
  | rankr | label 0.5.0 | already an editable link to this repo (so it was running current code); re-stamped with `pip install -e ~/projects/tagteam --no-deps` |

- **Deliberately not touched:** `northstar/northstar-test-automation/.venv`
  still has tagteam **3.12.0** (regular copy). Until it is upgraded, anything
  run through that venv (`.venv/bin/tagteam`, `python -m tagteam`) is pre-Phase-61
  code: its `setup` would re-create `templates/` and `docs/checklists/` there,
  and its `needs_setup()` considers the project not set up (no `templates/`),
  so `session start` / `quickstart` via that venv would run setup. The uv-tool
  `tagteam` on PATH is unaffected.

## Issues

### 1. A project venv's tagteam silently shadows the uv tool — FIXED in Phase 65 (reported by `doctor` / `state`)
Six venvs under `~/projects` carried tagteam 0.5.0 – 3.13.0 while the CLI on
PATH was current. Nothing reports this; it was found with
`find … -name "tagteam-*.dist-info"`. None of the five upgraded projects
except bugalizer declares tagteam as a dependency — the copies were installed
by hand at some point and forgotten.
*Suggestion:* `tagteam doctor` (and the `Framework:` line of `tagteam state`)
could report a `tagteam` found in `<project>/.venv` whose version differs from
the running one. Read-only, one line.

### 2. Editable installs keep the version they were installed at — FIXED in Phase 64
Seen three times today: the uv tool ran 3.14.0 code while reporting 3.13.0 —
the 3.14.0 preview sweep printed `package 3.13.0`, and a real sweep would have
stamped 41 manifests with the wrong version; this repo's `.venv` reported
3.12.0 (2 `test_upgrade_smoke` failures, identical on a clean tree —
environmental); rankr's link reported 0.5.0.
*Suggestion:* `scripts/release.py` could end by printing the two refresh
commands (`.venv/bin/python -m pip install -e . --no-deps`,
`uv tool install --force --editable <repo>`), or `package_version()` could
prefer the source tree's version when the import is editable.

### 3. This repo's `.venv/bin/pip` has a dead shebang
`.venv/bin/pip: bad interpreter: /Users/…/projects/ai-handoff/.venv/bin/python3`
— the venv predates the repo rename. `.venv/bin/python -m pip` works.
*Suggestion:* recreate the venv once.

### 4. The suite leaves `build/` and `tagteam.egg-info/` in the checkout — FIXED in Phase 65
Every full run (the wheel build in `tests/test_upgrade_smoke.py`), and every
`pip install -e` / `uv tool install --editable`, drops them. They are
git-ignored, but a stale `egg-info` is the documented cause of misleading
`upgrade_smoke` failures. Cleared by hand five times today.
*Suggestion:* build the test wheel from a copy of the tree, or remove the two
directories in the fixture's teardown.

### 5. Every version bump rewrites every project's manifest — FIXED in Phase 65
The sweep rewrote `tagteam-manifest.json` in all 41 projects although in
later runs only the top-level `"tagteam"` version changed (`_same_manifest()`
ignores `written_at` but not the version). Each release therefore dirties every
tracked manifest even when no file changed.
*Suggestion:* decide whether the top-level version means "last package that
ran here" (current behaviour) or "last package that changed something"; the
second would make a no-change upgrade a true no-op.

### 6. 36 repositories now have uncommitted framework changes
From the sweep: tracked deletions under `templates/` / `docs/checklists/`,
modified `docs/workflows.md`, modified or new `tagteam-manifest.json`.
Committing them is the owner's. Includes both Northstar repos
(`northstar-test-automation` 14 paths, `clearpath-cloud` 1), QA and both sonic
repos. bugalizer additionally has `uv.lock` modified (tagteam 3.12.0 → 3.14.1)
from today's upgrade, and already had uncommitted `templates/*` edits from an
earlier sweep before today.

### 7. Small things
- ~~`tagteam --version` → `Unknown command: --version`; the version is only
  visible via `tagteam state` or `uv tool list`.~~ Fixed in Phase 64.
- `scripts/upgrade_smoke.py --expect-version X` always fails against the
  editable checkout ("not under the interpreter prefix") — by design, it is for
  a wheel venv, but the release recipe does not say so.
- The hand-written GitHub Release must exist before `publish.yml` reaches its
  `Create GitHub Release` step or the generated notes win; today that was a
  several-minute window after the tag push, which is comfortable but manual.
- The Claude Code plugin still reads 3.13.0 (installed from the local
  marketplace). The contract did not change between 3.13.0 and 3.14.1, so
  behaviour is identical; only the label lags.
- Three registered projects have a `templates/` that is their own
  (Liminal, techpacker, screen_work). The never-recursive rule left them
  alone, as intended — noted because the directory name collides with what
  tagteam used to install.

### 8. The roadmap `tagteam setup` seeds is invalid out of the box — FIXED in Phase 63
Reproduced on 3.14.1 in an empty directory: run the framework plan (seeds
`docs/roadmap.md` from `data/templates/roadmap.md`), then `tagteam roadmap check`:

```
roadmap invalid (2 problem(s)):
  - duplicate slug 'name': Phase 1, Phase 2, Phase 3
  - name: depends on itself
```

The seed has three `### Phase N: [Name]` headings (all slug `name`) and
Phase 3 carries `- **Depends on:** Phase 2`, which resolves to the same slug.
Found because 8 of the 41 registered projects still have the unedited seed and
report exactly this (agent-gate, agent-ledger, jobs/demoapp,
northstar/clearpath-cloud, screen_work, skill-forge, token-economy,
token-mint); linkedin-articles reports both lines. It does not affect
ordinary `/tagteam:handoff` cycles; it breaks `roadmap ready` / `queue` and
full-roadmap mode until the owner renames the phases.
*Suggestion:* seed distinct placeholder titles (`[First phase]`, …) or treat
bracketed placeholder headings as "not a phase yet" in `roadmap check`. A code
change — needs a cycle.

### 9. Other roadmap findings from the per-project pass (2026-09-20)
- Free text in `Depends on:` is read as phase references. Fixed by hand in
  bugalizer (`Phase 8 (credential code), Dan's token …` → `Phase 8` + a
  `Needs:` line) and designwing (`homepage-fixes and about-page-fixes
  complete` → `Phase 11, Phase 12`). The error message names the unknown
  dependency but not the line; a line number would have saved a grep.
- `northstar-test-automation`: `duplicate phase number 7` and `8` — two phases
  share each number. Not touched (arbiter: leave that project for now).

### 10. Flaky under load: `test_start_watcher_reports_refusal_as_already_running` — DIAGNOSABLE since Phase 65 (cause still unknown)
2026-09-20, Phase 64 impl round 2 gate: 1 failed / 2,111 passed —
`tests/test_watcher_lock.py:376`, `assert _wait(lambda: not _lock_free(project))`:
the externally spawned `tagteam watch --mode notify` had not taken the project
lock after the 20 s wait. The round's diff touched only `tagteam/__init__.py`
and `tests/test_version.py`. Not reproduced: the test passed 6/6 alone (0.5 s
each) and the whole file passed 16/16 immediately afterwards; the same test had
passed in the four earlier full-suite gate runs that day. Load average at the
time was ~5.5 (the reviewer agent and the repo's own watcher were running).
Classified as timing, **cause not established** — the spawned watcher's output
is not captured, so there is nothing to read after the fact.
*Suggestion:* have `_start_watch` keep the child's stdout/stderr and print it
when the wait fails, so the next occurrence says whether the child was slow,
refused, or crashed.
