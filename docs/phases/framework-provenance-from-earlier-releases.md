# Phase 62: Framework provenance from earlier releases

## Status
- [x] Planning: approved round 1 (2026-09-20) at `76f8f6c`
- [x] Implementation: branch `phase/framework-provenance-from-earlier-releases`
- [x] Implementation Review: approved round 2 (2026-09-20) at `386261b`; gate 2,058 passed, 5 skipped. Round 1 was a gate bounce (shipped-docs audit), not a reviewer change request; the digest-only deviation below was approved explicitly.
- [x] Complete: PR #46 merged 2026-09-20 (rebase; impl is `ce80e03` + `305d82a` on main).

## Closeout
```
Phase report: framework-provenance-from-earlier-releases — plan approved r1 · impl approved r2
  plan   1 round · 0 change requests · 0 bounces
  impl   2 rounds · 0 change requests · 1 bounce · gate 2 runs, 15m 05s
  time   start→approve 23m 48s · implementation before first submit 3m 40s
         lead 2m 29s (1 span, 2 unknown) · reviewer 2m 33s (2 spans) · gate 15m 06s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 5 · no token data 0 · unmatched 3 · unknown 2
```
Reviewer's non-blocking note at approval — say in the user-facing docs that
one earlier source is digest-only — applied to `tagteam/data/workflows.md` in
the closeout commit (docs only, not re-gated).

**After merge:** release 3.14.1, `scripts/upgrade_smoke.py`, then the
registered-project `tagteam upgrade` sweep (expected from the preview:
480 retired, 40 `docs/workflows.md` refreshed, 0 kept).

## Summary
Phase 61 (3.14.0) retires `templates/*.md` and `docs/checklists/*.md` and
removes the copies tagteam can prove it wrote. Run against the real registry
it proves far too little. `tagteam upgrade --preview` on 3.14.0, 41 registered
projects, 2026-09-20 (nothing written):

| Planned action | Count |
|---|---|
| `retire` | 184 |
| `keep` under `templates/` — "no longer managed; differs from the package" | 296 |
| `keep docs/workflows.md` — "differs from the package" | 36 |
| `refresh docs/workflows.md` — manifest hash | 4 |
| `refuse` | 0 |

Three projects come out clean (12 retired each). The other 37 retire 4 files
and keep 8–9 templates, so the sweep would leave a half-emptied `templates/`
in almost every project — the cleanup the arbiter asked for does not happen.

**None of the 300+ kept files is an owner's edit.** Checked by rendering every
version of every framework file found under `tagteam/data/` (and the older
`ai_handoff/data/`) at every git tag with each project's configured agent
names and comparing sha256:

- all **296** kept templates are byte-exact renderings of the one earlier
  source of that template (shipped v0.3.0 – v3.11.0, carrying `{{lead}}` /
  `{{reviewer}}`) with the project's *configured* names — 224 as
  `claude`/`codex`, 72 as `Claude`/`Codex`. Until 3.12.0 `setup` baked the
  names in; since 3.12.0 the package ships `Lead (read current tagteam.yaml)`
  with no placeholder, so `_render_variants()` (which needs `{{` in the
  *current* package file) has nothing to render and the old rendering reads as
  `custom`;
- of the **36** kept `docs/workflows.md`, 35 are verbatim the v0.3.0 – v3.10.0
  file and 1 is verbatim the v3.10.1 – v3.11.0 file. They predate the manifest
  (3.13.0), and the 3.13.0 sweep was manifest-only, so nothing has ever been
  able to vouch for them. These are the pre-plugin workflow docs that still
  name commands which no longer exist — the most misleading stale file of all.

The history is small and closed. Distinct earlier sources, by last tag that
shipped them:

| Last shipped | Files |
|---|---|
| v3.3.0 | `templates/roadmap.md` |
| v3.4.0 | `checklists/code_review.md` |
| v3.10.0 | `workflows.md` |
| v3.11.0 | `workflows.md`; `templates/{cycle,decision_log,feedback,handoff_impl,handoff_plan,implementation_log,phase_plan,sync_state}.md` (the 8 with placeholders) |
| v3.12.0 | `workflows.md` |

13 files, about 31 KB. Retired paths never change again, and every
`workflows.md` from 3.13.0 on is vouched for by the project's manifest, so
this table does not grow.

## Scope
**In:**

1. **Ship the earlier sources** as package data under
   `tagteam/data/history/<last-tag>/<source_rel>` — exactly the 13 files
   above, byte-identical to `git show <last-tag>:tagteam/data/<source_rel>`.
   New glob in `pyproject.toml` `[tool.setuptools.package-data]`
   (`data/history/**/*.md`).
2. **Classification evidence.** `_classify()` gains one step, after the
   manifest-hash and known-contract checks and before `custom`: for each
   earlier source of this item's `source_rel`, the on-disk sha256 equals the
   source verbatim, or the source rendered for the configured / swapped names
   (the existing `_render_variants()`, applied to the historical bytes instead
   of only the current package file) → `framework`,
   `reconstructible = True`, reason
   `written by tagteam ≤3.11.0 (rendered for the configured names)` /
   `… ≤3.10.0`. Nothing else about classification changes.
3. **What follows, with no new action code:**
   - a retired path so classified is `retire`d without a git check — it is
     *reconstructible* in Phase 61's sense: the installed package reproduces
     the bytes exactly (source + the project's own names), which is the
     distinction the Phase 61 reviewer asked for;
   - `docs/workflows.md` so classified is `refresh`ed, as a manifest-hash or
     known-contract match already is.
4. **One mechanism, not two.** A sha256 table (the
   `vendored_contract_hashes.json` pattern) would be smaller for the verbatim
   `workflows.md` cases but cannot cover the templates — their bytes depend on
   each project's agent names — and would make the overwritten `workflows.md`
   bytes provenance-only. Shipping sources covers both and keeps every
   automatic overwrite/delete in this phase reproducible from the package.
5. **Phase 61 follow-up, same function family** (reviewer's non-blocking note,
   roadmap backlog "Retired-directory prune"): `_prune_retired_dirs()` stops
   swallowing `OSError`. `ENOTEMPTY`/`EEXIST` → silent (the `keep` lines
   already say what is in it); anything else → one report line
   `  kept     templates/ — directory left in place (PermissionError)`. Not a
   refusal, exit code unchanged. Backlog entry closed.
6. **Docs:** `tagteam/data/workflows.md` "Framework files and upgrades" and
   README "Safe migration" / "Retired files": copies written by any earlier
   release — including ones rendered with the project's agent names — are
   recognised as tagteam's. `CLAUDE.md` conventions: `data/history/` is frozen provenance —
   never edit, never add for a version ≥ 3.13.0.

**Out:**
- Names that changed since the render (a project set up as `claude`/`codex`
  and later renamed): renders with neither configured nor swapped names match,
  the file stays `custom`, `--accept` deletes it. No guessing at "common"
  names.
- A generator script / release-time regeneration / `publish.yml` check. The
  table is closed; a test pins it (criterion 1) instead.
- The vendored skill — already covered by `known_contract_hashes()`.
- Seeds (`docs/roadmap.md`, `docs/decision_log.md`, `AGENTS.md`, `CLAUDE.md`):
  the owner's from the first write, untouched.
- Running the registry sweep and releasing 3.14.1 — release chores after merge.

## Technical Approach
`tagteam/framework.py`:

- `_history_sources(data_dir, source_rel) -> list[tuple[str, bytes]]`:
  `(tag, bytes)` for every `data_dir/history/<tag>/<source_rel>` that is a
  regular file, newest tag first (sorted by parsed version, not lexically:
  `v3.10.0` > `v3.4.0`). Missing `history/` → `[]` (evidence absent means
  `custom`, the safe side).
- `_classify(item, package, root, known, history)`: `build_plan()` passes
  `_history_sources(data_dir, src_rel)` for managed and retired items, `[]`
  for the skill. Per source: verbatim, then `_render_variants(bytes, root)`.
  `_render_variants` is unchanged — it already returns `[]` when the bytes
  carry no `{{`, and reads the names from the project's `tagteam.yaml`.
- Reason text names the last tag (`≤3.11.0`), so a report line tells the owner
  which era the file is from.
- `_prune_retired_dirs()`: `errno` check per Scope item 5; `Plan.pruned_dirs`
  stays, new `Plan.unpruned_dirs: list[tuple[str, str]]`.

No change to `apply()`, `projected_manifest()`, `setup.py`, `bench.py`.

## Files
- `tagteam/framework.py`
- `tagteam/data/history/**` (13 new files), `pyproject.toml` (glob)
- `tests/test_framework.py`; `tests/test_upgrade_smoke.py`
  (`_old_project`'s rendered `phase_plan.md` is no longer `custom` — it is the
  exact case this phase exists for; its expectations flip from `keep` to
  `retire`, and a genuinely edited template is added to keep the `keep` path
  covered through the wheel)
- `tagteam/data/workflows.md`, `README.md`, `CLAUDE.md`, `docs/roadmap.md`

## Success Criteria
1. **The table is what git says it is.** For each of the 13 files,
   bytes == `git show <tag>:tagteam/data/<source_rel>`; and for each
   framework `source_rel`, the set {current package sha} ∪ {history shas}
   equals the set of distinct shas across all `v*` tags ≤ v3.12.0 ∪ current.
   Skips (not fails) when the checkout has no tags (shallow CI clone).
2. Rendered-era project: 8 placeholder templates rendered `claude`/`codex`,
   no manifest, not a git repo → all 8 `retire`d with the `≤3.11.0` reason,
   `templates/` removed. Same for `Claude`/`Codex`, and for swapped roles.
3. Names changed since the render → `keep`, `--accept` hint, nothing deleted.
4. One byte edited in a rendered template → `keep`.
5. `docs/workflows.md` verbatim from v3.10.0, from v3.11.0 and from v3.12.0,
   pre-manifest → `refresh`ed to the package; the manifest records it. With
   one line edited → `keep`, as today.
6. Old verbatim `templates/roadmap.md` (v3.3.0) and
   `docs/checklists/code_review.md` (v3.4.0) → `retire`d.
7. `history/` absent or unreadable → behaviour identical to 3.14.0 (every
   such file `custom`), no exception.
8. Wheel check: the 13 files are in the built wheel
   (`tests/test_upgrade_smoke.py` wheel venv: `_history_sources` non-empty
   from the installed package).
9. `_prune_retired_dirs`: not-empty → silent; `PermissionError` → the `kept`
   line, exit 0, manifest unaffected.
10. **The real registry, preview only** (writes nothing): after the change,
    `tagteam upgrade --preview` tallies are pasted into the impl submission.
    Expected: the 296 template `keep`s become `retire`; the 36 workflows
    `keep`s become `refresh`; any remaining `keep` is listed by path with its
    reason.
11. Gate: full suite green via `on_submit`.

## Open question for the arbiter
**Overwriting the 36 old `docs/workflows.md` automatically.** They are
provably tagteam's pre-plugin text and provably unedited, and refreshing
provably-tagteam bytes is what `setup` already does for a manifest or
known-contract match — so the plan refreshes them. In projects where the file
is tracked it shows up as a modification to commit. Say so if you would rather
these were reported and left for `--accept`.

## Implementation notes (from the plan approval)
- `tests/test_upgrade_smoke.py::_old_project` keeps its hand-written
  `phase_plan.md` as `custom` (it is no release's rendering); an exact ≤3.11.0
  rendering of `feedback.md` with the project's names was added for the
  installed-wheel `retire` assertion — which is also the proof that
  `data/history/` ships in the wheel.
- Manifest-first order is unchanged: a manifest hash match is decided before
  historical evidence, so such a copy keeps Phase 61's git rule
  (`test_manifest_match_is_still_decided_first`). Not every earlier copy is
  retired: renamed agents or any edit → `custom`.
- The git-pinned table test covers the older `ai_handoff/data/` prefix and
  skips only on a checkout without release tags; CI uses `fetch-depth: 0`.

## Deviation from the approved plan (impl round 1 gate bounce)
Scope item 4 chose "sources, not a sha table". The gate bounced on
`tests/test_plugin.py::TestShippedDocsAudit::test_no_legacy_command_family`
(Phase 49: nothing tagteam ships may mention the dead `/handoff-*` family):
`history/v3.10.0/workflows.md` — the pre-plugin doc — has 45 such lines. The
other 12 sources are clean. Rather than exempt `data/history/` from the audit,
that one file ships as `v3.10.0/workflows.md.sha256`. `_history_sources()`
returns `(tag, sha, bytes | None)`; a digest-only match is `framework` but
**not** `reconstructible`. For `docs/workflows.md` that changes nothing — a
refresh needs provenance only, as a manifest or known-contract match does — so
the 35 real copies are still refreshed. A digest-only match on a *retired*
path would fall to Phase 61's git rule by construction; none exists. Cost: the
overwritten v3.10.0 text is not reproducible from the package (it is in every
release ≤ 3.10.0 on PyPI and in git).

## Registry preview (criterion 10) — `tagteam upgrade --preview`, 41 projects, nothing written
| | 3.14.0 | this branch |
|---|---|---|
| `retire` | 184 | **480** |
| `keep` | 332 | **0** |
| `refresh docs/workflows.md` | 4 | **41** |
| `refuse` | 0 | 0 |

Reasons on this branch: 296 `written by tagteam ≤3.11.0 (rendered for the
configured names)`, 184 `matches the package`; workflows: 35 `≤3.10.0`,
1 `≤3.11.0`, 5 by manifest hash (3.13.0).
