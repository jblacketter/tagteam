# tagteam issue: a project cannot opt out of templates and checklists

Observed 2026-09-20 on the `linkedin-articles` project, tagteam 3.13.0.
Written by the lead session (claude) at Greg's request. Everything below was
read from the package source (`tagteam/framework.py`, `tagteam/bench.py`) and
from that project's tree; the one thing not exercised is marked.

## What happened

`linkedin-articles` is a prose project. Its first cycle (`tagteam-article`,
impl, approved at round 2) reviewed an article, a feed post and a facts table.
There is no code, no test suite and no gatekeeper.

After the cycle Greg asked for a cleanup of the tagteam scaffolding in that
repo. Of the 13 framework files `tagteam setup` installs, the project uses one:

| Path | Files | Used there |
|---|---|---|
| `docs/workflows.md` | 1 | yes, `CLAUDE.md` and `AGENTS.md` both point at it |
| `templates/*.md` | 10 | no |
| `docs/checklists/*.md` | 2 | no (`code_review.md` asks about XSS and migrations) |

`tagteam doctor` reported 0 findings and `tagteam setup --preview` reported
"current 13 file(s) match the package", so nothing was stale. The 12 unused
files are simply noise in a repo whose whole content is a handful of articles.

I did not delete them, because deleting does not hold.

## Why deleting does not hold

`framework._sources()` lists every `templates/*.md`, every `checklists/*.md`
and `workflows.md` from the package unconditionally. `_classify()` maps a
missing path to `ABSENT`, and the module docstring states the action:
`absent -> create`. `tagteam upgrade` runs the same plan over every registered
project.

So a project that deletes `templates/` gets all ten files back on the next
`tagteam upgrade` sweep or `tagteam setup`. Not exercised: I did not delete
and re-run to watch it happen, since the files there are untracked and there
would be no git undo. The code path is unambiguous.

There is also no configuration for it. `tagteam.yaml` has no key that affects
which framework files are managed, and `setup` takes only `--no-plugin`,
`--preview`, `--accept` and `--force`.

This is the mirror image of the legacy-skill-drift lesson. That phase fixed
setup deleting or overwriting what it did not write. This is setup
re-creating what the project deliberately removed. Both are the tool
overriding a decision the project owner made about their own tree.

## What actually reads these files

- `templates/*.md`: nothing in `tagteam/*.py` reads them at runtime. The only
  references are `framework.py`/`setup.py` (install) and `migrate.py` (backup
  of a legacy `templates/`). The 3.13.0 contract does not mention them.
  Several describe the pre-cycle flow (`handoff_plan.md`, `handoff_impl.md`,
  `feedback.md`, `sync_state.md`) that `cycle add` replaced.
- `docs/checklists/*.md`: `bench.py:599` appends the matching checklist to the
  bench review prompt, guarded by `is_file()`. A missing checklist is already
  handled; the bench prompt just goes without one.
- `docs/workflows.md`: load-bearing. The seeded `CLAUDE.md` and `AGENTS.md`
  tell agents to read it.

So for every installed version, removing templates and checklists is safe at
runtime. Only setup objects, by putting them back.

## Recommendation

One small, opt-in key in `tagteam.yaml`, honoured by `build_plan()`:

```yaml
framework:
  skip:
    - templates
    - checklists
```

Behaviour:

1. `_sources()` output is filtered by group before classification. A skipped
   group produces no items, so nothing is created, refreshed or reported for
   it, and `--preview` prints one line: `skipped (tagteam.yaml): templates,
   checklists`.
2. Accepted group names are `templates` and `checklists` only.
   `workflows.md` is not skippable while the seeded instruction files point at
   it. An unknown name is a warning, not an error.
3. Skipping never deletes. Files already on disk stay where they are and
   simply stop being managed; the owner removes them by hand. This keeps the
   "setup may only delete what setup wrote, and only when told" rule intact.
4. `projected_manifest()` drops entries for skipped groups, so the manifest
   keeps meaning "what tagteam manages here" and `doctor`'s
   `current N · none M` line stays truthful.
5. Default is unchanged. No key, no behaviour change, no migration.

Groups rather than per-path globs: two names cover the whole request, and a
glob language is a feature nobody has asked for.

## Deliberately not recommended

- **Detecting "prose project" automatically** (no test command, no code).
  Guessing project type is how the tool ends up overriding the owner again.
- **A `tagteam setup --prune` that deletes skipped files.** Deletion of
  untracked files has no undo. The owner can `rm -r templates` once.
- **Remembering deletions** (treating "in manifest, now absent" as intent).
  Tempting because it needs no config, but it makes an accidental `rm`
  permanent and silent, and a fresh clone without the manifest behaves
  differently from the original checkout. An explicit key is visible in review.

## A separate question this raised

Whether the ten templates should ship at all. Four of them describe the
pre-cycle manual flow (hand-written handoff, feedback and sync documents),
nothing reads any of them, and `upgrade` installs them into every registered
project (41 as of this date; I did not check each tree). That is a bigger call than this issue (it changes the
default for everyone), so it is noted here and not folded into the
recommendation. If most of them go, the `skip` key shrinks to checklists only
and may not be worth building.

## Acceptance sketch

- With `framework.skip: [templates]`, `setup --preview` on a fresh directory
  lists no `templates/*` items and says the group was skipped.
- With the key set on a project that already has `templates/`, a run writes
  nothing under `templates/`, deletes nothing, and the manifest afterwards
  has no `templates/*` entries.
- With the key set and `templates/` removed by hand, a second run creates
  nothing. `doctor` reports 0 findings.
- `framework.skip: [workflows]` warns and is ignored.
- No key: the existing setup/upgrade tests pass unchanged.
