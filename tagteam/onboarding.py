"""Read-only role orientation shared by existing status and priming surfaces."""
from pathlib import Path
from tagteam.config import read_config, validate_config, get_agent_names, get_launch_commands


def describe_roles(project_dir):
    root = Path(project_dir).resolve()
    config = read_config(root / 'tagteam.yaml')
    lines = [f'Project:    {root}']
    errors = validate_config(config)
    if errors:
        return '\n'.join(lines + ['Roles:      not configured; run tagteam init'])
    names = get_agent_names(config)
    commands = get_launch_commands(config)
    for role, name, command in zip(('Lead', 'Reviewer'), names, commands):
        lines.append(f'{role}: {name} | launch: {command}')
    lines.append('Workflow: tagteam contract; Claude Code: /tagteam:handoff (vendored: /handoff)')
    return '\n'.join(lines)
