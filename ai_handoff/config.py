"""
Centralized configuration handling for AI Handoff Framework.

This module provides a single source of truth for reading and validating
ai-handoff.yaml configuration files.
"""

from pathlib import Path

# PyYAML is optional
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def read_config(config_path: Path | str) -> dict | None:
    """Read and parse ai-handoff.yaml.

    Args:
        config_path: Path to config file

    Returns:
        Parsed config dict, or None if file doesn't exist or is invalid
    """
    path = Path(config_path)
    if not path.exists():
        return None

    try:
        content = path.read_text(encoding="utf-8")
        if HAS_YAML:
            result = yaml.safe_load(content)
            # Only return if it's a dict (not [], "foo", or other valid YAML)
            return result if isinstance(result, dict) else None

        # Fallback parsing without PyYAML
        lead_name = None
        reviewer_name = None
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'lead:' in line and i + 1 < len(lines):
                next_line = lines[i + 1]
                if 'name:' in next_line:
                    lead_name = next_line.split('name:')[1].strip()
            elif 'reviewer:' in line and i + 1 < len(lines):
                next_line = lines[i + 1]
                if 'name:' in next_line:
                    reviewer_name = next_line.split('name:')[1].strip()
        if lead_name and reviewer_name:
            return {'agents': {'lead': {'name': lead_name}, 'reviewer': {'name': reviewer_name}}}
    except Exception:
        pass
    return None


def validate_config(config: dict) -> list[str]:
    """Validate ai-handoff.yaml structure.

    Args:
        config: Parsed config dict

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not isinstance(config, dict):
        return ["Config must be a YAML mapping"]

    agents = config.get("agents")
    if not isinstance(agents, dict):
        errors.append("Missing 'agents' section")
        return errors

    # Validate lead
    lead = agents.get("lead")
    if not isinstance(lead, dict) or not lead.get("name"):
        errors.append("Missing or invalid 'agents.lead.name'")

    # Validate reviewer
    reviewer = agents.get("reviewer")
    if not isinstance(reviewer, dict) or not reviewer.get("name"):
        errors.append("Missing or invalid 'agents.reviewer.name'")

    # Validate model_patterns if present
    all_patterns: list[tuple[str, list[str]]] = []
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            continue
        patterns = agent.get("model_patterns")
        if patterns is not None:
            if not isinstance(patterns, list):
                errors.append(f"'agents.{role}.model_patterns' must be a list")
            elif not all(isinstance(p, str) and p for p in patterns):
                errors.append(f"'agents.{role}.model_patterns' must contain non-empty strings")
            else:
                all_patterns.append((role, [p.lower() for p in patterns]))

    # Check for pattern overlap (error, not warning)
    if len(all_patterns) == 2:
        role1, patterns1 = all_patterns[0]
        role2, patterns2 = all_patterns[1]
        for p1 in patterns1:
            for p2 in patterns2:
                if p1 in p2 or p2 in p1:
                    errors.append(
                        f"Pattern overlap: '{p1}' ({role1}) and '{p2}' ({role2}) "
                        f"could match the same model identifier"
                    )

    return errors


def get_agent_names(config: dict) -> tuple[str | None, str | None]:
    """Extract lead and reviewer names from config.

    Args:
        config: Parsed config dict

    Returns:
        (lead_name, reviewer_name) tuple, with None for missing values
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {})
    reviewer = agents.get("reviewer", {})

    lead_name = lead.get("name") if isinstance(lead, dict) else None
    reviewer_name = reviewer.get("name") if isinstance(reviewer, dict) else None

    return lead_name, reviewer_name
