# Review Cycle: template-variable-substitution (impl)

## Metadata
- **Phase:** template-variable-substitution
- **Type:** impl
- **Started:** 2026-02-02
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/template-variable-substitution.md`
- Implementation:
  - `ai_handoff/templates.py` (new)
  - `ai_handoff/setup.py` (modified)
  - `ai_handoff/data/templates/*.md` (7 files modified)
  - `tests/test_templates.py` (new)

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Implementation complete. Summary of changes:

**New files:**
1. `ai_handoff/templates.py` — Template rendering module with:
   - `render_template(content, variables)` — simple `{{var}}` replacement
   - `get_template_variables(config)` — extracts lead/reviewer from config dict

2. `tests/test_templates.py` — Unit tests covering:
   - Single/multiple variable substitution
   - Same variable multiple times
   - Unknown variables left as-is
   - Empty variables dict
   - None/empty/malformed config handling

**Modified files:**
1. `ai_handoff/setup.py` — Added:
   - Import of templates module
   - `read_config()` function (with PyYAML fallback)
   - `copy_md_file()` helper that applies substitution
   - Config reading at start of `main()` with feedback message
   - All `.md` copy operations now use `copy_md_file()`

2. Template files (7 total) — Replaced manual placeholders with `{{lead}}`/`{{reviewer}}`:
   - `cycle.md`
   - `phase_plan.md`
   - `handoff_plan.md`
   - `handoff_impl.md`
   - `feedback.md`
   - `implementation_log.md`
   - `sync_state.md`

**Testing done:**
- Inline tests for `render_template` and `get_template_variables` pass
- End-to-end test: `setup` with config present → templates have "Claude"/"Codex"
- End-to-end test: `setup` without config → templates have `{{lead}}`/`{{reviewer}}`
- CLI commands (`--help`, `state`) still work

**Success criteria status:**
- [x] All 7 templates use `{{lead}}`/`{{reviewer}}` syntax
- [x] `render_template()` correctly substitutes all variables
- [x] `get_template_variables()` reads config and builds correct variable dict
- [x] `setup.py` substitutes when config exists, copies as-is when it doesn't
- [x] Running `setup` after `init` produces templates with actual agent names
- [x] Unit tests pass (run inline, pytest not installed)
- [x] No regressions — CLI commands still work

### Reviewer
**Action:** REQUEST_CHANGES

One blocking issue:

1. **Unconverted agent-name placeholder in `decision_log.md`.** The plan's scope says to replace manual agent-name placeholders in `ai_handoff/data/templates/*.md` with `{{lead}}` / `{{reviewer}}`, but `ai_handoff/data/templates/decision_log.md` still contains `[Lead agent name / Reviewer agent name / Human / Consensus]`.
   Please either:
   - Update that line to use variables (e.g., `{{lead}} / {{reviewer}} / Human / Consensus`), or
   - If intentionally excluded, update the plan/success criteria to reflect that exception and align the implementation summary.

---

## Round 2

### Lead
**Action:** SUBMIT_FOR_REVIEW

Fixed. Updated `decision_log.md` to use `[{{lead}} / {{reviewer}} / Human / Consensus]` for the "Decided By" field.

Also updated the plan to reflect 8 templates instead of 7:
- Added `decision_log.md` to the template changes table
- Added to files-to-modify list
- Updated success criteria count

### Reviewer
**Action:** APPROVE

Decision log placeholder is updated, and the plan now reflects 8 templates. Implementation matches the plan.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: reviewer
ROUND: 2
STATE: approved
<!-- /CYCLE_STATUS -->
