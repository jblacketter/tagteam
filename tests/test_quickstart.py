"""Tests for quickstart command and onboarding helpers."""

from unittest.mock import MagicMock, patch

from ai_handoff.setup import needs_setup, run_setup
from ai_handoff.cli import needs_init, run_init, quickstart_command


# --- needs_setup tests ---

class TestNeedsSetup:
    def test_all_present(self, tmp_path):
        """Setup complete when skills + templates + checklists all exist."""
        (tmp_path / ".claude" / "skills" / "handoff").mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("skill")
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "phase_plan.md").write_text("template")
        (tmp_path / "docs" / "checklists").mkdir(parents=True)
        (tmp_path / "docs" / "checklists" / "code_review.md").write_text("checklist")

        assert needs_setup(str(tmp_path)) is False

    def test_missing_skills(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "phase_plan.md").write_text("template")
        (tmp_path / "docs" / "checklists").mkdir(parents=True)
        (tmp_path / "docs" / "checklists" / "code_review.md").write_text("checklist")

        assert needs_setup(str(tmp_path)) is True

    def test_missing_templates(self, tmp_path):
        (tmp_path / ".claude" / "skills" / "handoff").mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("skill")
        (tmp_path / "docs" / "checklists").mkdir(parents=True)
        (tmp_path / "docs" / "checklists" / "code_review.md").write_text("checklist")

        assert needs_setup(str(tmp_path)) is True

    def test_missing_checklists(self, tmp_path):
        (tmp_path / ".claude" / "skills" / "handoff").mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("skill")
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "phase_plan.md").write_text("template")

        assert needs_setup(str(tmp_path)) is True

    def test_empty_templates_dir(self, tmp_path):
        """Templates dir exists but has no .md files."""
        (tmp_path / ".claude" / "skills" / "handoff").mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("skill")
        (tmp_path / "templates").mkdir()
        (tmp_path / "docs" / "checklists").mkdir(parents=True)
        (tmp_path / "docs" / "checklists" / "code_review.md").write_text("checklist")

        assert needs_setup(str(tmp_path)) is True


# --- needs_init / run_init tests ---

class TestNeedsInit:
    def test_no_config(self, tmp_path):
        assert needs_init(str(tmp_path)) is True

    def test_config_exists(self, tmp_path):
        (tmp_path / "ai-handoff.yaml").write_text("agents: {}")
        assert needs_init(str(tmp_path)) is False


class TestRunInit:
    def test_skips_when_config_exists(self, tmp_path):
        (tmp_path / "ai-handoff.yaml").write_text("agents: {}")
        result = run_init(str(tmp_path))
        assert result is True

    def test_non_tty_fails_fast(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        result = run_init(str(tmp_path))
        assert result is False


# --- run_setup tests ---

class TestRunSetup:
    @patch("ai_handoff.setup.main")
    def test_skips_when_complete(self, mock_main, tmp_path):
        # Set up all required files
        (tmp_path / ".claude" / "skills" / "handoff").mkdir(parents=True)
        (tmp_path / ".claude" / "skills" / "handoff" / "SKILL.md").write_text("skill")
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "phase_plan.md").write_text("t")
        (tmp_path / "docs" / "checklists").mkdir(parents=True)
        (tmp_path / "docs" / "checklists" / "code_review.md").write_text("c")

        run_setup(str(tmp_path))
        mock_main.assert_not_called()

    @patch("ai_handoff.setup.main")
    def test_runs_when_needed(self, mock_main, tmp_path):
        run_setup(str(tmp_path))
        mock_main.assert_called_once_with(str(tmp_path))


# --- quickstart tests ---

class TestQuickstart:
    @patch("ai_handoff.session.ensure_session", return_value="created")
    @patch("ai_handoff.cli.run_init", return_value=True)
    @patch("ai_handoff.setup.run_setup")
    def test_happy_path(self, mock_setup, mock_init, mock_session, tmp_path):
        result = quickstart_command(["--dir", str(tmp_path)])
        assert result == 0
        mock_setup.assert_called_once()
        mock_init.assert_called_once()
        mock_session.assert_called_once()

    @patch("ai_handoff.session.ensure_session", return_value="created")
    @patch("ai_handoff.cli.run_init", return_value=True)
    @patch("ai_handoff.setup.run_setup")
    def test_passes_backend(self, mock_setup, mock_init, mock_session, tmp_path):
        quickstart_command(["--dir", str(tmp_path), "--backend", "tmux"])
        # ensure_session called with backend="tmux"
        assert mock_session.call_args[0][1] == "tmux"

    @patch("ai_handoff.session.ensure_session", return_value="exists")
    @patch("ai_handoff.cli.run_init", return_value=True)
    @patch("ai_handoff.setup.run_setup")
    def test_existing_session_returns_success(
        self, mock_setup, mock_init, mock_session, tmp_path
    ):
        result = quickstart_command(["--dir", str(tmp_path)])
        assert result == 0

    @patch("ai_handoff.session.ensure_session", return_value="manual")
    @patch("ai_handoff.cli.run_init", return_value=True)
    @patch("ai_handoff.setup.run_setup")
    def test_manual_session_returns_success(
        self, mock_setup, mock_init, mock_session, tmp_path
    ):
        result = quickstart_command(["--dir", str(tmp_path)])
        assert result == 0

    @patch("ai_handoff.session.ensure_session", return_value="error")
    @patch("ai_handoff.cli.run_init", return_value=True)
    @patch("ai_handoff.setup.run_setup")
    def test_session_error_returns_failure(
        self, mock_setup, mock_init, mock_session, tmp_path
    ):
        result = quickstart_command(["--dir", str(tmp_path)])
        assert result == 1

    @patch("ai_handoff.session.ensure_session", return_value="created")
    @patch("ai_handoff.cli.run_init", return_value=False)
    @patch("ai_handoff.setup.run_setup")
    def test_init_failure_returns_error(
        self, mock_setup, mock_init, mock_session, tmp_path
    ):
        result = quickstart_command(["--dir", str(tmp_path)])
        assert result == 1
        mock_session.assert_not_called()


# --- ensure_session tests ---

class TestEnsureSession:
    @patch("ai_handoff.session._tmux_supported", return_value=True)
    @patch("ai_handoff.session.subprocess")
    @patch("ai_handoff.session.session_exists", return_value=True)
    def test_existing_tmux_attaches_and_returns_exists(
        self, mock_exists, mock_subprocess, mock_tmux_supported
    ):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "tmux", launch=False)
        assert result == "exists"
        # Verify tmux attach was called
        mock_subprocess.run.assert_called_once()
        call_args = mock_subprocess.run.call_args[0][0]
        assert "attach" in call_args

    @patch("ai_handoff.session._tmux_supported", return_value=True)
    @patch("ai_handoff.session.create_tmux_session", return_value=True)
    @patch("ai_handoff.session.session_exists", return_value=False)
    def test_new_tmux_returns_created(self, mock_exists, mock_create, mock_tmux_supported):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "tmux", launch=False)
        assert result == "created"

    @patch("ai_handoff.session._iterm2_supported", return_value=True)
    @patch("ai_handoff.iterm._read_session_file", return_value={"tabs": {}})
    def test_existing_iterm_returns_exists(self, mock_read, mock_iterm_supported):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "iterm2", launch=False)
        assert result == "exists"

    @patch("ai_handoff.session.create_manual_session", return_value=True)
    def test_manual_backend_returns_manual(self, mock_manual):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "manual", launch=False)
        assert result == "manual"

    def test_invalid_backend_returns_error(self):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "invalid", launch=False)
        assert result == "error"

    @patch("ai_handoff.session.shutil.which", return_value=None)
    def test_unavailable_tmux_returns_error(self, mock_which):
        from ai_handoff.session import ensure_session
        result = ensure_session(".", "tmux", launch=False)
        assert result == "error"


class TestSessionBackendDetection:
    @patch("ai_handoff.session.sys.platform", "win32")
    @patch("ai_handoff.session.shutil.which", return_value=None)
    def test_default_backend_falls_back_to_manual(self, mock_which):
        from ai_handoff.session import default_backend
        assert default_backend() == "manual"

    @patch("ai_handoff.session._tmux", side_effect=FileNotFoundError)
    def test_session_exists_returns_false_without_tmux(self, mock_tmux):
        from ai_handoff.session import session_exists
        assert session_exists() is False


class TestQuickstartValidation:
    def test_invalid_backend_returns_error(self):
        result = quickstart_command(["--backend", "typo"])
        assert result == 1
