# Workflows: Lead/Reviewer Collaboration

This document describes how lead and reviewer agents collaborate on projects using this framework.

> **Note**: Agent names are configured in `ai-handoff.yaml`. Read that file to see which agent is the lead and which is the reviewer for your project.

## Roles

| Role | Responsibilities |
|------|------------------|
| **Lead** | Planning phases, implementing code, creating handoffs |
| **Reviewer** | Reviewing plans and implementations, providing feedback |
| **Arbiter** | Breaking ties, making final decisions, approving phases (typically Human) |

## Phase Workflow

Each phase follows this pattern:

```
┌─────────────────────────────────────────────────────────────────┐
│                        PLANNING CYCLE                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Lead: /plan create [phase]                                    │
│      │                                                          │
│      ▼                                                          │
│   Lead: /handoff plan [phase]  ──────► Reviewer: /review plan   │
│      │                                          │               │
│      │◄─────────────────────────────────────────┘               │
│      │  (feedback in docs/handoffs/[phase]_plan_feedback.md)    │
│      ▼                                                          │
│   Lead: /handoff read [phase]                                   │
│      │                                                          │
│      ├── If APPROVED ──────────────────────────────────►        │
│      │                                                          │
│      └── If CHANGES REQUESTED ─► Lead: /plan update [phase]     │
│                                          │                      │
│                                          └──► (repeat cycle)    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     IMPLEMENTATION CYCLE                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Lead: /implement start [phase]                                │
│      │                                                          │
│      ▼                                                          │
│   [Lead implements the phase]                                   │
│      │                                                          │
│      ▼                                                          │
│   Lead: /implement complete [phase]                             │
│      │                                                          │
│      ▼                                                          │
│   Lead: /handoff impl [phase]  ──────► Reviewer: /review impl   │
│      │                                          │               │
│      │◄─────────────────────────────────────────┘               │
│      │  (feedback in docs/handoffs/[phase]_impl_feedback.md)    │
│      ▼                                                          │
│   Lead: /handoff read [phase]                                   │
│      │                                                          │
│      ├── If APPROVED ──────────────────────────────────►        │
│      │                                                          │
│      └── If CHANGES REQUESTED ─► Lead fixes issues              │
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
| `/plan` | Create/update phase plans | Lead |
| `/handoff` | Create handoff documents | Lead |
| `/review` | Review plans or implementations | Reviewer |
| `/implement` | Start/track/complete implementation | Lead |
| `/phase` | Manage phase lifecycle | Both |
| `/status` | Check project status | Both |
| `/decide` | Log decisions | Both |
| `/escalate` | Escalate to human | Both |
| `/sync` | Generate sync summary for sessions | Both |

## Handoff Process

### From Lead to Reviewer
1. Lead completes work (plan or implementation)
2. Lead creates handoff document with `/handoff`
3. Human switches to reviewer session
4. Reviewer reads sync with `/sync reviewer`
5. Reviewer reviews with `/review`
6. Reviewer saves feedback

### From Reviewer to Lead
1. Reviewer saves feedback to `docs/handoffs/[phase]_[type]_feedback.md`
2. Human switches to lead session
3. Lead reads sync with `/sync lead`
4. Lead reads feedback with `/handoff read [phase]`
5. Lead incorporates feedback or explains why not

## Decision Authority

| Decision Type | Who Decides |
|---------------|-------------|
| Technical approach within a phase | Lead |
| Accepting/rejecting review feedback | Lead |
| Blocking implementation issues | Reviewer can flag, lead decides |
| Architecture affecting multiple phases | Requires consensus or Human |
| Disagreements after 2 review cycles | Human (Arbiter) |
| Scope changes to requirements | Human |

## Session Transitions

When switching between lead and reviewer:

1. Generate sync summary: `/sync` or `/sync [lead|reviewer]`
2. The sync summary captures current state
3. In new session, read the sync file: `docs/sync_state.md`
4. Continue work based on sync state

## Quick Reference

**Starting a new phase:**
```
/phase list           # See all phases
/plan create [phase]  # Create the plan
/handoff plan [phase] # Send to reviewer
```

**Reviewing:**
```
/sync reviewer        # Get context
/review plan [phase]  # Or /review impl [phase]
```

**After review (Lead):**
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
