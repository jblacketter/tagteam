# Workflows: Lead/Reviewer Collaboration

This document describes how the lead and reviewer agents collaborate on a
project using tagteam. It is installed by `tagteam setup` and refreshed by
`tagteam upgrade`; the authoritative, versioned contract the agents follow is
the output of `tagteam contract`.

> **Note**: Agent names are configured in `tagteam.yaml`. Read that file to see
> which agent is the lead and which is the reviewer for your project.

## Roles

| Role | Responsibilities |
|------|------------------|
| **Lead** | Plans phases, implements code, submits work for review |
| **Reviewer** | Reviews plans and implementations, approves or requests changes |
| **Arbiter** (human) | Rules on escalations, answers questions, steers with interjections |

## One contract, three ways in

The handoff contract is one document, served three ways:

| How | Who | Command |
|-----|-----|---------|
| Claude Code plugin (`tagteam`) | Claude | `/tagteam:handoff` |
| Vendored copy at `.claude/skills/handoff/SKILL.md` | Claude Code in a project that still carries one | `/handoff` |
| The tagteam CLI | Codex, or any agent with a shell | `tagteam contract` |

Slash commands are Claude Code entry points. In Codex or another shell agent,
read `tagteam contract` and follow the described workflow; do not type a Claude
slash command into that agent's command dispatcher. Either entry point follows
the same rules. The state file's command line tells
an agent which to use: *"Read the handoff contract (`tagteam contract`; in
Claude Code: /tagteam:handoff) and handoff-state.json, then act on your turn."*

## The cycle

Each phase in `docs/roadmap.md` goes through a **plan** cycle and then an
**impl** cycle. Every cycle is a sequence of rounds recorded under
`docs/handoffs/`, and `handoff-state.json` says whose turn it is.

```
Lead:      /tagteam:handoff start [phase]          → plan cycle, round 1
Reviewer:  /tagteam:handoff                        → APPROVE or REQUEST_CHANGES
Lead:      /tagteam:handoff                        → address feedback, round N+1
   … until APPROVE …
Lead:      implement, then
           /tagteam:handoff start [phase] impl     → impl cycle, round 1
Reviewer:  /tagteam:handoff                        → review the diff
   … until APPROVE …
```

Every turn makes exactly **one** cycle-writing call:

| Action | Who | Command |
|--------|-----|---------|
| Open a cycle | Lead | `tagteam cycle init --phase P --type plan\|impl --lead L --reviewer R --updated-by L --content "…"` |
| Submit a round | Lead | `tagteam cycle add --phase P --type T --role lead --action SUBMIT_FOR_REVIEW --round N --updated-by L --content "…"` |
| Amend mid-review | Lead | `… --action AMEND --round N …` (same round, turn stays with the reviewer) |
| Approve | Reviewer | `… --role reviewer --action APPROVE --round N …` |
| Request changes | Reviewer | `… --role reviewer --action REQUEST_CHANGES --round N` (feedback on stdin) |
| Escalate / ask a human | Reviewer | `… --action ESCALATE` / `… --action NEED_HUMAN` |
| Read the cycle | Both | `tagteam cycle rounds --phase P --type T [--tail N]` |

A cycle ends when the reviewer approves. It escalates to the arbiter when the
reviewer asks for it, asks a question only a human can answer, or after 10
consecutive stale rounds. The arbiter reads `tagteam brief` and rules with
`tagteam rule approve|request-changes|answer`.

When a phase's implementation is approved, `tagteam report --phase P` prints
what it took: rounds, change requests, gate bounces and minutes, elapsed turn
time per role (including time waiting for someone to relay the turn),
start-to-approve, the usage rows stored under the phase and how many turns
they can be matched to. It is read-only and writes nothing; paste the block
into the phase doc's closeout. Tokens appear only for turns tagteam ran and
recorded (headless turns, panel lenses, briefs); nothing is reported in
dollars.

`tagteam bench` replays recorded reviewer rounds against reviewer cells
(`claude:<model>:<effort>`) to compare each cell's verdict with the recorded
one: `bench select` lists rounds with a reviewer verdict, `bench run --round
P:T:N --cell …` is a dry run (grid + proxy token estimate) until `--yes`, and
`bench table` shows agreement, missed and extra change requests, tokens and
seconds per cell. Every lead submission and AMEND pins its working tree under
`refs/tagteam/snapshots/` (no model involved), so replays of new rounds are
exact; older rounds need `@BASE..REV` and are reported separately as
`asserted`. Each pair costs about one reviewer turn of window, and agreement is
with the recorded reviewer, not with ground truth.

## Verification: the one-run rule

An impl submission costs **one** full-suite run — the one on the record. With
`gatekeeper.on_submit: true` in `tagteam.yaml`, the gate runs it inside the
lead's `cycle add` and records the verdict; the lead pre-flights with
`tagteam gate check --skip-tests` and cites the gate. Without the gate, the lead
runs the suite once right before submitting and cites the numbers. The
reviewer reads the diff and does not re-run the suite.

## What runs by itself

| Mode | What happens |
|------|--------------|
| Manual | You paste each agent's handoff output into the other agent yourself |
| `tagteam watch --mode notify\|tmux\|iterm2\|terminal` | The watcher reads `handoff-state.json` and nudges the right terminal on each turn |
| `tagteam watch --mode headless` | Each turn is a fresh agent process (`claude -p` / `codex exec`) fed the contract, the state and the round tail; nobody is at the terminal |
| Gatekeeper (`gatekeeper:` block) | Tests + scope + plan-doc checks before every reviewer turn; a failure bounces the lead |
| Reviewer panel (`panel:` block) | 2–3 narrow lens reviews merged into one reviewer entry |
| Full roadmap (`/tagteam:handoff start --roadmap`) | All incomplete phases, in dependency order, with review gates between them |

One watcher runs per project. A second `tagteam watch` for the same project is
refused and names the running one's pid (`kill <pid>` stops it; a watcher shuts
down cleanly on SIGTERM, including any in-flight headless turn). Stopping
`tagteam serve` with Ctrl+C stops the watchers that cockpit started and leaves
any other watcher running, saying so.

## Steering

- `tagteam interject "note" [--to lead|reviewer]` — a note the next turn must honor.
- `tagteam orders` — standing orders, delivered to both agents in every turn
  (like an interjection that is never consumed). Project orders live in a
  committed `tagteam-orders.json`; `--run` sets an override for the current
  run, dropped when that run ends. **Enforced:** `tagteam orders stop
  phase|roadmap` decides whether the run stops for you after each phase or goes
  on to the next ready roadmap phase. The engine applies it when an
  implementation is approved. **Advisory:** `tagteam orders add "hold the PR
  for my approval"` notes are delivered but not enforced, because tagteam runs
  no git. Without orders, nothing changes.
- `tagteam config keys` / `tagteam config set KEY VALUE --preview` — edit the
  gate, panel, briefer and re-send settings in `tagteam.yaml` without losing
  its comments. An edit is written only when the engine would then do what
  you asked, and the preview shows the diff first.
- `tagteam job start ci-watch --pr N` (or `--run ID`, `--workflow NAME --ref
  REF`, `--pypi PKG==VER`): wait for CI or a release without a model. The job
  polls in the background until it knows the answer, then tells you once: a
  desktop notification, plus an interjection for the lead with `--to-lead`.
  A `--workflow` watch is tied to the commit `--ref` names, so the previous
  push's run can never answer it. `tagteam job list|status|log|cancel`. A job
  is not a turn and never holds the turn slot.
- `tagteam pause --reason "…"` / `tagteam resume` — hold dispatch without losing state.
- `tagteam cancel-turn` — abandon an in-flight headless turn.
- `tagteam serve` — the cockpit: talk to the lead, launch, watch, rule.

## Files tagteam writes

| Path | What |
|------|------|
| `handoff-state.json` | Whose turn, what command, current phase/type/round |
| `docs/handoffs/<phase>_<type>_rounds.jsonl` + `_status.json` | The cycle record |
| `docs/phases/<phase>.md` | The plan (the lead writes it; the reviewer reads it) |
| `docs/roadmap.md` | The phase list and each phase's status. A heading still titled `[Name]` is a placeholder, not a phase — `tagteam roadmap check` lists the ones left to rename |
| `docs/escalations/` | Decision briefs for escalated cycles |
| `.tagteam/` | Watcher and headless runtime state (not for editing) |
| `tagteam-manifest.json` | What `setup`/`upgrade` last wrote (path, sha256, version) — commit it |

## Framework files and upgrades

`docs/workflows.md` and, without the plugin, `.claude/skills/handoff/SKILL.md`
are framework files. After
`pip install -U tagteam`, run `tagteam upgrade --preview` (every registered
project) or `tagteam setup --preview` (this one) to see what would change,
then run it without `--preview`:

- files whose bytes tagteam wrote (recorded in `tagteam-manifest.json`, a
  known vendored contract, or an exact copy of what an earlier release
  installed — see below) are refreshed to the new package;
- files that match the new package are left alone;
- anything else is **kept** and reported with the exact
  `tagteam setup DIR --accept PATH` line that overwrites it. Accepting needs
  the path tracked and clean in git (so `git checkout -- PATH` undoes it);
  `--force` lifts only that check.

Earlier versions also installed `templates/*.md` and `docs/checklists/*.md`.
Nothing reads a project's copy of either, so they are **retired**: no longer
created, and on the next `setup` / `upgrade`

- a copy the installed package can reproduce byte for byte is removed
  (`retired  templates/cycle.md`), and the directory with it once empty —
  your own files in it, and the directory, are left alone;
- a copy tagteam wrote in an older version whose bytes it no longer ships is
  removed only when git can restore it (tracked and clean); otherwise it is
  kept until you commit it, or delete it with `--accept PATH --force`;
- a copy you edited is kept; `tagteam setup DIR --accept PATH` deletes it
  under the same tracked-and-clean rule;
- a symlink or non-regular file there is kept and never an error.

**Copies from earlier releases.** The package carries the earlier sources of
its framework files (through 3.12.0 — a fixed set; later versions are recorded
in the manifest). One of them, the pre-plugin `workflows.md` (through 3.10.0),
is carried as a sha256 digest only, because its text is nothing but commands
that no longer exist: an exact copy is still recognised and refreshed, but the
old text cannot be had back from the installed package. A file that equals one of them exactly — verbatim, or
rendered with this project's configured lead / reviewer names, or with the two
swapped (`setup` wrote the names into templates until 3.12.0) — counts as
tagteam's: an old `docs/workflows.md` is refreshed, an old template is
retired. The match is byte for byte. If the agent names in `tagteam.yaml`
changed since the file was written, or the file was edited at all, it is kept
as yours.

`tagteam bench` uses a project's own `docs/checklists/<type>_review.md` when
there is one and the package's otherwise.

Pre-plugin flat skills (`.claude/skills/handoff-*.md`) are never deleted
without `--accept PATH`. Symlinks or directories at a managed path — or at any
directory above one — are refused and left for you to fix by hand; setup never
creates or writes through a link, not for the framework directories, not for
the once-only seeds (`docs/roadmap.md`, `docs/decision_log.md`, `AGENTS.md`,
`CLAUDE.md`), not for the manifest. A new target directory (missing ancestors
included) is created the same checked way; `--preview` on one reports what a
fresh setup would create and creates nothing — nor does `tagteam upgrade
--preview` touch the project registry. A second run changes nothing. `tagteam state`
shows the package version, the manifest version and the plugin status side by
side — a package update does not move the other two. The manifest's version is
the tagteam that last *changed* the manifest: an upgrade that changes no
framework file rewrites nothing, so it can stay behind the package.

## Choosing and changing roles

Both assignments use the same workflow. For Codex lead / Claude reviewer:

```yaml
agents:
  lead:
    name: codex
  reviewer:
    name: claude
```

Swap the two names for Claude lead / Codex reviewer. Custom display names may
specify an explicit `command`. `tagteam state` shows the resolved roles, launch
commands, project root and contract entry point.

Finish the active cycle before switching roles. Stop the watcher and agent
sessions, edit `tagteam.yaml`, then recreate the sessions and watcher. An active
participant mismatch refuses ordinary cycle writes and automated dispatch;
restore the recorded assignment to finish the cycle. Historical participants
are preserved. Human rulings and read/status remain available.

The guard protects workflow submissions, not arbitrary agent edits or external
actions. Existing terminal panes are not identified or replaced automatically.
`TAGTEAM_READ_ONLY=1` likewise protects Tagteam writes, not every filesystem/API
operation. Desktop and headless sessions may have different tools and access.

Existing project instruction files are preserved. New instruction pointers are
created only when absent. Readiness does not mean an old project's own skills
and rules agree with the new assignment — run `tagteam doctor` after a switch.

## Diagnostics: `tagteam doctor`

`tagteam doctor [DIR] [--json]` is a read-only report. It writes nothing, probes
no service, and prints no configured value, command argument or secret. It is
safe for a read-only helper (`TAGTEAM_READ_ONLY=1`).

- **Other tagteam installs.** A tagteam installed in the project's own
  `.venv` or `venv` runs instead of the one on your PATH for anything launched
  through that venv (`.venv/bin/tagteam`, `python -m tagteam`, a watcher
  started from it). Doctor lists each copy it finds; one whose version differs
  from the running tagteam is a `warn`, and `tagteam state` adds
  `· .venv: tagteam X (differs from the running Y)` to its `Framework:` line.
  An editable link is listed, not judged. Look-only: nothing is imported or
  run, and nothing is read through a symlink. Upgrade or remove the copy —
  tagteam never does it for you.
- **Legacy workflow findings.** Project skills (`.claude/skills/`), commands
  (`.claude/commands/`), `AGENTS.md` and `CLAUDE.md` that use retired command
  syntax (the retired pre-plugin `handoff-*` slash commands) or name a fixed role holder ("Claude is
  always the lead"). A fixed role that contradicts `tagteam.yaml` is a `warn`;
  one that matches today is `info` (stale after a switch). Each finding shows
  the line and a manual remediation. Findings are candidates: tagteam has no
  provenance for these files and never edits or deletes them. `setup` and
  `upgrade` print one line pointing here when a project has any.
- **Per role, desktop and headless separately.** The launch executable and the
  headless provider/executable (`found` / `missing` / `unknown`), which
  instruction file each provider loads by itself, and what a headless turn
  injects (and whether it is truncated).
- **Contract and tools.** `tagteam contract`, the plugin (`unknown` when Claude
  Code could not be asked — not the same as `missing`), the vendored skill,
  `.mcp.json` server names and Claude hook events — configured, not probed.
- **Standing orders.** When `tagteam-orders.json` exists, doctor shows its
  stop order and how many advisory notes it has (never their text). A
  malformed, oversized or symlinked file is a `warn`, because the engine
  treats it as no project orders.
- **Protections.** Which guarantees are enforcement and which are instructions:
  a Claude hook does not bind a Codex process.

Symlinks and non-regular files are reported, never followed or opened.

## Capabilities and alternatives

Tagteam cannot know which tools a task needs. Record that in your project
instructions (`AGENTS.md` or `CLAUDE.md`), in role-neutral terms, so both
providers read the same thing:

```markdown
## Capabilities
- Datadog (logs, monitors) — needed for incident triage evidence.
  Without it: do not triage; ask the arbiter for an export.
- Markdown/docs edits need no external tools.
```

A missing tool should block only the tasks whose evidence depends on it.
`tagteam doctor` shows what is configured; it cannot show that a connection
works in a given session, so a task that needs one should verify it first.
Keep durable decisions and evidence in project files, not in a provider's
private memory, and never copy credentials between tools.
