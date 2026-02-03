# Review Cycle: template-variable-substitution (plan)

## Metadata
- **Phase:** template-variable-substitution
- **Type:** plan
- **Started:** 2026-02-02
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/template-variable-substitution.md`

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Phase 5 plan for Template Variable Substitution. Key decisions:

1. **Simple `{{variable}}` syntax** — no Jinja2 dependency, just string replacement via a new `ai_handoff/templates.py` module with `render_template()` and `get_template_variables()`.

2. **Substitution at setup-time** — `setup.py` reads `ai-handoff.yaml` (if it exists) and replaces `{{lead}}`/`{{reviewer}}`/`{{date}}` when copying templates to the project. Graceful fallback: if no config, templates are copied with raw `{{}}` placeholders.

3. **7 template files affected** — cycle.md, phase_plan.md, handoff_plan.md, handoff_impl.md, feedback.md, implementation_log.md, sync_state.md. All have manual `[Lead agent name]` style placeholders that get replaced with `{{lead}}`/`{{reviewer}}`.

4. **Skills excluded** — `.claude/skills/*.md` are instructional docs for AI agents, not generated documents. They tell agents to read config and follow role-specific instructions. Substituting names there would make them less generic.

5. **Unit tests** — tests for render_template with all variables, missing variables (left as-is), empty/None config.

### Reviewer
**Action:** REQUEST_CHANGES

Thanks — the plan is solid. One blocking item to resolve before implementation:

**Blocking**
1. **Decide and document `{{date}}` usage.** The plan adds a `{{date}}` variable but the template update list only mentions lead/reviewer placeholders. Please either:
   - Update date placeholders (e.g., `[YYYY-MM-DD]`) in the templates that include dates to `{{date}}`, and include those files in the template-change list and success criteria; or
   - Remove `{{date}}` from scope/success criteria if you want dates to remain manual.

**Non-blocking suggestions**
- In `setup.py`, scope substitution to known markdown templates (or `.md` files) to avoid accidental replacements in non-template assets.
- For tests, consider fixing the date via a clock injection or monkeypatch to avoid a brittle "today" assertion.

---

## Round 2

### Lead
**Action:** SUBMIT_FOR_REVIEW

Addressed both items:

**Blocking issue resolved:** Removed `{{date}}` from scope entirely. Date placeholders (`[YYYY-MM-DD]`) remain manual because they represent when the *document* is created by the agent, not when `setup` runs. Updated:
- In Scope: removed `{{date}}` line
- Out of Scope: added explicit note that `{{date}}` is excluded with rationale
- Technical Approach: removed date from `get_template_variables()`
- Open Questions: marked as resolved

**Non-blocking suggestions incorporated:**
- Setup Integration: added note that substitution only applies to `.md` files
- Test notes: updated to mention clock injection approach (though no date tests needed now that `{{date}}` is out of scope)

Plan is updated at `docs/phases/template-variable-substitution.md`.

### Reviewer
**Action:** APPROVE

Looks good. `{{date}}` removal is consistent with the rationale, and the `.md`-only substitution scope is a helpful safeguard. Plan is approved for implementation.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: reviewer
ROUND: 2
STATE: approved
<!-- /CYCLE_STATUS -->
