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
    for src in (DATA / "templates").glob("*.md"):
        assert (proj / "templates" / src.name).read_bytes() == src.read_bytes()
    assert (proj / SKILL).read_bytes() == PACKAGED_SKILL
    assert (proj / "docs" / "roadmap.md").exists() and (proj / "AGENTS.md").exists()
    m = manifest(proj)
    assert m["schema"] == 1 and m["tagteam"] == fw.package_version()
    assert set(m["files"]) == {i.rel for i in fw.build_plan(proj, data_dir=DATA).items if i.kind in ("file", "skill")}
    for rel, e in m["files"].items():
        assert e["sha256"] == fw.sha256_bytes((proj / rel).read_bytes()) and e["tagteam"] == fw.package_version()
    assert "created  templates/phase_plan.md" in out and "Manifest: tagteam-manifest.json written" in out
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
    assert "create   templates/cycle.md" in out
    assert f"keep     templates/phase_plan.md — differs from the package; accept with: tagteam setup {proj.resolve()} --accept templates/phase_plan.md" in out
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
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"Lead: {{lead}} / Reviewer: {{reviewer}}\n")
    (proj / "templates").mkdir()
    (proj / "templates" / "cycle.md").write_bytes(b"Lead: B / Reviewer: A\n")     # swapped names
    plan = fw.build_plan(proj, data_dir=DATA)
    it = next(i for i in plan.items if i.rel == "templates/cycle.md")
    assert it.cls == fw.FRAMEWORK and "swapped names" in it.reason and it.action == "refresh"


# ---------------------------------------------------------------------------
# --accept / --force / recoverability
# ---------------------------------------------------------------------------

def test_accept_requires_tracked_and_clean(proj, capsys):
    fresh(proj)
    t = proj / "templates" / "cycle.md"
    t.write_text("mine\n")
    # not a git repository
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 1 and t.read_text() == "mine\n"
    assert "refused  templates/cycle.md — not recoverable: not a git repository (use --force)" in out
    # untracked
    git(proj, "init", "-q")
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 1 and "not recoverable: untracked" in out
    # uncommitted changes
    commit_all(proj); t.write_text("mine again\n")
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 1 and "not recoverable: uncommitted changes" in out and t.read_text() == "mine again\n"
    # tracked and clean → overwritten, entry recorded
    commit_all(proj)
    code, out = run(proj, accept=["templates/cycle.md"], capsys=capsys)
    assert code == 0 and "accepted templates/cycle.md" in out and "(tracked and clean)" in out
    assert t.read_bytes() == (DATA / "templates" / "cycle.md").read_bytes()
    assert manifest(proj)["files"]["templates/cycle.md"]["sha256"] == fw.sha256_bytes(t.read_bytes())


def test_force_lifts_recoverability_only(proj, capsys):
    fresh(proj)
    t = proj / "templates" / "cycle.md"; t.write_text("mine\n")
    other = proj / "templates" / "feedback.md"; other.write_text("also mine\n")
    code, out = run(proj, accept=["templates/cycle.md"], force=True, capsys=capsys)
    assert code == 0
    assert "--force: recoverability refusals lifted" in out
    assert "accepted templates/cycle.md" in out and "; --force)" in out
    assert t.read_bytes() == (DATA / "templates" / "cycle.md").read_bytes()
    assert other.read_text() == "also mine\n" and "keep     templates/feedback.md" in out   # never widened


def test_unknown_accept_is_refused(proj, capsys):
    fresh(proj)
    code, out = run(proj, accept=["templates/cycle.md", "nope/x.md"], capsys=capsys)
    assert code == 1
    assert "refused  --accept templates/cycle.md — not a custom managed path" in out
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
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"cycle v2\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert "refreshed templates/cycle.md — written by tagteam 1.0.0" in out
    assert (proj / "templates" / "cycle.md").read_bytes() == b"cycle v2\n"
    m = manifest(proj)
    assert m["tagteam"] == "2.0.0"
    assert m["files"]["templates/cycle.md"]["tagteam"] == "2.0.0"
    assert m["files"]["templates/feedback.md"]["tagteam"] == "1.0.0"       # untouched keeps A


def test_custom_path_among_framework_refreshes(proj, monkeypatch, tmp_path, capsys):
    fresh(proj)
    (proj / "templates" / "feedback.md").write_text("mine\n")
    monkeypatch.setattr(fw, "package_version", lambda: "9.0.0")
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"cycle v9\n")
    code, out = run(proj, capsys=capsys)
    assert code == 0
    assert (proj / "templates" / "cycle.md").read_bytes() == b"cycle v9\n"
    assert (proj / "templates" / "feedback.md").read_text() == "mine\n"
    m = manifest(proj)
    assert "templates/feedback.md" not in m["files"] and m["files"]["templates/cycle.md"]["tagteam"] == "9.0.0"
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


def test_crash_before_manifest_write_converges(proj, monkeypatch, capsys):
    monkeypatch.setattr(fw, "_write_manifest", lambda plan: None)     # files land, manifest does not
    fresh(proj)
    assert not (proj / fw.MANIFEST_NAME).exists()
    monkeypatch.undo()
    no_cli(monkeypatch)
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
    real = proj / "real-cycle.md"; real.write_bytes((DATA / "templates" / "cycle.md").read_bytes())
    t = proj / "templates" / "cycle.md"; t.unlink(); t.symlink_to(real)
    _assert_refused_unchanged(proj, "templates/cycle.md", capsys, "templates/cycle.md is a symlink")


def test_symlinked_templates_dir_outside_project_is_refused(proj, tmp_path, capsys):
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "cycle.md").write_text("theirs\n")
    (proj / "templates").symlink_to(outside)
    before = snapshot(outside)
    code, out = run(proj, capsys=capsys)
    assert code == 1
    for src in (DATA / "templates").glob("*.md"):
        assert f"refused  templates/{src.name} — unsupported filesystem shape (templates is a symlink)" in out
    assert snapshot(outside) == before
    assert "templates/" not in json.dumps(manifest(proj)["files"])


def test_directory_at_managed_path_and_symlinked_skill_dir_are_refused(proj, tmp_path, capsys):
    (proj / "templates" / "cycle.md").mkdir(parents=True)
    elsewhere = tmp_path / "skill-elsewhere"; elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_bytes(PACKAGED_SKILL)
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".claude" / "skills" / "handoff").symlink_to(elsewhere)
    code, out = run(proj, capsys=capsys)
    assert code == 1
    assert "refused  templates/cycle.md — unsupported filesystem shape (templates/cycle.md is a directory)" in out
    assert f"refused  {SKILL} — unsupported filesystem shape (.claude/skills/handoff is a symlink)" in out
    assert (proj / ".claude" / "skills" / "handoff").is_symlink()
    assert (elsewhere / "SKILL.md").read_bytes() == PACKAGED_SKILL and (proj / "templates" / "cycle.md").is_dir()


# ---------------------------------------------------------------------------
# preimage re-checks between classification and apply
# ---------------------------------------------------------------------------

def test_file_appearing_at_absent_path_is_refused(proj):
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "templates").mkdir()
    (proj / "templates" / "cycle.md").write_text("appeared\n")
    fw.apply(plan)
    it = next(i for i in plan.items if i.rel == "templates/cycle.md")
    assert it.outcome.startswith("refused: changed since classification")
    assert (proj / "templates" / "cycle.md").read_text() == "appeared\n"
    assert "templates/cycle.md" not in fw.projected_manifest(plan)["files"]


def test_file_swapped_for_symlink_is_refused(proj, monkeypatch, tmp_path):
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "9.0.0")
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"v9\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    it = next(i for i in plan.items if i.rel == "templates/cycle.md")
    assert it.action == "refresh"
    t = proj / "templates" / "cycle.md"
    real = proj / "same-bytes.md"; real.write_bytes(t.read_bytes())
    t.unlink(); t.symlink_to(real)
    fw.apply(plan)
    assert it.outcome == "refused: changed since classification: templates/cycle.md is a symlink"
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
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"v2\n")
    plan = fw.build_plan(proj, data_dir=DATA)
    (proj / "templates" / "cycle.md").write_text("user edit in between\n")
    fw.apply(plan)
    it = next(i for i in plan.items if i.rel == "templates/cycle.md")
    assert it.outcome.startswith("refused") and (proj / "templates" / "cycle.md").read_text() == "user edit in between\n"
    assert manifest(proj)["files"]["templates/cycle.md"]["tagteam"] == "1.0.0"      # left as it was


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
        (p / "templates" / "feedback.md").write_text(f"custom {i}\n")
        projects.append(p)
    capsys.readouterr()
    monkeypatch.setattr(fw, "package_version", lambda: "2.0.0")
    replace_source(monkeypatch, tmp_path, "templates/cycle.md", b"v2\n")
    # preview first: nothing moves
    befores = [snapshot(p) for p in projects]
    assert upgrade_command(["--preview"]) == 0
    out = capsys.readouterr().out
    assert [snapshot(p) for p in projects] == befores and "Previewed 2 project(s); nothing written." in out
    assert upgrade_command() == 0
    out = capsys.readouterr().out
    for i, p in enumerate(projects):
        assert (p / "templates" / "cycle.md").read_bytes() == b"v2\n"
        assert (p / "templates" / "feedback.md").read_text() == f"custom {i}\n"
        assert f"accept with: tagteam setup {p.resolve()} --accept templates/feedback.md" in out
    assert out.count("refreshed templates/cycle.md") == 2 and "accepted" not in out
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
    (p / "templates" / "cycle.md").mkdir(parents=True)
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
    monkeypatch.setattr("sys.argv", ["tagteam", "setup", str(proj), "--accept", "templates/cycle.md"])
    assert cli.main() == 1            # nothing custom to accept


def test_version_line_distinguishes_package_manifest_plugin(proj, monkeypatch):
    monkeypatch.setattr(fw, "package_version", lambda: "1.0.0")
    assert fw.version_line(proj) == "package 1.0.0 · manifest none · plugin: not installed (claude CLI not found)"
    fresh(proj)
    monkeypatch.setattr(fw, "package_version", lambda: "1.1.0")
    line = fw.version_line(proj)
    assert line.startswith("package 1.1.0 · manifest 1.0.0 (written 20") and line.endswith("plugin: not installed (claude CLI not found)")
