"""Tests for the config module."""

import pytest
from pathlib import Path
import tempfile

from tagteam.config import read_config, validate_config, get_agent_names, get_launch_commands


class TestReadConfig:
    """Tests for read_config function."""

    def test_returns_none_for_nonexistent_file(self):
        result = read_config(Path("/nonexistent/path/config.yaml"))
        assert result is None

    def test_reads_valid_config(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("""
agents:
  lead:
    name: Claude
  reviewer:
    name: Codex
""")
        result = read_config(config_file)
        assert result is not None
        assert result["agents"]["lead"]["name"] == "Claude"
        assert result["agents"]["reviewer"]["name"] == "Codex"

    def test_reads_config_with_model_patterns(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("""
agents:
  lead:
    name: Claude
    model_patterns:
      - claude
      - anthropic
  reviewer:
    name: Codex
    model_patterns:
      - codex
""")
        result = read_config(config_file)
        assert result is not None
        assert result["agents"]["lead"]["model_patterns"] == ["claude", "anthropic"]

    def test_returns_none_for_non_dict_yaml_list(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("- item1\n- item2")
        result = read_config(config_file)
        assert result is None

    def test_returns_none_for_non_dict_yaml_string(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("just a string")
        result = read_config(config_file)
        assert result is None

    def test_returns_none_for_non_dict_yaml_number(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("42")
        result = read_config(config_file)
        assert result is None

    def test_returns_empty_dict_for_empty_yaml(self, tmp_path):
        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text("{}")
        result = read_config(config_file)
        assert result == {}


class TestValidateConfig:
    """Tests for validate_config function."""

    def test_valid_config_returns_empty_list(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert errors == []

    def test_missing_agents_section(self):
        config = {"other": "data"}
        errors = validate_config(config)
        assert "Missing 'agents' section" in errors

    def test_missing_lead_name(self):
        config = {
            "agents": {
                "lead": {},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert any("lead.name" in e for e in errors)

    def test_missing_reviewer_name(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {},
            }
        }
        errors = validate_config(config)
        assert any("reviewer.name" in e for e in errors)

    def test_invalid_model_patterns_not_list(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "model_patterns": "claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert any("must be a list" in e for e in errors)

    def test_invalid_model_patterns_empty_string(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "model_patterns": ["claude", ""]},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert any("non-empty strings" in e for e in errors)

    def test_pattern_overlap_substring(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "model_patterns": ["code"]},
                "reviewer": {"name": "Codex", "model_patterns": ["codex"]},
            }
        }
        errors = validate_config(config)
        assert any("overlap" in e.lower() for e in errors)

    def test_pattern_overlap_exact_match(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "model_patterns": ["ai"]},
                "reviewer": {"name": "Codex", "model_patterns": ["ai"]},
            }
        }
        errors = validate_config(config)
        assert any("overlap" in e.lower() for e in errors)

    def test_no_overlap_with_distinct_patterns(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "model_patterns": ["claude", "anthropic"]},
                "reviewer": {"name": "Codex", "model_patterns": ["codex", "openai"]},
            }
        }
        errors = validate_config(config)
        assert errors == []

    def test_non_dict_config(self):
        errors = validate_config("not a dict")
        assert "must be a YAML mapping" in errors[0]


class TestGetAgentNames:
    """Tests for get_agent_names function."""

    def test_extracts_both_names(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        lead, reviewer = get_agent_names(config)
        assert lead == "Claude"
        assert reviewer == "Codex"

    def test_returns_none_for_missing_lead(self):
        config = {
            "agents": {
                "reviewer": {"name": "Codex"},
            }
        }
        lead, reviewer = get_agent_names(config)
        assert lead is None
        assert reviewer == "Codex"

    def test_returns_none_for_empty_config(self):
        lead, reviewer = get_agent_names({})
        assert lead is None
        assert reviewer is None

    def test_handles_malformed_agent_entries(self):
        config = {
            "agents": {
                "lead": "not a dict",
                "reviewer": {"name": "Codex"},
            }
        }
        lead, reviewer = get_agent_names(config)
        assert lead is None
        assert reviewer == "Codex"


class TestGetLaunchCommands:
    """Tests for get_launch_commands function."""

    def test_returns_explicit_commands(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": "claude --model opus"},
                "reviewer": {"name": "Codex", "command": "codex --full-auto"},
            }
        }
        lead_cmd, reviewer_cmd = get_launch_commands(config)
        assert lead_cmd == "claude --model opus"
        assert reviewer_cmd == "codex --full-auto"

    def test_falls_back_to_lowercase_name(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        lead_cmd, reviewer_cmd = get_launch_commands(config)
        assert lead_cmd == "claude"
        assert reviewer_cmd == "codex"

    def test_empty_command_falls_back_to_name(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": ""},
                "reviewer": {"name": "Codex", "command": ""},
            }
        }
        lead_cmd, reviewer_cmd = get_launch_commands(config)
        assert lead_cmd == "claude"
        assert reviewer_cmd == "codex"

    def test_missing_agents_uses_defaults(self):
        lead_cmd, reviewer_cmd = get_launch_commands({})
        assert lead_cmd == "claude"
        assert reviewer_cmd == "codex"

    def test_malformed_agent_entry_uses_defaults(self):
        config = {
            "agents": {
                "lead": "not a dict",
                "reviewer": {"name": "Gemini"},
            }
        }
        lead_cmd, reviewer_cmd = get_launch_commands(config)
        assert lead_cmd == "claude"
        assert reviewer_cmd == "gemini"

    def test_mixed_explicit_and_default(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": "claude --dangerously-skip-permissions"},
                "reviewer": {"name": "Codex"},
            }
        }
        lead_cmd, reviewer_cmd = get_launch_commands(config)
        assert lead_cmd == "claude --dangerously-skip-permissions"
        assert reviewer_cmd == "codex"


class TestValidateConfigCommand:
    """Tests for command field validation."""

    def test_valid_command_no_errors(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": "claude"},
                "reviewer": {"name": "Codex", "command": "codex"},
            }
        }
        errors = validate_config(config)
        assert errors == []

    def test_empty_command_is_error(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": ""},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert any("command" in e and "empty" in e for e in errors)

    def test_non_string_command_is_error(self):
        config = {
            "agents": {
                "lead": {"name": "Claude", "command": 42},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert any("command" in e and "string" in e for e in errors)

    def test_absent_command_is_fine(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        errors = validate_config(config)
        assert errors == []


class TestFallbackParserCommand:
    """Tests for no-PyYAML fallback parsing of command fields."""

    def test_fallback_parses_command_fields(self, tmp_path, monkeypatch):
        # Force fallback parser by pretending yaml is unavailable
        import tagteam.config as config_mod
        monkeypatch.setattr(config_mod, "HAS_YAML", False)

        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text(
            "agents:\n"
            "  lead:\n"
            "    name: Claude\n"
            "    command: claude --model opus\n"
            "  reviewer:\n"
            "    name: Codex\n"
            "    command: codex --full-auto\n"
        )
        result = read_config(config_file)
        assert result is not None
        assert result["agents"]["lead"]["command"] == "claude --model opus"
        assert result["agents"]["reviewer"]["command"] == "codex --full-auto"

    def test_fallback_without_command_fields(self, tmp_path, monkeypatch):
        import tagteam.config as config_mod
        monkeypatch.setattr(config_mod, "HAS_YAML", False)

        config_file = tmp_path / "tagteam.yaml"
        config_file.write_text(
            "agents:\n"
            "  lead:\n"
            "    name: Claude\n"
            "  reviewer:\n"
            "    name: Codex\n"
        )
        result = read_config(config_file)
        assert result is not None
        assert "command" not in result["agents"]["lead"]
        assert "command" not in result["agents"]["reviewer"]
