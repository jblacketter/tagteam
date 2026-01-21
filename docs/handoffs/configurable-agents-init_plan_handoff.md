# Handoff: Configurable Agents Init - Plan Review

**Date:** 2025-01-20
**From:** Claude (Lead)
**To:** Codex (Reviewer)
**Type:** Planning Review

## Summary

This phase creates an interactive init command for the AI Handoff Framework. New users will run `python -m ai_handoff init`, answer prompts for their two AI agents and roles, and get a generated `ai-handoff.yaml` config file. Skills will be updated to read this config at runtime instead of using hardcoded agent names.

This is a simplified MVP focused on the core onboarding experience. Complex features from the Rev 3 design (model patterns, multiple agents, drift detection, migration) are deferred to later phases.

## What Needs Review

1. **Config schema simplicity** - Is the flat 4-line YAML schema sufficient, or does it need more structure?
2. **Init flow** - Does the interactive prompt sequence make sense?
3. **Skill preamble approach** - Will adding a "read config first" preamble to each skill work reliably?
4. **Scope decisions** - Are the right things in/out of scope for MVP?
5. **File list completeness** - Am I missing any files that need modification?

## Specific Questions for Codex

1. The plan uses role-based language ("Lead creates plan" vs "Claude creates plan"). Should we keep any agent-name references for clarity, or go fully role-based?

2. For the skill preamble, I'm proposing a blockquote at the top of each skill. Is there a better format that ensures the AI reads it first?

3. Should `init` validate that the two agents have different roles (one lead, one reviewer), or allow any combination?

## Phase Plan Location
`docs/phases/configurable-agents-init.md`

## Review Checklist for Codex
- [ ] Technical approach is sound
- [ ] Scope is appropriate (not too big/small)
- [ ] Success criteria are testable
- [ ] No major risks overlooked
- [ ] File structure makes sense
- [ ] Dependencies are identified

## Response Instructions
Please provide feedback in `docs/handoffs/configurable-agents-init_plan_feedback.md` using the feedback template.

---
*Handoff created by Claude. Codex: use `/review plan configurable-agents-init` to begin review.*
