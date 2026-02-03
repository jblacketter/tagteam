"""Tests for the template rendering module."""

import pytest

from ai_handoff.templates import get_template_variables, render_template


class TestRenderTemplate:
    """Tests for render_template function."""

    def test_substitutes_single_variable(self):
        content = "Hello {{name}}!"
        result = render_template(content, {"name": "World"})
        assert result == "Hello World!"

    def test_substitutes_multiple_variables(self):
        content = "Lead: {{lead}}, Reviewer: {{reviewer}}"
        result = render_template(content, {"lead": "Claude", "reviewer": "Codex"})
        assert result == "Lead: Claude, Reviewer: Codex"

    def test_substitutes_same_variable_multiple_times(self):
        content = "{{lead}} plans, {{lead}} implements"
        result = render_template(content, {"lead": "Claude"})
        assert result == "Claude plans, Claude implements"

    def test_leaves_unknown_variables_as_is(self):
        content = "{{lead}} and {{unknown}}"
        result = render_template(content, {"lead": "Claude"})
        assert result == "Claude and {{unknown}}"

    def test_empty_variables_dict(self):
        content = "Hello {{name}}!"
        result = render_template(content, {})
        assert result == "Hello {{name}}!"

    def test_no_variables_in_content(self):
        content = "Plain text without variables"
        result = render_template(content, {"lead": "Claude"})
        assert result == "Plain text without variables"

    def test_empty_content(self):
        result = render_template("", {"lead": "Claude"})
        assert result == ""

    def test_multiline_content(self):
        content = """# Header
- **Lead:** {{lead}}
- **Reviewer:** {{reviewer}}
"""
        result = render_template(content, {"lead": "Claude", "reviewer": "Codex"})
        expected = """# Header
- **Lead:** Claude
- **Reviewer:** Codex
"""
        assert result == expected


class TestGetTemplateVariables:
    """Tests for get_template_variables function."""

    def test_extracts_lead_and_reviewer(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
                "reviewer": {"name": "Codex"},
            }
        }
        result = get_template_variables(config)
        assert result == {"lead": "Claude", "reviewer": "Codex"}

    def test_returns_empty_dict_for_none_config(self):
        result = get_template_variables(None)
        assert result == {}

    def test_returns_empty_dict_for_empty_config(self):
        result = get_template_variables({})
        assert result == {}

    def test_handles_missing_agents_key(self):
        config = {"other": "data"}
        result = get_template_variables(config)
        assert result == {}

    def test_handles_missing_lead(self):
        config = {
            "agents": {
                "reviewer": {"name": "Codex"},
            }
        }
        result = get_template_variables(config)
        assert result == {"reviewer": "Codex"}

    def test_handles_missing_reviewer(self):
        config = {
            "agents": {
                "lead": {"name": "Claude"},
            }
        }
        result = get_template_variables(config)
        assert result == {"lead": "Claude"}

    def test_handles_empty_name(self):
        config = {
            "agents": {
                "lead": {"name": ""},
                "reviewer": {"name": "Codex"},
            }
        }
        result = get_template_variables(config)
        # Empty name is falsy, so lead shouldn't be included
        assert result == {"reviewer": "Codex"}

    def test_handles_malformed_agent_entries(self):
        config = {
            "agents": {
                "lead": "not a dict",
                "reviewer": {"name": "Codex"},
            }
        }
        result = get_template_variables(config)
        assert result == {"reviewer": "Codex"}
