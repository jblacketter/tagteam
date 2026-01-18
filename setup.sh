#!/bin/bash
# AI Handoff Framework Setup Script
# Copies framework files to the target project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-.}"

echo "AI Handoff Framework Setup"
echo "=========================="
echo "Source: $SCRIPT_DIR"
echo "Target: $TARGET_DIR"
echo ""

# Create directory structure
echo "Creating directories..."
mkdir -p "$TARGET_DIR/.claude/skills"
mkdir -p "$TARGET_DIR/docs/phases"
mkdir -p "$TARGET_DIR/docs/handoffs"
mkdir -p "$TARGET_DIR/docs/escalations"
mkdir -p "$TARGET_DIR/docs/checklists"
mkdir -p "$TARGET_DIR/templates"

# Copy skills
echo "Copying skills..."
cp -r "$SCRIPT_DIR/.claude/skills/"* "$TARGET_DIR/.claude/skills/"

# Copy templates
echo "Copying templates..."
cp -r "$SCRIPT_DIR/templates/"* "$TARGET_DIR/templates/"

# Copy checklists
echo "Copying checklists..."
cp -r "$SCRIPT_DIR/checklists/"* "$TARGET_DIR/docs/checklists/"

# Copy workflow docs
echo "Copying workflow documentation..."
cp "$SCRIPT_DIR/docs/workflows.md" "$TARGET_DIR/docs/"

# Initialize files if they don't exist
if [ ! -f "$TARGET_DIR/docs/roadmap.md" ]; then
    echo "Creating roadmap template..."
    cp "$SCRIPT_DIR/templates/roadmap.md" "$TARGET_DIR/docs/"
fi

if [ ! -f "$TARGET_DIR/docs/decision_log.md" ]; then
    echo "Creating decision log..."
    cp "$SCRIPT_DIR/templates/decision_log.md" "$TARGET_DIR/docs/"
fi

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit docs/roadmap.md with your project phases"
echo "  2. Run /status to verify setup"
echo "  3. Run /plan create [first-phase] to begin"
