# Phase 73: Subagent kit

## Status
- [ ] Planning: in review (plan cycle opened 2026-09-24)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
**Deterministic first, a cheap model second, the top model last** (the rule
from Phase 72). The lead, on the top model, still does bulky work itself:
- runs focused tests and reads the raw output;
- re-reads files to check a claim;
- fans out searches.

Every line of that output stays in its context for the rest of the turn, so
each later call pays for it again.

This phase ships **three helpers in the tagteam Claude Code plugin**. Each is
cheap, has no write tools, and is read-only for tagteam itself. The lead
hands them the bulky steps and gets back a digest:

| Agent | Model | Tools | Job |
|---|---|---|---|
| `tagteam:test-runner` | haiku | Bash, Read, Grep, Glob | Runs the command it is given. Returns counts, the failing test ids and the last lines of each failure. Never fixes, and never runs the full suite (the one-run rule stays with the lead and the gate). |
| `tagteam:verifier` | sonnet | Bash, Read, Grep, Glob | Re-checks one claim ("12 new tests, all green", "X is only called from Y") and answers CONFIRMED / REFUTED / UNVERIFIABLE with evidence. It can't fix what it finds, which is the arbiter's rule for verifiers. |
| `tagteam:explore` | haiku | Bash, Read, Grep, Glob | Read-only fan-out search. Returns file:line pointers and a conclusion, not file dumps. Tagteam-aware: it knows `docs/handoffs/`, `docs/phases/` and `tagteam cycle rounds`. |

All three have `disallowedTools: Write, Edit, NotebookEdit`.

This is Claude-side only. Codex has no plugin subagents, and the reviewer's
savings come from the gate, the panel and effort.

**What it saves, stated honestly** (research pack 02): subagents don't reduce
what the top model *writes*. They reduce what it *reads back*, and they keep
its context small, so each later call in the turn is cheaper. Expect a gain
on long impl turns with many test runs, and none on a short plan turn.

## Scope
**In**
- `plugin/agents/{test-runner,verifier,explore}.md`: frontmatter
  (`name`, `description`, `model`, `tools`, `disallowedTools`) and a
  focused body for each.
- **Read-only enforcement for their Bash** (the one thing frontmatter can't
  do; see below): a `PreToolUse` hook in `plugin/hooks/hooks.json` and
  `tagteam hook pre-tool-use`.
- **The contract** (`plugin/skills/handoff/SKILL.md` and its packaged copy):
  the "Read-only helpers" paragraph names the kit and says when to use each
  helper. The rules don't change: one full-suite run per submission, the
  gate's run is the run on the record, and a helper never writes a cycle.
- `tagteam doctor`: one line saying whether the kit's agents are available
  (the plugin is installed and at a version that ships them).
- Tests, plus **one live check in a real Claude Code session** (below).

**Out**
- Codex-side helpers.
- A per-activity model policy (Phase 57 stays deferred).
- `CLAUDE_CODE_SUBAGENT_MODEL` in headless spawns.
- A lead self-review pre-flight (research step 5).
- `codex-brief`. It already exists at user level, and the kit doesn't
  duplicate it.

## Technical approach

### Where the agents live
Claude Code scans a plugin's `agents/` directory, and each agent is named
`<plugin>:<file>`, i.e. `tagteam:test-runner`. `plugin.json` needs no
`agents` field; setting one would replace the default scan. Plugin agents
support `tools`, `disallowedTools`, `model` and `effort`. They **don't**
support `hooks`, `permissionMode` or environment variables; that is a
plugin security restriction (plugins-reference).

### `TAGTEAM_READ_ONLY=1` for the helpers' Bash
The contract requires every delegated helper to run with
`TAGTEAM_READ_ONLY=1`. Tool restriction alone isn't enough: a helper with
Bash could run `tagteam cycle add`. Frontmatter can't set the variable, so
the plugin ships a **session-level `PreToolUse` hook on `Bash`**:
- **A shell prefilter.** The hook command reads stdin once, and continues
  only when the JSON contains `"agent_type"` starting with `tagteam:`.
  Every other Bash call in every session with the plugin exits in
  microseconds, with no Python start. This matters because the hook runs on
  all Bash calls, not just the kit's.
- **`tagteam hook pre-tool-use`** (Python, testable) parses the input. For a
  `tagteam:*` agent it returns `hookSpecificOutput.updatedInput` with the
  command rewritten to `export TAGTEAM_READ_ONLY=1; <command>`. The `export`
  covers every part of a compound command (`a && tagteam cycle add …`); a
  bare `VAR=1 a && b` would cover only `a`. A command that already starts
  with that export is left alone, so the rewrite is idempotent.
- **It fails closed.** If `tagteam` is missing, or the input can't be parsed
  while the prefilter matched, the hook **denies** the Bash call with a
  reason ("tagteam kit agents need the tagteam CLI to run Bash read-only").
  A helper never runs unguarded.
- **The limit, stated:** a helper could `unset` the variable deliberately.
  The helpers aren't adversarial; this stops accidents, which is the
  contract's purpose. The CLI's own read-only guards (Phase 50, and the
  jobs boundary in Phase 72) do the actual refusing.

### The contract text
The "Read-only helpers" paragraph gains a short table of the three agents,
each with *when to use it*:
- **`test-runner`:** a focused test file, not the suite.
- **`verifier`:** before you claim something in a submission.
- **`explore`:** when answering means reading across many files.

It also says tagteam sets `TAGTEAM_READ_ONLY` for them itself (the hook), as
it already does for panel lenses and the briefer. `SKILL.md` stays the one
contract: the packaged copy is updated with it, the plugin/package parity
test covers that, and so does `TestShippedDocsAudit`.

### Measurement: no A/B in this phase (the arbiter's decision, 2026-09-24)
The roadmap asks for savings measured before and after with Phase 55's usage
data. The arbiter chose to **skip a dedicated before/after comparison**. It
would cost two headless lead turns and yield a single observation.
Measurement happens through use instead:
- Headless turns record `model_usage` per model, and subagent requests are
  included in it (Agent SDK cost-tracking).
- So once the kit is used in headless turns, `tagteam usage --by model`
  shows its haiku and sonnet tokens beside the lead's own.
- Interactive turns record nothing, so there is no "before" for the recent
  phases.
- The closeout says plainly that the savings are **unmeasured** at merge.

## Files
- `plugin/agents/test-runner.md`, `plugin/agents/verifier.md`,
  `plugin/agents/explore.md` (new).
- `plugin/hooks/hooks.json`: adds the `PreToolUse` Bash entry.
- `tagteam/hook.py` (where `session-start` lives): the `pre-tool-use`
  subcommand.
- `plugin/skills/handoff/SKILL.md` and the packaged copy: the helpers
  paragraph.
- `tagteam/diagnostics.py` (doctor): the kit line.
- No `pyproject.toml` change: the plugin tree isn't wheel package-data. It
  reaches users through the marketplace (`.claude-plugin/marketplace.json`
  → `./plugin`), so the agents ship the way the skill and the SessionStart
  hook already do.
- Tests: `tests/test_plugin.py` (the agent files and hook entry) and
  `tests/test_hook_pre_tool_use.py`.

## Success criteria
1. **The agent files are pinned.** Each has `name`, `description` and
   `model` as in the table. Its `tools` contain no write tool, and its
   `disallowedTools` include `Write`, `Edit` and `NotebookEdit`. Its body
   states it never fixes, never runs the full suite (test-runner) and never
   writes a cycle.
2. **The hook logic** (unit tests on real-shaped hook input):
   - a `tagteam:*` agent's Bash is rewritten to
     `export TAGTEAM_READ_ONLY=1; …`, compound commands included;
   - an already-prefixed command is unchanged;
   - a non-kit agent and the main session pass through untouched;
   - malformed input for a kit agent is **denied**;
   - the shell prefilter exits without starting Python for a non-kit
     payload (timed, and with `tagteam` absent from PATH).
3. **Fail-closed with no CLI:** the prefilter matched and `tagteam` is not
   on PATH, so the hook denies.
4. **The contract:** `SKILL.md` names the three agents with when to use
   each. The plugin copy equals the packaged copy. The shipped-docs audit
   passes.
5. **Doctor** says whether the kit is available.
6. **Live, in a real Claude Code session** (once, recorded in the
   closeout): with the plugin loaded (`claude --plugin-dir plugin`) in a
   scratch project, asking `tagteam:verifier` to run
   `echo $TAGTEAM_READ_ONLY` prints `1`, and asking it to run
   `tagteam cycle add …` is refused by the CLI's read-only guard.

## Risks and open questions for the reviewer
- **The hook runs on every Bash call** in every session with the plugin.
  The shell prefilter keeps a non-kit call at a `cat` plus a `case`
  (criterion 2 times it). If even that is judged too much, the alternative
  is to give the helpers no Bash at all; but then test-runner can't run
  tests, which is its whole job.
- **`updatedInput` on PreToolUse and `agent_type` in the hook input** are
  documented (hooks reference). Criterion 6 proves them live, because the
  suite can't. If the live check shows either missing on the installed
  Claude Code version, the fallback is **deny unless already prefixed**:
  the agent bodies tell the helper to start every command with the export,
  and the hook enforces it. That is a smaller change of the same shape.
- **`explore` overlaps Claude Code's built-in Explore.** It is kept because
  it is pinned to haiku, is tagteam-aware, and is read-only for tagteam by
  the hook. Built-in Explore is none of those.
