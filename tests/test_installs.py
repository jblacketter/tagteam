"""Phase 65: doctor and `tagteam state` see a tagteam installed in the
project's own venv. Look-only: lstat, bounded reads, nothing through a link."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import tagteam
from tagteam import diagnostics as dg
from tagteam import framework as fw
from tagteam import installs as ins
from tests._plugin_env import no_cli

RUNNING = tagteam.__version__
OTHER = "3.12.0" if RUNNING != "3.12.0" else "3.11.0"


def _venv(root: Path, version: str | None, *, name=".venv", layout="posix", editable=False,
          metadata: str | None = None) -> Path:
    sp = root / name / ("lib/python3.12/site-packages" if layout == "posix" else "Lib/site-packages")
    di = sp / f"tagteam-{version or '0'}.dist-info"
    di.mkdir(parents=True)
    body = metadata if metadata is not None else f"Metadata-Version: 2.1\nName: tagteam\nVersion: {version}\n\nVersion: 9.9.9 in the body\n"
    (di / "METADATA").write_text(body)
    if editable:
        (sp / f"__editable__.tagteam-{version}.pth").write_text("x")
    return di


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def proj(tmp_path, monkeypatch) -> Path:
    no_cli(monkeypatch)
    p = tmp_path / "proj"; p.mkdir()
    (p / "tagteam.yaml").write_text("agents:\n  lead: {name: claude}\n  reviewer: {name: codex}\n")
    return p


def test_a_different_version_is_a_warning_everywhere(proj, capsys):
    _venv(proj, OTHER)
    before = _snapshot(proj)
    rows = ins.observe_installs(proj)
    assert [(r["venv"], r["version"], r["state"]) for r in rows] == [(".venv", OTHER, "differs")]
    rep = dg.build_report(proj)
    assert rep.installs == rows and rep.counts["warn"] == 1 and rep.findings == []   # not a legacy-workflow finding
    text = dg.format_report(rep)
    assert "Other tagteam installs" in text
    assert f"  warn  .venv/lib/python3.12/site-packages/tagteam-{OTHER}.dist-info — tagteam {OTHER}; running {RUNNING}." in text
    assert "run different code; upgrade or remove it" in text and "findings: 1 warn" in text
    assert rep.to_json()["installs"] == rows and rep.to_json()["schema"] == 1
    assert fw.version_line(proj).endswith(f" · .venv: tagteam {OTHER} (differs from the running {RUNNING})")
    assert _snapshot(proj) == before                                                 # look-only


def test_newer_copy_is_worded_as_different_not_older(proj):
    _venv(proj, "99.0.0")
    line = ins.describe(ins.observe_installs(proj)[0], RUNNING)
    assert "different code" in line and "older" not in line and "newer" not in line


def test_same_version_editable_and_unknown_are_listed_without_a_warning(proj):
    _venv(proj, RUNNING)
    _venv(proj, OTHER, name="venv", editable=True)
    rows = {r["venv"]: r for r in ins.observe_installs(proj)}
    assert rows[".venv"]["state"] == "same" and rows["venv"]["state"] == "editable"
    rep = dg.build_report(proj)
    assert rep.counts["warn"] == 0 and fw.version_line(proj).count(".venv:") == 0
    text = dg.format_report(rep)
    assert "  note  .venv/" in text and "same as the running one" in text and "editable link to a source tree" in text


@pytest.mark.parametrize("metadata", ["", "Name: tagteam\n", "Version: 1.0\nVersion: 2.0\n", "Name: x\n\nVersion: 3.12.0\n",
                                       "Version:\n",
                                       "Version: 3.12.0\nVersion:\n",                  # review r1: a second header
                                       "Version: 3.12.0\nVersion: invalid value\n",     # counts whatever its value
                                       "Version:\nVersion: 3.12.0\n",
                                       "version: 3.12.0\nVERSION: 3.12.0\n",           # header names are case-insensitive
                                       "Version: 3.12.0 extra\n", "Version: ../../x\n"],
                         ids=["empty", "no-version", "duplicate", "only-in-body", "blank-value", "dup-blank",
                              "dup-invalid", "blank-then-valid", "dup-other-case", "two-tokens", "not-a-version"])
def test_malformed_metadata_is_unknown_never_a_mismatch(proj, metadata):
    _venv(proj, OTHER, metadata=metadata)
    assert [r["state"] for r in ins.observe_installs(proj)] == ["unknown"]
    assert dg.build_report(proj).counts["warn"] == 0 and ".venv:" not in fw.version_line(proj)


def test_metadata_that_is_not_a_plain_file_is_unknown(proj, tmp_path):
    di = _venv(proj, OTHER)
    (di / "METADATA").unlink()
    outside = tmp_path / "outside-METADATA"; outside.write_text(f"Version: {OTHER}\n")
    (di / "METADATA").symlink_to(outside)
    assert [r["state"] for r in ins.observe_installs(proj)] == ["unknown"]


def test_windows_layout(proj):
    _venv(proj, OTHER, layout="windows")
    rows = ins.observe_installs(proj)
    assert len(rows) == 1 and rows[0]["state"] == "differs" and rows[0]["path"].lower().startswith(".venv/lib/site-packages/")


def test_nothing_is_read_through_a_symlink_at_any_level(proj, tmp_path):
    elsewhere = tmp_path / "elsewhere"; elsewhere.mkdir()
    real = _venv(elsewhere, OTHER)                                   # elsewhere/.venv/lib/python3.12/site-packages/…
    # 1. the venv itself is a link
    (proj / ".venv").symlink_to(elsewhere / ".venv")
    rows = ins.observe_installs(proj)
    assert [(r["state"], r["detail"]) for r in rows] == [("not scanned", ".venv is a symlink")]
    assert dg.build_report(proj).counts["warn"] == 0
    (proj / ".venv").unlink()
    # 2. lib, python3.12 and site-packages linked in turn: nothing found, nothing followed
    for depth in (("lib",), ("lib", "python3.12"), ("lib", "python3.12", "site-packages")):
        base = proj / ".venv"
        target = elsewhere / ".venv"
        for part in depth[:-1]:
            base = base / part; target = target / part
        base.mkdir(parents=True, exist_ok=True)
        (base / depth[-1]).symlink_to(target / depth[-1])
        assert ins.observe_installs(proj) == [], depth
        import shutil
        (base / depth[-1]).unlink(); shutil.rmtree(proj / ".venv")
    # 3. the dist-info directory itself is a link
    sp = proj / ".venv" / "lib" / "python3.12" / "site-packages"; sp.mkdir(parents=True)
    (sp / real.name).symlink_to(real)
    rows = ins.observe_installs(proj)
    assert [r["state"] for r in rows] == ["not scanned"] and rows[0]["detail"].endswith("is a symlink")


def test_the_venv_we_are_running_from_is_not_a_shadow(proj):
    _venv(proj, OTHER)
    assert ins.observe_installs(proj, prefix=proj / ".venv") == []
    assert len(ins.observe_installs(proj, prefix=sys.prefix)) == 1


def test_no_venv_no_section_no_clause(proj):
    assert ins.observe_installs(proj) == []
    assert "Other tagteam installs" not in dg.format_report(dg.build_report(proj))
    (proj / ".venv").write_text("a file, not a venv\n")
    assert ins.observe_installs(proj) == []


def test_doctor_and_state_report_it_under_read_only(proj, monkeypatch, capsys):
    from tagteam import cli
    _venv(proj, OTHER)
    before = _snapshot(proj)
    monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(sys, "argv", ["tagteam", "doctor", "--json"])
    assert cli.main() in (0, 1)
    data = json.loads(capsys.readouterr().out)
    assert data["installs"][0]["state"] == "differs" and data["counts"]["warn"] == 1
    assert _snapshot(proj) == before


# ---------------------------------------------------------------------------
# impl review round 1 — the read cap and the check-then-open window
# ---------------------------------------------------------------------------

def test_header_block_cut_off_by_the_read_cap_is_unknown(proj):
    """Another Version header may sit beyond the cap, so an incomplete header
    block proves nothing. A complete block followed by a long body is fine —
    that is every real METADATA (the README is the body)."""
    padding = "".join(f"Classifier: padding {i:06d}\n" for i in range(ins.METADATA_LIMIT // 27 + 50))
    _venv(proj, OTHER, metadata=f"Version: {OTHER}\n{padding}Version: 9.9.9\n\nbody\n")
    assert len(padding) > ins.METADATA_LIMIT
    assert [r["state"] for r in ins.observe_installs(proj)] == ["unknown"]
    assert dg.build_report(proj).counts["warn"] == 0 and ".venv:" not in fw.version_line(proj)
    import shutil; shutil.rmtree(proj / ".venv")
    _venv(proj, OTHER, metadata=f"Metadata-Version: 2.1\nVersion: {OTHER}\n\n" + "x" * (ins.METADATA_LIMIT * 2))
    assert [(r["version"], r["state"]) for r in ins.observe_installs(proj)] == [(OTHER, "differs")]


def test_metadata_that_is_not_utf8_is_unknown(proj):
    di = _venv(proj, OTHER)
    (di / "METADATA").write_bytes(b"Version: 3.12.0\n\xff\xfe\n\nbody\n")
    assert [r["state"] for r in ins.observe_installs(proj)] == ["unknown"]


@pytest.mark.parametrize("swap", ["symlink", "fifo"])
def test_metadata_replaced_between_the_check_and_the_open_is_not_followed(proj, tmp_path, monkeypatch, swap):
    """Deterministic version of the race: the path is a plain file when it is
    lstat-ed and something else by the time it is opened. O_NOFOLLOW refuses
    the link; O_NONBLOCK + fstat refuse the FIFO without blocking."""
    from tagteam import safe_read
    di = _venv(proj, OTHER)
    outside = tmp_path / "outside-METADATA"; outside.write_text("Version: 9.8.7\n\n")
    real = safe_read._lstat_chain

    def lstat_then_swap(root, rel):
        r = real(root, rel)
        if rel.endswith("/METADATA") and r.state == "file":
            (di / "METADATA").unlink()
            if swap == "symlink":
                (di / "METADATA").symlink_to(outside)
            else:
                os.mkfifo(di / "METADATA")
        return r
    monkeypatch.setattr(safe_read, "_lstat_chain", lstat_then_swap)
    rows = ins.observe_installs(proj)
    assert [(r["version"], r["state"]) for r in rows] == [(None, "unknown")]          # never 9.8.7, never a hang
    assert outside.read_text() == "Version: 9.8.7\n\n"
