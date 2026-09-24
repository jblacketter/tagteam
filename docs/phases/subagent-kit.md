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
| `tagteam:explore` | haiku | Read, Grep, Glob (**no Bash**) | Read-only fan-out search. Returns file:line pointers and a conclusion, not file dumps. Tagteam-aware: it knows `docs/handoffs/*_rounds.jsonl`, `docs/phases/` and `docs/roadmap.md`, and reads them directly. With no Bash it is read-only on any Claude Code version, without the hook (plan r1). |

All three have `disallowedTools: Write, Edit, NotebookEdit`. Only `test-runner` and `verifier` have Bash, so only they need the hook below.

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
the plugin ships a **session-level `PreToolUse` hook on `Bash`**. It has two
layers: a shell wrapper in `hooks.json` and the Python guard.

**The wrapper (the shipped `hooks.json` command) is the fail-closed layer**
(plan review r1):
- **Prefilter.** The wrapper reads stdin once, and continues only when the
  payload mentions `"agent_type"` with a `tagteam:` value. Every other Bash
  call exits 0 with no output and no Python start. Its overhead is
  **measured and reported** (criterion 2), not promised.
- **Guard.** For a payload the prefilter matched, it runs
  `tagteam hook pre-tool-use`. The **guard's exit code is its verdict**
  (plan review r2). A rewrite is forwarded only after **structural
  validation** by a small validator shipped in the plugin (plan review r3):

  | guard exit | meaning | wrapper does |
  |---|---|---|
  | `0` | kit call, rewritten | pipes stdout to the validator. On success it forwards **the validator's re-serialized object** and exits 0. Otherwise it **exits 2**, and no guard output is forwarded. |
  | `3` | **verified non-kit**: the top-level `agent_type` is not `tagteam:*` (a prefilter false positive) | exits 0 with **no output**: the command passes untouched |
  | `2` | guard deny (unparseable input) | exits 2, passing the guard's stderr reason |
  | anything else | `127` (no CLI), `1` (an **older CLI** answering `unknown hook`; a plain exit 1 is non-blocking in Claude Code), a crash | **exits 2** with "tagteam kit agents need the tagteam CLI ≥ <minVersion> to run Bash read-only" |

  Exit 3 can only come from a CLI that has the guard: today's `hook.py`
  exits 1 for an unknown hook, and a missing CLI gives 127. So "pass
  untouched" is reachable only through a guard that parsed the payload and
  found no kit identity. Everything the wrapper can't positively verify
  blocks.

- **The validator (`plugin/hooks/validate_guard.py`)** is not a subcommand
  of the CLI it checks. It is plugin code, run with `python3` only for
  payloads the prefilter matched, so non-kit calls keep the no-Python fast
  path. It receives the guard's stdout on stdin and the original hook
  payload in an environment variable. It **exits 0 and prints the
  re-serialized object only if all of these hold**:
  - the whole stdout parses as **one JSON object**;
  - `hookSpecificOutput.hookEventName == "PreToolUse"`;
  - `updatedInput` is an object;
  - `updatedInput.command` is a string that **starts with** the exact
    prefix `export TAGTEAM_READ_ONLY=1; `, and equals the prefix plus the
    original command (or the original, when it was already prefixed);
  - every other `updatedInput` field equals the original `tool_input`'s
    (nothing dropped, nothing added);
  - no `permissionDecision` of `allow` (no auto-allow).

  Anything else exits non-zero, and the wrapper exits 2. That covers
  truncated JSON, the prefix in the wrong field, a hollow
  `{"hookSpecificOutput": {}}`, a missing or changed field, and `python3`
  missing (fail closed). A kit helper's Bash never runs unguarded because the CLI and the
  plugin are at different versions.

**The guard (`tagteam hook pre-tool-use`, Python) decides the identity:**
- It parses the payload. The **top-level** `agent_type` must start with
  `tagteam:`, so a `tagteam:` string elsewhere in the command doesn't count.
- For a kit agent it returns
  `hookSpecificOutput.updatedInput`: the **whole** `tool_input` with only
  `command` rewritten to `export TAGTEAM_READ_ONLY=1; <command>`, and every
  other field kept.
  - The `export` covers every part of a compound command
    (`a && tagteam cycle add …`); a bare `VAR=1 a && b` would cover only
    `a`.
  - An already-prefixed command is left alone (idempotent).
  - It sets **no** `permissionDecision` that would auto-allow the command,
    so normal Bash permission evaluation still applies.
- For a prefilter false positive (the top-level identity is not a kit
  agent), it **exits 3 with no output**, and the wrapper lets the command
  through untouched.
- For unparseable input on a payload the wrapper matched, it **exits 2**
  with a reason on stderr (a deny).

**When `agent_type` is missing (plan review r1).** The hook can't tell a
kit helper from the main session without the identity, and no other field
identifies it independently. So there is **no fallback that enforces
anything**, and the plan does not pretend there is:
- **Supported versions.** Automatic enforcement is supported only on Claude
  Code versions that send `agent_type` for subagent tool events (documented
  in the hooks reference, "common input fields").
- **The live check** (criterion 6) records the Claude Code version it ran
  on and the identity it observed (e.g. `tagteam:verifier`). That version
  becomes the kit's documented minimum.
- **Doctor** reads `claude --version`. Below that minimum it warns that the
  kit's Bash helpers run **unguarded** and should not be delegated to. It
  also says `explore` (no Bash) is unaffected.
- **The agent bodies** tell `test-runner` and `verifier` to start every
  command with the export. That is a courtesy, **not** enforcement, and the
  contract text says so.

**CLI/plugin compatibility.** `plugin.json`'s `tagteam.minVersion` becomes
the release that adds `hook pre-tool-use` (the next release, 3.14.9). The
existing SessionStart skew warning (`hook.skew_warning`) then flags an older
CLI in every session. Doctor reports it too, beside the Claude Code version
check.

**The stated limit.** A helper could `unset` the variable deliberately. The
helpers aren't adversarial; this stops accidents, which is the contract's
purpose. The CLI's own read-only guards (Phase 50, and the jobs boundary in
Phase 72) do the actual refusing.

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
  subcommand, using exit codes 0 / 3 / 2.
- `plugin/hooks/validate_guard.py` (new): the structural validator of a
  rewrite. It uses only the standard library.
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
2. **The guard** (unit tests on real-shaped payloads):
   - a top-level `agent_type: tagteam:*` gets its Bash rewritten to
     `export TAGTEAM_READ_ONLY=1; …`, compound commands included, with every
     other `tool_input` field kept and no auto-allow decision;
   - an already-prefixed command is unchanged;
   - a non-kit agent, the main session, and a `tagteam:` string inside the
     command (not the identity) all pass through;
   - unparseable input is denied;
   - a **missing-identity** fixture (a kit-shaped call with no
     `agent_type`) passes through unguarded. This documents the unsupported
     case that doctor warns about.
3. **The shipped wrapper, executed as shipped.** The test runs the exact
   `hooks.json` command string under `sh -c`, with PATH set up for each
   case:
   - a non-kit payload, with `tagteam` absent from PATH: exits 0 with no
     output, and the overhead of 200 runs is **measured and reported**;
   - a kit payload with the real CLI: valid `updatedInput` JSON, exit 0;
   - a kit payload with **no CLI**, an **old CLI** (a fake `tagteam` that
     answers `unknown hook`, exit 1), a CLI that **exits 0 with empty
     output**, and one that prints **invalid output**: each **exits 2**
     with a reason naming the upgrade;
   - **a prefilter false positive, with the real CLI**: a payload whose
     *text* matches the prefilter (a `"agent_type": "tagteam:…"` string
     nested in `tool_input`) while its **top-level** identity is the main
     session or another agent. The wrapper exits 0 with **no output**, so
     the command passes untouched;
   - **exit 0 with no usable result**, each from a fake CLI and each
     **exit 2** with nothing forwarded:
     - `{"hookSpecificOutput": {}}`;
     - `updatedInput` **without** the read-only prefix;
     - **truncated JSON** that contains both markers (review r3,
       counterexample 1);
     - **valid JSON with the prefix in the wrong field**, e.g. in
       `description` while `command` is `tagteam cycle add` (review r3,
       counterexample 2);
     - a rewrite that **drops or changes** another `tool_input` field;
     - `permissionDecision: allow`;
   - `python3` not on PATH while the prefilter matched: exit 2;
   - a guard deny (unparseable input, real CLI): exit 2, with the guard's
     reason;
   - the normal permission flow: the rewritten output carries no
     `permissionDecision`.
4. **The contract:** `SKILL.md` names the three agents with when to use
   each, and says the prefix instruction in the agent bodies is not the
   enforcement. The plugin copy equals the packaged copy. The shipped-docs
   audit passes.
5. **Doctor** reports:
   - whether the plugin (and so the kit) is installed;
   - the CLI's version against the plugin's `minVersion`;
   - the Claude Code version against the kit's recorded minimum, with the
     "Bash helpers unguarded" warning below it.

   Each branch is tested with fakes.
6. **Live, in a real Claude Code session** (once, recorded in the
   closeout):
   - with the plugin loaded (`claude --plugin-dir plugin`) in a scratch
     project, `tagteam:verifier` running `echo $TAGTEAM_READ_ONLY` prints
     `1`;
   - its `tagteam cycle add …` is refused by the CLI's read-only guard;
   - the hook saw `agent_type: tagteam:verifier`;
   - the Claude Code version is recorded, and becomes the kit's documented
     minimum;
   - a normal main-session Bash call is unaffected, and still goes through
     the usual permission prompt where one applies.

## Risks and open questions for the reviewer
- **The hook runs on every Bash call** in every session with the plugin.
  The prefilter keeps a non-kit call to a `cat` and a `case`, with no Python
  start. Criterion 3 measures and reports the overhead. If even that is
  judged too much, the only alternative is no Bash for the helpers; but
  then test-runner can't run tests, which is its whole job.
- **`updatedInput` without a permission decision.** The hooks reference
  documents `updatedInput` for PreToolUse. Criterion 6 confirms live that
  the rewrite applies with no auto-allow. If the installed Claude Code only
  honours `updatedInput` together with a decision, the guard switches to
  **deny unless already prefixed**: the wrapper and the guard enforce the
  prefix the agent bodies ask for. Enforcement stays in the hook either way.
- **Unsupported Claude Code versions** (no `agent_type`) get no automatic
  enforcement. Doctor names them and says not to delegate to the Bash
  helpers there. `explore` is unaffected.
- **`explore` overlaps Claude Code's built-in Explore.** It is kept because
  it is pinned to haiku, is tagteam-aware, and has no Bash, so it is
  read-only on every version. Built-in Explore is none of those.
