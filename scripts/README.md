# Project Scripts

Helper scripts for the tagteam project to streamline common operations and avoid permission prompts.

## project-helper.sh

A unified helper script for common project operations.

### Usage

```bash
./scripts/project-helper.sh <command> [args...]
```

### Commands

| Command | Description | Example |
|---------|-------------|---------|
| `test [args]` | Run tests (default: all tests) | `./scripts/project-helper.sh test` |
| `test-cycle [args]` | Run cycle tests | `./scripts/project-helper.sh test-cycle -v` |
| `test-watch [args]` | Run tests in watch mode | `./scripts/project-helper.sh test-watch` |
| `state [args]` | Show current handoff state | `./scripts/project-helper.sh state` |
| `cycle [args]` | Run cycle operations | `./scripts/project-helper.sh cycle status --phase my-phase --type plan` |
| `roadmap [args]` | Run roadmap operations | `./scripts/project-helper.sh roadmap queue` |
| `format` | Format code with black | `./scripts/project-helper.sh format` |
| `lint` | Lint code with ruff | `./scripts/project-helper.sh lint` |
| `clean` | Clean up generated files | `./scripts/project-helper.sh clean` |
| `install` | Install package | `./scripts/project-helper.sh install` |
| `install-dev` | Install with dev dependencies | `./scripts/project-helper.sh install-dev` |
| `help` | Show help message | `./scripts/project-helper.sh help` |

### Benefits

- Reduces permission prompts when using Claude Code
- Provides consistent interface for common operations
- Pre-configured in `.claude/settings.local.json` for auto-approval
- Centralizes common workflows

### Examples

```bash
# Run all tests
./scripts/project-helper.sh test

# Run cycle tests with verbose output
./scripts/project-helper.sh test-cycle -v

# Check current handoff state
./scripts/project-helper.sh state

# Show cycle status
./scripts/project-helper.sh cycle status --phase stale-state-overlay-fix --type plan

# Clean up Python cache files
./scripts/project-helper.sh clean
```
