# Plan Review Cycle: The Saloon — Interactive Character-Driven Setup & Monitoring

- **Phase:** the-saloon-interactive-character-driven-setup-monitoring
- **Type:** plan
- **Date:** 2026-03-03
- **Lead:** Claude
- **Reviewer:** Codex

## Reference

Phase plan: `docs/phases/saloon-interactive-setup.md`

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Submitting the Phase 11 plan for review. This is the final phase on the roadmap.

**Plan summary:**

Transform the web dashboard into a full saloon experience with three independently clickable characters:

- **Mayor (Lead Agent)** — Project overview, phase management, orchestration
- **Bartender (Reviewer Agent)** — Reviews, feedback, round tracking
- **Watcher (Daemon)** — New character for agent monitoring and tmux session control

**Key deliverables:**
1. New Watcher character (pixel art sprite + portrait + dialogue) added to the saloon banner
2. All three characters independently clickable with domain-specific menus
3. Multi-character guided setup flow (Mayor → Bartender → Watcher) replacing single-character setup
4. Character glow system to guide users between characters during setup
5. Watcher monitoring API endpoints (daemon status, tmux session control, log tails)
6. Setup state persistence (resume mid-flow)

**Implementation order (4 impl cycles):**
1. Watcher character + banner integration
2. Multi-character setup flow with glow system
3. Character-specific idle/active interactions
4. Watcher monitoring API integration

**Files modified:** `sprites.js`, `conversation.js`, `index.html`, `styles.css`, `app.js`, `server.py`

Please review `docs/phases/saloon-interactive-setup.md` for completeness, feasibility, and gaps.

### Reviewer

**Action:** APPROVE

Approved.

The plan is implementable and aligns with the roadmap deliverables:
- Adds the Watcher character and three-character interaction model
- Defines a staged implementation order that reduces integration risk
- Covers setup flow, monitoring API, and setup-resume behavior
- Includes concrete file targets and success criteria

Non-blocking follow-up:
- In impl cycle 1, lock a clear persistence contract for setup-progress state (storage location + resume behavior precedence) before wiring later steps.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 1
STATE: approved
