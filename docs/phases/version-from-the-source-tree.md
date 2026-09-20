# Phase 64: Version from the source tree

## Status
- [ ] Planning
- [ ] Implementation: branch `phase/version-from-the-source-tree` (stacked on `phase/roadmap-placeholders-are-not-phases` until PR #47 merges; to be rebased onto `main` then)
- [ ] Implementation Review
- [ ] Complete

## Summary
`tagteam.__version__` is `importlib.metadata.version("tagteam")`. For an
**editable** install that metadata is written once, at install time, and never
again — while the code it points at moves with every commit. Observed three
times on 2026-09-20 (issue 2 in
`docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`):

- the arbiter's CLI (`uv tool install --editable`) ran 3.14.0 code and
  reported `package 3.13.0`. The 3.14.0 registry preview printed that, and a
  real sweep in that state would have stamped all 41 projects'
  `tagteam-manifest.json` with the wrong version — a manifest's `tagteam`
  field is provenance, and `_classify()` reasons from it;
- this repo's `.venv` reported 3.12.0 for a 3.13.0 tree: two
  `tests/test_upgrade_smoke.py` failures, identical on a clean checkout,
  diagnosed as environmental only after a bounce (2026-09-15) and again today;
- rankr's editable link reported 0.5.0 while importing current code.

The workaround — re-run `pip install -e` / `uv tool install --force
--editable` after every version bump — is in the release notes-to-self and was
still missed. Also: `tagteam --version` answers `Unknown command: --version`,
so the quickest way to ask "which tagteam is this?" does not exist.

## Scope
**In:**

1. **`__version__` prefers the source tree.** In `tagteam/__init__.py`: if
   `Path(__file__).parent.parent / "pyproject.toml"` exists **and** its
   `[project]` table declares `name = "tagteam"`, `__version__` is that file's
   `version`. Otherwise — every wheel / sdist install, where no such file sits
   next to the package — it is `importlib.metadata.version("tagteam")` exactly
   as today, with today's `0.0.0+unknown` fallback.
   - Read with a small regex over the `[project]` table, not `tomllib`
     (`requires-python` is 3.10; `tomllib` is 3.11+). The release script
     already edits the same line with a regex, so the two agree on its shape.
   - Any failure to read or parse → fall back to metadata. Importing tagteam
     must never raise because of this.
   - The `name` check is what keeps an unrelated `pyproject.toml` that happens
     to sit above a `site-packages/tagteam/` from being believed.
2. **`tagteam --version`** (also `-V`, `version`): prints
   `tagteam X.Y.Z` and, on a second line, `  <directory the package was
   imported from>` — the second line is the answer to "which copy is this",
   the question behind every incident above. Exit 0. Listed in `HELP_TEXT`.
3. **Tests** (`tests/test_version.py`, new):
   - in this checkout, `tagteam.__version__ == ` the version in
     `pyproject.toml` — regardless of what dist-info the venv holds (the test
     that would have caught all three incidents);
   - a temp copy of the package under `site-packages`-like layout with **no**
     `pyproject.toml` beside it → metadata path (monkeypatched) is used;
   - a `pyproject.toml` beside it naming a *different* project → ignored;
   - unreadable / malformed `pyproject.toml` → metadata, no exception;
   - `tagteam --version`, `-V`, `version` → exit 0, first line
     `tagteam <__version__>`, second line an existing directory.
   - installed-wheel check (existing `wheel_venv` fixture in
     `tests/test_upgrade_smoke.py`): `__version__` from the wheel equals the
     built version — the fallback path, exercised for real.
4. **Docs:** `CLAUDE.md` release-flow note (the editable-metadata trap no
   longer affects what tagteam reports or writes; `uv tool list` / `pip list`
   still show the install-time number — cosmetic); README command list gains
   `--version`; issue 2 and the `--version` bullet of issue 7 marked fixed.

**Out:**
- Refreshing editable metadata automatically, or having `scripts/release.py`
  do it. With item 1 nothing tagteam does depends on it any more.
- `scripts/upgrade_smoke.py`'s `stale_egg_info()` precondition. It stays: a
  stale `*.egg-info` in the checkout still confuses `importlib.metadata` for
  *other* readers (pip, uv), and the check costs nothing.
- Detecting a project `.venv` whose tagteam differs from the running one
  (issue 1), the suite leaving `build/` + `egg-info` behind (issue 4), the
  manifest rewritten on every bump (issue 5) — the next hardening phase.

## Technical Approach
```python
def _source_tree_version() -> str | None:
    try:
        text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    project = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    ...name == "tagteam" → version
```
- `__version__ = _source_tree_version() or <metadata as today>`.
- `resolve()` so an editable finder that maps the package by path still finds
  the real tree.
- `cli.main()`: handle `--version` / `-V` / `version` next to the help flags.
- No change to `framework.package_version()`, `hook.py`, `server.py`: they
  read `__version__` and inherit the fix.

## Files
- `tagteam/__init__.py`, `tagteam/cli.py`
- `tests/test_version.py` (new), `tests/test_upgrade_smoke.py`
- `CLAUDE.md`, `README.md`, `docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`, `docs/roadmap.md`

## Success Criteria
1. Checkout: `__version__` == `pyproject.toml` version, with the venv's
   dist-info deliberately at a different version in the test (monkeypatched
   metadata returning `"0.0.1"`).
2. No `pyproject.toml` beside the package → metadata value; foreign-named
   `pyproject.toml` → metadata value; malformed / unreadable → metadata value,
   no exception; metadata missing too → `0.0.0+unknown`.
3. Installed wheel: `__version__` == the built version.
4. `tagteam --version` / `-V` / `version`: exit 0, `tagteam X.Y.Z`, then the
   import directory.
5. Proof on the real thing, read-only: from `/tmp`, the arbiter's uv-tool
   `tagteam --version` and `tagteam state`'s `Framework: package …` agree with
   `pyproject.toml` — reported in the impl submission with the uv tool's
   (stale or not) metadata version beside it.
6. Gate: full suite green via `on_submit`.
