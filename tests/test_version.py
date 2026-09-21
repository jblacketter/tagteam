"""Phase 64: `tagteam.__version__` answers from the source tree when there is
one, from the installed metadata otherwise; `tagteam --version` says which
copy is running."""
from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

import tagteam
from tagteam import cli

REPO = Path(__file__).resolve().parent.parent
DECLARED = re.search(r'^version[ \t]*=[ \t]*"([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M).group(1)

GOOD = '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "tagteam"\nversion = "7.8.9"\n\n[tool.x]\nversion = "0.0.0"\n'


def _tree(tmp_path: Path, pyproject: str | bytes | None) -> Path:
    """<tmp>/root/tagteam/ with an optional pyproject.toml beside the package."""
    pkg = tmp_path / "root" / "tagteam"
    pkg.mkdir(parents=True)
    if isinstance(pyproject, bytes):
        (pkg.parent / "pyproject.toml").write_bytes(pyproject)
    elif pyproject is not None:
        (pkg.parent / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return pkg


@pytest.fixture
def metadata(monkeypatch):
    """Installed metadata says something the tree does not."""
    monkeypatch.setattr(tagteam, "_pkg_version", lambda name: "0.0.1")


def test_this_checkout_reports_what_pyproject_declares_whatever_the_metadata_says(metadata):
    """The incident: an editable install's dist-info is frozen at install time."""
    assert tagteam._source_tree_version() == DECLARED
    assert tagteam._resolve_version() == DECLARED != "0.0.1"
    assert tagteam.__version__ == DECLARED


def test_source_tree_wins_over_metadata(tmp_path, metadata):
    assert tagteam._resolve_version(_tree(tmp_path, GOOD)) == "7.8.9"


def test_no_pyproject_beside_the_package_means_installed_metadata(tmp_path, metadata):
    pkg = _tree(tmp_path, None)                       # a wheel install's layout
    assert tagteam._source_tree_version(pkg) is None and tagteam._resolve_version(pkg) == "0.0.1"


@pytest.mark.parametrize("pyproject", [
    GOOD.replace('name = "tagteam"', 'name = "somebody-else"'),                 # another project's file
    GOOD.replace('version = "7.8.9"', ''),                                        # no version in [project]
    GOOD.replace('version = "7.8.9"', '# version = "7.8.9"'),                     # commented out
    GOOD.replace('version = "7.8.9"', 'version = "7.8.9"\nversion = "7.9.0"'),    # ambiguous
    GOOD.replace('name = "tagteam"', 'name = "tagteam"\nname = "other"'),
    GOOD.replace('version = "7.8.9"', 'version = "7.8.9"\n version = "9.9.9"'),     # indented duplicate (review r1)
    GOOD.replace('version = "7.8.9"', 'version = "7.8.9"\n\tversion = "9.9.9"'),
    GOOD.replace('name = "tagteam"', 'name = "tagteam"\n  name = "other"'),
    GOOD.replace('version = "7.8.9"', 'version = "7.8.9"\n"version" = "9.9.9"'),    # quoted spelling of the same key
    GOOD.replace('version = "7.8.9"', '  version = "7.8.9"'),                        # indented only: not the release shape
    GOOD.replace('version = "7.8.9"', "version = '7.8.9'"),                       # not the shape release.py writes
    GOOD.replace('version = "7.8.9"', 'version = ""'),
    GOOD.replace('version = "7.8.9"', 'dynamic = ["version"]'),
    GOOD.replace("[project]", "[project-ish]"),                                   # no [project] table
    GOOD + '\n[project]\nname = "tagteam"\nversion = "1.0.0"\n',                  # two [project] tables
    '[tool.x]\nname = "tagteam"\nversion = "9.9.9"\n',                            # only a tool table
    "",
    b"\xff\xfe[project]\nname = \"tagteam\"\nversion = \"7.8.9\"\n",              # invalid UTF-8
], ids=["foreign-name", "no-version", "commented", "dup-version", "dup-name",
        "indented-dup-version", "tab-indented-dup-version", "indented-dup-name", "quoted-key-dup-version",
        "indented-only", "single-quoted",
        "empty", "dynamic", "no-table", "two-tables", "tool-table-only", "blank", "bad-utf8"])
def test_any_doubt_falls_back_to_metadata_without_raising(tmp_path, metadata, pyproject):
    pkg = _tree(tmp_path, pyproject)
    assert tagteam._source_tree_version(pkg) is None
    assert tagteam._resolve_version(pkg) == "0.0.1"


@pytest.mark.parametrize("header", ["[project] # package metadata", "[project]\t#x", "[project]   ", "[project] # a [bracket] in it"])
def test_commented_project_header_still_wins_over_stale_metadata(tmp_path, metadata, header):
    """Review r1: under DOTALL the header comment swallowed the whole table,
    which silently restored the stale-metadata behaviour."""
    text = f'{header}\nname = "tagteam"\nversion = "7.8.9"\n\n[tool.x]\nversion = "0.0.0"\nname = "nope"\n'
    pkg = _tree(tmp_path, text)
    assert tagteam._source_tree_version(pkg) == "7.8.9" and tagteam._resolve_version(pkg) == "7.8.9"


def test_tool_table_version_is_never_picked_up(tmp_path, metadata):
    text = '[tool.x]\nversion = "0.0.0"\n\n[project]\nname = "tagteam"   # ours\nversion = "7.8.9"  # bumped by release.py\n'
    assert tagteam._resolve_version(_tree(tmp_path, text)) == "7.8.9"


def test_unreadable_pyproject_and_odd_paths_never_raise(tmp_path, metadata):
    pkg = _tree(tmp_path, None)
    (pkg.parent / "pyproject.toml").mkdir()            # a directory where the file should be
    assert tagteam._resolve_version(pkg) == "0.0.1"
    assert tagteam._resolve_version(tmp_path / "does" / "not" / "exist") == "0.0.1"
    assert tagteam._source_tree_version(tmp_path / "\0bad" / "tagteam") is None     # ValueError, not OSError


def test_no_tree_and_no_metadata_is_the_old_unknown(tmp_path, monkeypatch):
    def missing(name):
        raise PackageNotFoundError(name)
    monkeypatch.setattr(tagteam, "_pkg_version", missing)
    assert tagteam._resolve_version(_tree(tmp_path, None)) == "0.0.0+unknown"


@pytest.mark.parametrize("flag", ["--version", "-V", "-v", "version", "VERSION"])
def test_version_command(monkeypatch, capsys, tmp_path, flag):
    monkeypatch.chdir(tmp_path)                        # outside any project
    monkeypatch.setattr(sys, "argv", ["tagteam", flag])
    assert cli.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"tagteam {tagteam.__version__}"
    assert Path(out[1].strip()) == Path(tagteam.__file__).resolve().parent and Path(out[1].strip()).is_dir()


@pytest.mark.parametrize("flag", ["--version", "-V", "version"])
def test_version_command_is_a_read_under_read_only(monkeypatch, capsys, tmp_path, flag):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
    monkeypatch.setattr(sys, "argv", ["tagteam", flag])
    assert cli.main() == 0
    assert capsys.readouterr().out.startswith(f"tagteam {tagteam.__version__}\n")
    assert cli.read_only_refusal([flag]) is None
    assert cli.read_only_refusal(["setup"]) is not None            # the guard itself still refuses writes
    assert not any(tmp_path.iterdir())                              # nothing created outside a project


def test_help_lists_version():
    assert "--version" in cli.HELP_TEXT
