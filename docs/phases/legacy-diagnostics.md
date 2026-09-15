# Phase 53: Legacy Diagnostics and Capability Visibility

## Status
- [ ] Planning
- [ ] Approved
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
Phase 52 made `setup`/`upgrade` safe for the files tagteam manages. It says
nothing about the workflow artifacts *around* them: a project can carry
pre-plugin skills that assign fixed roles ("Claude is always the lead for
planning") or use retired command syntax (`/handoff-cycle`), and nothing tells
the user. Neither does anything say what each role's process can actually
reach: which executables exist, which instruction file each provider loads,
which tool configuration is only configured and never verified, which
protections are instructions rather than enforcement.

This phase adds one read-only report, `tagteam doctor`, covering both, plus a
one-line pointer from `setup`/`upgrade`. It reports. It never edits, deletes
or probes a service, and it never prints a configured value, argument or
secret (see "Output boundary" for what is read versus what is printed).
Depends on Phase 52 (merged, PR #36).

Source of scope: priority 2 item 3 and priority 3 of
`docs/role-neutrality-recommendations-2026-09-09.md`; the Phase 53 line in
`docs/phases/role-neutrality.md` ("report-only recognized legacy workflow
diagnostics and bounded capability/context visibility, retaining Phase 49's
user-level preservation rule"). Roles come from `tagteam.yaml`.

## Scope
In:
1. `tagteam/diagnostics.py`: a pure, read-only scanner for recognized legacy
   workflow artifacts, returning findings with path, rule, evidence (line
   number + excerpt), provenance and remediation.
2. Capability/context visibility in the same module: per-role executable,
   contract entry points, instruction sources and what each provider sees,
   known tool configuration (names only), protection notes.
3. `tagteam doctor [DIR] [--json]`, allowed under `TAGTEAM_READ_ONLY=1`.
4. `setup`, `setup --preview`, `upgrade` and `upgrade --preview` print one
   summary line when findings exist, pointing at `tagteam doctor`. `upgrade`
   prints it per project, next to that project's framework report.
5. A short "Capabilities and alternatives" section in shipped
   `tagteam/data/workflows.md`: how to document task-relevant capabilities in
   project instructions, and what doctor can and cannot establish.

Out: any fix, rewrite, move or delete of a finding (Phase 52's `--accept` is
the only write path, and it covers managed files only); live probing of MCP
servers, credentials, or network services; reading provider memory stores;
new `tagteam.yaml` schema; a capability registry; enforcement (hooks,
sandboxes); downstream project cleanup (Northstar etc.); release/tag.

## Technical Approach

### Legacy workflow findings
**Scanned paths** (project root only, through the shape-checked reader in
"Output boundary and config reads"; Markdown capped at 256 KB with a
`truncated scan` note):
- `.claude/skills/*/SKILL.md` and `.claude/skills/*.md`
- `.claude/commands/*.md`
- `AGENTS.md`, `CLAUDE.md`

Excluded: the managed vendored `.claude/skills/handoff/SKILL.md` and every
path in the Phase 52 managed set. Their state is already reported by the
framework classification (`current` / `framework` / `custom`); doctor prints
that classification in its framework section, not as findings, so one path
never gets two verdicts.

**Rules.** A finding needs content evidence. A file name alone (`plan`,
`review`) is never a finding; a mention of Claude or Codex alone is never a
finding.

| Rule | Evidence | Why it matters |
|---|---|---|
| `retired-command` | `/handoff-(cycle\|plan\|review\|implement\|decide\|escalate\|phase\|status\|sync\|handoff)` as a command token; `ai_handoff` / `python -m ai_handoff` | Commands removed in Feb 2026 (`20f644e`) and the pre-rename package; an agent following them fails or runs the wrong thing |
| `fixed-role` | A configured provider name stated as a permanent role holder: `<name> is (always )?the (lead\|reviewer)`, `<name> (Lead) / <name> (Reviewer)`, `<name> has (the )?final (decision\|say)` — reported **only when it contradicts current `tagteam.yaml`** | Instructions that override the configured assignment. A statement that matches config is not a defect today, but is reported as `info` (stale after a role switch) |
| `legacy-skill-shape` | A skill named like the retired shipped set (`plan`, `review`, `implement`, `decide`, `escalate`, `phase`, `status`, `sync`) **and** at least one `retired-command` or `fixed-role` hit in the same file | Raises the finding from "a line" to "this whole skill is probably a pre-plugin copy"; still a candidate |

Pattern matching is case-insensitive, and names are matched against the
configured agent names plus `claude`/`codex`. Rules are a module-level table so
tests pin them and additions are one row.

**Provenance.** Tagteam never shipped these directory skills and holds no
hashes for them (historical hash-mining stays out of scope), so every finding
is `candidate: content match, no provenance`. Severity is `warn` for
`retired-command` and contradicting `fixed-role`, `info` for a matching
`fixed-role`.

**Remediation text** names the manual action and the current equivalent
(`/tagteam:handoff` or `tagteam contract`; "roles come from tagteam.yaml").
It never prints a shell command that deletes or edits, matching Phase 49.

**User level.** Phase 49's `legacy_handoff_skill_candidates` result is
included unchanged, by path, as `user-level candidate`. User-level files are
never content-scanned. Phase 49's rule stands: a project tool reports on
`~/.claude/`, it does not write there.

### Capability and context visibility
States, used consistently: `configured` (a file says so), `found` (verified
locally without contacting anything: an executable on `PATH`, a readable
regular file), `missing` (confirmed absent), `unknown` (could not be
determined; the reason is always shown). A failed or unavailable check is
`unknown`, never `missing`.

Sections:
1. **Roles: two modes, observed separately.** Doctor does not call
   `onboarding.describe_roles` (it prints the whole launch command). It builds
   structured role data from `config.get_agent_names`,
   `get_launch_commands`, `get_headless_spec` and `infer_headless_provider`:
   - *Desktop / terminal launch*: display name; the command's first token
     only (`shlex.split`), shown as its basename, resolved with
     `shutil.which` → `found <resolved path>` / `missing`. A first token of
     the form `NAME=value`, or a command `shlex` cannot split, is `unknown
     (launch command not parsed)`, echoing nothing from it. Arguments are
     never printed or put in JSON.
   - *Headless*: provider from `infer_headless_provider` (an explicit
     `headless.provider` wins; the display name is never used when a
     provider or command says otherwise, exactly as the headless engine
     resolves it), labelled with its source (`explicit` / `inferred from
     command` / `inferred from name` / `unresolved`); executable =
     `headless.executable` if configured, else the provider name, resolved
     with `shutil.which` → `found` / `missing`; an unknown or unresolved
     provider → `unknown`. `headless.args` are never printed.
   The two modes are separate rows in text and separate objects in JSON, so
   one executable check never stands for both.
2. **Contract.** `tagteam contract` always `found` (in-process). The vendored
   skill with its Phase 52 classification. **Plugin availability** is
   derived from `plugin.list_plugins` with doctor's own classification, not
   from `PluginStatus.installed` (whose `False` folds "could not ask" into
   "not installed" for fallback selection; that behavior stays unchanged):
   - `list_plugins` returned no records (CLI missing, timeout, non-zero exit,
     malformed output) → `unknown (<reason>)`;
   - records parsed, no applicable `tagteam@tagteam` record → `missing`;
   - applicable, `enabled` not `true` → `configured, disabled`;
   - applicable and enabled, handoff skill absent under `installPath` or no
     `installPath` → `configured, broken (<reason>)`;
   - unsupported scope or disagreeing records → `unknown (<reason>)`;
   - enabled with the skill present → `found (<scope> scope)`.
   The framework line shows package and manifest versions from Phase 52 and
   this plugin state, not `version_line`'s `not installed` wording.
3. **Instruction sources.** For each of `AGENTS.md` / `CLAUDE.md`: absent,
   regular file with size, or unsupported shape (symlink, directory, other)
   named without following it. Per role and per mode: *desktop* states the
   file the configured launch executable's provider auto-loads
   (`headless.PROVIDER_AUTOLOADS`, keyed by the launch basename when it is a
   known provider, else `unknown`); *headless* uses the resolved headless
   provider with `select_context_file` and `PROJECT_CONTEXT_MAX_CHARS` to
   state which file a headless turn injects and whether it truncates.
   Unknown provider → `unknown`, no guess.
4. **Tool configuration.** Project `.mcp.json`: server *names* only →
   `configured (not probed)`. `.claude/settings.json` /
   `.claude/settings.local.json`: hook event names and matchers only.
   `.codex/config.toml`: presence only (not parsed). Nothing under the user's
   home is read except Phase 49's skill directory listing.

### Output boundary and config reads
Every file doctor opens (legacy Markdown, instruction files, `.mcp.json`,
Claude settings) goes through one reader with the Phase 52 shape rules:
every component from the project root down is `lstat`ed; a symlink at the
path or at any parent, a directory, or any non-regular file is not opened
and is reported as `unsupported filesystem shape (<what>)`; reads are
bounded: Markdown reads the first 256 KB and notes `truncated scan`; JSON
config over 64 KB is `skipped: over size limit` and not parsed. Unreadable or malformed JSON
→ `unknown (malformed <file>)`, and the report continues. So a project
`.mcp.json` symlinked to a credential file in the home directory is never
opened.

What is read versus printed: parsing a config with `env` or `headers`
necessarily reads those bytes into memory. The guarantee is about output:
text and JSON carry only file paths, shapes, sizes, server names, hook event
names and matchers, executable basenames and resolved paths, provider names
and states. No config values, command arguments, `env`, `headers` or
`args` are emitted, and nothing is logged or written.
5. **Protections.** Static, accurate notes generated from what was found:
   a Claude hook applies to Claude processes only and does not bind the
   `<codex>` role; `TAGTEAM_READ_ONLY=1` blocks tagteam writes, not arbitrary
   filesystem or API mutation; one cycle writer per turn is enforced by the
   CLI/orchestrator; desktop and headless sessions can differ in tools,
   credentials, working directory and approvals.

Doctor never claims a setup is unusable or equivalent. It prints facts per
role and ends with `findings: N warn, M info`.

### Output and exit codes
Text report by section; `--json` emits the same data (`schema: 1`) for
consumers such as the cockpit. Exit 0 whenever the report was produced, even
with findings (it is a report, not a gate). Exit 2 for a missing or unreadable
target directory. No `--strict` in this phase.

### Read-only guarantees
- Doctor is added to the `TAGTEAM_READ_ONLY` allowlist in `cli.py`.
- No DB open/create/migrate, no state read-through that writes, no registry
  write, no manifest write, no cycle or diagnostics log write.
- Only subprocess: the existing `claude plugin list` inside `plugin_status`
  (overridable with `TAGTEAM_CLAUDE_BIN`, `""` = none, as today).
- Tests snapshot the project tree and a fake home (paths, bytes, mtimes)
  before and after `doctor` and the setup/upgrade summary, and assert identity.

### Setup/upgrade pointer
After the framework report: `note: N legacy workflow finding(s) (M warn) —
run: tagteam doctor <dir>`. Nothing when there are none. Printed in preview
too. Exit codes of setup/upgrade are unchanged by findings.

## Files
- New: `tagteam/diagnostics.py`, `tests/test_diagnostics.py`.
- Modified: `tagteam/cli.py` (`doctor` subcommand, read-only allowlist,
  help), `tagteam/setup.py` and the `upgrade` path in `cli.py` (summary line),
  `tagteam/data/workflows.md` (+ this repo's `docs/workflows.md` via the
  Phase 52 engine), `README.md` (command list), `docs/roadmap.md`, this plan.
- Not modified: `framework.py` classification, `plugin.py` discovery rules,
  `headless.py` context selection (reused, not changed), templates, contract.

## Success Criteria and Verification
Fixtures in `tests/test_diagnostics.py` (temp projects, fake `HOME` and
`CLAUDE_CONFIG_DIR`, `TAGTEAM_CLAUDE_BIN=""` unless a test says otherwise):
- A Northstar-shaped project (`.claude/skills/plan/SKILL.md` with "Claude is
  always the lead", `review` skill with `/handoff-cycle`) under codex-lead
  config → `fixed-role` warn + `legacy-skill-shape`, `retired-command` warn;
  under claude-lead config → `fixed-role` info, `retired-command` still warn.
- A user-authored `plan` skill that mentions Claude and Codex without a fixed
  role or retired command → no finding.
- `CLAUDE.md` containing "Claude (Lead) / Codex (Reviewer)" contradicting
  config → one finding with the right line number.
- Managed `handoff/SKILL.md` with retired text → no finding (framework
  section only).
- Symlinked skill file, directory at a file path, oversized file → skipped or
  truncated with a note, no traceback.
- User-level `~/.claude/skills/handoff-cycle/SKILL.md` → reported by path, not
  read.
- Capability: launch executable present on a fake `PATH` vs absent; AGENTS.md
  only / CLAUDE.md only / both / neither / over the injection limit, for both
  role assignments; Claude hook present with Codex as a role → the
  hook-scope note appears; unknown provider → `unknown`.
- Output boundary: a launch command `claude --api-key SENTINEL-1` and
  `FOO=SENTINEL-2 codex`, `headless.args` containing `SENTINEL-3`, and
  `.mcp.json` with `SENTINEL-4` in `env` and `headers` → no sentinel string
  in text or JSON output; the assignment-prefixed command is `unknown`.
- Config shapes: `.mcp.json` and `.claude/settings.json` as symlinks to a
  fake-home file containing a sentinel; `.claude/` itself a symlink; each as
  a directory; a FIFO; an oversized file; malformed JSON; valid JSON of the
  wrong type → never opened (asserted by the sentinel and, for the FIFO, by
  the run not blocking), reported as unsupported / skipped / `unknown`, and
  the rest of the report is produced with exit 0.
- Plugin availability: `TAGTEAM_CLAUDE_BIN=""` (CLI missing), a fake `claude`
  that sleeps past a patched timeout, one that prints malformed JSON, one
  that exits non-zero → `unknown` with the reason; a valid empty list →
  `missing`; a disabled record → `configured, disabled`; an enabled record
  without the skill → `configured, broken`; an enabled record with the skill
  → `found`. `plugin_status` results for the same fixtures are unchanged.
- Modes: a custom display name `Architect` with `command: claude --model x`
  and `headless: {provider: codex, executable: /opt/fake/codex}` → desktop row
  `claude` (found/missing on the fake `PATH`, auto-loads `CLAUDE.md`),
  headless row `codex (explicit)` with executable `/opt/fake/codex` checked
  separately and `AGENTS.md` not injected (Codex auto-loads it) — JSON keeps
  the two modes as separate objects. A name-only agent with no command and
  no headless block → both modes inferred from the name, labelled as such.
- Read-only: tree + fake home unchanged after doctor, after `setup --preview`
  and after `upgrade --preview`; doctor succeeds with `TAGTEAM_READ_ONLY=1`
  and does not create `.tagteam/` or a DB.
- `--json` round-trips and matches the text report's counts; exit codes 0/2.
- Setup/upgrade: summary line present with findings, absent without; exit
  codes unchanged.

Focused tests while working; the on_submit gate supplies the recorded full
suite for the impl submission. Plan revisions need document checks only.

## Risks and Review Focus
- **False positives.** The `fixed-role` patterns are deliberately narrow and
  config-relative; review the table. A missed stale instruction is cheaper
  than a noisy report people learn to ignore.
- **Name `doctor`.** New top-level command vs. extending `state diagnose`
  (which is handoff-state health, needs `handoff-state.json`, and would mix
  two audiences). Proposed: new command; `state diagnose` unchanged.
- **Capability documentation is prose, not schema.** The recommendation asks
  for "a way to document task-relevant capabilities and their alternatives";
  this plan delivers guidance in `workflows.md` and no `capabilities:` config
  key. A schema can follow once a real project uses the prose form.
- **`claude plugin list` is a subprocess.** It is local and already runs from
  `tagteam state`; doctor adds no new external calls. Doctor classifies its
  result itself; `plugin_status` and every setup/fallback decision built on
  it are untouched.
- **Reading is not printing.** Config parsing reads secret-bearing bytes; the
  guarantee is the output boundary plus the shape/size rules, stated as such
  (reviewer, plan round 1).

## Plan revision log
Round 2 (reviewer round 1 findings): output boundary replaces
`describe_roles` (no command args in text or JSON; sentinel fixtures); one
shape-checked, bounded reader for every file including `.mcp.json` and
Claude settings; "never reads a secret" narrowed to what is emitted; plugin
availability classified from `list_plugins` with `unknown` distinct from
`missing` / `disabled` / `broken`; desktop launch and headless
provider/executable observed and reported as separate modes, headless via
`get_headless_spec` / `infer_headless_provider`.
- Scanning only project-level `.claude/` and root instruction files misses
  other agents' skill locations; out of scope until a concrete one is found.

No human clarification is required for plan review.
