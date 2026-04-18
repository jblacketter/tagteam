"""Tests for quickstart command and onboarding helpers."""

import os
from unittest.mock import MagicMock, patch

from ai_handoff.setup import needs_setup, run_setup
from ai_handoff.cli import (
    HANDOFF_EXPLAINER,
    init_command,
    needs_init,
    quickstart_command,
    run_init,
)


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
    @patch("ai_handoff.iterm._any_session_alive", return_value=True)
    @patch(
        "ai_handoff.iterm._read_session_file",
        return_value={"tabs": {"lead": {"session_id": "x"}}},
    )
    def test_existing_iterm_returns_exists(
        self, mock_read, mock_alive, mock_iterm_supported
    ):
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

    @patch("ai_handoff.session._ITERM_APP_PATHS", ("/nonexistent/iTerm.app",))
    @patch("ai_handoff.session.sys.platform", "darwin")
    @patch("ai_handoff.session.shutil.which", return_value="/usr/bin/osascript")
    def test_iterm2_unsupported_when_app_not_installed(self, mock_which):
        """On macOS without iTerm2.app, _iterm2_supported must return False."""
        from ai_handoff.session import _iterm2_supported
        assert _iterm2_supported() is False

    @patch("ai_handoff.session.sys.platform", "darwin")
    @patch("ai_handoff.session.shutil.which", return_value="/usr/bin/osascript")
    def test_iterm2_supported_when_app_installed(self, mock_which, tmp_path):
        """When iTerm2.app exists, _iterm2_supported returns True."""
        fake_app = tmp_path / "iTerm.app"
        fake_app.mkdir()
        with patch(
            "ai_handoff.session._ITERM_APP_PATHS", (str(fake_app),)
        ):
            from ai_handoff.session import _iterm2_supported
            assert _iterm2_supported() is True

    @patch("ai_handoff.session._ITERM_APP_PATHS", ("/nonexistent/iTerm.app",))
    @patch("ai_handoff.session.sys.platform", "darwin")
    @patch("ai_handoff.session.shutil.which", return_value="/usr/bin/osascript")
    def test_default_backend_picks_tmux_on_mac_without_iterm2(self, mock_which):
        """Mac with tmux but no iTerm2 → default_backend() returns tmux."""
        from ai_handoff.session import default_backend
        with patch(
            "ai_handoff.session._tmux_supported", return_value=True
        ):
            assert default_backend() == "tmux"


class TestQuickstartValidation:
    def test_invalid_backend_returns_error(self):
        result = quickstart_command(["--backend", "typo"])
        assert result == 1


# --- init prompt simplification tests ---

class TestInitPrompts:
    def _run_init_with_inputs(self, tmp_path, inputs, monkeypatch):
        """Run init_command in tmp_path with a queue of mocked inputs."""
        queue = list(inputs)
        monkeypatch.setattr("builtins.input", lambda prompt="": queue.pop(0))
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            return init_command(show_explainer=False)
        finally:
            os.chdir(original_dir)

    def test_prompts_lead_then_reviewer(self, tmp_path, monkeypatch, capsys):
        """First input becomes lead, second becomes reviewer — no role prompt."""
        prompts_shown: list[str] = []

        def fake_input(prompt=""):
            prompts_shown.append(prompt)
            return ["alice", "bob"][len(prompts_shown) - 1]

        monkeypatch.setattr("builtins.input", fake_input)
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            init_command(show_explainer=False)
        finally:
            os.chdir(original_dir)

        assert prompts_shown == ["Lead agent name: ", "Reviewer agent name: "]
        config = (tmp_path / "ai-handoff.yaml").read_text()
        assert "name: alice" in config
        assert "name: bob" in config
        # No "role" prompt anywhere
        assert not any("role" in p.lower() for p in prompts_shown)

    def test_rejects_empty_agent_name(self, tmp_path, monkeypatch):
        """Empty name is re-prompted; first non-empty wins."""
        self._run_init_with_inputs(tmp_path, ["", "alice", "bob"], monkeypatch)
        config = (tmp_path / "ai-handoff.yaml").read_text()
        assert "name: alice" in config
        assert "name: bob" in config

    def test_preserves_casing(self, tmp_path, monkeypatch):
        """Agent names stored as-typed, no lowercasing."""
        self._run_init_with_inputs(
            tmp_path, ["ClaudeCode", "CodexCLI"], monkeypatch
        )
        config = (tmp_path / "ai-handoff.yaml").read_text()
        assert "name: ClaudeCode" in config
        assert "name: CodexCLI" in config

    def test_overwrite_confirm_no_aborts(self, tmp_path, monkeypatch):
        """Existing config + 'n' to overwrite → file unchanged."""
        (tmp_path / "ai-handoff.yaml").write_text(
            "agents:\n  lead:\n    name: keep\n  reviewer:\n    name: me\n"
        )
        self._run_init_with_inputs(tmp_path, ["n"], monkeypatch)
        config = (tmp_path / "ai-handoff.yaml").read_text()
        assert "name: keep" in config
        assert "name: me" in config


class TestInitExplainer:
    def test_shows_explainer_by_default(self, tmp_path, monkeypatch, capsys):
        """Standalone init_command() prints HANDOFF_EXPLAINER."""
        monkeypatch.setattr("builtins.input", lambda prompt="": "x")
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            init_command()
        finally:
            os.chdir(original_dir)
        out = capsys.readouterr().out
        assert "How the handoff works" in out
        assert "Arbiter" in out

    def test_suppresses_explainer_when_flag_false(
        self, tmp_path, monkeypatch, capsys
    ):
        """init_command(show_explainer=False) hides HANDOFF_EXPLAINER."""
        monkeypatch.setattr("builtins.input", lambda prompt="": "x")
        original_dir = os.getcwd()
        os.chdir(tmp_path)
        try:
            init_command(show_explainer=False)
        finally:
            os.chdir(original_dir)
        out = capsys.readouterr().out
        assert "How the handoff works" not in out


class TestQuickstartOutput:
    """Priming box and explainer rendering in quickstart output."""

    def _mock_quickstart(self, tmp_path, outcome, backend):
        """Helper: prewrite config + stub all mutating calls, return captured stdout."""
        (tmp_path / "ai-handoff.yaml").write_text(
            "agents:\n  lead:\n    name: Alice\n  reviewer:\n    name: Bob\n"
        )

        with patch(
            "ai_handoff.session.ensure_session", return_value=outcome
        ) as _m1, patch(
            "ai_handoff.cli.run_init", return_value=True
        ) as _m2, patch(
            "ai_handoff.setup.run_setup"
        ) as _m3, patch(
            "ai_handoff.session.default_backend", return_value=backend
        ) as _m4:
            return quickstart_command(["--dir", str(tmp_path)])

    def test_prints_explainer_once(self, tmp_path, capsys):
        """Explainer appears exactly once on a fresh quickstart run."""
        result = self._mock_quickstart(tmp_path, "created", "iterm2")
        assert result == 0
        out = capsys.readouterr().out
        assert out.count("How the handoff works") == 1

    def test_priming_box_iterm2(self, tmp_path, capsys):
        self._mock_quickstart(tmp_path, "created", "iterm2")
        out = capsys.readouterr().out
        assert "SESSION READY" in out
        assert "Lead tab" in out
        assert "Alice" in out
        assert "Reviewer tab" in out

    def test_priming_box_tmux(self, tmp_path, capsys):
        self._mock_quickstart(tmp_path, "created", "tmux")
        out = capsys.readouterr().out
        assert "Lead pane" in out
        assert "Reviewer pane" in out
        assert "Lead tab" not in out

    def test_priming_box_manual(self, tmp_path, capsys):
        self._mock_quickstart(tmp_path, "manual", "manual")
        out = capsys.readouterr().out
        assert "Lead terminal" in out
        assert "Reviewer terminal" in out

    def test_no_priming_box_when_exists(self, tmp_path, capsys):
        """Existing session → no priming box, just the one-liner."""
        self._mock_quickstart(tmp_path, "exists", "iterm2")
        out = capsys.readouterr().out
        assert "SESSION READY" not in out
        assert "Session already running" in out
