# Phase: Template Variable Substitution

## Status
- [x] Planning
- [x] In Review
- [x] Approved
- [x] Implementation
- [x] Implementation Review
- [x] Complete

## Roles
- Lead: Claude
- Reviewer: Codex
- Arbiter: Human

## Summary
**What:** Add programmatic variable substitution to all markdown templates so generated documents automatically contain the configured agent names instead of manual placeholder text.
**Why:** Currently, templates contain manual placeholders like `[Lead agent name from ai-handoff.yaml]` that AI agents must fill in by reading config. This is fragile — agents sometimes forget, use inconsistent formatting, or leave placeholders unfilled. Programmatic substitution at setup-time produces correct documents immediately.
**Depends on:** Phase 4 (TUI Consolidation) — Complete

## Scope

### In Scope
- Define a `{{variable}}` syntax for template variables
- Replace all manual agent-name placeholders in `ai_handoff/data/templates/*.md` with `{{lead}}` and `{{reviewer}}`
- Add a `render_template(content, variables)` utility function in a new `ai_handoff/templates.py` module
- Modify `ai_handoff/setup.py` to read `ai-handoff.yaml` and substitute variables when copying `.md` templates
- Support graceful fallback when config doesn't exist yet (copy templates unmodified)
- Unit tests for the template rendering function (with clock injection for any date-dependent tests)

### Out of Scope
- Full Jinja2 dependency — use simple string replacement
- Variable substitution in skill files (`.claude/skills/*.md`) — these are instructional documents for AI agents, not generated documents
- Conditionals, loops, or other template logic
- Variable substitution at runtime in the watcher/state/server modules (they already use Python `.format()`)
- `{{date}}` variable — date placeholders (`[YYYY-MM-DD]`) remain manual since they represent when the *document* is created by the agent, not when setup runs

## Technical Approach

### Variable Syntax
Use `{{variable_name}}` (double curly braces). This avoids conflicts with:
- Markdown formatting (no curly braces in standard markdown)
- Python's `str.format()` which uses single `{}`
- HTML template syntax (which also uses `{{}}` but these are markdown files, not HTML)

### Template Engine
A simple function in `ai_handoff/templates.py`:

```python
def render_template(content: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders in template content."""
    for key, value in variables.items():
        content = content.replace("{{" + key + "}}", value)
    return content

def get_template_variables(config: dict | None = None) -> dict[str, str]:
    """Build variable dict from config, with defaults."""
    variables = {}
    if config:
        agents = config.get("agents", {})
        variables["lead"] = agents.get("lead", {}).get("name", "Lead")
        variables["reviewer"] = agents.get("reviewer", {}).get("name", "Reviewer")
    return variables
```

### Setup Integration
In `setup.py`, when copying `.md` templates:
1. Try to read `ai-handoff.yaml` from the target directory
2. If config exists, build variables dict and substitute in each `.md` file before writing
3. If config doesn't exist, copy templates as-is (variables remain as `{{lead}}` etc.)
4. Only apply substitution to `.md` files (not other assets like `.wav` or `.html`)

This means `setup` works before or after `init`, but templates are most useful when run after `init`.

### Template Changes
Every `ai_handoff/data/templates/*.md` file gets its manual placeholders replaced:

| Template | Current Placeholder | New Variable |
|----------|-------------------|--------------|
| cycle.md | `[lead agent name from ai-handoff.yaml]` | `{{lead}}` |
| cycle.md | `[reviewer agent name from ai-handoff.yaml]` | `{{reviewer}}` |
| phase_plan.md | `[from ai-handoff.yaml]` (lead line) | `{{lead}}` |
| phase_plan.md | `[from ai-handoff.yaml]` (reviewer line) | `{{reviewer}}` |
| handoff_plan.md | `[Lead agent name]` | `{{lead}}` |
| handoff_plan.md | `[Reviewer agent name]` | `{{reviewer}}` |
| handoff_impl.md | `[Lead agent name]` | `{{lead}}` |
| handoff_impl.md | `[Reviewer agent name]` | `{{reviewer}}` |
| feedback.md | `[Reviewer agent name]` | `{{reviewer}}` |
| implementation_log.md | `[Lead agent name from ai-handoff.yaml]` | `{{lead}}` |
| sync_state.md | `[Lead agent name from ai-handoff.yaml]` | `{{lead}}` |
| sync_state.md | `[Reviewer agent name from ai-handoff.yaml]` | `{{reviewer}}` |
| decision_log.md | `[Lead agent name / Reviewer agent name / ...]` | `[{{lead}} / {{reviewer}} / ...]` |

## Files to Create/Modify

### Create
- `ai_handoff/templates.py` — Template rendering utility (`render_template`, `get_template_variables`)
- `tests/test_templates.py` — Unit tests for template rendering

### Modify
- `ai_handoff/setup.py` — Import templates module, read config, substitute when copying
- `ai_handoff/data/templates/cycle.md` — Replace placeholders with `{{lead}}`/`{{reviewer}}`
- `ai_handoff/data/templates/phase_plan.md` — Replace placeholders
- `ai_handoff/data/templates/handoff_plan.md` — Replace placeholders
- `ai_handoff/data/templates/handoff_impl.md` — Replace placeholders
- `ai_handoff/data/templates/feedback.md` — Replace placeholders
- `ai_handoff/data/templates/implementation_log.md` — Replace placeholders
- `ai_handoff/data/templates/sync_state.md` — Replace placeholders
- `ai_handoff/data/templates/decision_log.md` — Replace placeholders

## Success Criteria
- [x] All 8 templates with agent-name placeholders use `{{lead}}`/`{{reviewer}}` syntax
- [x] `render_template()` correctly substitutes all variables in a template string
- [x] `get_template_variables()` reads config and builds correct variable dict
- [x] `setup.py` substitutes variables when config exists, copies as-is when it doesn't
- [x] Running `python -m ai_handoff setup` after `init` produces templates with actual agent names
- [x] Unit tests pass for render_template with: all variables present, missing variables (left as-is), empty config, None config
- [x] No regressions — existing CLI commands still work

## Open Questions
- None (resolved: `{{date}}` removed from scope — dates remain manual)

## Risks
- **Variable syntax collision:** `{{}}` could theoretically appear in user-edited markdown. Mitigation: unlikely in practice, and only applied to template source files, not user-edited documents.
- **Setup order dependency:** If user runs `setup` before `init`, templates have raw `{{lead}}` strings. Mitigation: document the recommended order (`init` then `setup`), and `{{lead}}` is more obviously a placeholder than the old bracket syntax.
