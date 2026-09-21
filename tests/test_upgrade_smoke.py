"""Phase 36: scripts/upgrade_smoke.py — the isolated upgrade harness.

* the real registry (if any) is never touched, an unrelated sentinel is
  neither visited nor changed, the temporary registry lists exactly one
  entry, and a source-set-up project upgrades as a no-op;
* the helper runs under the interpreter selected with --python, in
  isolated mode from a cwd outside the repo, and reports its identity
  before any registry call — a same-named package in the checkout cannot
  shadow the target interpreter's installed one, and a version mismatch
  refuses to proceed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "upgrade_smoke.py"


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def _real_registry_path() -> Path:
    from tagteam import registry
    return Path(registry.registry_path())


def _setup_project_isolated(project: Path, tmp: Path) -> None:
    """Run tagteam.setup.main with the registry globals patched (same rule as
    the harness): the real registry never sees the disposable project."""
    from tagteam import registry, setup as tsetup
    reg_dir = tmp / "setup-registry"
    old = (registry.REGISTRY_DIR, registry.REGISTRY_FILE)
    registry.REGISTRY_DIR, registry.REGISTRY_FILE = reg_dir, reg_dir / "projects.json"
    try:
        assert registry.REGISTRY_FILE.resolve().is_relative_to(tmp.resolve())
        project.mkdir(parents=True, exist_ok=True)
        (project / "tagteam.yaml").write_text("agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n", encoding="utf-8")
        tsetup.main(str(project))
    finally:
        registry.REGISTRY_DIR, registry.REGISTRY_FILE = old
    assert json.loads((reg_dir / "projects.json").read_text(encoding="utf-8")) == [str(project.resolve())]


def _run(args: list[str]) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, str(HARNESS), "--json", *args], capture_output=True, text=True, encoding="utf-8", cwd=str(REPO))
    try:
        rep = json.loads(r.stdout)
    except ValueError:
        rep = {"raw": r.stdout, "stderr": r.stderr}
    return r.returncode, rep


def test_upgrade_smoke_isolated(tmp_path):
    project = tmp_path / "disposable"
    _setup_project_isolated(project, tmp_path)
    sentinel = tmp_path / "unrelated"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("do not touch\n", encoding="utf-8")
    real = _real_registry_path()
    real_before = _sha(real)

    code, rep = _run(["--project", str(project), "--sentinel", str(sentinel)])
    assert code == 0, rep
    assert rep["problems"] == []
    assert rep["project_diff"] == []                                   # source upgrade over source setup: no-op
    assert rep["temp_registry_after"] == [str(project.resolve())]     # exactly one entry, still
    assert str(sentinel.resolve()) not in rep["helper_stdout"]
    assert sorted(p.name for p in sentinel.iterdir()) == ["keep.txt"]
    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "do not touch\n"
    assert _sha(real) == real_before                                   # independent of the harness's own check
    # identity line: the helper is this interpreter and imported this checkout's package
    h = rep["helper"]
    assert Path(h["executable"]).resolve() == Path(sys.executable).resolve()
    import tagteam
    assert Path(h["file"]) == Path(tagteam.__file__).resolve()
    assert h["version"] == tagteam.__version__
    # the visited path is the disposable project only
    visited = [l.split(": ", 1)[1].strip() for l in rep["helper_stdout"].splitlines() if l.startswith(("Project: ", "Target: "))]
    assert visited and set(visited) == {str(project.resolve())}


def test_upgrade_smoke_detects_project_change_without_breaking_isolation(tmp_path, monkeypatch):
    """A project that is NOT a no-op is reported as exit 1 with the diff,
    while every isolation check still holds. Since Phase 52 the customised
    file itself is kept — what changes is the manifest, which drops the
    entry for bytes that are no longer tagteam's."""
    monkeypatch.setenv("TAGTEAM_CLAUDE_BIN", "")
    project = tmp_path / "stale"
    _setup_project_isolated(project, tmp_path)
    template = project / "docs" / "workflows.md"
    template.write_text("- Lead: Claude\n", encoding="utf-8")
    # a preview of the project writes nothing at all
    code, rep = _run(["--project", str(project), "--preview"])
    assert code == 0, rep
    assert rep["problems"] == [] and rep["project_diff"] == []
    assert "keep     docs/workflows.md" in rep["helper_stdout"]
    assert "would be written (preview" in rep["helper_stdout"]
    # the apply keeps the file and rewrites only the manifest
    code, rep = _run(["--project", str(project)])
    assert code == 1, rep
    assert rep["problems"] == []
    assert rep["project_diff"] == ["~ tagteam-manifest.json"]
    assert template.read_text(encoding="utf-8") == "- Lead: Claude\n"
    assert "keep     docs/workflows.md" in rep["helper_stdout"]


STUB_INIT = textwrap.dedent('''
    __version__ = "9.9.9"
''')
STUB_REGISTRY = textwrap.dedent('''
    from pathlib import Path
    REGISTRY_DIR = Path.home() / ".tagteam"
    REGISTRY_FILE = REGISTRY_DIR / "projects.json"
    def registry_path():
        return REGISTRY_FILE
    def read_registry_raw():
        import json
        return json.loads(REGISTRY_FILE.read_text()) if REGISTRY_FILE.exists() else []
''')
STUB_CLI = textwrap.dedent('''
    import json
    from pathlib import Path
    def upgrade_command():
        from tagteam import registry
        entries = json.loads(Path(registry.REGISTRY_FILE).read_text())
        for e in entries:
            print("Project: " + e)
            Path(e, "STUB-VISITED").write_text("stub upgrade ran\\n")
        return 0
''')


@pytest.fixture(scope="module")
def stub_venv(tmp_path_factory) -> dict:
    """A real venv (no pip, no network) with a same-named stub `tagteam`
    package copied into its purelib."""
    root = tmp_path_factory.mktemp("stubvenv")
    vdir = root / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=(os.name != "nt")).create(str(vdir))
    py = vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = subprocess.run([str(py), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    pkg = Path(purelib) / "tagteam"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(STUB_INIT, encoding="utf-8")
    (pkg / "registry.py").write_text(STUB_REGISTRY, encoding="utf-8")
    (pkg / "cli.py").write_text(STUB_CLI, encoding="utf-8")
    return {"python": str(py), "prefix": str(vdir), "pkg": pkg}


def test_harness_selects_target_interpreter_and_cannot_be_shadowed(stub_venv, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    # launched from the checkout cwd; the checkout has a real `tagteam` package right here
    code, rep = _run(["--project", str(project), "--python", stub_venv["python"], "--expect-version", "9.9.9"])
    assert code == 1, rep            # 1 = isolation held, project changed (the stub writes a marker on purpose)
    assert rep["problems"] == []
    assert rep["project_diff"] == ["+ STUB-VISITED"]
    h = rep["helper"]
    assert Path(h["executable"]).resolve() == Path(stub_venv["python"]).resolve()
    assert Path(h["file"]).resolve() == (stub_venv["pkg"] / "__init__.py").resolve()   # the venv's stub, not the checkout
    assert Path(h["file"]).resolve().is_relative_to(Path(h["prefix"]).resolve())
    assert h["version"] == "9.9.9"
    assert (project / "STUB-VISITED").exists()                     # the stub's upgrade_command ran...
    assert "REGISTRY: " in rep["helper_stdout"]                     # ...against the temporary registry
    reg_line = [l for l in rep["helper_stdout"].splitlines() if l.startswith("REGISTRY: ")][0]
    assert "tagteam-upgrade-smoke-" in reg_line
    assert rep["temp_registry_after"] == [str(project.resolve())]


def test_harness_refuses_wrong_version_before_any_call(stub_venv, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    code, rep = _run(["--project", str(project), "--python", stub_venv["python"], "--expect-version", "3.0.0"])
    assert code == 2, rep
    assert any("9.9.9" in p and "3.0.0" in p for p in rep["problems"])
    assert not (project / "STUB-VISITED").exists()                 # `go` was never sent
    assert "helper_stdout" not in rep


def test_harness_default_interpreter_reports_checkout_package(tmp_path):
    project = tmp_path / "proj"
    _setup_project_isolated(project, tmp_path)
    code, rep = _run(["--project", str(project)])
    assert code == 0, rep
    import tagteam
    assert Path(rep["helper"]["file"]) == Path(tagteam.__file__).resolve()
    # installed-wheel mode would refuse an editable checkout (file outside the prefix) — prove the guard exists
    code2, rep2 = _run(["--project", str(project), "--expect-version", tagteam.__version__])
    under = Path(tagteam.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    if not under:
        assert code2 == 2 and any("not under the interpreter prefix" in p for p in rep2["problems"])
    else:   # a non-editable CI install: identity passes and the run is a no-op
        assert code2 == 0


# ---------------------------------------------------------------------------
# Phase 52: the built distribution, not the source tree
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def wheel_venv(tmp_path_factory) -> dict:
    """The checkout built into a wheel and installed (with its dependencies)
    into a fresh venv. Skips, with the reason, when the build, the venv or
    the offline install is not possible here — never silently."""
    root = tmp_path_factory.mktemp("wheelvenv")
    wheels = root / "wheels"
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "-q", "-w", str(wheels), str(REPO)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"environmental: wheel build failed: {r.stderr.strip()[-300:]}")
    whl = sorted(wheels.glob("tagteam-*.whl"))
    if not whl:
        pytest.skip("environmental: pip wheel produced no tagteam wheel")
    vdir = root / "venv"
    try:
        venv.EnvBuilder(with_pip=True, symlinks=(os.name != "nt")).create(str(vdir))
    except Exception as e:      # ensurepip missing, etc.
        pytest.skip(f"environmental: venv with pip unavailable: {e.__class__.__name__}: {e}")
    py = vdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run([str(py), "-m", "pip", "install", "-q", "--no-index", "--find-links", str(wheels),
                        str(whl[-1])], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"environmental: offline install into the venv failed: {r.stderr.strip()[-300:]}")
    version = subprocess.run([str(py), "-I", "-c", "import tagteam; print(tagteam.__version__)"],
                             capture_output=True, text=True, check=True).stdout.strip()
    return {"python": str(py), "version": version, "wheel": str(whl[-1])}


def _old_project(root: Path) -> dict[str, bytes]:
    """Old Claude-lead scaffolding: a hand-written template (custom — it is
    no release's rendering), an untouched one (package bytes — Phase 61
    retires it), an exact ≤3.11.0 rendering with this project's names (Phase
    62 retires it, which also proves data/history/ is in the wheel), a
    customised checklist, a current workflows.md, real history. Returns the
    bytes that must survive untouched."""
    from tagteam import setup as tsetup
    data = tsetup.get_data_dir()
    root.mkdir(parents=True)
    (root / "tagteam.yaml").write_text("agents:\n  lead:\n    name: claude\n  reviewer:\n    name: codex\n", encoding="utf-8")
    (root / "templates").mkdir()
    (root / "templates" / "phase_plan.md").write_text("# Phase\n\n## Roles\n- Lead: Claude\n- Reviewer: Codex\n", encoding="utf-8")
    (root / "templates" / "cycle.md").write_bytes((data / "templates" / "cycle.md").read_bytes())
    era = (data / "history" / "v3.11.0" / "templates" / "feedback.md").read_text(encoding="utf-8")
    assert "{{lead}}" in era or "{{reviewer}}" in era
    (root / "templates" / "feedback.md").write_text(
        era.replace("{{lead}}", "claude").replace("{{reviewer}}", "codex"), encoding="utf-8")
    (root / "docs" / "checklists").mkdir(parents=True)
    (root / "docs" / "checklists" / "code_review.md").write_bytes(
        (data / "checklists" / "code_review.md").read_bytes() + b"\n- [ ] our extra check\n")
    (root / "docs" / "workflows.md").write_bytes((data / "workflows.md").read_bytes())
    (root / "docs" / "roadmap.md").write_text("# Roadmap\n\n### Phase 1: X\n- **Status:** done\n", encoding="utf-8")
    (root / "docs" / "decision_log.md").write_text("# Decisions\n\n- kept\n", encoding="utf-8")
    (root / "docs" / "handoffs").mkdir()
    (root / "docs" / "handoffs" / "x_plan_rounds.jsonl").write_text('{"round": 1}\n', encoding="utf-8")
    (root / "CLAUDE.md").write_text("# ours\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "old"], check=True)
    keep = ["templates/phase_plan.md", "docs/checklists/code_review.md", "docs/roadmap.md",
            "docs/decision_log.md", "docs/handoffs/x_plan_rounds.jsonl", "CLAUDE.md"]
    return {k: (root / k).read_bytes() for k in keep}


@pytest.mark.parametrize("plugin", ["absent", "present"])
def test_installed_wheel_migrates_old_project(wheel_venv, tmp_path, monkeypatch, plugin):
    from tests._plugin_env import fake_plugin, no_cli
    if plugin == "present":
        fake_plugin(tmp_path, monkeypatch)          # TAGTEAM_CLAUDE_BIN → fake `claude`, inherited by the helper
    else:
        no_cli(monkeypatch)                          # no claude executable at all
    project = tmp_path / "old"
    keep = _old_project(project)
    common = ["--project", str(project), "--python", wheel_venv["python"], "--expect-version", wheel_venv["version"]]

    # preview: writes nothing, says what it would do
    code, rep = _run([*common, "--preview"])
    assert code == 0, rep
    assert rep["problems"] == [] and rep["project_diff"] == []
    out = rep["helper_stdout"]
    # rep["problems"] == [] already proved the helper imported the wheel under
    # the venv prefix at the expected version (the harness's identity check).
    assert rep["helper"]["version"] == wheel_venv["version"]
    assert "keep     templates/phase_plan.md — no longer managed; differs from the package" in out
    assert "keep     docs/checklists/code_review.md — no longer managed; differs from the package" in out
    assert "retire   templates/cycle.md — no longer installed; matches the package" in out
    assert ("retire   templates/feedback.md — no longer installed; written by tagteam ≤3.11.0 "
            "(rendered for the configured names)") in out
    assert "create   templates/" not in out and "create   docs/checklists/" not in out
    assert "would be written (preview" in out

    # apply: only framework paths move; custom content and history survive
    code, rep = _run(common)
    assert code == 1, rep                            # 1 = isolation held, project changed
    assert rep["problems"] == []
    diff = rep["project_diff"]
    assert "+ tagteam-manifest.json" in diff and "- templates/cycle.md" in diff
    assert "- templates/feedback.md" in diff
    assert not any(d.startswith(("+ templates/", "+ docs/checklists/")) for d in diff), diff
    assert not any(d.startswith("~ ") for d in diff), diff       # nothing existing was modified
    assert ("+ .claude/skills/handoff/SKILL.md" in diff) == (plugin == "absent")
    for rel, data in keep.items():
        assert (project / rel).read_bytes() == data, rel
    out = rep["helper_stdout"]
    assert "keep     templates/phase_plan.md" in out and "keep     docs/checklists/code_review.md" in out
    assert "retired  templates/cycle.md" in out
    assert f"delete with: tagteam setup {project.resolve()} --accept templates/phase_plan.md" in out
    manifest = json.loads((project / "tagteam-manifest.json").read_text(encoding="utf-8"))
    assert manifest["tagteam"] == wheel_venv["version"]
    assert "templates/phase_plan.md" not in manifest["files"] and "docs/checklists/code_review.md" not in manifest["files"]
    assert manifest["files"]["docs/workflows.md"]["tagteam"] == wheel_venv["version"]     # adopted current file
    assert (".claude/skills/handoff/SKILL.md" in manifest["files"]) == (plugin == "absent")

    # retry: byte-identical no-op
    code, rep = _run(common)
    assert code == 0, rep
    assert rep["problems"] == [] and rep["project_diff"] == []
    assert "All 1 project(s) upgraded successfully." in rep["helper_stdout"]


# ---------------------------------------------------------------------------
# Stale build metadata in the checkout
# ---------------------------------------------------------------------------

def _load_smoke_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_upgrade_smoke", REPO / "scripts" / "upgrade_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_checkout(tmp_path: Path, declared: str, egg_version: str | None) -> Path:
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "tagteam"\nversion = "{declared}"\n', encoding="utf-8")
    if egg_version is not None:
        egg = repo / "tagteam.egg-info"
        egg.mkdir()
        (egg / "PKG-INFO").write_text(
            f"Metadata-Version: 2.1\nName: tagteam\nVersion: {egg_version}\n",
            encoding="utf-8")
    return repo


class TestStaleEggInfo:
    """`uv build` leaves `tagteam.egg-info/` in the checkout. With the repo
    root on `sys.path`, that is a discoverable distribution, so the version
    `importlib.metadata` answers with can depend on the working directory.
    The symptoms surface far from the cause, so the harness names it."""

    def test_disagreeing_egg_info_is_reported(self, tmp_path):
        mod = _load_smoke_module()
        repo = _fake_checkout(tmp_path, "3.12.0", "3.13.0.dev0")
        problems = mod.stale_egg_info(repo)
        assert len(problems) == 1
        p = problems[0]
        assert "stale build metadata" in p
        assert "tagteam.egg-info" in p
        assert "'3.13.0.dev0'" in p and "'3.12.0'" in p
        assert "rm -rf" in p          # the remedy, not just the diagnosis

    def test_agreeing_egg_info_is_not_a_problem(self, tmp_path):
        mod = _load_smoke_module()
        repo = _fake_checkout(tmp_path, "3.12.0", "3.12.0")
        assert mod.stale_egg_info(repo) == []

    def test_no_egg_info_is_not_a_problem(self, tmp_path):
        mod = _load_smoke_module()
        repo = _fake_checkout(tmp_path, "3.12.0", None)
        assert mod.stale_egg_info(repo) == []

    def test_unreadable_pyproject_is_not_a_problem(self, tmp_path):
        """No pyproject to compare against: say nothing rather than guess."""
        mod = _load_smoke_module()
        repo = tmp_path / "bare"
        repo.mkdir()
        assert mod.stale_egg_info(repo) == []

    def test_this_checkout_is_clean(self):
        """The repo the suite runs from must not carry stale metadata — this
        is the assertion that would have caught the original failure."""
        mod = _load_smoke_module()
        assert mod.stale_egg_info(REPO) == []


def test_installed_wheel_version_comes_from_metadata_not_a_source_tree(wheel_venv):
    """Phase 64: a wheel install has no pyproject.toml beside the package, so
    `__version__` takes the fallback path — checked against the version in the
    wheel's own filename and in pyproject.toml, not against another read of
    `__version__`."""
    import re
    code = ("import json, tagteam, importlib.metadata as m; "
            "print(json.dumps({'tree': tagteam._source_tree_version(), 'version': tagteam.__version__, "
            "'metadata': m.version('tagteam'), 'file': tagteam.__file__}))")
    out = json.loads(subprocess.run([wheel_venv["python"], "-I", "-c", code],
                                    capture_output=True, text=True, check=True).stdout)
    built = re.match(r"tagteam-([^-]+)-", Path(wheel_venv["wheel"]).name).group(1)
    declared = re.search(r'^version[ \t]*=[ \t]*"([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M).group(1)
    assert out["tree"] is None                                   # no source tree → fallback taken
    assert out["version"] == out["metadata"] == built == declared
    assert str(REPO) not in out["file"]                          # imported from the venv, not the checkout
    r = subprocess.run([wheel_venv["python"], "-I", "-m", "tagteam", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.splitlines()[0] == f"tagteam {built}"
