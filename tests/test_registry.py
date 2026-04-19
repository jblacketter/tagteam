"""Tests for the project registry module."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from tagteam.registry import (
    register_project,
    get_registered_projects,
    unregister_project,
    _read_registry,
    _write_registry,
)


@pytest.fixture
def mock_registry(tmp_path):
    """Patch registry constants to use a temp directory."""
    reg_dir = tmp_path / ".tagteam"
    reg_file = reg_dir / "projects.json"
    with patch("tagteam.registry.REGISTRY_DIR", reg_dir), \
         patch("tagteam.registry.REGISTRY_FILE", reg_file):
        yield reg_dir, reg_file


class TestRegisterProject:
    """Tests for register_project function."""

    def test_creates_registry_dir_and_file(self, tmp_path, mock_registry):
        reg_dir, reg_file = mock_registry
        project = tmp_path / "myproject"
        project.mkdir()
        register_project(str(project))
        data = json.loads(reg_file.read_text())
        assert str(project.resolve()) in data

    def test_idempotent_registration(self, tmp_path, mock_registry):
        _, reg_file = mock_registry
        project = tmp_path / "myproject"
        project.mkdir()
        register_project(str(project))
        register_project(str(project))
        data = json.loads(reg_file.read_text())
        assert data.count(str(project.resolve())) == 1

    def test_multiple_projects(self, tmp_path, mock_registry):
        _, reg_file = mock_registry
        p1 = tmp_path / "project1"
        p2 = tmp_path / "project2"
        p1.mkdir()
        p2.mkdir()
        register_project(str(p1))
        register_project(str(p2))
        data = json.loads(reg_file.read_text())
        assert len(data) == 2


class TestGetRegisteredProjects:
    """Tests for get_registered_projects function."""

    def test_returns_empty_when_no_registry(self, mock_registry):
        assert get_registered_projects() == []

    def test_filters_nonexistent_directories(self, tmp_path, mock_registry):
        reg_dir, reg_file = mock_registry
        reg_dir.mkdir(parents=True)
        existing = tmp_path / "exists"
        existing.mkdir()
        reg_file.write_text(json.dumps([
            str(existing),
            str(tmp_path / "gone"),
        ]))
        result = get_registered_projects()
        assert result == [str(existing)]
        # Verify registry was cleaned
        cleaned = json.loads(reg_file.read_text())
        assert len(cleaned) == 1

    def test_handles_corrupt_json(self, mock_registry):
        reg_dir, reg_file = mock_registry
        reg_dir.mkdir(parents=True)
        reg_file.write_text("not json{{{")
        assert get_registered_projects() == []

    def test_handles_non_list_json(self, mock_registry):
        reg_dir, reg_file = mock_registry
        reg_dir.mkdir(parents=True)
        reg_file.write_text('{"key": "value"}')
        assert get_registered_projects() == []


class TestUnregisterProject:
    """Tests for unregister_project function."""

    def test_removes_project(self, tmp_path, mock_registry):
        reg_dir, reg_file = mock_registry
        reg_dir.mkdir(parents=True)
        project = str(tmp_path / "myproject")
        reg_file.write_text(json.dumps([project]))
        unregister_project(project)
        data = json.loads(reg_file.read_text())
        assert data == []

    def test_no_error_when_not_registered(self, mock_registry):
        # Should not raise even with no registry file
        unregister_project("/some/path")
