# Plan Review Cycle: Cycle Storage & CLI

- **Phase:** cycle-storage-cli
- **Type:** plan
- **Date:** 2026-03-21
- **Lead:** Claude
- **Reviewer:** Codex

## Reference

Phase plan: `docs/phases/cycle-storage-cli.md`
Design doc: `docs/design/handoff-performance-recommendations.md`

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Submitting a focused phase plan based on the recommendations in `docs/design/handoff-performance-recommendations.md`. This is Phase A from that document — cycle storage and CLI only.

**What this covers:**
- New `cycle.py` module: JSONL rounds + JSON status + CLI commands (`init`, `add`, `status`, `rounds`, `render`)
- Centralized `list_cycles()` with de-duplicated discovery (JSONL precedence over legacy `.md`)
- Updated `SKILL.md` so agents use CLI commands instead of manual markdown edits
- All consumers updated: `parser.py`, `server.py`, `app.js`, `handoff_reader.py`, `review_replay.py`
- Synthesized markdown via `cycle render` for human readability

**What this intentionally excludes (per the design doc's phased rollout):**
- Migration of existing `_cycle.md` files (Phase B)
- Migration tests (Phase B)
- Roadmap format migration (Phase C, if still needed after A lands)

This tighter scope addresses the core bottleneck — repeated markdown read/modify/write on every handoff turn — without the scope creep that escalated the previous plan.

Please review `docs/phases/cycle-storage-cli.md` for completeness, feasibility, and gaps.

### Reviewer

**Action:** REQUEST_CHANGES

The scope is appropriately tighter than the previous plan, but there is one workflow-semantics issue in the core storage API.

**Blocking 1: `add_round()` advances the round counter on the wrong turn.** The plan says `ai_handoff/cycle.py::add_round(...)` "increments round when role is reviewer". That conflicts with the current cycle semantics in `.claude/skills/handoff/SKILL.md`, where the reviewer responds within the current round and the **lead** creates `Round N+1` when addressing feedback. In other words:

- Round 1: lead submits, reviewer reviews
- Round 2 only begins when the lead submits the revision

If `add_round()` increments on reviewer writes, the status file's `round` field will move ahead before the lead has actually started the next round, which will desynchronize state/UI output and make rendered cycles awkward.

Please update the plan to define round progression explicitly, likely by making reviewer writes keep the current round number and making the next lead submission create `N+1`.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed round progression semantics. Updated `add_round()` in the plan:

- The `round` field is passed explicitly by the caller via `--round N` — `add_round` does not auto-increment
- Lead passes `--round N+1` when starting a new round (matches existing SKILL.md: lead creates Round N+1)
- Reviewer passes `--round N` (same round — reviewer responds within the current round)
- Status JSON's `round` field updates to match the incoming `round` value, so it only advances when the lead submits

This matches existing cycle semantics: Round 1 = lead submits + reviewer reviews. Round 2 begins only when lead submits the revision.

Updated plan: `docs/phases/cycle-storage-cli.md`

### Reviewer

**Action:** REQUEST_CHANGES

The round progression issue is fixed, but the storage API still leaves the cycle state machine underspecified.

**Blocking 1: `cycle add` does not define how reviewer actions update status state/turn, yet there is no other mutation command to do it.** The plan says `cycle add` is the hot-path write command and that `add_round()` updates `status.json`, but it never defines the transition rules for the fields that consumers actually depend on: `state` and `ready_for`.

Right now the plan is missing the concrete mapping for cases like:
- lead `SUBMIT_FOR_REVIEW` -> `ready_for: reviewer`, `state: in-progress`
- reviewer `REQUEST_CHANGES` -> `ready_for: lead`, `state: in-progress`
- reviewer `APPROVE` -> `state: approved` (and what `ready_for` becomes)
- reviewer `ESCALATE` / `NEED_HUMAN` -> `state: escalated` or `needs-human`, `ready_for: human`

Because there is no separate `cycle set-status` command in scope, these transitions have to be explicitly defined inside `cycle add` / `add_round()`. Otherwise `cycle status`, the dashboard, the TUI, and `render_cycle()` will all have ambiguous or incorrect status behavior.

Related inconsistency: the plan now says the caller passes `--round N`, but the listed `add_round(...)` function signature still omits a `round` parameter, and the Step 2 example command for the lead still omits `--round`. Please make the write API internally consistent while defining the status transitions above.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed both issues. Updated the plan with:

**1. Explicit status transition table in `add_round()`:**

| Action | `state` | `ready_for` | `round` |
|--------|---------|-------------|---------|
| `SUBMIT_FOR_REVIEW` | `in-progress` | `reviewer` | caller's value |
| `REQUEST_CHANGES` | `in-progress` | `lead` | unchanged |
| `APPROVE` | `approved` | _(cleared)_ | unchanged |
| `ESCALATE` | `escalated` | `human` | unchanged |
| `NEED_HUMAN` | `needs-human` | `human` | unchanged |

These transitions are applied to `_status.json` automatically by `add_round()` — no separate `set-status` command needed.

**2. Fixed all signature/example inconsistencies:**
- `add_round()` function signature now includes `round` parameter
- Step 2 examples now include `--round N` for both lead and reviewer
- Added reviewer example alongside lead example

Updated plan: `docs/phases/cycle-storage-cli.md`

### Reviewer

**Action:** REQUEST_CHANGES

The status-transition rules are now defined, but the CLI/action vocabulary is still internally inconsistent.

**Blocking 1: The plan mixes `SUBMIT` and `SUBMIT_FOR_REVIEW` as if they were the same action, but only one should exist in the contract.** In `docs/phases/cycle-storage-cli.md`, the transition table and round examples use `SUBMIT_FOR_REVIEW`, which matches the current workflow and parser/tests. But the CLI surface and Step 2 examples still use `--action SUBMIT`. If implemented literally, that leaves one of two bad outcomes:

- `cycle add --action SUBMIT` does not match the documented transition table, or
- the implementation quietly supports multiple spellings for the same semantic action, which adds avoidable ambiguity to rendering, tests, and backward compatibility logic.

Please make the action vocabulary explicit and consistent across the whole plan. The simplest fix is probably to standardize on the existing `SUBMIT_FOR_REVIEW` name everywhere unless there is a strong reason to introduce aliases.

## Round 4

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed. Standardized on `SUBMIT_FOR_REVIEW` everywhere in the plan — CLI surface, Step 2 examples, and transition table all use the same action name. No aliases, no ambiguity.

The canonical action vocabulary is now: `SUBMIT_FOR_REVIEW`, `REQUEST_CHANGES`, `APPROVE`, `ESCALATE`, `NEED_HUMAN` — matching existing SKILL.md usage.

Updated plan: `docs/phases/cycle-storage-cli.md`

### Reviewer

**Action:** REQUEST_CHANGES

The action vocabulary is fixed, but the initialization semantics still create an invalid intermediate cycle state.

**Blocking 1: `cycle init` currently creates a status file that says the cycle is already ready for the reviewer before the lead has submitted Round 1.** In the plan, `init_cycle(...)` creates `_status.json` with `state: "in-progress"`, `ready_for: "reviewer"`, and `round: 1`, while also creating an empty JSONL file. That means there is a window where:

- the cycle appears active,
- the reviewer appears to be up,
- but there is no lead submission in the round log yet.

That does not match the existing workflow semantics, where Round 1 begins with the lead submission. It also means `cycle status`, `render_cycle()`, the dashboard, and any recovery flow after a failed `cycle add` could observe a logically impossible state.

Please update the plan so initialization is semantically valid. The simplest fixes are:
1. `cycle init` creates a pre-submission state such as `ready_for: lead` with `round: 0`, and the first `SUBMIT_FOR_REVIEW` advances it to Round 1 / reviewer, or
2. collapse initialization and first lead submission into a single atomic command so this invalid intermediate state cannot exist.

## Round 5

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed with option 2 — collapsed init and first submission into a single atomic command. `cycle init` now requires `--content` and writes both status JSON and the first JSONL round entry in one operation. No observable intermediate state where the cycle exists without round data.

Updated: function signature (`init_cycle` now takes `content`), CLI command (`cycle init` includes `--content`), and Step 2 example (single command, no separate `cycle add` for first round).

Updated plan: `docs/phases/cycle-storage-cli.md`

### Reviewer

**Action:** REQUEST_CHANGES

The plan is substantially improved, but one gap remains and this is now round 5, so I’m escalating to the human arbiter.

**Blocking 1: The write API still assumes rich review content can be safely passed inline as `--content`, which may undercut review depth or create brittle shell behavior.** The phase goal is to make cycles faster **without sacrificing depth of analysis**, but every example still uses:

`python -m ai_handoff cycle add ... --content "..."`

That is fine for short summaries, but real review feedback often contains:
- multiple paragraphs
- quotes
- bullets
- backticks
- line breaks

If `cycle add` only accepts inline `--content`, agents either have to compress feedback into a short shell-safe string or rely on fragile quoting/escaping for long structured feedback. That pushes against the stated goal of preserving review depth while improving speed.

**Arbiter question:** Should this phase explicitly support a richer input path for content, such as `--content-file`, stdin, or both, before approval? Or is inline `--content` alone acceptable for Phase A?

## Round 6

### Human Arbiter

**Decision:** Accept Codex's feedback. Add stdin support for rich content input.

### Lead

**Action:** SUBMIT_FOR_REVIEW

Resolved the escalation per arbiter decision. Updated the plan with dual content input:

- `--content "short text"` — Inline, for simple approvals and summaries
- stdin (when `--content` is omitted) — For detailed multi-line feedback via heredocs

Agents use `<<'EOF'` heredocs for structured feedback — backticks, quotes, and special characters are safe without escaping. This preserves review depth (the stated goal) while keeping it to one Bash call.

Updated: function docs, CLI surface, and Step 2 examples (added detailed reviewer example with heredoc).

Updated plan: `docs/phases/cycle-storage-cli.md`

### Reviewer

**Action:** APPROVE

Approved. The plan now resolves the round semantics, status transitions, action vocabulary, initialization behavior, and rich multi-line content input without expanding scope beyond the performance-critical cycle path.

---

<!-- CYCLE_STATUS -->
READY_FOR: reviewer
ROUND: 6
STATE: approved
