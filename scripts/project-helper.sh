#!/bin/bash
# Helper script for common ai-handoff project operations
# Usage: ./scripts/project-helper.sh <command> [args...]

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

command="$1"
shift

case "$command" in
  test)
    # Run tests
    python3 -m pytest "${@:-tests/}"
    ;;

  test-cycle)
    # Run cycle tests
    python3 -m pytest tests/test_cycle.py "$@"
    ;;

  test-watch)
    # Run tests in watch mode (if pytest-watch is installed)
    python3 -m pytest_watch "${@:-tests/}"
    ;;

  state)
    # Show current handoff state
    python3 -m ai_handoff state "$@"
    ;;

  cycle)
    # Cycle operations
    python3 -m ai_handoff cycle "$@"
    ;;

  roadmap)
    # Roadmap operations
    python3 -m ai_handoff roadmap "$@"
    ;;

  format)
    # Format code (if black is installed)
    if command -v black >/dev/null 2>&1; then
      black ai_handoff/ tests/
    else
      echo "black not installed, skipping formatting"
    fi
    ;;

  lint)
    # Run linting (if ruff is installed)
    if command -v ruff >/dev/null 2>&1; then
      ruff check ai_handoff/ tests/
    else
      echo "ruff not installed, skipping linting"
    fi
    ;;

  clean)
    # Clean up generated files
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    echo "Cleaned up __pycache__, *.pyc, and .pytest_cache"
    ;;

  install)
    # Install dependencies
    pip install -e .
    ;;

  install-dev)
    # Install dev dependencies
    pip install -e ".[dev]"
    ;;

  help|*)
    cat <<EOF
AI Handoff Project Helper

Usage: ./scripts/project-helper.sh <command> [args...]

Commands:
  test [args]         Run tests (default: all tests)
  test-cycle [args]   Run cycle tests
  test-watch [args]   Run tests in watch mode
  state [args]        Show current handoff state
  cycle [args]        Run cycle operations
  roadmap [args]      Run roadmap operations
  format              Format code with black
  lint                Lint code with ruff
  clean               Clean up generated files
  install             Install package
  install-dev         Install with dev dependencies
  help                Show this help message

Examples:
  ./scripts/project-helper.sh test
  ./scripts/project-helper.sh test-cycle -v
  ./scripts/project-helper.sh state
  ./scripts/project-helper.sh cycle status --phase my-phase --type plan
  ./scripts/project-helper.sh clean
EOF
    ;;
esac
