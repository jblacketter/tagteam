"""Phase 52: safe framework migration — the fixtures the plan enumerates.

Classification, manifest bootstrap/adoption, --accept / --force /
recoverability, the skill and legacy-flat-skill rules, preimage re-checks
and unsupported filesystem shapes. Everything in-process and fast; the
wheel-installed run is in test_upgrade_smoke.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tagteam import framework as fw
from tagteam import registry as registry_mod
from tagteam import setup as su
from tests._plugin_env import fake_plugin, no_cli

DATA = su.get_data_dir()
SKILL = fw.SKILL_REL
PACKAGED_SKILL = (DATA / ".claude" / "skills" / "handoff" / "SKILL.md").read_bytes()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def proj(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    no_cli(monkeypatch)
    p = tmp_path / "proj"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    return p


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True)


def commit_all(root: Path) -> None:
    if not (root / ".git").exists():
        git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "snap", "--allow-empty")


def snapshot(root: Path, *, skip_manifest: bool = False) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        if skip_manifest and rel == fw.MANIFEST_NAME:
            continue
        if p.is_symlink():
            out[rel] = ("link", os.readlink(p))
        elif p.is_dir():
            out[rel] = "dir"
        else:
            out[rel] = p.read_bytes()
    return out


def fresh(root: Path, **kw) -> int:
    return su.main(str(root), report_user_skills=False, **kw)


def run(root: Path, *, accept=(), force=False, preview=False, no_plugin=False, capsys=None) -> tuple[int, str]:
    if capsys:
        capsys.readouterr()                     # drop anything earlier calls printed
    code = su.main(str(root), report_user_skills=False, accept=accept, force=force,
                   preview=preview, no_plugin=no_plugin)
    return code, (capsys.readouterr().out if capsys else "")


def manifest(root: Path) -> dict:
    return json.loads((root / fw.MANIFEST_NAME).read_text())


def replace_source(monkeypatch, tmp_path: Path, rel: str, new_bytes: bytes) -> None:
    """Make the package ship different bytes for one managed path (a newer
    package version) without touching the real data dir."""
    real = fw._sources
    alt = tmp_path / "alt-src"; alt.mkdir(exist_ok=True)

    def patched(data_dir):
        out = []
        for r, src, src_rel in real(data_dir):
            if r == rel:
                f = alt / Path(src_rel).name; f.write_bytes(new_bytes); src = f
            out.append((r, src, src_rel))
        return out
    monkeypatch.setattr(fw, "_sources", patched)


# ---------------------------------------------------------------------------
# fresh project / preview / idempotency
# ---------------------------------------------------------------------------

def test_fresh_setup_creates_everything_and_writes_manifest(proj, capsys):
    assert fresh(proj) == 0
    out = capsys.readouterr().out
    assert (proj / "docs" / "workflows.md").read_bytes() == (DATA / "workflows.md").read_bytes()
    assert not (proj / "templates").exists() and not (proj / "docs" / "checklists").exists()   # Phase 61
    assert (proj / SKILL).read_bytes() == PACKAGED_SKILL
    assert (proj / "docs" / "roadmap.md").exists() and (proj / "AGENTS.md").exists()
    m = manifest(proj)
    assert m["schema"] == 1 and m["tagteam"] == fw.package_version()
    assert set(m["files"]) == {i.rel for i in fw.build_plan(proj, data_dir=DATA).items if i.kind in ("file", "skill")}
    for rel, e in m["files"].items():
        assert e["sha256"] == fw.sha256_bytes((proj / rel).read_bytes()) and e["tagteam"] == fw.package_version()
    assert "created  docs/workflows.md" in out and "templates/" not in out and "Manifest: tagteam-manifest.json written" in out
    assert not su.needs_setup(str(proj))
    assert json.loads((registry_mod.REGISTRY_FILE).read_text()) == [str(proj.resolve())]


def test_second_run_writes_nothing(proj, capsys):
    fresh(proj)
    before = snapshot(proj)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and snapshot(proj) == before
    assert "Manifest: tagteam-manifest.json unchanged" in out and "created" not in out


def test_preview_writes_nothing_and_reports(proj, capsys):
    (proj / "templates").mkdir()
    (proj / "templates" / "phase_plan.md").write_text("- Lead: Claude\n")
    before = snapshot(proj)
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 0 and snapshot(proj) == before
    assert "preview (nothing will be written)" in out
    assert "create   docs/workflows.md" in out
    assert f"keep     templates/phase_plan.md — no longer managed; differs from the package; delete with: tagteam setup {proj.resolve()} --accept templates/phase_plan.md" in out
    assert "would be written (preview — nothing written)" in out
    assert not (proj / fw.MANIFEST_NAME).exists() and not (proj / "docs").exists()


def test_old_rendered_templates_are_custom_and_kept(proj, capsys):
    (proj / "templates").mkdir()
    old = "# Phase\n\n## Roles\n- Lead: Claude\n- Reviewer: Codex\n"
    (proj / "templates" / "phase_plan.md").write_text(old)
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert (proj / "templates" / "phase_plan.md").read_text() == old
    assert "keep     templates/phase_plan.md" in out
    assert "templates/phase_plan.md" not in manifest(proj)["files"]


def test_render_variant_counts_as_framework(proj, monkeypatch, tmp_path):
    """Pre-manifest evidence: the file equals the package rendered for the
    configured (or swapped) names → an older tagteam wrote it → refresh."""
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"Lead: {{lead}} / Reviewer: {{reviewer}}\n")
    (proj / "docs").mkdir()
    (proj / "docs" / "workflows.md").write_bytes(b"Lead: B / Reviewer: A\n")     # swapped names
    plan = fw.build_plan(proj, data_dir=DATA)
    it = next(i for i in plan.items if i.rel == "docs/workflows.md")
    assert it.cls == fw.FRAMEWORK and "swapped names" in it.reason and it.action == "refresh"


# ---------------------------------------------------------------------------
# --accept / --force / recoverability
# ---------------------------------------------------------------------------

def test_accept_requires_tracked_and_clean(proj, capsys):
    fresh(proj)
    t = proj / "docs" / "workflows.md"
    t.write_text("mine\n")
    # not a git repository
    code, out = run(proj, accept=["docs/workflows.md"], capsys=capsys)
    assert code == 1 and t.read_text() == "mine\n"
    assert "refused  docs/workflows.md — not recoverable: not a git repository (use --force)" in out
    # untracked
    git(proj, "init", "-q")
    code, out = run(proj, accept=["docs/workflows.md"], capsys=capsys)
    assert code == 1 and "not recoverable: untracked" in out
    # uncommitted changes
    commit_all(proj); t.write_text("mine again\n")
    code, out = run(proj, accept=["docs/workflows.md"], capsys=capsys)
    assert code == 1 and "not recoverable: uncommitted changes" in out and t.read_text() == "mine again\n"
    # tracked and clean → overwritten, entry recorded
    commit_all(proj)
    code, out = run(proj, accept=["docs/workflows.md"], capsys=capsys)
    assert code == 0 and "accepted docs/workflows.md" in out and "(tracked and clean)" in out
    assert t.read_bytes() == (DATA / "workflows.md").read_bytes()
    assert manifest(proj)["files"]["docs/workflows.md"]["sha256"] == fw.sha256_bytes(t.read_bytes())


def test_force_lifts_recoverability_only(proj, capsys):
    fresh(proj)
    t = proj / "docs" / "workflows.md"; t.write_text("mine\n")
    other = proj / SKILL; other.write_text("also mine\n")
    code, out = run(proj, accept=["docs/workflows.md"], force=True, capsys=capsys)
    assert code == 0
    assert "--force: recoverability refusals lifted" in out
    assert "accepted docs/workflows.md" in out and "; --force)" in out
    assert t.read_bytes() == (DATA / "workflows.md").read_bytes()
    assert other.read_text() == "also mine\n" and f"keep     {SKILL}" in out   # never widened


def test_unknown_accept_is_refused(proj, capsys):
    fresh(proj)
    code, out = run(proj, accept=["docs/workflows.md", "nope/x.md"], capsys=capsys)
    assert code == 1
    assert "refused  --accept docs/workflows.md — not a custom managed path" in out
    assert "refused  --accept nope/x.md — not a custom managed path" in out


# ---------------------------------------------------------------------------
# vendored skill and legacy flat skills
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("via", ["plugin-absent", "no-plugin-flag"])
def test_customised_skill_kept_when_vendoring(proj, tmp_path, monkeypatch, capsys, via):
    fresh(proj)
    mine = PACKAGED_SKILL + b"\n# local rule\n"
    (proj / SKILL).write_bytes(mine)
    if via == "no-plugin-flag":
        fake_plugin(tmp_path, monkeypatch)
    code, out = run(proj, no_plugin=(via == "no-plugin-flag"), capsys=capsys)
    assert code == 0 and (proj / SKILL).read_bytes() == mine
    assert f"keep     {SKILL} — modified since tagteam" in out
    assert SKILL not in manifest(proj)["files"]
    # accepted when tracked-clean
    commit_all(proj)
    code, out = run(proj, accept=[SKILL], no_plugin=(via == "no-plugin-flag"), capsys=capsys)
    assert code == 0 and (proj / SKILL).read_bytes() == PACKAGED_SKILL and f"accepted {SKILL}" in out


def test_known_contract_plus_extra_file_when_vendoring(proj, monkeypatch, capsys):
    old = b"# an older vendored contract\n"
    monkeypatch.setattr(fw, "known_contract_hashes", lambda: {fw.sha256_bytes(old): "v0.9.0"})
    d = proj / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(old); (d / "extra.md").write_text("ours")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert (d / "SKILL.md").read_bytes() == PACKAGED_SKILL and (d / "extra.md").read_text() == "ours"
    assert f"refreshed {SKILL} — v0.9.0 contract" in out
    assert "keep     .claude/skills/handoff/extra.md — not managed by tagteam; never touched" in out


def test_known_contract_plus_extra_file_plugin_installed_is_kept(proj, tmp_path, monkeypatch, capsys):
    fake_plugin(tmp_path, monkeypatch)
    d = proj / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(PACKAGED_SKILL); (d / "extra.md").write_text("ours")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and (d / "SKILL.md").exists() and (d / "extra.md").exists()
    assert "kept .claude/skills/handoff/: extra files present: extra.md" in out


def test_plugin_handover_unlinks_sole_known_skill_and_empty_dir(proj, tmp_path, monkeypatch, capsys):
    fake_plugin(tmp_path, monkeypatch)
    d = proj / ".claude" / "skills" / "handoff"; d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(PACKAGED_SKILL)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and not d.exists()
    assert "removed vendored handoff skill (" in out and "contract) — served by the plugin" in out
    assert SKILL not in manifest(proj)["files"]
    # preview names the removal without doing it
    (d).mkdir(parents=True); (d / "SKILL.md").write_bytes(PACKAGED_SKILL)
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 0 and d.exists() and f"remove   {SKILL}" in out


def test_legacy_flat_skill_kept_unless_accepted(proj, capsys):
    fresh(proj)
    legacy = proj / ".claude" / "skills" / "handoff-notes.md"
    legacy.write_text("user notes\n")
    commit_all(proj)                                   # tracked and clean, yet:
    code, out = run(proj, capsys=capsys)
    assert code == 0 and legacy.exists()
    assert f"keep     .claude/skills/handoff-notes.md — legacy flat skill; no provenance; delete with: tagteam setup {proj.resolve()} --accept .claude/skills/handoff-notes.md" in out
    code, out = run(proj, accept=[".claude/skills/handoff-notes.md"], capsys=capsys)
    assert code == 0 and not legacy.exists() and "removed  .claude/skills/handoff-notes.md" in out
    # untracked: refused without --force, deleted with it
    bare = proj / ".claude" / "skills" / "handoff.md"; bare.write_text("x")
    code, out = run(proj, accept=[".claude/skills/handoff.md"], capsys=capsys)
    assert code == 1 and bare.exists() and "not recoverable: untracked" in out
    code, out = run(proj, accept=[".claude/skills/handoff.md"], force=True, capsys=capsys)
    assert code == 0 and not bare.exists()


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def _current_tree(root: Path) -> None:
    """A pre-manifest project whose files equal the current package."""
    for rel, src, _ in fw._sources(DATA):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(src.read_bytes())
    (root / ".claude" / "skills" / "handoff").mkdir(parents=True)
    (root / SKILL).write_bytes(PACKAGED_SKILL)


def test_bootstrap_adopts_current_tree_writing_only_the_manifest(proj, capsys):
    _current_tree(proj)
    before = snapshot(proj, skip_manifest=True)
    code, out = run(proj, capsys=capsys)
    assert code == 0
    after = snapshot(proj, skip_manifest=True)
    # seed-only files are created as always; every managed file is untouched
    assert {k: v for k, v in after.items() if k in before} == before
    m = manifest(proj)
    assert set(m["files"]) == {rel for rel, _, _ in fw._sources(DATA)} | {SKILL}
    assert all(e["tagteam"] == fw.package_version() for e in m["files"].values())
    assert "Manifest: tagteam-manifest.json written" in out
    assert "match the package — no change" in out


def test_bootstrap_at_A_then_package_B_refreshes_changed_paths_only(proj, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    fresh(proj)
    assert all(e["tagteam"] == "1.0.0" for e in manifest(proj)["files"].values())
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"cycle v2\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert "refreshed docs/workflows.md — written by tagteam 1.0.0" in out
    assert (proj / "docs" / "workflows.md").read_bytes() == b"cycle v2\n"
    m = manifest(proj)
    assert m["tagteam"] == "2.0.0"
    assert m["files"]["docs/workflows.md"]["tagteam"] == "2.0.0"
    assert m["files"][SKILL]["tagteam"] == "1.0.0"       # untouched keeps A


def test_custom_path_among_framework_refreshes(proj, monkeypatch, tmp_path, capsys):
    fresh(proj)
    (proj / SKILL).write_text("mine\n")
    monkeypatch.setattr(fw, "package_version", lambda: "9.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"cycle v9\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert (proj / "docs" / "workflows.md").read_bytes() == b"cycle v9\n"
    assert (proj / SKILL).read_text() == "mine\n"
    m = manifest(proj)
    assert SKILL not in m["files"] and m["files"]["docs/workflows.md"]["tagteam"] == "9.0.0"
    before = snapshot(proj)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and snapshot(proj) == before and "unchanged" in out


def test_edited_since_written_drops_entry_and_names_version(proj, monkeypatch, capsys):
    monkeypatch.setattr(fw, "package_version", lambda: "3.1.0")
    fresh(proj)
    (proj / "docs" / "workflows.md").write_text("edited\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert "keep     docs/workflows.md — modified since tagteam 3.1.0 wrote it" in out
    assert "docs/workflows.md" not in manifest(proj)["files"]


def test_crash_before_manifest_write_converges(proj, capsys):
    # A scoped patch: monkeypatch.undo() here would also undo the proj
    # fixture's registry redirect and register the temp dir for real.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fw, "_write_manifest", lambda plan: None)     # files land, manifest does not
        fresh(proj)
    assert not (proj / fw.MANIFEST_NAME).exists()
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "Manifest: tagteam-manifest.json written" in out
    m = manifest(proj)
    for rel, e in m["files"].items():
        assert e["sha256"] == fw.sha256_bytes((proj / rel).read_bytes())
    before = snapshot(proj)
    run(proj)
    assert snapshot(proj) == before


def test_invalid_manifest_is_pre_manifest(proj, capsys):
    _current_tree(proj)
    (proj / fw.MANIFEST_NAME).write_text("{not json")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "manifest invalid (" in out
    assert manifest(proj)["schema"] == 1


# ---------------------------------------------------------------------------
# filesystem shapes — refused under every flag, tree unchanged
# ---------------------------------------------------------------------------

def _assert_refused_unchanged(proj, rel, capsys, detail):
    for kw in ({}, {"accept": [rel]}, {"accept": [rel], "force": True}):
        before = snapshot(proj, skip_manifest=True)
        code, out = run(proj, capsys=capsys, **kw)
        assert code == 1, out
        assert f"refused  {rel} — unsupported filesystem shape ({detail})" in out
        assert snapshot(proj, skip_manifest=True) == before


def test_symlink_to_identical_bytes_is_refused(proj, capsys):
    fresh(proj)
    real = proj / "real-cycle.md"; real.write_bytes((DATA / "workflows.md").read_bytes())
    t = proj / "docs" / "workflows.md"; t.unlink(); t.symlink_to(real)
    _assert_refused_unchanged(proj, "docs/workflows.md", capsys, "docs/workflows.md is a symlink")


def test_directory_at_managed_path_and_symlinked_skill_dir_are_refused(proj, tmp_path, capsys):
    (proj / "docs" / "workflows.md").mkdir(parents=True)
    elsewhere = tmp_path / "skill-elsewhere"; elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_bytes(PACKAGED_SKILL)
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".claude" / "skills" / "handoff").symlink_to(elsewhere)
    code, out = run(proj, capsys=capsys)
    assert code == 1
    assert "refused  docs/workflows.md — unsupported filesystem shape (docs/workflows.md is a directory)" in out
    assert f"refused  {SKILL} — unsupported filesystem shape (.claude/skills/handoff is a symlink)" in out
    assert (proj / ".claude" / "skills" / "handoff").is_symlink()
    assert (elsewhere / "SKILL.md").read_bytes() == PACKAGED_SKILL and (proj / "docs" / "workflows.md").is_dir()


# ---------------------------------------------------------------------------
# preimage re-checks between classification and apply
# ---------------------------------------------------------------------------

def test_file_appearing_at_absent_path_is_refused(proj):
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "docs").mkdir()
    (proj / "docs" / "workflows.md").write_text("appeared\n")
    fw.apply(plan)
    it = next(i for i in plan.items if i.rel == "docs/workflows.md")
    assert it.outcome.startswith("refused: changed since classification")
    assert (proj / "docs" / "workflows.md").read_text() == "appeared\n"
    assert "docs/workflows.md" not in fw.projected_manifest(plan)["files"]


def test_file_swapped_for_symlink_is_refused(proj, monkeypatch, tmp_path):
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "9.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"v9\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    it = next(i for i in plan.items if i.rel == "docs/workflows.md")
    assert it.action == "refresh"
    t = proj / "docs" / "workflows.md"
    real = proj / "same-bytes.md"; real.write_bytes(t.read_bytes())
    t.unlink(); t.symlink_to(real)
    fw.apply(plan)
    assert it.outcome == "refused: changed since classification: docs/workflows.md is a symlink"
    assert t.is_symlink() and real.read_bytes() != b"v9\n"


def test_delete_target_replaced_is_refused(proj):
    fresh(proj)
    legacy = proj / ".claude" / "skills" / "handoff-old.md"; legacy.write_text("old\n")
    commit_all(proj)
    plan = fw.build_plan(proj, data_dir=DATA, accept=[".claude/skills/handoff-old.md"])
    it = next(i for i in plan.items if i.rel == ".claude/skills/handoff-old.md")
    assert it.action == "remove"
    legacy.write_text("replaced\n")
    fw.apply(plan)
    assert it.outcome == "refused: changed since classification: content differs"
    assert legacy.read_text() == "replaced\n"


def test_refreshed_file_changed_underneath_is_refused_and_entry_kept(proj, monkeypatch, tmp_path):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"v2\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "docs" / "workflows.md").write_text("user edit in between\n")
    fw.apply(plan)
    it = next(i for i in plan.items if i.rel == "docs/workflows.md")
    assert it.outcome.startswith("refused") and (proj / "docs" / "workflows.md").read_text() == "user edit in between\n"
    assert manifest(proj)["files"]["docs/workflows.md"]["tagteam"] == "1.0.0"      # left as it was


# ---------------------------------------------------------------------------
# upgrade across projects / CLI parsing / state line
# ---------------------------------------------------------------------------

def test_upgrade_refreshes_framework_paths_and_never_accepts(tmp_path, monkeypatch, capsys):
    from tagteam.cli import upgrade_command
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    no_cli(monkeypatch)
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    projects = []
    for i in range(2):
        p = tmp_path / f"p{i}"; p.mkdir()
        (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
        fresh(p)
        (p / SKILL).write_text(f"custom {i}\n")
        projects.append(p)
    capsys.readouterr()
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"v2\n")
    # preview first: nothing moves
    befores = [snapshot(p) for p in projects]
    assert upgrade_command(["--preview"]) == 0
    out = capsys.readouterr().out
    assert [snapshot(p) for p in projects] == befores and "Previewed 2 project(s); nothing written." in out
    assert upgrade_command() == 0
    out = capsys.readouterr().out
    for i, p in enumerate(projects):
        assert (p / "docs" / "workflows.md").read_bytes() == b"v2\n"
        assert (p / SKILL).read_text() == f"custom {i}\n"
        assert f"accept with: tagteam setup {p.resolve()} --accept {SKILL}" in out
    assert out.count("refreshed docs/workflows.md") == 2 and "accepted" not in out
    assert "All 2 project(s) upgraded successfully." in out
    assert upgrade_command(["--bogus"]) == 2


def test_upgrade_reports_refused_projects(tmp_path, monkeypatch, capsys):
    from tagteam.cli import upgrade_command
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    no_cli(monkeypatch)
    p = tmp_path / "p"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    (p / "docs" / "workflows.md").mkdir(parents=True)
    registry_mod.register_project(str(p))
    assert upgrade_command() == 1
    assert "1 project(s) with refused paths" in capsys.readouterr().out


def test_parse_setup_args():
    assert su.parse_setup_args([]) == (".", {"no_plugin": False, "preview": False, "accept": (), "force": False})
    target, opts = su.parse_setup_args(["proj", "--no-plugin", "--preview", "--accept", "a.md", "--accept=b.md", "--force"])
    assert target == "proj" and opts == {"no_plugin": True, "preview": True, "accept": ("a.md", "b.md"), "force": True}
    with pytest.raises(ValueError):
        su.parse_setup_args(["--accept"])
    with pytest.raises(ValueError):
        su.parse_setup_args(["a", "b"])
    with pytest.raises(ValueError):
        su.parse_setup_args(["--what"])


def test_cli_setup_flags_and_exit_code(proj, monkeypatch, capsys):
    import tagteam.cli as cli
    monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(proj), "--preview"])
    assert cli.main() == 0
    assert not (proj / "templates").exists()
    monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(proj), "--accept"])
    assert cli.main() == 2
    assert "usage: tagteam setup" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(proj), "--accept", "docs/workflows.md"])
    assert cli.main() == 1            # nothing custom to accept


def test_version_line_distinguishes_package_manifest_plugin(proj, monkeypatch):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    assert fw.version_line(proj) == "package 1.0.0 · manifest none · plugin: not installed (claude CLI not found)"
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "1.1.0")
    line = fw.version_line(proj)
    assert line.startswith("package 1.1.0 · manifest 1.0.0 (written 20") and line.endswith("plugin: not installed (claude CLI not found)")


# ---------------------------------------------------------------------------
# round 3 — setup-level boundaries: directories and seed files go through
# the same lexical-parent checks; nothing is created or written through a
# link or a non-directory, and unaffected paths still proceed
# ---------------------------------------------------------------------------

DOCS_PATHS = ["docs/phases", "docs/handoffs", "docs/escalations",
              "docs/workflows.md", "docs/roadmap.md", "docs/decision_log.md"]


def _each_flag(proj, capsys, rel):
    for kw in ({}, {"accept": [rel]}, {"accept": [rel], "force": True}, {"no_plugin": True}):
        yield run(proj, capsys=capsys, **kw)


def test_symlinked_docs_dir_outside_project_refuses_docs_and_proceeds_elsewhere(proj, tmp_path, capsys):
    """The reviewer's reproduction: docs → an empty directory outside the
    project. Nothing lands there; the skill, the pointers and the manifest
    are still produced; exit 1 names every refused docs path."""
    outside = tmp_path / "outside"; outside.mkdir()
    (proj / "docs").symlink_to(outside)
    for code, out in _each_flag(proj, capsys, "docs/workflows.md"):
        assert code == 1, out
        assert snapshot(outside) == {}
        for rel in DOCS_PATHS:
            assert f"refused  {rel} — unsupported filesystem shape (docs is a symlink)" in out, rel
    assert (proj / SKILL).read_bytes() == PACKAGED_SKILL
    assert (proj / "AGENTS.md").read_text() == fw.POINTER and (proj / "CLAUDE.md").read_text() == fw.POINTER
    files = manifest(proj)["files"]
    assert files and all(not rel.startswith("docs/") for rel in files)
    assert (proj / "docs").is_symlink()


def test_symlinked_claude_dir_refuses_skill_paths_and_proceeds_elsewhere(proj, tmp_path, capsys):
    outside = tmp_path / "outside"; outside.mkdir()
    (proj / ".claude").symlink_to(outside)
    for code, out in _each_flag(proj, capsys, SKILL):
        assert code == 1, out
        assert snapshot(outside) == {}
        assert "refused  .claude/skills — unsupported filesystem shape (.claude is a symlink)" in out
        assert f"refused  {SKILL} — unsupported filesystem shape (.claude is a symlink)" in out
    assert (proj / "docs" / "workflows.md").exists() and (proj / "docs" / "roadmap.md").exists()
    assert SKILL not in manifest(proj)["files"]


def test_regular_file_at_required_directory_refuses_that_subtree_only(proj, capsys):
    """A file where a directory is required used to raise out of mkdir before
    any report; now the subtree is refused path by path and the rest runs.
    (A file at a *retired* directory is not an error — see the Phase 61 tests.)"""
    (proj / "docs").write_text("not a directory\n")
    for kw in ({}, {"no_plugin": True}):
        code, out = run(proj, capsys=capsys, **kw)
        assert code == 1, out
        assert (proj / "docs").read_text() == "not a directory\n"
        assert "Traceback" not in out
        for rel in DOCS_PATHS:
            assert f"refused  {rel} — unsupported filesystem shape (docs is not a directory)" in out
    assert (proj / SKILL).exists()
    assert all(not r.startswith("docs/") for r in manifest(proj)["files"])


def test_seed_symlinks_are_left_alone_not_written_through(proj, tmp_path, capsys):
    """A symlink *at* a seed path (AGENTS.md → CLAUDE.md, roadmap kept
    elsewhere) is an existing project file: never touched, never a refusal.
    A symlink *above* one is a write through a link and is refused."""
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "roadmap.md").write_text("theirs\n")
    (proj / "docs").mkdir()
    (proj / "docs" / "roadmap.md").symlink_to(outside / "roadmap.md")
    (proj / "CLAUDE.md").write_text("# ours\n")
    (proj / "AGENTS.md").symlink_to(proj / "CLAUDE.md")
    dangling = proj / "docs" / "decision_log.md"; dangling.symlink_to(tmp_path / "nowhere")
    before_outside = snapshot(outside)
    for code, out in _each_flag(proj, capsys, "docs/roadmap.md"):
        assert snapshot(outside) == before_outside
        assert (proj / "docs" / "roadmap.md").is_symlink() and (proj / "AGENTS.md").is_symlink()
        assert dangling.is_symlink() and not dangling.exists()
        assert (proj / "CLAUDE.md").read_text() == "# ours\n"
        for rel in ("docs/roadmap.md", "AGENTS.md", "docs/decision_log.md"):
            for verb in ("refused ", "keep    ", "create  ", "created "):
                assert f"{verb} {rel} —" not in out, (verb, rel)
    # the accept flags were unknown accepts (seeds are not managed) → 1; the plain runs → 0
    assert run(proj, capsys=capsys)[0] == 0
    # directories are still provided next to the links
    assert (proj / "docs" / "phases").is_dir() and (proj / "docs" / "handoffs").is_dir()


def test_directory_items_never_mkdir_through_a_link_appearing_late(proj, tmp_path):
    """Preimage check for a directory: docs turned into a symlink between
    classification and apply → docs/* creates are refused, nothing lands."""
    outside = tmp_path / "outside"; outside.mkdir()
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "docs").symlink_to(outside)
    fw.apply(plan)
    assert snapshot(outside) == {}
    for it in plan.items:
        if it.rel.startswith("docs/") and it.kind != "retired":   # absent retired paths: no action
            assert it.outcome == "refused: changed since classification: docs is a symlink", it.rel
    assert (proj / SKILL).exists() and (proj / ".claude" / "skills").is_dir()


def test_fresh_setup_reports_directories_and_seeds(proj, capsys):
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 0
    assert "create   4 directories" in out
    assert "create   docs/roadmap.md — seeded once; yours from now on" in out
    assert "create   AGENTS.md — seeded once; yours from now on" in out
    assert not (proj / "docs").exists()
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "created  4 directories" in out and "created  CLAUDE.md" in out
    for rel in fw.SEED_DIRS:
        assert (proj / rel).is_dir()
    assert (proj / "docs" / "roadmap.md").read_bytes() == (DATA / "seeds" / "roadmap.md").read_bytes()
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "director" not in out and "AGENTS.md" not in out


# ---------------------------------------------------------------------------
# round 3 — the manifest write has the same preimage guard as every path
# ---------------------------------------------------------------------------

def test_manifest_appearing_between_classification_and_apply_is_refused(proj):
    """The reviewer's reproduction: no manifest at classification, one written
    concurrently, apply → refused, concurrent bytes intact."""
    plan = fw.build_plan(proj, data_dir=DATA)
    assert plan.manifest is None and plan.manifest_shape.kind == "absent"
    concurrent = b'{"schema": 1, "files": {}, "tagteam": "concurrent"}\n'
    (proj / fw.MANIFEST_NAME).write_bytes(concurrent)
    fw.apply(plan)
    assert plan.manifest_outcome == "refused: changed since classification: now file, was absent"
    assert (proj / fw.MANIFEST_NAME).read_bytes() == concurrent
    assert any(line.startswith(f"{fw.MANIFEST_NAME}: refused") for line in plan.refused)
    assert f"Manifest: {fw.MANIFEST_NAME} refused: changed since classification" in fw.format_report(plan)
    assert (proj / "docs" / "workflows.md").exists()        # the files themselves still landed


def test_manifest_edited_between_classification_and_apply_is_refused(proj, monkeypatch, tmp_path):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"v2\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    assert plan.manifest_shape.kind == "file" and fw.manifest_pending(plan)
    m = manifest(proj); m["tagteam"] = "someone-else"; m["files"][SKILL]["note"] = "edited"
    concurrent = (json.dumps(m, indent=2, sort_keys=True) + "\n").encode()
    (proj / fw.MANIFEST_NAME).write_bytes(concurrent)
    fw.apply(plan)
    assert plan.manifest_outcome == "refused: changed since classification: content differs"
    assert (proj / fw.MANIFEST_NAME).read_bytes() == concurrent
    # the refresh itself happened; the next run adopts it and writes the manifest
    assert (proj / "docs" / "workflows.md").read_bytes() == b"v2\n"
    no_cli(monkeypatch)
    plan2 = fw.apply(fw.build_plan(proj, data_dir=DATA))
    assert plan2.manifest_outcome == "written" and manifest(proj)["files"]["docs/workflows.md"]["tagteam"] == "2.0.0"


def test_manifest_disappearing_between_classification_and_apply_is_refused(proj, monkeypatch, tmp_path):
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "9.0.0")
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"v9\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / fw.MANIFEST_NAME).unlink()
    fw.apply(plan)
    assert plan.manifest_outcome == "refused: changed since classification: now absent, was file"
    assert not (proj / fw.MANIFEST_NAME).exists()


@pytest.mark.parametrize("shape", ["symlink", "directory"])
def test_manifest_shape_refused_in_preview_and_apply(proj, tmp_path, capsys, shape):
    """Preview says the manifest would be refused instead of 'would be
    written'; apply refuses it and writes nothing through the link."""
    target = tmp_path / "real-manifest.json"; target.write_text("{}\n")
    p = proj / fw.MANIFEST_NAME
    if shape == "symlink":
        p.symlink_to(target); detail = f"{fw.MANIFEST_NAME} is a symlink"
    else:
        p.mkdir(); detail = f"{fw.MANIFEST_NAME} is a directory"
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 1
    assert f"Manifest: {fw.MANIFEST_NAME} would be refused: unsupported filesystem shape ({detail}) (preview — nothing written)" in out
    assert "would be written" not in out and "1 path(s) would be refused." in out
    assert not (proj / "templates").exists()
    code, out = run(proj, capsys=capsys)
    assert code == 1
    assert f"Manifest: {fw.MANIFEST_NAME} refused: unsupported filesystem shape ({detail})" in out
    assert target.read_text() == "{}\n"
    assert (p.is_symlink() if shape == "symlink" else p.is_dir())
    assert (proj / "docs" / "workflows.md").exists()


# ---------------------------------------------------------------------------
# round 4 — the project directory is a preimage too; preview never touches
# the registry; preview names refused directories and seeds
# ---------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Registry redirect + no claude CLI, without pre-creating any project."""
    h = tmp_path / "home"; h.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", h)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", h / "projects.json")
    no_cli(monkeypatch)
    return h


@pytest.mark.parametrize("depth", ["new", "deeper/still/new"])
def test_fresh_setup_creates_an_absent_target(home, tmp_path, capsys, depth):
    """The reviewer's reproduction: a target that does not exist yet
    (ancestors included). Preview reports it and creates nothing; apply
    creates it the checked way, then everything below, and registers it."""
    target = tmp_path / depth
    code, out = run(target, preview=True, capsys=capsys)
    assert code == 0, out
    assert f"create   {target} — new project directory" in out and "create   4 directories" in out
    assert not target.exists() and not (tmp_path / Path(depth).parts[0]).exists()
    assert not registry_mod.REGISTRY_FILE.exists()
    code, out = run(target, capsys=capsys)
    assert code == 0, out
    assert f"created  {target} — new project directory" in out and "created  4 directories" in out
    assert "FileNotFoundError" not in out and "Traceback" not in out
    for rel in fw.SEED_DIRS:
        assert (target / rel).is_dir()
    assert (target / "docs" / "workflows.md").read_bytes() == (DATA / "workflows.md").read_bytes()
    assert (target / SKILL).read_bytes() == PACKAGED_SKILL and (target / "AGENTS.md").read_text() == fw.POINTER
    assert manifest(target)["files"] and not su.needs_setup(str(target))
    assert json.loads(registry_mod.REGISTRY_FILE.read_text()) == [str(target.resolve())]
    before = snapshot(target)
    code, out = run(target, capsys=capsys)
    assert code == 0 and snapshot(target) == before and "new project directory" not in out


def test_file_at_target_refuses_everything_without_traceback(home, tmp_path, capsys):
    target = tmp_path / "proj"; target.write_text("a file\n")
    for kw in ({"preview": True}, {}, {"no_plugin": True}):
        code, out = run(target, capsys=capsys, **kw)
        assert code == 1, out
        assert "Traceback" not in out
        verb = "refuse " if kw.get("preview") else "refused"
        assert f"{verb}  {target} — unsupported filesystem shape ({target} is not a directory)" in out
        assert f"{verb}  docs/workflows.md — unsupported filesystem shape ({target} is not a directory)" in out
        assert target.read_text() == "a file\n"
    assert not registry_mod.REGISTRY_FILE.exists()


def test_target_appearing_between_classification_and_apply_is_refused(home, tmp_path):
    target = tmp_path / "proj"
    plan = fw.build_plan(target, data_dir=DATA)
    assert plan.root_shape.kind == "absent" and all(i.action == "create" for i in plan.items if i.kind == "file")
    target.mkdir()
    (target / "docs").mkdir(); (target / "docs" / "workflows.md").write_text("theirs\n")
    fw.apply(plan)
    assert plan.root_outcome == "refused: changed since classification: now dir, was absent"
    assert snapshot(target) == {"docs": "dir", "docs/workflows.md": b"theirs\n"}
    assert plan.refused[0].startswith(f"{target.resolve()}: refused")
    assert all(i.outcome.startswith("refused") for i in plan.items if i.kind == "file")
    assert plan.manifest_outcome == "unchanged" and not (target / fw.MANIFEST_NAME).exists()   # nothing to record
    assert "refused  " + str(target.resolve()) in fw.format_report(plan)
    # the next run sees the tree as it is and converges
    plan2 = fw.apply(fw.build_plan(target, data_dir=DATA))
    assert plan2.root_outcome == "" and plan2.manifest_outcome == "written"
    assert (target / "docs" / "workflows.md").read_text() == "theirs\n"


def test_upgrade_preview_never_touches_the_registry(home, tmp_path, capsys):
    """The reviewer's reproduction: a registered directory that is missing.
    Preview skips it with a note and leaves the registry bytes alone; the
    apply run prunes it as before."""
    from tagteam.cli import upgrade_command
    p = tmp_path / "p"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
    fresh(p)
    gone = tmp_path / "gone"
    registry_mod._write_registry([str(gone), str(p.resolve())])
    raw = registry_mod.REGISTRY_FILE.read_bytes()
    capsys.readouterr()
    assert upgrade_command(["--preview"]) == 0
    out = capsys.readouterr().out
    assert registry_mod.REGISTRY_FILE.read_bytes() == raw
    assert f"note: registered project not found, skipped: {gone}" in out
    assert f"Project: {p.resolve()}" in out and "Previewed 1 project(s); nothing written." in out
    # only missing entries: nothing to preview, still nothing written
    registry_mod._write_registry([str(gone)])
    raw = registry_mod.REGISTRY_FILE.read_bytes()
    assert upgrade_command(["--preview"]) == 0
    assert registry_mod.REGISTRY_FILE.read_bytes() == raw and "No registered projects found." in capsys.readouterr().out
    # apply prunes
    assert upgrade_command() == 0
    assert json.loads(registry_mod.REGISTRY_FILE.read_text()) == []


def test_preview_names_refused_directories_and_seeds(proj, tmp_path, capsys):
    """Round-3 gap: the planned verb is `refuse`, the applied one `refused`;
    preview used to test for the latter and print nothing for the seeds."""
    outside = tmp_path / "outside"; outside.mkdir()
    (proj / "docs").symlink_to(outside)
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 1
    for rel in DOCS_PATHS:
        assert f"refuse   {rel} — unsupported filesystem shape (docs is a symlink)" in out, rel
    assert snapshot(outside) == {} and not (proj / "templates").exists()
    n = len([l for l in out.splitlines() if l.strip().startswith("refuse ")])
    assert f"{n} path(s) would be refused." in out
    # a file where a directory is required: the subtree, in preview too
    (proj / ".claude").write_text("not a directory\n")
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 1
    assert f"refuse   {SKILL} — unsupported filesystem shape (.claude is not a directory)" in out
    assert ".claude —" not in out
    (proj / ".claude").unlink()
    # apply says the same, past tense
    code, out = run(proj, capsys=capsys)
    assert code == 1
    for rel in DOCS_PATHS:
        assert f"refused  {rel} — unsupported filesystem shape (docs is a symlink)" in out, rel
    assert snapshot(outside) == {}


# ---------------------------------------------------------------------------
# Phase 61 — templates/*.md and docs/checklists/*.md are retired: never
# created, removed when provably tagteam's, kept when not
# ---------------------------------------------------------------------------

RETIRED = [rel for rel, _, _ in fw._retired_sources(DATA)]
OLD_CYCLE = b"# cycle template as an older tagteam shipped it\n"


def _installed_by_3_13(root: Path, *, version: str = "3.13.0", historical: dict[str, bytes] | None = None) -> None:
    """The tree a pre-Phase-61 setup left behind: workflows.md, the vendored
    skill and the 12 retired files, all recorded in the manifest.
    ``historical`` swaps in bytes the current package no longer ships."""
    fresh(root)
    m = manifest(root)
    for rel, src, src_rel in fw._retired_sources(DATA):
        data = (historical or {}).get(rel, src.read_bytes())
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        m["files"][rel] = {"sha256": fw.sha256_bytes(data), "source": src_rel, "tagteam": version}
    for e in m["files"].values():
        e["tagteam"] = version
    m["tagteam"] = version
    (root / fw.MANIFEST_NAME).write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


def test_retired_set_is_the_twelve_and_disjoint_from_managed():
    assert len(RETIRED) == 12 and {r.split("/")[0] for r in RETIRED} == {"templates", "docs"}
    assert [rel for rel, _, _ in fw._sources(DATA)] == ["docs/workflows.md"]
    assert not set(fw.RETIRED_DIRS) & set(fw.SEED_DIRS)


def test_project_set_up_by_3_13_retires_all_twelve_then_is_a_noop(proj, capsys):
    _installed_by_3_13(proj)
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 0 and "retire   templates/cycle.md — no longer installed; matches the package" in out
    code, out = run(proj, capsys=capsys)
    assert code == 0, out
    for rel in RETIRED:
        assert f"retired  {rel} — no longer installed; matches the package" in out
        assert not (proj / rel).exists()
    assert "removed  templates/ — empty" in out and "removed  docs/checklists/ — empty" in out
    assert not (proj / "templates").exists() and not (proj / "docs" / "checklists").exists()
    assert set(manifest(proj)["files"]) == {"docs/workflows.md", SKILL}
    assert not su.needs_setup(str(proj))
    before = snapshot(proj)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and snapshot(proj) == before
    assert "retired" not in out and "keep  " not in out and "unchanged" in out


def test_preview_of_a_retire_touches_nothing(proj, capsys):
    _installed_by_3_13(proj)
    before = snapshot(proj)
    code, out = run(proj, preview=True, capsys=capsys)
    assert code == 0 and snapshot(proj) == before
    assert out.count("retire   ") == 12 and "would be written (preview — nothing written)" in out


def test_edited_retired_file_is_kept_and_deleted_only_on_accept(proj, capsys):
    _installed_by_3_13(proj)
    mine = proj / "templates" / "cycle.md"; mine.write_text("my cycle notes\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and out.count("retired  ") == 11
    assert (f"keep     templates/cycle.md — no longer managed; modified since tagteam 3.13.0 wrote it; "
            f"delete with: tagteam setup {proj.resolve()} --accept templates/cycle.md") in out
    assert mine.read_text() == "my cycle notes\n" and (proj / "templates").is_dir()
    assert not (proj / "docs" / "checklists").exists()
    assert "templates/cycle.md" not in manifest(proj)["files"]
    # untracked: refused without --force
    git(proj, "init", "-q")
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 1 and mine.exists() and "not recoverable: untracked (use --force)" in out
    # tracked and clean: removed, and the directory with it
    commit_all(proj)
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 0, out
    assert "removed  templates/cycle.md — no longer managed" in out and "(tracked and clean)" in out
    assert not (proj / "templates").exists()


def test_pre_manifest_package_bytes_are_retired_other_bytes_kept(proj, capsys):
    (proj / "templates").mkdir()
    same = proj / "templates" / "feedback.md"; same.write_bytes((DATA / "templates" / "feedback.md").read_bytes())
    other = proj / "templates" / "phase_plan.md"; other.write_text("- Lead: Claude\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert "retired  templates/feedback.md — no longer installed; matches the package" in out and not same.exists()
    assert "keep     templates/phase_plan.md — no longer managed; differs from the package" in out
    assert other.read_text() == "- Lead: Claude\n"


def test_render_variant_of_a_retired_file_is_reconstructible(proj, monkeypatch, tmp_path):
    real = fw._retired_sources
    src = tmp_path / "cycle.md"; src.write_bytes(b"Lead: {{lead}} / Reviewer: {{reviewer}}\n")
    monkeypatch.setattr(fw, "_retired_sources", lambda d: [
        (r, src if r == "templates/cycle.md" else s, sr) for r, s, sr in real(d)])
    (proj / "templates").mkdir()
    (proj / "templates" / "cycle.md").write_bytes(b"Lead: A / Reviewer: B\n")
    it = next(i for i in fw.build_plan(proj, data_dir=DATA).items if i.rel == "templates/cycle.md")
    assert it.cls == fw.FRAMEWORK and it.reconstructible and it.action == "retire"


def test_owner_file_in_a_retired_directory_keeps_the_directory(proj, capsys):
    _installed_by_3_13(proj)
    (proj / "templates" / "mine.md").write_text("ours\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and out.count("retired  ") == 12
    assert snapshot(proj)["templates/mine.md"] == b"ours\n"
    assert sorted(p.name for p in (proj / "templates").iterdir()) == ["mine.md"]
    assert "removed  templates/" not in out and "mine.md" not in out


def test_odd_shapes_at_retired_paths_are_kept_not_refused(proj, tmp_path, capsys):
    """Nothing is written under a retired path any more, so a symlink or a
    file-for-a-directory there is the owner's business: kept, exit 0, and
    nothing is unlinked through the link."""
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "cycle.md").write_bytes((DATA / "templates" / "cycle.md").read_bytes())
    (proj / "templates").symlink_to(outside)
    (proj / "docs").mkdir(); (proj / "docs" / "checklists").write_text("not a directory\n")
    before = snapshot(outside)
    for kw in ({"preview": True}, {}, {"no_plugin": True}):
        code, out = run(proj, capsys=capsys, **kw)
        assert code == 0, out
        assert "keep     templates/cycle.md — no longer managed; templates is a symlink; never touched" in out
        assert "keep     docs/checklists/code_review.md — no longer managed; docs/checklists is not a directory; never touched" in out
        assert "refuse" not in out
    assert snapshot(outside) == before and (proj / "templates").is_symlink()
    assert (proj / "docs" / "checklists").read_text() == "not a directory\n"
    # the leaf itself a symlink to identical bytes
    (proj / "templates").unlink(); (proj / "templates").mkdir()
    (proj / "templates" / "cycle.md").symlink_to(outside / "cycle.md")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and (proj / "templates" / "cycle.md").is_symlink() and (outside / "cycle.md").exists()
    assert "keep     templates/cycle.md — no longer managed; templates/cycle.md is a symlink; never touched" in out
    # accepting one is an unknown accept, as for any path with nothing custom to delete
    code, out = run(proj, accept=["templates/cycle.md"], force=True, capsys=capsys)
    assert code == 1 and "refused  --accept templates/cycle.md" in out and (proj / "templates" / "cycle.md").is_symlink()


def test_retired_file_changed_between_classification_and_apply_is_refused_alone(proj):
    _installed_by_3_13(proj)
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "templates" / "cycle.md").write_text("edited in between\n")
    fw.apply(plan)
    by = {i.rel: i for i in plan.items if i.kind == "retired"}
    assert by["templates/cycle.md"].outcome == "refused: changed since classification: content differs"
    assert (proj / "templates" / "cycle.md").read_text() == "edited in between\n"
    assert sum(i.outcome == "retired" for i in by.values()) == 11
    assert (proj / "templates").is_dir() and plan.pruned_dirs == ["docs/checklists"]
    assert "templates/cycle.md" in manifest(proj)["files"]          # provenance survives the refusal


def test_historical_bytes_need_git_recovery(proj, capsys):
    """Reviewer finding 1: a manifest hash proves tagteam wrote the bytes, not
    that they can be had again. Bytes the installed package no longer ships
    are removed only when git can restore them."""
    _installed_by_3_13(proj, version="3.11.0", historical={"templates/cycle.md": OLD_CYCLE})
    t = proj / "templates" / "cycle.md"
    it = next(i for i in fw.build_plan(proj, data_dir=DATA).items if i.rel == "templates/cycle.md")
    assert it.cls == fw.FRAMEWORK and not it.reconstructible and it.action == "keep"
    code, out = run(proj, capsys=capsys)
    assert code == 0 and out.count("retired  ") == 11
    assert (f"keep     templates/cycle.md — no longer installed; written by tagteam 3.11.0; "
            f"not recoverable: not a git repository; commit it and re-run, or delete with: "
            f"tagteam setup {proj.resolve()} --accept templates/cycle.md --force") in out
    assert t.read_bytes() == OLD_CYCLE
    entry = manifest(proj)["files"]["templates/cycle.md"]
    assert entry["sha256"] == fw.sha256_bytes(OLD_CYCLE) and entry["tagteam"] == "3.11.0"
    # a second run says the same and still writes nothing
    before = snapshot(proj)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and snapshot(proj) == before and "not recoverable: not a git repository" in out
    # once the owner commits it, the next plain run retires it
    commit_all(proj)
    code, out = run(proj, capsys=capsys)
    assert code == 0, out
    assert "retired  templates/cycle.md — no longer installed; written by tagteam 3.11.0 (tracked and clean)" in out
    assert not (proj / "templates").exists() and "templates/cycle.md" not in manifest(proj)["files"]
    assert git(proj, "show", "HEAD:templates/cycle.md").stdout.encode() == OLD_CYCLE


def test_historical_bytes_untracked_go_with_accept_force(proj, capsys):
    _installed_by_3_13(proj, version="3.11.0", historical={"templates/cycle.md": OLD_CYCLE})
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 1 and (proj / "templates" / "cycle.md").exists()
    assert "refused  templates/cycle.md — not recoverable: not a git repository (use --force)" in out
    assert "templates/cycle.md" in manifest(proj)["files"]
    code, out = run(proj, accept=["templates/cycle.md"], force=True, capsys=capsys)
    assert code == 0 and not (proj / "templates").exists()
    assert "removed  templates/cycle.md — no longer installed; written by tagteam 3.11.0" in out
    assert "templates/cycle.md" not in manifest(proj)["files"]


def test_failed_retire_keeps_provenance_and_the_retry_succeeds(proj, capsys):
    """Reviewer finding 2: dropping the entry of a file that is still on disk
    forgets the only proof tagteam wrote it; the next run would call it
    custom and never retry."""
    _installed_by_3_13(proj, version="3.11.0", historical={"templates/cycle.md": OLD_CYCLE})
    commit_all(proj)
    target = proj / "templates" / "cycle.md"
    real_unlink = os.unlink

    def flaky(path, *a, **kw):
        if Path(path) == target:
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fw.os, "unlink", flaky)
        code, out = run(proj, capsys=capsys)
    assert code == 1
    assert "refused  templates/cycle.md — PermissionError" in out and out.count("retired  ") == 11
    assert target.read_bytes() == OLD_CYCLE and (proj / "templates").is_dir()
    m = manifest(proj)
    assert m["files"]["templates/cycle.md"] == {"sha256": fw.sha256_bytes(OLD_CYCLE),
                                                "source": "templates/cycle.md", "tagteam": "3.11.0"}
    assert set(m["files"]) == {"docs/workflows.md", SKILL, "templates/cycle.md"}
    code, out = run(proj, capsys=capsys)
    assert code == 0, out
    assert "retired  templates/cycle.md" in out and not (proj / "templates").exists()
    assert set(manifest(proj)["files"]) == {"docs/workflows.md", SKILL}


def test_upgrade_retires_across_projects(tmp_path, monkeypatch, capsys):
    from tagteam.cli import upgrade_command
    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setattr(registry_mod, "REGISTRY_DIR", home)
    monkeypatch.setattr(registry_mod, "REGISTRY_FILE", home / "projects.json")
    no_cli(monkeypatch)
    projects = []
    for i in range(2):
        p = tmp_path / f"p{i}"; p.mkdir()
        (p / "tagteam.yaml").write_text("agents:\n  lead: {name: A}\n  reviewer: {name: B}\n")
        _installed_by_3_13(p)
        projects.append(p)
    capsys.readouterr()
    assert upgrade_command() == 0
    out = capsys.readouterr().out
    assert out.count("retired  templates/cycle.md") == 2 and "All 2 project(s) upgraded successfully." in out
    assert all(not (p / "templates").exists() and not (p / "docs" / "checklists").exists() for p in projects)


# ---------------------------------------------------------------------------
# Phase 62 — provenance from earlier releases: data/history/<tag>/… holds the
# 13 earlier framework sources; a copy equal to one, verbatim or rendered for
# the configured / swapped names, is tagteam's and reconstructible
# ---------------------------------------------------------------------------

HISTORY = DATA / "history"
REPO = Path(__file__).resolve().parent.parent
PLACEHOLDER_TEMPLATES = ["cycle", "decision_log", "feedback", "handoff_impl", "handoff_plan",
                         "implementation_log", "phase_plan", "sync_state"]


def _names(root: Path, lead: str, reviewer: str) -> None:
    (root / "tagteam.yaml").write_text(f"agents:\n  lead: {{name: {lead}}}\n  reviewer: {{name: {reviewer}}}\n")


def _rendered_era(root: Path, lead: str, reviewer: str) -> None:
    """templates/ as a ≤3.11.0 setup left it: names baked in, no manifest."""
    (root / "templates").mkdir(exist_ok=True)
    for name in PLACEHOLDER_TEMPLATES:
        text = (HISTORY / "v3.11.0" / "templates" / f"{name}.md").read_text(encoding="utf-8")
        assert "{{lead}}" in text or "{{reviewer}}" in text, name
        (root / "templates" / f"{name}.md").write_text(
            text.replace("{{lead}}", lead).replace("{{reviewer}}", reviewer), encoding="utf-8")


from tests import _provenance as prov                  # noqa: E402

_tagged_blob = prov.tagged_blob


def test_history_table_is_what_git_history_says():
    """Every shipped earlier source equals the tagged file, and together with
    the current package they are *all* the bytes tagteam shipped through
    v3.12.0 (later workflows.md versions are vouched for by manifests)."""
    tags = subprocess.run(["git", "-C", str(REPO), "tag", "--list", "v*"], capture_output=True, text=True)
    if tags.returncode != 0 or "v3.12.0" not in tags.stdout.split():
        pytest.skip("release tags not available in this checkout")
    shipped = [p.relative_to(HISTORY).as_posix() for p in prov.history_paths()]
    assert len(shipped) == 13 and [r for r in shipped if r.endswith(".sha256")] == ["v3.10.0/workflows.md.sha256"]
    for rel in shipped:
        tag, source_rel = rel.split("/", 1)
        if rel.endswith(".sha256"):          # the one source the Phase 49 audit forbids shipping as text
            blob = _tagged_blob(tag, source_rel[:-len(".sha256")])
            assert (HISTORY / rel).read_text().strip() == fw.sha256_bytes(blob), rel
        else:
            assert (HISTORY / rel).read_bytes() == _tagged_blob(tag, source_rel), rel
    era = [t for t in tags.stdout.split() if fw._tag_key(t) <= (3, 12, 0)]
    for _, src, source_rel in fw._sources(DATA) + fw._retired_sources(DATA):
        in_git = {fw.sha256_bytes(b) for b in (_tagged_blob(t, source_rel) for t in era) if b is not None}
        known = {sha for _, sha, _ in fw._history_sources(DATA, source_rel)} | {fw.sha256_bytes(src.read_bytes())}
        assert in_git <= known, source_rel
        assert known - in_git <= {fw.sha256_bytes(src.read_bytes())}, source_rel   # nothing invented


def test_history_sources_are_newest_first_by_version_not_by_string():
    wf = fw._history_sources(DATA, "workflows.md")
    assert [t for t, _, _ in wf] == ["v3.12.0", "v3.11.0", "v3.10.0"]
    assert [b is None for _, _, b in wf] == [False, False, True]             # v3.10.0: digest only
    assert all(sha == fw.sha256_bytes(b) for _, sha, b in wf if b is not None)
    assert [t for t, _, _ in fw._history_sources(DATA, "templates/roadmap.md")] == ["v3.3.0"]
    assert fw._history_sources(DATA, "templates/requirements_brief.md") == []   # never changed


@pytest.mark.parametrize("config,baked", [(("claude", "codex"), ("claude", "codex")),
                                          (("Claude", "Codex"), ("Claude", "Codex")),
                                          (("codex", "claude"), ("claude", "codex"))])      # roles swapped since
def test_rendered_era_templates_are_retired_without_git(proj, capsys, config, baked):
    _names(proj, *config)
    _rendered_era(proj, *baked)
    label = "configured names" if config == baked else "swapped names"
    code, out = run(proj, capsys=capsys)
    assert code == 0, out
    for name in PLACEHOLDER_TEMPLATES:
        assert (f"retired  templates/{name}.md — no longer installed; written by tagteam ≤3.11.0 "
                f"(rendered for the {label})") in out
    assert not (proj / "templates").exists() and "removed  templates/ — empty" in out
    assert not any(r.startswith("templates/") for r in manifest(proj)["files"])


def test_rendering_with_other_names_or_an_edit_stays_custom(proj, capsys):
    _names(proj, "architect", "critic")                 # renamed since the render
    _rendered_era(proj, "claude", "codex")
    edited = proj / "templates" / "cycle.md"
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "retired" not in out
    assert out.count("no longer managed; differs from the package; delete with:") == 8
    _names(proj, "claude", "codex")                     # names restored, one file edited by a byte
    edited.write_bytes(edited.read_bytes() + b"\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and out.count("retired  ") == 7
    assert "keep     templates/cycle.md — no longer managed; differs from the package" in out
    assert edited.exists() and (proj / "templates").is_dir()


def _earlier_workflows(tag: str) -> bytes:
    f = HISTORY / tag / "workflows.md"
    if f.is_file():
        return f.read_bytes()
    blob = _tagged_blob(tag, "workflows.md")            # shipped as a digest only — take it from git
    if blob is None:
        pytest.skip(f"{tag} not available in this checkout")
    return blob


@pytest.mark.parametrize("tag", ["v3.10.0", "v3.11.0", "v3.12.0"])
def test_pre_manifest_workflows_from_an_earlier_release_is_refreshed(proj, capsys, tag):
    (proj / "docs").mkdir()
    wf = proj / "docs" / "workflows.md"
    wf.write_bytes(_earlier_workflows(tag))
    it = next(i for i in fw.build_plan(proj, data_dir=DATA).items if i.rel == "docs/workflows.md")
    assert it.cls == fw.FRAMEWORK and it.reconstructible == (tag != "v3.10.0")
    code, out = run(proj, capsys=capsys)
    assert code == 0, out
    assert f"refreshed docs/workflows.md — written by tagteam ≤{tag[1:]}" in out
    assert wf.read_bytes() == (DATA / "workflows.md").read_bytes()
    assert manifest(proj)["files"]["docs/workflows.md"]["tagteam"] == fw.package_version()


def test_edited_earlier_workflows_is_still_custom(proj, capsys):
    (proj / "docs").mkdir()
    wf = proj / "docs" / "workflows.md"
    mine = _earlier_workflows("v3.10.0") + b"\n## Our own section\n"
    wf.write_bytes(mine)
    code, out = run(proj, capsys=capsys)
    assert code == 0 and wf.read_bytes() == mine
    assert "keep     docs/workflows.md — differs from the package; accept with:" in out


def test_earlier_verbatim_retired_sources_are_retired(proj, capsys):
    (proj / "templates").mkdir(); (proj / "docs" / "checklists").mkdir(parents=True)
    (proj / "templates" / "roadmap.md").write_bytes((HISTORY / "v3.3.0" / "templates" / "roadmap.md").read_bytes())
    (proj / "docs" / "checklists" / "code_review.md").write_bytes(
        (HISTORY / "v3.4.0" / "checklists" / "code_review.md").read_bytes())
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert "retired  templates/roadmap.md — no longer installed; written by tagteam ≤3.3.0" in out
    assert "retired  docs/checklists/code_review.md — no longer installed; written by tagteam ≤3.4.0" in out
    assert not (proj / "templates").exists() and not (proj / "docs" / "checklists").exists()


def test_without_history_everything_earlier_is_custom_as_in_3_14_0(proj, tmp_path, monkeypatch, capsys):
    assert fw._history_sources(tmp_path, "workflows.md") == []              # no history/ at all
    (tmp_path / "history").write_text("not a directory\n")
    assert fw._history_sources(tmp_path, "workflows.md") == []
    monkeypatch.setattr(fw, "_history_sources", lambda data_dir, source_rel: [])
    _names(proj, "claude", "codex")
    _rendered_era(proj, "claude", "codex")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "retired" not in out and out.count("no longer managed; differs from the package") == 8


def test_manifest_match_is_still_decided_first(proj, capsys):
    """Scoped conservatively (plan review): a manifest hash match returns
    before the historical evidence is consulted, so such a copy keeps the
    Phase 61 git rule even when an earlier source would also explain it."""
    _names(proj, "claude", "codex")
    fresh(proj)
    _rendered_era(proj, "claude", "codex")
    m = manifest(proj)
    rel = "templates/cycle.md"
    m["files"][rel] = {"sha256": fw.sha256_bytes((proj / rel).read_bytes()), "source": rel, "tagteam": "3.11.0"}
    (proj / fw.MANIFEST_NAME).write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    it = next(i for i in fw.build_plan(proj, data_dir=DATA).items if i.rel == rel)
    assert it.cls == fw.FRAMEWORK and not it.reconstructible and it.action == "keep"
    assert "written by tagteam 3.11.0; not recoverable" in it.reason


def test_prune_reports_a_directory_it_could_not_remove(proj, capsys):
    _installed_by_3_13(proj)
    real_rmdir = os.rmdir

    def denied(path, *a, **kw):
        if Path(path).name == "templates":
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmdir(path, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fw.os, "rmdir", denied)
        code, out = run(proj, capsys=capsys)
    assert code == 0, out                                # not a refusal
    assert "kept     templates/ — directory left in place (PermissionError)" in out
    assert "removed  docs/checklists/ — empty" in out and (proj / "templates").is_dir()
    assert set(manifest(proj)["files"]) == {"docs/workflows.md", SKILL}
    # not-empty is the ordinary case and stays silent
    (proj / "templates" / "mine.md").write_text("ours\n")
    (proj / "templates" / "cycle.md").write_bytes((DATA / "templates" / "cycle.md").read_bytes())
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "retired  templates/cycle.md" in out and "templates/ —" not in out


# ---------------------------------------------------------------------------
# Phase 63 — data/templates and data/checklists are frozen: they are the
# byte-for-byte evidence that retires a project's old copies. Seeds live in
# data/seeds/ so that editing a seed never touches them.
# ---------------------------------------------------------------------------

def test_retired_path_sources_are_frozen_at_the_tag():
    if not prov.has_tag(prov.FROZEN_TAG):
        pytest.skip(f"{prov.FROZEN_TAG} not available in this checkout")
    for sub in ("templates", "checklists"):
        assert sorted(p.name for p in (DATA / sub).glob("*.md")) == prov.tagged_listing(prov.FROZEN_TAG, sub), sub
    for path in prov.frozen_paths():
        rel = path.relative_to(DATA).as_posix()
        assert path.read_bytes() == prov.tagged_blob(prov.FROZEN_TAG, rel), (
            f"{rel} is frozen provenance (Phase 61/62); change data/seeds/ instead")
    assert {p.relative_to(DATA).as_posix() for p in prov.frozen_paths()} == {s for _, _, s in fw._retired_sources(DATA)}


def test_seeds_come_from_data_seeds_not_from_the_frozen_templates(proj):
    assert [src for _, src in fw.SEED_FILES if src] == ["seeds/roadmap.md", "seeds/decision_log.md"]
    assert (DATA / "seeds" / "decision_log.md").read_bytes() == (DATA / "templates" / "decision_log.md").read_bytes()
    seed = (DATA / "seeds" / "roadmap.md").read_text(encoding="utf-8")
    assert seed != (DATA / "templates" / "roadmap.md").read_text(encoding="utf-8")
    for dead in ("`/phase`", "`/plan create", "`/status`"):
        assert dead not in seed
    assert "/tagteam:handoff start [phase]" in seed and "tagteam roadmap ready" in seed
    fresh(proj)
    assert (proj / "docs" / "roadmap.md").read_text(encoding="utf-8") == seed
    assert (proj / "docs" / "decision_log.md").read_bytes() == (DATA / "seeds" / "decision_log.md").read_bytes()


# ---------------------------------------------------------------------------
# Phase 65 — a release that changes no framework file rewrites no manifest
# ---------------------------------------------------------------------------

def test_version_bump_alone_does_not_rewrite_the_manifest(proj, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    fresh(proj)
    raw = (proj / fw.MANIFEST_NAME).read_bytes()
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "Manifest: tagteam-manifest.json unchanged" in out
    assert (proj / fw.MANIFEST_NAME).read_bytes() == raw                 # not even the stamp
    code, out = run(proj, preview=True, capsys=capsys)
    assert "unchanged (preview — nothing written)" in out
    # a real change still rewrites, and then the stamp and that entry move
    replace_source(monkeypatch, tmp_path, "docs/workflows.md", b"workflows v2\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0 and "Manifest: tagteam-manifest.json written" in out
    m = manifest(proj)
    assert m["tagteam"] == "2.0.0" and m["files"]["docs/workflows.md"]["tagteam"] == "2.0.0"
    assert m["files"][SKILL]["tagteam"] == "1.0.0"                       # untouched entry keeps its provenance
