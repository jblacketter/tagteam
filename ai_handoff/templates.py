"""
Template rendering utilities for AI Handoff Framework.

Provides simple {{variable}} substitution for markdown templates.
"""


def render_template(content: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders in template content.

    Args:
        content: Template content with {{variable}} placeholders
        variables: Dict mapping variable names to values

    Returns:
        Content with placeholders replaced. Unknown variables are left as-is.
    """
    for key, value in variables.items():
        content = content.replace("{{" + key + "}}", value)
    return content


def get_template_variables(config: dict | None = None) -> dict[str, str]:
    """Build variable dict from ai-handoff.yaml config.

    Args:
        config: Parsed ai-handoff.yaml config dict, or None

    Returns:
        Dict with 'lead' and 'reviewer' keys if config provided,
        empty dict otherwise.
    """
    variables: dict[str, str] = {}
    if config:
        agents = config.get("agents", {})
        lead = agents.get("lead", {})
        reviewer = agents.get("reviewer", {})
        if isinstance(lead, dict) and lead.get("name"):
            variables["lead"] = lead["name"]
        if isinstance(reviewer, dict) and reviewer.get("name"):
            variables["reviewer"] = reviewer["name"]
    return variables
