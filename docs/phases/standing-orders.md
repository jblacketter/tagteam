# Phase 70: Standing orders

## Status
- [ ] Planning: plan cycle open (round 1)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
The arbiter's run-level instructions live in chat today: "go to the end of all
phases unless you have a question", "commit at phase end but hold the PR for my
approval", "open the PR when you are done". They are lost when the session ends,
never reach a headless turn, and differ between the two agents' terminals.

Make them durable **standing orders**: per project, with a per-run override,
delivered to both agents in every turn, like an interjection that is never
consumed. There are two kinds, and they stay separate in storage, CLI output and
delivered text:

- **Enforced:** `stop`, meaning when the run stops for the arbiter. It is a
  switch over the existing single-phase / full-roadmap machinery, so the engine
  does it. The agents do not have to remember it.
- **Advisory:** free-text conduct notes (git, PRs, anything else). Tagteam
  delivers them but cannot enforce them, because it runs no git. Every surface
  says they are advisory.

Engine + CLI only. The cockpit's Rules tab is Phase 71.

## Scope
**In**
- New module `tagteam/orders.py`: storage, resolution, rendering.
- New CLI `tagteam orders …`.
- The enforced `stop` order is applied at the impl-approval state write.
- Delivery: the headless prompt, `cycle rounds` (stderr), `tagteam state`, the
  contract.
- `tagteam-orders.json` excluded from the impl scope check, like other
  bookkeeping.
- Tests: `tests/test_orders.py`, plus additions to the existing cycle,
  watcher and headless tests.

**Out**
- Any cockpit UI (Phase 71).
- Enforcing advisory orders.
- Orders scoped to a single phase. `tagteam interject` already covers
  one-cycle notes.
- New stop points: stop after the plan, stop after N phases.

## Technical approach

### The orders
| Order | Kind | Values | Default |
|---|---|---|---|
| `stop` | enforced | `phase`: the run stops after each phase's impl is approved (today's single-phase behaviour). `roadmap`: after an impl approval the run goes on to the next **ready** roadmap phase and stops only at the end of the roadmap. | `phase` |
| advisory notes | advisory | free text, numbered | none |

The roadmap's third wording, "only on questions", is not a separate value.
Under both values, an escalation, a NEED_HUMAN and a blocked roadmap already
stop a run. So "run to the end unless you have a question"
*is* `stop: roadmap`. The delivered text says that in words.

### Where they live
- **Project orders:** `tagteam-orders.json` at the project root, beside
  `tagteam.yaml` and `tagteam-manifest.json`. It is committed: these are durable
  preferences both agents read, and git history shows who changed what.
  Shape: `{"version": 1, "stop": "phase"|"roadmap"|null, "advisory": [{"id", "text", "by", "ts"}]}`.
  A missing file means no orders. That is today's behaviour, and nothing is
  created until the first `tagteam orders` write. The file is read with the
  guarded bounded reader (`safe_read`). If the file is unreadable or malformed,
  the orders are "none", with a `warn:` in `tagteam orders` and `doctor`. It is
  never an exception on a dispatch path.
- **Run override:** an `orders` key in `handoff-state.json`, which is
  git-ignored and belongs to the run:
  `{"stop": …|absent, "advisory": [...], "set_at", "by"}`.
  `_derive_top_level_state()` rewrites the state with `replace=True`, so it must
  **preserve** `orders` explicitly, as it already does for `roadmap`. Otherwise
  the first cycle write would drop it.

### Resolution: one function
`orders.effective(state, project_dir) -> {"stop": ("phase"|"roadmap", source), "advisory": [(note, source)…]}`
where `source` is one of `run`, `run-mode`, `project`, `default`.

Precedence for `stop`:
1. The run override, if it sets `stop`.
2. `run_mode == "full-roadmap"` gives `roadmap`. A run started with
   `start --roadmap` has already chosen, which makes it a per-run choice.
3. The project order.
4. `phase`.

Advisory notes: project notes first, then run notes labelled "this run". The
delivered text says run notes win where the two conflict. There is no removing
a project note for one run (out of scope). Every surface calls this one
function; none of them re-derive it.

### Enforcement: at the approval write, not in the watcher
The decision is made once, deterministically, in `_derive_top_level_state()`
when it maps an **impl** cycle to `approved`. That covers a reviewer APPROVE
and an arbiter `rule approve`, because both go through `add_round`. It is
applied by rewriting the fields the watcher already keys on. **The watcher does
not change.** It still advances only when `run_mode == "full-roadmap"`.

| Effective `stop` | `run_mode` now | Written with the approval |
|---|---|---|
| `roadmap` | full-roadmap | unchanged (today). |
| `roadmap` | single-phase | `run_mode: full-roadmap` and `roadmap: {queue, current_index, completed: []}`, where `queue = roadmap.build_queue(docs/roadmap.md)` (all incomplete phases, topological). `current_index` is the approved phase's index in the queue. If the phase is not in the roadmap, the index is 0 and the watcher's `completed` bookkeeping covers it. From here the watcher's existing `_try_roadmap_advance` → `_select_next_phase` picks the next ready phase, or reports roadmap-complete, or pauses as blocked. If the roadmap is missing or invalid, nothing is converted, the run stops as single-phase, and the writer prints one line saying why. |
| `phase` | full-roadmap | `run_mode: single-phase`, `roadmap` dropped. A run override `--run stop phase` set during a roadmap run halts it cleanly at the end of the current phase. |
| `phase` | single-phase | unchanged (today). |

Because the decision is recorded in the state, the watcher, `tagteam state`,
`now.headline` and the cockpit all see the same outcome, and none of them
re-derive it. Plan approvals are untouched: 68a's headless plan→impl advance
and the tab-mode notice stay as they are.

**Without a watcher**, the state still says what happens next
(`run_mode`/`queue`), and the contract tells the lead to follow it. That is
already true of full-roadmap mode today.

### Lifetime of the run override
A run override ends when its run ends:
- **The run stops:** the impl-approval write resolves `stop: phase`. That same
  write drops `orders` from the state.
- **The roadmap completes:** the `_select_next_phase` write that sets
  `result: roadmap-complete` drops it. That is one extra key in an update the
  watcher already makes. It is the only watcher edit.
- **The arbiter clears it:** `tagteam orders clear --run`.

A blocked pause, an escalation and a NEED_HUMAN keep the override, because the
run resumes. A run override set while no run is active (state `done`) applies
to the next run the lead starts. `cycle init` for a new phase keeps `orders`
through the same preservation. That is how the arbiter says "for the next run,
go to the end".

### CLI: `tagteam orders`
```
tagteam orders [--json]                       # effective orders, each with its source; the file path
tagteam orders stop phase|roadmap [--run] [--by NAME]
tagteam orders stop --unset [--run]
tagteam orders add "<note>" [--run] [--by NAME]
tagteam orders remove ID [--run]
tagteam orders clear --run
```
- Output always prints two headed sections, `Enforced (the engine does
  this)` and `Advisory (delivered, not enforced)`, plus one line saying what
  happens at the next impl approval ("the run stops for you" / "goes on to the
  next ready phase").
- A project write goes through a temp file + `replace`, and the target is
  checked with the same lstat / no-symlink rule `setup` uses. A run write
  goes through `update_state` under `dualwrite.writer_lock`.
- Every write is refused under `TAGTEAM_READ_ONLY=1` before anything touches
  disk. The bare read is allowed there and creates nothing.
- `--run` needs no active cycle (see lifetime above).

### Delivery
- **Headless prompt:** `compose_prompt()` gains a `=== STANDING ORDERS ===`
  block right after the interjections block. It has the enforced line in words
  ("When this phase's implementation is approved the run goes on to the next
  ready phase; escalations and questions still stop it") and the advisory
  notes, marked "advisory — tagteam does not enforce these". It is absent when
  there are no orders and `stop` is the default. The block is capped like the
  other blocks, and when capped it points to `tagteam orders`.
- **`tagteam cycle rounds`:** the same block goes to **stderr**, before the
  JSON lines, so stdout stays machine-readable. An interactive agent sees it;
  a parser does not.
- **`tagteam state`:** one `Orders:` line: `stop: roadmap (project) · 2
  advisory`.
- **Contract** (`tagteam/data/.claude/skills/handoff/SKILL.md` + the plugin
  copy, kept in sync by the existing test): one paragraph. It says standing
  orders arrive with every turn, the enforced one is applied by the engine
  (read `run_mode` / the NEXT box; don't second-guess it), and advisory ones are
  instructions to follow, e.g. hold the PR. The "Approved / done" branch says
  the next step comes from the state, not a hard-coded "Start next phase".
  `docs/workflows.md` gets a "Standing orders" row under **Steering**.

### Scope check
`tagteam-orders.json` joins `_TAGTEAM_ARTIFACT_FILES`. An arbiter editing
orders mid-cycle is not implementation work and must not appear in, or satisfy,
the impl scope check.

## Files
- `tagteam/orders.py` (new): load/save, `effective()`, `render_block()`,
  `apply_at_impl_approval(updates, state, project_dir)`, CLI.
- `tagteam/cli.py`: dispatch `orders`, add it to `READ_ONLY_COMMANDS` for the
  bare read.
- `tagteam/cycle.py`: `_derive_top_level_state()` preserves `orders`, calls
  `apply_at_impl_approval` on impl → approved. `_TAGTEAM_ARTIFACT_FILES` +=
  `tagteam-orders.json`. `_cli_rounds` prints the block to stderr.
- `tagteam/watcher.py`: the roadmap-complete write drops `orders`.
- `tagteam/headless.py`: `compose_prompt(..., orders_block=...)` and its
  caller.
- `tagteam/state.py` / the `tagteam state` printer: the `Orders:` line.
- `tagteam/data/.claude/skills/handoff/SKILL.md` (+ plugin copy),
  `docs/workflows.md` (+ `tagteam/data` copy, provenance/manifest per the
  framework rules).
- `tests/test_orders.py` (new). Additions to `tests/test_cycle.py`,
  `tests/test_watcher*.py`, `tests/test_headless*.py`.

## Success criteria
1. With no `tagteam-orders.json` and no run override, every state write,
   prompt and CLI output is byte-identical to today, pinned by a test over
   single-phase and full-roadmap approvals. Nothing is created on disk by a
   read.
2. `tagteam orders stop roadmap` followed by an impl approval in a
   single-phase run leaves the state `full-roadmap` with a queue whose
   current entry is the approved phase. The **unchanged** watcher then advances
   to the next ready phase (tested end to end with a real roadmap in `tmp_path`:
   next ready phase started, a blocked phase skipped, roadmap-complete at the
   end).
3. `tagteam orders stop phase --run` during a full-roadmap run: the next impl
   approval leaves `single-phase` with no roadmap, the watcher does not advance,
   and the run override is gone.
4. A run override survives every intermediate cycle write (plan submit,
   request changes, plan approve, impl init, escalation + ruling) and is dropped
   exactly at the run's end (stop, roadmap-complete) or on `clear --run`.
5. Precedence is `run > run-mode > project > default`, pinned by a table test
   over `effective()`.
6. The headless prompt contains the standing-orders block when there are
   orders. Advisory notes are labelled as not enforced. `cycle rounds` stdout
   is unchanged and the block is on stderr.
7. Under `TAGTEAM_READ_ONLY=1` every `orders` write is refused before disk is
   touched. The bare `tagteam orders` works and creates nothing.
8. A malformed or symlinked `tagteam-orders.json` gives "no orders" plus a
   `warn:`. It never raises on the approval write or a dispatch path.
9. `tagteam-orders.json` does not appear in the impl scope diff.
10. The contract and workflows docs describe both kinds. The shipped-docs
    audit and plugin-sync tests pass.

## Risks and open questions for the reviewer
- **Enforcement in `_derive_top_level_state`, not the watcher.** I chose the
  approval write so the decision is deterministic, recorded, and independent
  of whether a watcher runs or which mode it is in. The cost is a roadmap parse
  inside the approval write. It is bounded and only happens when the stop is
  `roadmap` and the run is single-phase. If parsing fails, the run stops as
  single-phase.
- **Committed `tagteam-orders.json`**, rather than something under the
  git-ignored runtime `.tagteam/`. Arbiter's call if they would rather keep
  orders out of git. Only the path constant changes.
- **No structured PR presets** ("hold PR" / "open PR"). Free text only, since
  tagteam cannot act on them. Phase 71 can offer canned texts without an engine
  change.
