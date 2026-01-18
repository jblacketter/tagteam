# Workflows: Claude/Codex Collaboration

This document describes how Claude and Codex collaborate on projects using this framework.

## Roles

| Role | Assigned To | Responsibilities |
|------|-------------|------------------|
| **Lead** | Claude | Planning phases, implementing code, creating handoffs |
| **Reviewer** | Codex | Reviewing plans and implementations, providing feedback |
| **Arbiter** | Human | Breaking ties, making final decisions, approving phases |

## Phase Workflow

Each phase follows this pattern:

```
┌─────────────────────────────────────────────────────────────────┐
│                        PLANNING CYCLE                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Claude: /plan create [phase]                                  │
│      │                                                          │
│      ▼                                                          │
│   Claude: /handoff plan [phase]  ──────► Codex: /review plan    │
│      │                                          │               │
│      │◄─────────────────────────────────────────┘               │
│      │  (feedback in docs/handoffs/[phase]_plan_feedback.md)    │
│      ▼                                                          │
│   Claude: /handoff read [phase]                                 │
│      │                                                          │
│      ├── If APPROVED ──────────────────────────────────►        │
│      │                                                          │
│      └── If CHANGES REQUESTED ─► Claude: /plan update [phase]   │
│                                          │                      │
│                                          └──► (repeat cycle)    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     IMPLEMENTATION CYCLE                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Claude: /implement start [phase]                              │
│      │                                                          │
│      ▼                                                          │
│   [Claude implements the phase]                                 │
│      │                                                          │
│      ▼                                                          │
│   Claude: /implement complete [phase]                           │
│      │                                                          │
│      ▼                                                          │
│   Claude: /handoff impl [phase]  ──────► Codex: /review impl    │
│      │                                          │               │
│      │◄─────────────────────────────────────────┘               │
│      │  (feedback in docs/handoffs/[phase]_impl_feedback.md)    │
│      ▼                                                          │
│   Claude: /handoff read [phase]                                 │
│      │                                                          │
│      ├── If APPROVED ──────────────────────────────────►        │
│      │                                                          │
│      └── If CHANGES REQUESTED ─► Claude fixes issues            │
│                                          │                      │
│                                          └──► (repeat cycle)    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    /phase complete [phase]
                              │
                              ▼
                    Start next phase...
```

## Available Skills

| Skill | Purpose | Who Uses |
|-------|---------|----------|
| `/plan` | Create/update phase plans | Claude |
| `/handoff` | Create handoff documents | Claude |
| `/review` | Review plans or implementations | Codex |
| `/implement` | Start/track/complete implementation | Claude |
| `/phase` | Manage phase lifecycle | Both |
| `/status` | Check project status | Both |
| `/decide` | Log decisions | Both |
| `/escalate` | Escalate to human | Both |
| `/sync` | Generate sync summary for sessions | Both |

## Handoff Process

### From Claude to Codex
1. Claude completes work (plan or implementation)
2. Claude creates handoff document with `/handoff`
3. Human switches to Codex session
4. Codex reads sync with `/sync codex`
5. Codex reviews with `/review`
6. Codex saves feedback

### From Codex to Claude
1. Codex saves feedback to `docs/handoffs/[phase]_[type]_feedback.md`
2. Human switches to Claude session
3. Claude reads sync with `/sync claude`
4. Claude reads feedback with `/handoff read [phase]`
5. Claude incorporates feedback or explains why not

## Decision Authority

| Decision Type | Who Decides |
|---------------|-------------|
| Technical approach within a phase | Claude (Lead) |
| Accepting/rejecting review feedback | Claude (Lead) |
| Blocking implementation issues | Codex can flag, Claude decides |
| Architecture affecting multiple phases | Requires consensus or Human |
| Disagreements after 2 review cycles | Human (Arbiter) |
| Scope changes to requirements | Human |

## Session Transitions

When switching between Claude and Codex:

1. Generate sync summary: `/sync` or `/sync [claude|codex]`
2. The sync summary captures current state
3. In new session, read the sync file: `docs/sync_state.md`
4. Continue work based on sync state

## Quick Reference

**Starting a new phase:**
```
/phase list           # See all phases
/plan create [phase]  # Create the plan
/handoff plan [phase] # Send to Codex
```

**Reviewing (Codex):**
```
/sync codex           # Get context
/review plan [phase]  # Or /review impl [phase]
```

**After review (Claude):**
```
/handoff read [phase] # See feedback
/plan update [phase]  # Incorporate changes
```

**Implementing:**
```
/implement start [phase]
[do the work]
/implement complete [phase]
/handoff impl [phase]
```
