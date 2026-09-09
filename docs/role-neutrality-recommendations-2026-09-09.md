# Role-neutral Tagteam: recommendations and implementation handoff

Date: 2026-09-09
Origin: `northstar/northstar-test-automation`
Author: Codex, acting as that project's lead
Review: submitted to Claude in the originating project's plan cycle
Phase: `tagteam-neutrality-recommendations-sep09`
Status: proposal pending review; not an approved implementation plan

## Purpose and sequencing

Make choosing Claude or Codex as lead a normal configuration choice, without
requiring users to discover and repair contradictory skills, prompts, or setup
assumptions. The reviewer should work equally well with either provider.

The user requested this sequence:

1. Review this recommendations document in the Northstar project.
2. Open a separate session in the Tagteam repository, read its own roles and
   handoff state, and plan/implement the accepted upstream work there.
3. Release or otherwise identify a tested, installable Tagteam version.
4. Load that version in Northstar and perform its project-specific migration.

This document does not start or replace Tagteam's own development cycle. Its
review record lives in the originating project. Approval of the recommendations
does not authorize an automatic implementation cycle in Northstar or a release.

## What neutrality means

- Role determines responsibility: lead plans and implements; reviewer evaluates;
  human arbiter resolves escalations. Provider determines how a process launches
  and which tools/instruction mechanisms it supports.
- `tagteam.yaml` determines the roles for new work. Do not infer a role from the
  words Claude or Codex, their defaults, a template, or a terminal position.
- Both assignments must work through manual, supported terminal, and headless
  entry points with the same cycle semantics and verification budget.
- Neutrality does not mean equal MCP connections, identical permissions, shared
  private memory, or identical provider features. Make relevant differences
  visible and actionable; do not silently copy credentials or broaden access.

## Evidence and existing foundations

These observations come from a bounded source/configuration inspection, not a
full behavioral audit. Reverify against the working tree in the implementation
session. Both repositories already had unrelated local changes.

| Observation | Evidence | Implication |
|---|---|---|
| Configurable roles and launch commands already exist. | `tagteam/config.py`: `get_launch_commands`, `get_agent_names`, `infer_headless_provider` | Extend existing configuration; a wholesale role-system rewrite is unnecessary. |
| Shipped templates already substitute configured names. | `tagteam/templates.py`; `tagteam/setup.py` uses `get_template_variables` and `copy_md_file`; `tagteam/data/templates/` | Preserve this behavior and test both assignments. Rendered names may become stale after a later role switch. |
| The contract already serves shell agents and Claude's plugin. | `tagteam contract`; `plugin/`; `tagteam/data/.claude/skills/handoff/` | Keep one authoritative contract; provider-specific entry points are adapters. |
| Headless context parity is partly implemented. | `tagteam/headless.py`: `PROVIDER_AUTOLOADS`, `CONTEXT_FILENAMES`, `select_context_file`, `read_project_context` | Audit and test this existing logic rather than recommending it as entirely missing. Manual sessions still need explicit onboarding. |
| Setup readiness is tied to Claude's handoff skill/plugin. | `tagteam/setup.py`: `SKILL_RELDIR`, `needs_setup`, `_sync_handoff_skill` | A shell/Codex user should not need Claude installed or configured merely to be considered set up. |
| Both repositories have local templates with literal role assignments. Tagteam's say Claude lead / Codex reviewer; Northstar's say claude lead / codex reviewer, now stale after its role switch. | In each repository: `templates/phase_plan.md`, `templates/handoff_plan.md`, `templates/handoff_impl.md` | Northstar demonstrates rendered names becoming stale after a role switch. Distinguish these local rendered artifacts from package sources; they alone do not establish a shipped-template defect. |
| Legacy downstream skills contradict the new assignment. | Northstar `.claude/skills/{plan,implement,review,sync,phase,decide}/SKILL.md` | Upgrading the handoff contract alone can leave an incompatible surrounding workflow. |
| Legacy-skill diagnostics are narrower than that problem. | `tagteam/setup.py`: `report_legacy_user_skills`; `tagteam/plugin.py` legacy handoff discovery | Extend migration diagnostics to recognized legacy workflow artifacts, with provenance-aware handling. |

In Northstar, the planning skill still says “Claude is always the lead for
planning,” and other skills use old handoff commands or assign final authority
to Claude. Northstar also has project rules only in `CLAUDE.md`, a Claude-specific
repository-protection hook, and user-level Claude MCP configuration and memory
files. The MCP configuration is outside the Northstar repository, not in a
project `.mcp.json`. The hook matches `Edit|Write` only; it does not enforce the
read-only boundary for Bash writes. These are downstream integration concerns,
not proof that Tagteam's runtime requires those integrations.

## Priority 1: reliable role selection and onboarding

1. Audit setup/init/quickstart, terminal priming, watcher prompts, headless turns,
   UI labels, panel/briefer paths, templates, and documentation for implicit
   provider-to-role assumptions. Retain explicit provider adapters where needed.
2. Show the resolved lead and reviewer, provider/launch command, project root,
   and handoff entry point during onboarding. Include a concrete example for
   each assignment. Preserve support for display names and explicit commands.
3. Make reusable workflow instructions refer to roles and read current config.
   Keep historical cycle entries and approved plans as historical evidence;
   never globally replace agent names across a project.
4. Define safe role-switch behavior. Recommend permitting a simple switch
   between completed cycles. If a cycle/dispatch is active, report the mismatch
   and require an explicit migration/abort/resume path; never silently reinterpret
   its participants, launch a second writer, or rewrite prior attribution.
5. Make setup readiness reflect access to the canonical contract, not mandatory
   Claude plugin installation. Preserve the current plugin and vendored fallback
   compatibility for users who choose them.

Acceptance: a fresh project can run plan submission, changes requested,
resubmission, and approval with either assignment. Swapping between completed
cycles gives correct new prompts and attribution. An active-cycle mismatch has
a clear, non-destructive outcome. No separate workflow fork is required.

## Priority 2: shared instructions and safe upgrades

1. Provide a minimal, maintained onboarding reference usable by both providers.
   Prefer a shared project instruction source with provider-specific pointers;
   agree on exact layout during implementation. Do not overwrite user-authored
   `AGENTS.md` or `CLAUDE.md` wholesale.
2. Retain one versioned handoff contract. Any Codex-discoverable skill or Claude
   plugin wrapper should delegate to it or be generated and parity-tested.
   Do not maintain independent copies of the workflow by hand.
3. Extend upgrade diagnostics to recognize obsolete Tagteam workflow skills,
   stale role assignments, and old command syntax. Report path, evidence,
   ownership/provenance, and a concrete remediation. Treat arbitrary mentions
   of Claude/Codex as context, not automatic defects.
4. Offer reviewable migration changes. Automatically refresh only framework-owned
   content whose provenance is established. Preserve customized content and show
   a proposed merge or manual action. Preserve state, history, roadmap, decisions,
   project rules, and unrelated files. Existing broad copy/delete paths deserve
   inspection before expanding their scope.
5. Make migration repeatable and idempotent, with a preview and recovery strategy.
   Distinguish package version, plugin version, and installed project artifacts
   so a successful package update cannot imply all three are synchronized.

Acceptance: migration fixtures cover old Claude-lead scaffolding, customized
skills/instructions, plugin installed/absent, and no Claude installation. A second
migration makes no unnecessary changes. Conflicts remain visible; custom content
and cycle history survive. Test the built distribution, not only the source tree.

## Priority 3: capability and context visibility

Prefer extending existing diagnostics/status surfaces over introducing a large
new subsystem. Exact command names and schema changes are design decisions for
the Tagteam session, not existing features promised by this document.

- Report what can be established: installed executables, selected provider,
  contract availability, project instruction sources, and known tool configuration.
  Separate configured, successfully probed, missing, and unknown states.
- Explain that a desktop session and a headless CLI child may have different
  tools, credentials, working directories, and approvals. Do not infer that a
  connection is usable from its configuration file alone.
- Provide a way to document task-relevant capabilities and their acceptable
  alternatives. Missing Datadog need not block editing a Markdown file; it can
  block a task whose evidence must come from Datadog.
- Encourage durable decisions and evidence in shared project files. Do not
  automatically scrape provider memory stores, import secrets, or replicate MCP
  credentials. Memory and tool transfers remain explicit project work.
- Explain which protections are instructions versus runtime enforcement.
  Tagteam should not claim a Claude hook protects a Codex process. Preserve
  `TAGTEAM_READ_ONLY=1` for delegated helpers and one cycle writer per turn;
  that variable protects Tagteam writes, not every filesystem/API mutation.

Acceptance: diagnostics can describe an asymmetric setup without falsely calling
it unusable or equivalent. Read-only inspection does not mutate workflow state,
authenticate services, print secrets, or change permissions.

## Verification and delivery

Use focused tests while implementing and follow Tagteam's one-run contract for
the recorded full-suite result. Existing starting points include
`tests/test_config.py`, `test_templates.py`, `test_setup.py`, `test_plugin.py`,
`test_upgrade_smoke.py`, `test_headless.py`, and `test_session_prime.py`.

Cover both role assignments across:

- Fresh setup, legacy upgrade, rerun, and role switch.
- Plugin, vendored, and shell contract entry points.
- Manual/terminal command generation and headless provider dispatch.
- Plan and implementation cycles, approval, feedback, and state attribution.
- Gatekeeper bounce/pass, panel/briefer selection, interjections, pause/resume,
  roadmap advancement, and read-only helpers where role/provider logic applies.
- `CLAUDE.md` only, `AGENTS.md` only, both present, absent, and truncated context.

Use mocked provider launches and temporary project fixtures for deterministic
coverage; paid live model runs and live integrations need not become routine
unit-test dependencies. Include package-data and plugin/contract parity checks.

Deliver release notes specifying the tested version, actual supported upgrade
command, migration preview/conflict behavior, known limitations, and a short
checklist for existing projects. Stage these priorities if necessary; reliable
role selection and migration are the minimum useful upstream release.

## Deferred Northstar work

After installing the tested version, handle these in Northstar, not in Tagteam:

- Merge project instructions and repair/retire its customized legacy skills.
- Curate relevant Claude memory and port the Northstar triage procedure.
- Connect and verify Datadog/Engram or other task-required tools as appropriate.
- Establish actual read-only enforcement for the application repository.
- Choose project-specific verification commands and any gatekeeper configuration.

Do not bundle those credentials, hooks, project policies, or test commands into
Tagteam defaults. Do not upgrade Northstar as a side effect of implementing this
proposal without returning to the agreed downstream migration step.

## Instructions for the receiving session

Read this repository's `tagteam.yaml`, current state, current `tagteam contract`,
and local instructions first. Consult the originating review before treating
this proposal as approved:

```sh
cd /Users/jackblacketter/projects/northstar/northstar-test-automation
tagteam cycle rounds --phase tagteam-neutrality-recommendations-sep09 --type plan
```

Return to the Tagteam repository before any Tagteam development-cycle write.
Recheck source findings, identify what already works, and turn the accepted gaps
into a bounded implementation plan under that repository's own workflow. This
document requests upstream work; it is not a command to take over an active turn.
