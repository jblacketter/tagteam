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
        lead_command = None
        reviewer_command = None
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if 'lead:' in line:
                for j in range(i + 1, min(i + 4, len(lines))):
                    sub = lines[j]
                    if 'name:' in sub:
                        lead_name = sub.split('name:')[1].strip()
                    elif 'command:' in sub:
                        lead_command = sub.split('command:')[1].strip()
                    elif not sub.startswith(' ') and not sub.startswith('\t'):
                        break
            elif 'reviewer:' in line:
                for j in range(i + 1, min(i + 4, len(lines))):
                    sub = lines[j]
                    if 'name:' in sub:
                        reviewer_name = sub.split('name:')[1].strip()
                    elif 'command:' in sub:
                        reviewer_command = sub.split('command:')[1].strip()
                    elif not sub.startswith(' ') and not sub.startswith('\t'):
                        break
        if lead_name and reviewer_name:
            result = {'agents': {
                'lead': {'name': lead_name},
                'reviewer': {'name': reviewer_name},
            }}
            if lead_command:
                result['agents']['lead']['command'] = lead_command
            if reviewer_command:
                result['agents']['reviewer']['command'] = reviewer_command
            return result
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

    # Validate command if present
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if isinstance(agent, dict):
            command = agent.get("command")
            if command is not None and not isinstance(command, str):
                errors.append(f"'agents.{role}.command' must be a string")
            elif isinstance(command, str) and not command.strip():
                errors.append(f"'agents.{role}.command' is empty")

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


def get_launch_commands(config: dict) -> tuple[str, str]:
    """Extract launch commands for lead and reviewer agents.

    Uses the optional 'command' field from each agent config,
    falling back to the lowercase agent name.

    Args:
        config: Parsed config dict

    Returns:
        (lead_command, reviewer_command) tuple
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {}) if isinstance(agents.get("lead"), dict) else {}
    reviewer = agents.get("reviewer", {}) if isinstance(agents.get("reviewer"), dict) else {}

    lead_cmd = lead.get("command") or (lead.get("name") or "claude").lower()
    reviewer_cmd = reviewer.get("command") or (reviewer.get("name") or "codex").lower()

    return lead_cmd, reviewer_cmd


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
