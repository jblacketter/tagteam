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
one-line pointer from `setup`/`upgrade`. It reports. It never edits, deletes,
probes a service, or reads a secret. Depends on Phase 52 (merged, PR #36).

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
**Scanned paths** (project root only, lexical, no link following, regular
files only, each file capped at 256 KB with a `truncated scan` note):
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
Four states, used consistently: `configured` (a file says so), `found`
(verified locally without contacting anything: an executable on `PATH`, a
readable file), `missing`, `unknown` (not determinable without probing, which
doctor never does).

Sections:
1. **Roles.** `onboarding.describe_roles` plus, per role, argv0 of the launch
   command resolved with `shutil.which` → `found <path>` / `missing`.
2. **Contract.** `tagteam contract` always `found` (in-process). Plugin status
   from `framework.version_line` (the `claude plugin list` call Phase 52
   already makes; a missing `claude` executable reads `not installed` and is
   not an error). The vendored skill with its Phase 52 classification.
3. **Instruction sources.** For each of `AGENTS.md` / `CLAUDE.md`:
   present/absent/symlink target, size. Per role, reuse `headless.PROVIDER_AUTOLOADS`,
   `select_context_file` and `PROJECT_CONTEXT_MAX_CHARS` to state what that
   provider auto-loads interactively, what a headless turn injects, and
   whether injection truncates. Unknown provider → `unknown`, no guess.
4. **Tool configuration.** Project `.mcp.json`: server *names* only →
   `configured (not probed)`. `.claude/settings.json` /
   `.claude/settings.local.json`: presence, and hook event names/matchers
   only. `.codex/config.toml` if present: presence only (no TOML parse, no
   values). Nothing under the user's home is read except Phase 49's skill
   directory listing. No values, env, headers or args are ever printed.
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
  role assignments; `.mcp.json` with secrets in `env` → names printed, secret
  string absent from text and JSON output; Claude hook present with Codex as
  a role → the hook-scope note appears; unknown provider → `unknown`.
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
  `tagteam state`; doctor adds no new external calls.
- Scanning only project-level `.claude/` and root instruction files misses
  other agents' skill locations; out of scope until a concrete one is found.

No human clarification is required for plan review.
