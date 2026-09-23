# Phase 70: Standing orders

## Status
- [x] Planning: plan approved round 3 (2026-09-23) at `fd4fda3`. r1: reconciliation, terminal outcomes, no-orders promise; r2: continue vs convert.
- [x] Implementation: branch `phase-70-standing-orders`
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

### Explicit orders, and the no-orders promise
Orders are **explicit** when `tagteam-orders.json` holds a non-null `stop` or at
least one note, or when the state carries a run override. **Without explicit
orders, Phase 70 writes and prints nothing new.** That covers an ordinary
full-roadmap run, whose effective `stop` is `roadmap` from its run mode.
Specifically, there is:
- no `run_decision` in the cycle status,
- no STANDING ORDERS block in the prompt or on stderr,
- no `Orders:` line in `tagteam state`,
- no change to the approval write.

A full-roadmap run with no orders is byte-identical to today. With explicit
orders, the new output is the intended change: the prompt/stderr block, the
`Orders:` line, and `run_decision` in the approved impl cycle's status.

### Enforcement: decided once at the fresh approval, recorded, re-applied safely
`_derive_top_level_state()` has several callers:
- `add_round` (reviewer APPROVE, arbiter `rule approve`),
- `init_cycle`,
- `rearm`,
- the gatekeeper,
- **`tagteam state sync`** (`state.py:856`), which re-derives any cycle,
  current or old.

So resolving orders inside it would re-decide on every sync. Instead the
decision is split in two.

**1. Decide: in `add_round` only, on a fresh impl APPROVE.** This runs inside
the writer lock, *before* the status file is written. It fires only when the
action is APPROVE, the type is impl, and the orders are explicit. It calls
`orders.effective()` once and records the result on the **cycle status**
(`<phase>_impl_status.json`, the per-cycle source of truth):

```json
"run_decision": {"outcome": "continue" | "convert" | "stop" | "complete",
                 "reason": "order" | "roadmap-invalid: …" | "roadmap-exhausted",
                 "stop": "phase" | "roadmap", "source": "run" | "run-mode" | "project",
                 "queue": [...], "current_index": N, "ts": "…"}
```

There are two kinds of advance, and they are deliberately different outcomes:
- **`continue`**: the run is already full-roadmap. Nothing is recorded but
  the outcome. The run's own `roadmap` (`queue`, `current_index`, **and its
  `completed` list**) is left exactly as it is, by the existing preservation
  rule. The watcher's `_try_roadmap_advance` adds the phase just approved to
  that `completed` list, and `_select_next_phase` keeps using it to exclude
  earlier approvals and satisfy dependencies. That is today's behaviour, byte
  for byte.
- **`convert`**: the run was single-phase and becomes a new full-roadmap run.
  Only this outcome records `queue` and `current_index`, and only this one
  starts `completed` empty, because the run has no earlier approvals. `add_round`
then calls `_derive_top_level_state(..., fresh_decision=True)`. That flag is the
only thing that lets a derive (a) materialise a `convert` queue and (b) drop the
run override when the outcome ends the run (`stop`, `complete`). No other caller
passes it, so no sync, gate, rearm or init ever converts a run or consumes an
override.

**2. Apply: in `_derive_top_level_state()`, from the recorded decision only,
never by re-resolving.** It reads `run_decision` from the cycle status it
already reads:

| Recorded outcome | Applied on every derive (fresh or sync) |
|---|---|
| none (pre-Phase-70 or no explicit orders) | today's behaviour, unchanged |
| `stop` / `complete` | `run_mode: single-phase`, no `roadmap`. Idempotent, and on the safe side: re-applying it can never start a phase. |
| `continue` | The existing roadmap-preservation rule and nothing else, fresh or sync. The run's `queue`, `current_index` and `completed` are never rewritten. |
| `convert` | **Fresh write:** `run_mode: full-roadmap` + a new `roadmap` from the recorded `queue` / `current_index` with `completed: []`. **Sync:** the existing roadmap-preservation rule and nothing else. The state's roadmap (with whatever `completed` the watcher has since added) is kept if it still points at this phase, otherwise single-phase. A sync never re-creates a queue, because that could restart an advance the watcher already made. |

`orders` (the run override) is preserved by every derive except the fresh
run-ending one.

The two regressions this closes (success criterion 11):
- Project `stop: roadmap`, run `stop: phase`. The impl approval records
  `stop` and drops the override. A `state sync` of that cycle re-applies the
  recorded `stop` and stays single-phase. Without the recording it would
  re-resolve the project `roadmap` and convert the run.
- Run `done`. The arbiter queues `--run stop roadmap` for the next run. A
  `state sync` of the previous approved cycle preserves the override and
  applies that cycle's recorded decision (or none). It never consumes the
  override and never converts the old cycle.

### The outcomes at a fresh impl approval (explicit orders)
| Effective `stop` | `run_mode` | Roadmap | Outcome | Run override |
|---|---|---|---|---|
| `roadmap` | full-roadmap | (not consulted) | `continue`. The run's roadmap, including `completed`, is untouched (state unchanged from today), and the watcher advances as it does now. | kept |
| `roadmap` | single-phase | valid, and other non-terminal phases remain | `convert`. `queue = [approved phase] + build_queue(roadmap)` minus the approved phase if it appears again, `current_index: 0`. The approved phase is always `queue[0]`, **even when the roadmap already marks it Complete** (`build_queue` omits terminal phases). That way the existing preservation rule (`queue[idx] == phase`) holds and the watcher's `_try_roadmap_advance` records it as completed. The watcher then starts the next ready phase, reports roadmap-complete, or pauses as blocked. | kept |
| `roadmap` | single-phase | valid, **nothing else non-terminal**: `build_queue` raises its all-complete `ValueError`, or returns only the approved phase | `complete` / `roadmap-exhausted`. The run is over and there is nothing to advance to. | **dropped** |
| `roadmap` | single-phase | missing, unparseable, or `check_graph` problems | `stop` / `roadmap-invalid: <first problem>`. One line on stderr from the writer says the run stopped and why. | **dropped** |
| `phase` | full-roadmap | (not consulted) | `stop` / `order`: `run_mode: single-phase`, `roadmap` dropped. `--run stop phase` during a roadmap run halts it at the end of this phase. | **dropped** |
| `phase` | single-phase | (not consulted) | `stop` / `order`, state unchanged from today. | **dropped** |

The `ValueError` from `build_queue` is the only exception the decision
catches by type. The all-complete case is recognised by the roadmap
module's own test (`ready_phases`/terminal status), not by message text. Any
other exception from reading the roadmap becomes `roadmap-invalid`. **Nothing
can raise out of the approval write.** At worst, the decision is `stop`.

Plan approvals are untouched: 68a's headless plan→impl advance and the tab-mode
notice stay as they are. **Without a watcher**, the state still records what
comes next, and the contract tells the lead to follow it, as full-roadmap mode
does today.

### Lifetime of the run override
It is dropped at **every terminal outcome** and kept only through **resumable
pauses**:
- **Dropped** by the fresh impl approval whose outcome is `stop` or
  `complete` (every row marked *dropped* above).
- **Dropped** by the watcher's `_select_next_phase` write that sets `result:
  roadmap-complete`. That is one extra key in an update the watcher already
  makes, and it is the only watcher edit.
- **Dropped** by `tagteam orders clear --run`.
- **Kept** through the resumable pauses, which are these and only these: an
  escalation, a NEED_HUMAN, and the full-roadmap pauses with `pause_reason`
  `blocked:` / `roadmap invalid:` / `stale queue:` (all resumed with
  `roadmap resume` or a ruling). It is also kept through every intermediate
  cycle write: submit, request changes, plan approve, `init_cycle`, `rearm`, a
  gate bounce.

A run override set while the state is `done` applies to the next run the lead
starts. It is preserved by the next `cycle init`, which is how the arbiter
says "for the next run, go to the end".

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
  notes, marked "advisory — tagteam does not enforce these". It is absent
  unless orders are explicit (see above). The block is capped like the
  other blocks, and when capped it points to `tagteam orders`.
- **`tagteam cycle rounds`:** the same block goes to **stderr**, before the
  JSON lines, so stdout stays machine-readable. An interactive agent sees it;
  a parser does not.
- **`tagteam state`:** one `Orders:` line, only when orders are explicit:
  `stop: roadmap (project) · 2 advisory`. After an approval with a
  `run_decision` it adds the outcome: `· last approval: continued` / `converted to a roadmap run` /
  `stopped (order)` / `roadmap exhausted` / `stopped (roadmap invalid: …)`.
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
  `decide(state, phase, project_dir)`, `apply_decision(updates, decision, fresh)`, CLI.
- `tagteam/cli.py`: dispatch `orders`, add it to `READ_ONLY_COMMANDS` for the
  bare read.
- `tagteam/cycle.py`: `add_round` records `run_decision` on a fresh impl
  APPROVE (`orders.decide()`); `_derive_top_level_state()` preserves `orders`
  (dropped only with `fresh_decision=True`) and applies a recorded decision. `_TAGTEAM_ARTIFACT_FILES` +=
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
1. Without explicit orders (no file, an empty file, `stop: null` with no notes,
   no run override), the approval write, cycle status, state, prompt,
   `cycle rounds` stdout+stderr and `tagteam state` output are byte-identical
   to today for single-phase **and** full-roadmap approvals. A test pins both.
   A read creates nothing on disk.
2. `tagteam orders stop roadmap` followed by an impl approval in a
   single-phase run leaves the state `full-roadmap` with a queue whose
   current entry is the approved phase. The **unchanged** watcher then advances
   to the next ready phase (tested end to end with a real roadmap in `tmp_path`:
   next ready phase started, a blocked phase skipped, roadmap-complete at the
   end).
3. `tagteam orders stop phase --run` during a full-roadmap run: the next impl
   approval records `stop`, leaves `single-phase` with no roadmap, the watcher
   does not advance, and the run override is gone.
4. A run override survives every intermediate cycle write and every resumable
   pause listed above. It is dropped exactly at each terminal outcome (`stop`,
   `complete` for exhausted and for invalid roadmaps, watcher roadmap-complete)
   or on `clear --run`. Each case has its own test.
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
11. Reconciliation is idempotent:
    (a) project `roadmap` + run `phase`, approve, `state sync` of the same
        cycle stays single-phase, and a second sync changes nothing;
    (b) an override queued while `done` survives a `state sync` of the previous
        approved cycle and is not applied to it;
    (c) a `state sync` of a `convert` cycle after the watcher moved on never
        re-creates the queue.
13. An existing roadmap run keeps its history under explicit orders (the r2
    review). A real roadmap in `tmp_path` with phases A → B → C, where C
    depends on A, and the docs still mark A and B non-terminal. The run state
    has `queue [A, B, C]`, `completed [A]` and current B, and there is one
    advisory note. Approving B records `continue`, and the state's `roadmap`
    is unchanged by the approval write. The unchanged watcher then starts C
    with `completed [A, B]`: A is not restarted, and C's dependency on A counts
    as satisfied through the run's `completed`, not the roadmap text.
12. Conversion edge cases: the approved phase already marked Complete in the
    roadmap is `queue[0]` and the watcher advances past it; an exhausted
    roadmap gives `complete`; a missing roadmap, a malformed roadmap and a
    `check_graph` problem each give `stop` / `roadmap-invalid`. In every case
    the approval write succeeds.

## Risks and open questions for the reviewer
- **Decided in `add_round`, applied in `_derive_top_level_state`** (the r1
  review). The decision is deterministic, recorded on the cycle status and
  independent of the watcher. The cost is a roadmap parse inside a fresh impl
  approval, which happens only when the stop is `roadmap` on a single-phase run
  with explicit orders. Any failure becomes `stop`.
- **Committed `tagteam-orders.json`**, rather than something under the
  git-ignored runtime `.tagteam/`. Arbiter's call if they would rather keep
  orders out of git. Only the path constant changes.
- **No structured PR presets** ("hold PR" / "open PR"). Free text only, since
  tagteam cannot act on them. Phase 71 can offer canned texts without an engine
  change.
