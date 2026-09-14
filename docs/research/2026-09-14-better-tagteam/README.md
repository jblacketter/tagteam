# Better tagteam — research pack (2026-09-14)

Documentation and research only. No code changed, no handoff cycle opened.

Open `index.html` in a browser (static files, no server needed):

| File | What it is |
|---|---|
| `index.html` | The argument in five points, impact/effort matrix, proposed phase sequence (55–60), verified vs. hypothesis, questions for Jack |
| `01-cockpit-ux.html` | Heuristic audit of the 3.8 cockpit with before/after mockups; sequence for a fourth UX pass |
| `02-agents-and-models.html` | Model tiering: activity taxonomy, this repo's real usage numbers, Anthropic's cost guidance applied, an interactive cost calculator, a measure-first plan |
| `03-features.html` | Fourteen feature ideas, scored; six recommended |
| `04-engineering-and-adoption.html` | Suite time, reliability backlog, watcher seams, docs, portfolio |
| `shared.css` | One stylesheet for all pages |

Headline recommendations:

1. Tomorrow, config only: reviewer `--effort medium` with a Sonnet fallback, one real phase, watch rounds and `tagteam usage`.
2. Phase 55 "Measure the loop": usage by model/kind, window signal per provider, phase cost block on approval (tokens, rounds, minutes; no dollars), `tagteam grade`.
3. Phase 56 "Review bench": replay historical rounds at model × effort cells; your handoff history is the eval set.
4. Then model policy by activity kind, a subagent kit in the plugin (test-runner on Haiku, read-only), and a cockpit pass built around "since you left".

Decisions recorded 2026-09-14 (Jack): order accepted; Claude usually leads but roles stay
switchable (discussion in index.html § "Who should lead"); no API-dollar figures anywhere;
tagteam has no dependency on Aegis (feature #13 is a consumer-agnostic JSONL export).
