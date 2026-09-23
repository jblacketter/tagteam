# Phase 71: Cockpit Rules tab

## Status
- [x] Planning: approved round 2 (2026-09-23) at `a7ee220`. r1: runtime resolvers; saved vs effective stop.
- [x] Implementation: branch `phase-71-cockpit-rules-tab`
- [ ] Implementation Review
- [ ] Complete

## Implementation notes: what looking at it changed
The tab was driven in a real page (a scratch project, `tagteam serve --port 8768`, Playwright MCP). Screenshots are in the git-ignored `.playwright-mcp/`: `p71-1` project phase + run roadmap, `p71-2` the confirm for unsetting the run stop, `p71-3` a full-roadmap run with no override, `p71-4` a malformed orders file plus a stale-config watcher, `p71-5` 390 px. After every write, `tagteam orders` agreed with the page:
- the run stop was unset and the run note kept (9c);
- a free-text project note was added;
- clear-run removed the stop and the notes (9d);
- a shadowed project edit to `roadmap` was shown as saved, and the shadow note disappeared (9a).

The first look found three things the plan had not:
1. **The advisory scope toggle stretched to the full width** (a flex column), and neither toggle said *what* it scoped. Both now have a caption ("Saved setting for:" / "Add a note for:") and size to their content.
2. **A malformed `tagteam-orders.json` still invited project edits** that every CLI write refuses, and the Project radio read "Not set" when in fact the file could not be read. The payload now carries `project_orders_ok`. When it is false, the Project stop radios, preset chips and free text are disabled, none is checked, and one line says why. This-run editing stays available.
3. **The stop order was shown twice** (as the editor's "Now:" line and as a row). It is now shown once, as the editor.

**Deviations from the plan text:**
- The tab's behaviour is tested in **real Chromium** (the shipped markup, CSS and slice, driven by clicks) instead of under the node DOM stub. The stub has no layout, and 68/68b showed it hides exactly what matters here: computed border style, checked/disabled state, and overflow.
- The effective line and the shadow note are sentences the server writes (`stop.effective_text`, `stop.shadow_note`), so the slice composes no precedence text.

**Seen in the page:** every state in criterion 9, the warnings banner, the stale-watcher notice (with a faked heartbeat on a live pid), and 390 px width.
**Table-tested only:** panel ON and briefer ON as rendered rows. Both are pinned by API tests, and the payload was previewed on this repo, whose briefer is on.
**By design:** a change made from the CLI shows up in the tab on Refresh, since the SSE signature is not extended.

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

### `GET /api/rules`: one payload, derived server-side, from the runtime resolvers
The front end presents it and never derives anything. It reads files only: the
state file, `tagteam.yaml`, `tagteam-orders.json`, and the watcher heartbeat
through `watchlog.beat_view()`. It never opens the database, creates nothing,
and is allowed under `TAGTEAM_READ_ONLY`.

**Engine rows describe what runs, not what the YAML says** (r1 review). Each
row comes from the resolver the engine itself uses, never from a raw
`get_*_spec` getter, because the getters assume a validated block:

| Row | Resolver (shared with the engine) | What the row states |
|---|---|---|
| gate | `gatekeeper.resolve_gatekeeper(config)` | `enabled` after validation. The cycle types it gates (`on`: "implementation reviews only" for `[impl]`). When it runs: at the lead's submission (`on_submit`) or in the watcher before the reviewer's turn. Which checks are active: tests configured or not, scope on or off, plan-doc always. Its bounce cap. Never the test command. |
| panel | `panel.resolve_panel(config, root)` | enabled after validation *and* lens-brief/provider resolution; the lens names; `on` cycle types; the `phases` restriction if any. |
| briefer | `briefer.resolve_briefer(config, root)` | enabled after validation *and* provider resolution; the provider. |
| re-send | new `config.resolve_watcher(config) -> (resend_minutes, problems)`, factored out of `watcher._watch_locked` (the watcher then calls it, so the two cannot drift): an invalid block gives the default 15, as the watcher does | "a turn still owed after N min is re-sent" (0 = never) |
| auto-escalation | `cycle.STALE_ROUND_LIMIT` | "10 consecutive unchanged re-submissions escalate to you" |
| stop | `orders.effective()` | see below |

- A resolver's `problems` become row-level `warnings` ("gatekeeper block
  invalid: … — the gate is OFF"). An invalid block never hides a valid
  independent one, because each row has its own resolver, and a test pins
  this with one invalid and one valid block side by side.
- Resolvers that look up an executable do only a PATH/file lookup. Nothing
  is run.
- **Configured versus running.** Some settings are read by the watcher once,
  when it starts: the watcher-run gate, the panel, the briefer, and the
  re-send interval. Others are read on every call: the `on_submit` gate and
  the standing orders. Each row carries `applies`: `"each submission"` /
  `"each approval"` / `"when the watcher starts"`. When the heartbeat shows a
  running watcher (`beat_view` = fresh / in-turn) whose `started_at` is
  earlier than `tagteam.yaml`'s mtime, the payload sets
  `watcher_stale_config: {started_at, config_mtime}`. The page then says that
  the running watcher still uses the settings it started with, and to restart
  it to apply the change. The page cannot see the running watcher's own
  settings, and it says that rather than guessing.

**The stop order: effective versus saved per scope** (r1 review):
```json
"stop": {"effective": "roadmap", "source": "run" | "run-mode" | "project" | "default",
         "project": "phase" | "roadmap" | null,     // saved in tagteam-orders.json; null = not set
         "run": "phase" | "roadmap" | null,         // saved in the run override; null = not set
         "run_mode_roadmap": true | false,          // a full-roadmap run supplies roadmap without a stop override
         "project_shadowed_by": "run" | "run-mode" | null}
```
`project_shadowed_by` is set when the project's saved value is not the
effective one because a higher-precedence choice wins.

The rest of the payload:
- `advisory`: `[{id, scope: "project"|"run", text, by}]`
- `last_decision`: the text of `orders.describe_decision`, or `null`
- `presets` (below)
- `warnings`, one list covering: a malformed orders file, and each
  resolver's problems

**Presets** are defined server-side in one table (`ORDER_PRESETS`):
- stop: `phase` "Stop after each phase for my review" (recommended) ·
  `roadmap` "Run to the end of the roadmap unless there is a question".
- advisory: "Commit at the end of each phase, but hold the PR for my approval"
  (recommended) · "Open the PR when the phase is done" · "Keep commits small
  and describe how each change was verified".
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
  nor position alone tells them apart. Engine rows show their `applies` note,
  and the stale-config notice appears when the payload sets it.
- **Stop:**
  - **The effective line comes first:** "Now: the run stops after each phase
    (project order)", or "Now: runs the roadmap (this is a full-roadmap run)".
  - **Then a scope toggle, Project | This run**, which picks which *saved*
    value is being edited. The radio always shows that scope's saved value
    from `stop.project` or `stop.run`, never the effective one. It has three
    options: the two presets (recommended marked) and **Not set — inherit**.
  - **When editing Project while `project_shadowed_by` is set**, the editor
    says "This run uses *roadmap* (set for this run / a full-roadmap run);
    your project setting applies from the next run". A successful project
    edit therefore never looks like it reverted.
  - **"Not set" on This run** runs `orders stop --unset --run`. That removes
    only the run's stop and **keeps that run's advisory notes**.
  - **A separate "Clear this run's overrides" action** runs
    `orders clear --run`. It removes the run's stop *and* notes, and its
    confirmation says so.
- **Advisory:**
  - a list of notes, each with its scope and a remove button;
  - preset chips that add a note in one click;
  - one free-text box with Add;
  - a scope toggle (Project | This run).
- **`tagteam.yaml` rows:** read-only, each naming the key to change, e.g.
  "`gatekeeper.enabled` in tagteam.yaml", until 71b.
- Every write goes through the existing confirmation pattern, which shows the
  exact CLI line, then reloads `/api/rules`. The tab loads when it is first
  opened, and again on Refresh or after a write.
- **Last approval** (when there is a `run_decision`): one line, "Last
  approval: converted to a roadmap run".

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
- `tagteam/config.py`: `resolve_watcher()`; `tagteam/watcher.py`: `_watch_locked` calls it (no behaviour change).
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
8. Engine rows match the runtime outcome, pinned by API tests that compare
   each row with the resolver the engine uses:
   - gatekeeper `enabled: true` with an invalid `scope` shows the gate OFF,
     with the problem;
   - a watcher block with an unknown key shows 15 min, the default the
     watcher actually uses;
   - a panel lens with a missing brief shows the panel OFF;
   - a briefer with no resolvable provider shows it OFF;
   - an invalid gate block beside a valid panel block leaves the panel row
     correct;
   - an impl-only gate says "implementation reviews only";
   - a gate with no tests configured, or with scope off, says so.
   The watcher's re-send setting and `_watch_locked` share
   `resolve_watcher`.
9. Saved versus effective stop, through the API **and** in the real page:
   (a) project `phase` + run `roadmap`: the Project radio shows *phase*, the
       This-run radio shows *roadmap*, the effective line says roadmap (this
       run), and a Project edit to `roadmap` and back is shown as saved, with
       the shadow note;
   (b) a full-roadmap run with no stop override: This run shows *Not set*,
       the effective line says a full-roadmap run, and the Project radio shows
       the saved project value;
   (c) This-run stop *Not set* while run notes exist: the stop is removed
       and the run's notes are still listed, both on the page and in
       `tagteam orders`;
   (d) "Clear this run's overrides" removes the run's stop and notes.
10. When a running watcher started before `tagteam.yaml`'s last change, the
    page says it is still using its earlier settings. That is tested with a
    heartbeat fixture, and the page does not claim to know the watcher's
    settings.

## Risks and open questions for the reviewer
- **The split (71 / 71b).** Editing `tagteam.yaml` is the riskier half and
  needs its own plan, covering the targeted-edit grammar, comments, and which
  keys are safe. If you would rather have the whole roadmap entry in one phase,
  say so and I'll plan both.
- **The preset list** is my proposal. It lives in one server-side table, so
  changing it is cheap.
