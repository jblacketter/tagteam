#!/usr/bin/env python3
"""Isolated `tagteam upgrade` smoke (Phase 36 release gate). Stdlib only.

    python scripts/upgrade_smoke.py --project DIR [--sentinel DIR]
        [--python EXE] [--expect-version V] [--preview] [--json]

Bare `tagteam upgrade` reads ~/.tagteam/projects.json, prunes it and re-runs
setup over EVERY registered project. This harness is the only permitted
way to exercise an upgrade in the Phase 36 recipes:

* the helper runs in a fresh process — `<--python> -I -c <helper>` with cwd
  in a temporary directory outside the repository (isolated mode: no user
  site, no PYTHONPATH, no cwd on sys.path — the source checkout cannot
  shadow the target interpreter's installed package);
* the helper prints an identity line first (sys.executable, sys.prefix,
  tagteam.__version__, resolved tagteam.__file__) and waits for `go`; the
  parent verifies the executable resolves to the selected interpreter and,
  with --expect-version, that the version matches and the package lives
  under that interpreter's prefix, before sending `go`;
* the helper then patches `tagteam.registry.REGISTRY_DIR` / `REGISTRY_FILE`
  to a temporary registry listing exactly one entry (--project), asserts
  fail-closed that both resolve under the temporary directory, and only
  then calls `tagteam.cli.upgrade_command()`;
* the parent snapshots (existence + sha256) the real registry file, every
  managed destination in every real registered project, the disposable
  project and the sentinel before and after, and exits 2 on any change
  outside --project, on any identity mismatch, or if the helper names any
  other path.

--preview runs `tagteam upgrade --preview` instead (Phase 52): the helper
classifies and reports but writes nothing, so a preview must exit 0.

Exit 0: isolation held and --project is unchanged (a no-op upgrade).
Exit 1: isolation held but --project changed (diff reported).
Exit 2: identity / isolation failure — something outside the sandbox moved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# What tagteam.setup.main can write — or, for the retired templates/ and
# docs/checklists/ (Phase 61), delete — inside a project (kept in sync with
# tagteam/framework.py; whole subtrees are snapshotted so new files show up).
MANAGED_SUBTREES = (".claude/skills", "templates", "docs/checklists")
MANAGED_FILES = ("docs/workflows.md", "docs/roadmap.md", "docs/decision_log.md",
                 "tagteam-manifest.json", "AGENTS.md", "CLAUDE.md")
MANAGED_DIRS = (".claude/skills", "docs/phases", "docs/handoffs", "docs/escalations", "docs/checklists", "templates")

HELPER = r'''
import json, sys, pathlib
import tagteam
sys.stdout.write(json.dumps({
    "executable": sys.executable, "prefix": sys.prefix,
    "version": getattr(tagteam, "__version__", None),
    "file": str(pathlib.Path(tagteam.__file__).resolve()),
}) + "\n"); sys.stdout.flush()
if sys.stdin.readline().strip() != "go":
    sys.exit(3)
import tagteam.registry as reg
tmp_root = pathlib.Path(sys.argv[1]).resolve()
reg.REGISTRY_DIR = pathlib.Path(sys.argv[2])
reg.REGISTRY_FILE = pathlib.Path(sys.argv[3])
rd, rf = reg.REGISTRY_DIR.resolve(), reg.REGISTRY_FILE.resolve()
if not (rd.is_relative_to(tmp_root) and rf.is_relative_to(tmp_root)):
    sys.stdout.write("FAIL-CLOSED: registry globals not under %s (%s, %s)\n" % (tmp_root, rd, rf))
    sys.exit(4)
rp = getattr(reg, "registry_path", None)
if rp is not None and pathlib.Path(rp()).resolve() != rf:
    sys.stdout.write("FAIL-CLOSED: registry_path() does not resolve to the temporary registry\n")
    sys.exit(4)
from tagteam.cli import upgrade_command
sys.stdout.write("REGISTRY: %s\n" % rf); sys.stdout.flush()
extra = ["--preview"] if len(sys.argv) > 4 and sys.argv[4] == "preview" else []
try:
    sys.exit(upgrade_command(extra))
except TypeError:            # pre-3.13 package: no arguments
    sys.exit(upgrade_command())
'''


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_tree(root: Path) -> dict[str, str]:
    """existence + sha256 of every file under root ('dir:' entries for dirs)."""
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            out["dir:" + rel] = ""
        elif p.is_file():
            out[rel] = _sha(p)
    return out


def snapshot_managed(project: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for sub in MANAGED_SUBTREES:
        for k, v in snapshot_tree(project / sub).items():
            out[f"{sub}/{k}"] = v
    for f in MANAGED_FILES:
        p = project / f
        if p.is_file():
            out[f] = _sha(p)
    for d in MANAGED_DIRS:
        if (project / d).is_dir():
            out["dir:" + d] = ""
    return out


def _diff(a: dict, b: dict) -> list[str]:
    keys = sorted(set(a) | set(b))
    out = []
    for k in keys:
        if k not in a:
            out.append(f"+ {k}")
        elif k not in b:
            out.append(f"- {k}")
        elif a[k] != b[k]:
            out.append(f"~ {k}")
    return out


def _real_registry() -> tuple[Path | None, list[str]]:
    """The real registry path and entries, read WITHOUT mutation."""
    try:
        import tagteam.registry as reg
    except Exception:
        return None, []
    path = Path(reg.registry_path()) if hasattr(reg, "registry_path") else Path(reg.REGISTRY_FILE)
    entries: list[str] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries = [str(x) for x in data]
        except (OSError, ValueError):
            entries = []
    return path, entries


REPO = Path(__file__).resolve().parents[1]


def _pyproject_version(repo: Path) -> str | None:
    """The version `pyproject.toml` declares, or None if it cannot be read."""
    try:
        text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"\'')
    return None


def stale_egg_info(repo: Path = REPO) -> list[str]:
    """Problems caused by build metadata left in the checkout.

    `uv build` / `pip wheel` leave a `*.egg-info/` behind. When the repo root
    is on `sys.path` — which it is for anything run with the checkout as its
    working directory — that directory is discovered as an installed
    distribution, so `importlib.metadata.version("tagteam")` can answer with
    its stale `Version:` instead of the one the checkout declares. The
    symptoms show up far from the cause (a manifest written with one version
    and re-read with another, an unexplained `project_diff`), so name it here
    rather than let it surface as a diff.
    """
    declared = _pyproject_version(repo)
    if declared is None:
        return []
    problems: list[str] = []
    for egg in sorted(repo.glob("*.egg-info")):
        pkg_info = egg / "PKG-INFO"
        try:
            lines = pkg_info.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        found = next((l.split(":", 1)[1].strip()
                      for l in lines if l.startswith("Version:")), None)
        if found is not None and found != declared:
            problems.append(
                f"stale build metadata: {egg.name}/PKG-INFO says version "
                f"{found!r} but pyproject.toml declares {declared!r}. "
                f"Remove it (rm -rf {egg.name} build dist) and re-run; while "
                f"it is there, importlib.metadata can report either version "
                f"depending on the working directory."
            )
    return problems


def run(project: Path, sentinel: Path | None, python: str, expect_version: str | None,
        preview: bool = False) -> tuple[int, dict]:
    report: dict = {"project": str(project), "sentinel": str(sentinel) if sentinel else None,
                    "python": python, "expect_version": expect_version, "preview": preview,
                    "problems": []}
    project = project.resolve()
    if not project.is_dir():
        report["problems"].append(f"--project is not a directory: {project}")
        return 2, report
    if sentinel is not None:
        sentinel = sentinel.resolve()
        if not sentinel.is_dir():
            report["problems"].append(f"--sentinel is not a directory: {sentinel}")
            return 2, report
        if sentinel == project or project.is_relative_to(sentinel) or sentinel.is_relative_to(project):
            report["problems"].append("--sentinel must be unrelated to --project")
            return 2, report

    # ---- before snapshots (real registry, real projects, project, sentinel)
    real_reg_path, real_entries = _real_registry()
    real_reg_bytes = real_reg_path.read_bytes() if real_reg_path and real_reg_path.is_file() else None
    real_before = {e: snapshot_managed(Path(e)) for e in real_entries if Path(e).is_dir()}
    proj_before = snapshot_tree(project)
    sent_before = snapshot_tree(sentinel) if sentinel else {}

    tmp = Path(tempfile.mkdtemp(prefix="tagteam-upgrade-smoke-")).resolve()
    reg_dir = tmp / "registry"
    reg_dir.mkdir()
    reg_file = reg_dir / "projects.json"
    reg_file.write_text(json.dumps([str(project)], indent=2) + "\n", encoding="utf-8")
    cwd = tmp / "cwd"
    cwd.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSTARTUP")}

    proc = subprocess.Popen(
        [python, "-I", "-c", HELPER, str(tmp), str(reg_dir), str(reg_file),
         "preview" if preview else "apply"],
        cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    assert proc.stdin and proc.stdout
    ident_line = proc.stdout.readline()
    try:
        ident = json.loads(ident_line)
    except ValueError:
        proc.kill()
        _o, err = proc.communicate()
        report["problems"].append(f"helper did not print an identity line: {ident_line!r} {err.strip()[-400:]}")
        return 2, report
    report["helper"] = ident

    # ---- identity checks, before `go`
    problems: list[str] = list(stale_egg_info())
    try:
        same_exe = Path(ident["executable"]).resolve() == Path(python).resolve() or os.path.samefile(ident["executable"], python)
    except OSError:
        same_exe = False
    if not same_exe:
        problems.append(f"helper executable {ident['executable']!r} is not the selected interpreter {python!r}")
    if expect_version is not None:
        if ident.get("version") != expect_version:
            problems.append(f"imported tagteam version {ident.get('version')!r} != expected {expect_version!r}")
        try:
            under = Path(ident["file"]).resolve().is_relative_to(Path(ident["prefix"]).resolve())
        except (OSError, KeyError):
            under = False
        if not under:
            problems.append(f"imported tagteam at {ident.get('file')!r} is not under the interpreter prefix {ident.get('prefix')!r}")
    if problems:
        try:
            proc.stdin.write("abort\n"); proc.stdin.flush()
        except OSError:
            pass
        proc.communicate(timeout=30)
        report["problems"] = problems
        return 2, report

    proc.stdin.write("go\n"); proc.stdin.flush()
    out, err = proc.communicate(timeout=600)
    report["helper_exit"] = proc.returncode
    report["helper_stdout"] = out
    report["helper_stderr"] = err[-2000:]

    # ---- after snapshots + checks
    real_reg_after = real_reg_path.read_bytes() if real_reg_path and real_reg_path.is_file() else None
    if real_reg_after != real_reg_bytes:
        problems.append(f"REAL REGISTRY CHANGED: {real_reg_path}")
    for e, before in real_before.items():
        if e == str(project):
            continue
        d = _diff(before, snapshot_managed(Path(e)))
        if d:
            problems.append(f"REAL PROJECT CHANGED: {e}: {d[:5]}")
    # Paths the helper VISITED: `Project: <p>` (upgrade_command) and
    # `Target: <p>` (setup.main). `Source: <data dir>` is the package's own
    # data directory and is deliberately not a visit.
    visited = [line.split(": ", 1)[1].strip() for line in out.splitlines()
               if line.startswith("Project: ") or line.startswith("Target: ")]
    for v in visited:
        if v != str(project):
            problems.append(f"helper visited an unexpected path: {v}")
    if sentinel is not None:
        d = _diff(sent_before, snapshot_tree(sentinel))
        if d:
            problems.append(f"SENTINEL CHANGED: {sentinel}: {d[:5]}")
        s = str(sentinel)
        if any(v == s or v.startswith(s + os.sep) for v in visited) or (s + os.sep) in out or out.rstrip().endswith(s):
            problems.append("helper output names the sentinel")
    for e in real_entries:
        if e != str(project) and any(v == e or v.startswith(e + os.sep) for v in visited):
            problems.append(f"helper visited a real registered project: {e}")
    try:
        after_entries = json.loads(reg_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        after_entries = None
    report["temp_registry_after"] = after_entries
    if after_entries != [str(project)]:
        problems.append(f"temporary registry no longer lists exactly the project: {after_entries!r}")
    if proc.returncode != 0:
        problems.append(f"helper exited {proc.returncode}")
    proj_diff = _diff(proj_before, snapshot_tree(project))
    report["project_diff"] = proj_diff
    if preview and proj_diff:
        problems.append(f"PREVIEW WROTE: {proj_diff[:5]}")
    report["problems"] = problems
    if problems:
        return 2, report
    return (1 if proj_diff else 0), report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="upgrade_smoke.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--sentinel", type=Path, default=None)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the helper (default: this one)")
    ap.add_argument("--expect-version", default=None, help="require this imported tagteam version and a package under the interpreter prefix")
    ap.add_argument("--preview", action="store_true", help="run `tagteam upgrade --preview` (must write nothing)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    code, report = run(args.project, args.sentinel, args.python, args.expect_version, args.preview)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        h = report.get("helper") or {}
        print(f"helper: {h.get('executable')}  prefix={h.get('prefix')}")
        print(f"tagteam: {h.get('version')} at {h.get('file')}")
        for p in report["problems"]:
            print(f"PROBLEM: {p}")
        if "project_diff" in report:
            print("project diff: " + ("(none — no-op)" if not report["project_diff"] else ", ".join(report["project_diff"])))
        print({0: "OK: isolation held, project unchanged", 1: "isolation held, project CHANGED", 2: "FAILED"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
