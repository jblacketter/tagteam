# Phase: The Saloon - Interactive Character-Driven Setup & Monitoring

## Summary
Transform the web dashboard from a "click the Mayor" single-interaction model into a full saloon experience where users walk up to three distinct characters — the Mayor (Lead), the Bartender (Reviewer), and the Watcher — each handling their domain. Setup becomes a guided conversation where each character introduces themselves and collects their piece of the configuration. Monitoring becomes character-driven with the Watcher reporting agent status.

## Goals
1. Add a third character (the Watcher) to the saloon banner and dialogue system
2. Make all three characters independently clickable with domain-specific interactions
3. Turn first-time setup into a guided multi-character conversation flow
4. Add project monitoring capabilities through the Watcher character
5. Support launching and monitoring tmux sessions from the dashboard

## Characters & Roles

### The Mayor (Lead Agent)
- **Domain:** Project overview, phase management, orchestration
- **Setup role:** Welcomes the user, explains the saloon, asks for the Lead agent name
- **Active role:** Reports phase status, explains what's happening, announces transitions
- **Idle interactions:** Start new phases, explain how handoffs work

### The Bartender (Reviewer Agent)
- **Domain:** Reviews, feedback, round tracking
- **Setup role:** Asks for the Reviewer agent name, explains the review process
- **Active role:** Reports review status, shows feedback history, tracks rounds
- **Idle interactions:** Show past review summaries, explain review criteria

### The Watcher (Daemon)
- **Domain:** Automated orchestration, agent monitoring, tmux sessions
- **Setup role:** Offers to set up automated monitoring, explains the watcher daemon
- **Active role:** Shows agent activity (idle/working), log tails, session health
- **Idle interactions:** Start/stop watcher, configure monitoring, show session status
- **New character:** Needs pixel art sprite, portrait, and dialogue lines

## Technical Approach

### Step 1: Watcher Character Art & Integration
- Design Watcher pixel art sprite (theme: lookout/sheriff's deputy with binoculars or a telescope)
- Add to `sprites.js`: `WATCHER_PIXELS`, `WATCHER_PORTRAIT_PIXELS`, render functions
- Add third character slot to banner HTML/CSS
- Register click handler for Watcher character

### Step 2: Character-Specific Click Interactions
- Each character is independently clickable (currently only Mayor)
- Clicking a character opens dialogue panel with that character's portrait
- Each character has mode-aware dialogue:
  - **Welcome mode** (no config): character's part of the setup flow
  - **Idle mode** (config exists, no active handoff): character-specific menu
  - **Active mode** (handoff in progress): character-specific status report

### Step 3: Multi-Character Setup Flow
Replace the current single-character setup with a guided bar tour:

```
1. User opens dashboard (no config exists)
2. Mayor auto-greets: "Welcome to the Saloon! Let me introduce you to the crew."
3. Mayor asks for Lead agent name (input node)
4. Mayor: "Now go talk to the Bartender — they handle the review side."
   → Bartender glows (like Mayor currently does)
5. User clicks Bartender
6. Bartender: "Welcome! I'll be tracking the reviews. What's your Reviewer agent?"
   → Bartender asks for Reviewer name (input node)
7. Bartender: "All set on my end! Talk to the Watcher if you want automated monitoring."
   → Watcher glows
8. User clicks Watcher
9. Watcher: "I keep an eye on the agents. Want me to set up automated monitoring?"
   → Choice: "Set up tmux session" / "I'll run agents manually" / "Tell me more"
10. Config saved, all characters settle into idle positions
```

### Step 4: Watcher Monitoring API
New server endpoints for the Watcher character's domain:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/watcher/status` | GET | Is watcher daemon running? PID, uptime |
| `/api/watcher/start` | POST | Start watcher daemon for current project |
| `/api/watcher/stop` | POST | Stop watcher daemon |
| `/api/session/status` | GET | tmux session info (pane layout, agent status) |
| `/api/session/start` | POST | Launch tmux session |
| `/api/session/logs` | GET | Recent watcher log lines |

### Step 5: Character State Tracking
Track setup progress in a lightweight way:

```json
{
  "setup_step": "bartender",  // null | "mayor" | "bartender" | "watcher" | "complete"
  "lead_configured": true,
  "reviewer_configured": true,
  "watcher_offered": false
}
```

This lets the dashboard resume setup if the user closes and reopens mid-flow.

## Files

### New Files
| File | Purpose |
|------|---------|
| None — all changes extend existing files | |

### Modified Files
| File | Change |
|------|--------|
| `ai_handoff/data/web/sprites.js` | Add Watcher sprite pixels + portrait + render functions |
| `ai_handoff/data/web/conversation.js` | Add Watcher dialogue scripts, multi-character setup flow, character-specific idle/active scripts |
| `ai_handoff/data/web/index.html` | Add Watcher banner slot, add data attributes for character click targets |
| `ai_handoff/data/web/styles.css` | Add Watcher banner styles, character glow states, responsive adjustments |
| `ai_handoff/data/web/app.js` | Add click handlers for Bartender + Watcher, setup flow state machine, Watcher API calls |
| `ai_handoff/server.py` | Add watcher/session API endpoints |

## Implementation Order (for handoff cycles)

### Plan Cycle
Review and refine this phase plan with the reviewer.

### Impl Cycle 1: Watcher Character + Banner
- Pixel art for Watcher (sprite + portrait)
- Add to banner HTML/CSS alongside Mayor and Bartender
- Basic click handler (shows placeholder dialogue)
- All three characters visible and clickable

### Impl Cycle 2: Multi-Character Setup Flow
- New SETUP_FLOW conversation script replacing SETUP_INTRO
- Character glow system (highlight next character to talk to)
- Setup state tracking (resume mid-flow)
- Config creation through character dialogue
- Auto-play on first visit

### Impl Cycle 3: Character-Specific Interactions
- Mayor idle/active menus (mostly exists, refine)
- Bartender idle/active menus (review history, feedback)
- Watcher idle/active menus (monitoring status, controls)
- State-driven dialogue updates for all three characters

### Impl Cycle 4: Watcher Monitoring Integration
- Server endpoints for watcher daemon status/control
- Server endpoints for tmux session management
- Watcher character shows live agent status
- Log tail display in dialogue or dedicated panel

## Success Criteria
1. Three characters visible in the saloon banner (Mayor, Bartender, Watcher)
2. Each character is independently clickable with their own dialogue
3. First-time setup guides user through all three characters in sequence
4. Clicking "next character" glows guide the user through the setup flow
5. Watcher character shows daemon/session status when clicked during active handoff
6. Setup can be resumed if user closes browser mid-flow
7. All existing functionality preserved (controls, timeline, cycle viewer)
8. Responsive layout works with three characters
9. Test against `/Users/jackblacketter/projects/handoff-test` as a fresh project

## Test Project
Use `/Users/jackblacketter/projects/handoff-test` (fresh project, no ai-handoff config) to validate:
- First-time setup flow works end-to-end
- Config file gets created correctly
- Dashboard transitions from welcome → idle → active modes
- Watcher integration works with real tmux sessions
