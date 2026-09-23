# Phase 71: Cockpit Rules tab

## Status
- [ ] Planning: plan cycle open (round 1)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
Phase 70 made standing orders durable, but they can only be seen and set from
a terminal. `tagteam.yaml` holds the other half of "what governs a run": the
gate, the panel, the briefer, the watcher's re-send interval, and the
auto-escalation rule. Nothing in the cockpit shows any of it. Add a **Rules**
tab that says in plain language what governs this project's runs. It keeps
what the engine **enforces** visibly apart from what the agents are merely
**told** (advisory standing orders). The tab can also **edit the standing
orders**, through presets with a recommended default plus one free-text box.

**Split (proposed, like 68 → 68b).** The roadmap entry also asks for editing a
small safe set of `tagteam.yaml` through the CLI with a dry-run diff, using
targeted line edits rather than a comment-dropping PyYAML rewrite. That is a
new engine write path with its own risk: preserving comments and ordering,
validating, and deciding which keys count as safe. It is split out as a new
roadmap entry, **Phase 71b: safe `tagteam.yaml` edits (CLI, then the Rules
tab)**. In 71 the `tagteam.yaml` rows are read-only, and each row says which
key and file to change.

## Scope
**In**
- `GET /api/rules` (cockpit mode), built by a new `cockpit_api.rules_payload()`.
- `POST /api/orders` (cockpit mode): the existing action pattern (`_plan` /
  `run_action` / `cli_preview`, `{ok, message, cli}`, `dry_run`), mapped
  onto `tagteam orders …`.
- A Rules tab in `cockpit.html` / `cockpit.js` / `cockpit.css`.
- `orders.orders_command` gains an `out=` parameter, like the `controls`
  commands, so the cockpit can capture its output. The CLI behaviour is
  unchanged.
- A roadmap entry for 71b.
- Tests: Python for the payload and action, node for the JS slice, and one
  real-Chromium check. The real page is also looked at by hand.

**Out**
- Editing `tagteam.yaml` (71b).
- Regrouping the tabs into Now · Roadmap · Rules · History · Usage (Phase 69,
  which owns the tab regroup). 71 adds one tab beside the existing four.
- New kinds of standing order.

## Technical approach

### `GET /api/rules`: one payload, derived server-side
The front end presents it and never derives anything from it. It reads only
files: `tagteam.yaml` via `read_config` + the `get_*_spec` helpers,
`orders.effective()`, and the state file. It never opens the database,
creates nothing, and is allowed under `TAGTEAM_READ_ONLY`.

```json
{
  "enforced": [
    {"key": "stop", "label": "When a run stops for you",
     "value": "roadmap", "text": "After each phase's implementation is approved, the run goes on to the next ready roadmap phase.",
     "source": "standing order (project)", "editable": "orders"},
    {"key": "gate", "label": "Gate before every review",
     "value": "on · runs at submit", "text": "Tests, scope and plan-doc checks run inside the lead's submission; a failure hands the turn back.",
     "source": "tagteam.yaml gatekeeper", "editable": null},
    {"key": "panel", …}, {"key": "briefer", …},
    {"key": "resend", "label": "Re-send a stuck turn", "value": "15 min", …},
    {"key": "stale", "label": "Auto-escalation", "value": "10 stale rounds", "source": "built in", …}
  ],
  "advisory": [{"id": 1, "scope": "project"|"run", "text": "…", "by": "…"}],
  "stop": {"value": "roadmap", "source": "project", "run_override": null|{…}},
  "last_decision": "converted to a roadmap run …" | null,
  "presets": {"stop": [...], "advisory": [...]},
  "warnings": ["tagteam-orders.json is malformed — treated as no project orders", "tagteam.yaml: …validation problem…"]
}
```

- **Enforced rows** are the stop order plus the five engine rules. Each row
  has a plain-language `text` that says what happens, not how the key is
  spelled. Its `source` names where it comes from. The gate's test command is
  **not** shown (doctor's rule: no configured command or argument), only
  "tests configured".
- **Advisory** is `effective().advisory`. Notes marked `run` are labelled
  "this run".
- Validation problems in `tagteam.yaml` become `warnings`. When the file is
  invalid, a row shows what the engine actually does (the spec defaults),
  never a guess.
- **Presets** are defined server-side, so the CLI, the cockpit and 71b share
  one list:
  - stop: `phase` "Stop after each phase for my review" (recommended) ·
    `roadmap` "Run to the end of the roadmap unless there is a question".
  - advisory: "Commit at the end of each phase, but hold the PR for my
    approval" (recommended) · "Open the PR when the phase is done" ·
    "Keep commits small and describe how each change was verified".
  - Plus one free-text box.

### `POST /api/orders`
`{op: "stop", value: "phase"|"roadmap"|"unset", run: bool}` ·
`{op: "add", text, run}` · `{op: "remove", id, run}` · `{op: "clear-run"}`.
It is mapped in `_plan` to `orders.orders_command` argv, with `--by` set to
the web user. `dry_run: true` returns the exact CLI line (the confirmation
shows it). Bad params give a 400 with a message, a refused write gives
`{ok: false, message}` (409), and the page never sees a traceback. Text is
limited to one line of 500 characters, and the server enforces that.

### The tab
- A heading, then two visually distinct groups: **Enforced — the engine does
  this** and **Advisory — the agents are told this; tagteam cannot enforce
  it**. Each group has its own marker and wording, so neither colour alone
  nor position alone tells them apart.
- **Stop:** a two-option radio built from the presets, with the recommended
  one marked. A scope toggle chooses **Project** (saved in
  `tagteam-orders.json`) or **This run only**. When a run override is in
  force, it is shown with a "clear" link.
- **Advisory:**
  - a list of notes, each with its scope and a remove button;
  - preset chips that add a note in one click;
  - one free-text box with Add;
  - the same scope toggle.
- **`tagteam.yaml` rows:** read-only, each with "change `gatekeeper.enabled`
  in tagteam.yaml" as the way out, until 71b.
- Every write goes through the existing confirmation pattern, which shows the
  exact CLI line, then reloads `/api/rules`. The tab loads the first time it
  is opened, and again on Refresh or after a write. The page's SSE signature
  is not extended, so changing orders from the CLI shows up on Refresh.
- **Last approval** (when there is a `run_decision`): one line, "Last
  approval: converted to a roadmap run". This is the same text as
  `tagteam state`, from `orders.describe_decision`.

### Verification
- **Python:**
  - `rules_payload` over no config / full config / invalid config, with no
    orders / project orders / a run override / a malformed orders file;
  - the payload never includes the test command;
  - the payload creates nothing and works under `TAGTEAM_READ_ONLY`;
  - every `POST /api/orders` op, plus `dry_run`, bad params, and the
    one-line/500-character limit;
  - end to end through the router, in the style the existing cockpit
    endpoint tests use.
- **Node:** the Rules slice renders enforced and advisory apart; the preset
  and free-text posts send the right body; the page does no derivation (it
  only presents payload fields).
- **Real Chromium:** a check that the tab renders from a live server, using
  the existing `_run_in_chromium` pattern, with no new dependency.
- **Looked at by hand:** a scratch project on the memory recipe, served with
  `tagteam serve --port 8768`, driven through Playwright MCP. I'll take
  screenshots of: no orders; project orders; a run override; a malformed
  orders file; and one add, one remove and one stop change, each followed by
  the CLI (`tagteam orders`) agreeing with the page.

## Files
- `tagteam/cockpit_api.py`: `rules_payload()`, `ORDER_PRESETS`, `_plan("orders", …)`.
- `tagteam/server.py`: `GET /api/rules`, `POST /api/orders` (cockpit routes).
- `tagteam/orders.py`: `orders_command(..., out=None)` (errors go to `out` when given).
- `tagteam/data/web/cockpit.html`, `cockpit.js` (a banner-delimited "Phase 71" slice), `cockpit.css`.
- `docs/roadmap.md`: the Phase 71b entry.
- Tests: `tests/test_cockpit_rules.py` (new), plus the node/Chromium harness already used by `tests/test_cockpit_activity.py`.

## Success criteria
1. The Rules tab shows every enforced rule (stop, gate, panel, briefer,
   re-send, auto-escalation) with its plain-language effect and source, and
   the advisory notes with their scope. The two groups are distinguishable
   without colour.
2. The payload is derived server-side and file-only. It creates nothing, is
   allowed under read-only, and never contains the gate's test command.
3. From the tab, the arbiter can set or unset the stop order and add or remove
   advisory notes, for the project or for this run, from presets or free
   text. Each write shows its exact CLI line first, and `tagteam orders`
   agrees with the page afterwards (checked in the real page).
4. A malformed `tagteam-orders.json` or an invalid `tagteam.yaml` is shown as
   a warning, with the values the engine actually uses. The page still loads.
5. `tagteam.yaml` rows are read-only and name the key to change. Phase 71b
   is on the roadmap.
6. No existing tab, lane or bar changes behaviour. The existing cockpit tests
   pass.
7. It has been looked at in a real browser, and the states that were only
   table-tested are listed plainly.

## Risks and open questions for the reviewer
- **The split (71 / 71b).** Editing `tagteam.yaml` is the riskier half and
  needs its own plan, covering the targeted-edit grammar, comments, and which
  keys are safe. If you would rather have the whole roadmap entry in one phase,
  say so and I'll plan both.
- **The preset list** is my proposal. It lives in one server-side table, so
  changing it is cheap.
