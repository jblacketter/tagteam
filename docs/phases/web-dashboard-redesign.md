# Phase: Web Dashboard Redesign

## Summary
Redesign the web dashboard from ASCII art embedded in a flat `<pre>` block to a modern, smooth, clickable interface with pixel art sprites, full-width flow layout, and an RPG dialogue system with typewriter effects.

## Scope
- Replace ASCII art characters with SVG pixel art sprites (Mayor, Rabbit, Clock, Saloon backdrop)
- Replace 2-column sidebar layout with full-width banner + responsive card grid
- Implement full RPG dialogue system with typewriter effect, portraits, and conversation trees
- Port conversation engine, dialogue scripts, and transition templates from Python TUI to JavaScript
- Keep all existing functionality: forms, controls, polling, timeline, cycle viewer, phase map

## Technical Approach
- **Pixel art**: 2D color arrays rendered to inline SVG via `pixelsToSVG()` — no external image files needed
- **Dialogue engine**: `ConversationEngine` state machine + `TypewriterEffect` + `DialogueController` ported from Python TUI
- **Layout**: CSS Grid for card sections, absolute positioning for banner characters, flexbox for dialogue panel
- **Animations**: CSS `@keyframes` for pendulum swing, cuckoo pop-out, mayor glow, cursor blink
- **Responsive**: Cards collapse to single column <768px, portrait hides <480px

## Files

### New Files
| File | Purpose |
|------|---------|
| `ai_handoff/data/web/sprites.js` | SVG pixel art data + rendering functions |
| `ai_handoff/data/web/conversation.js` | Conversation engine, scripts, typewriter, dialogue controller |

### Modified Files
| File | Change |
|------|--------|
| `ai_handoff/data/web/index.html` | Rewritten: full-width flow layout with banner, dialogue panel, card sections |
| `ai_handoff/data/web/styles.css` | Rewritten: modern layout, pixel art styles, dialogue panel, responsive |
| `ai_handoff/data/web/app.js` | Refactored: removed ASCII art, integrated sprites + conversation engine |
| `ai_handoff/server.py` | Added `.svg` to `_CONTENT_TYPES` dict |
| `MANIFEST.in` | Added `*.svg` to web includes |

## Success Criteria
1. `python -m ai_handoff serve` renders pixel art banner with Mayor, Rabbit, Clock, backdrop
2. Click Mayor opens dialogue panel with typewriter intro conversation
3. Setup flow works via dialogue (input nodes for lead/reviewer agent names)
4. Phase start works, status cards update, controls enable/disable correctly
5. State changes trigger character reactions + auto-dialogue with transitions
6. Pendulum animates, cuckoo pops on significant transitions
7. All existing controls work: approve, request changes, escalate, abort
8. Timeline and cycle viewer populate correctly
9. Responsive layout: cards stack on narrow screens, portrait hides on mobile
10. All existing tests still pass (no regressions in server.py)
