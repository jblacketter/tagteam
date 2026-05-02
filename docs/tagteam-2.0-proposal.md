---
title: Tagteam 2.0 — Proposal & Research Synthesis
date: 2026-04-27
status: draft (input for handoff review cycle)
author: Jack Blacketter (with Claude research synthesis)
---

# Tagteam 2.0 — Proposal & Research Synthesis

This document captures a strategic rethink of the tagteam project. It is the input for a future handoff review cycle: the lead and reviewer agents should treat this as a brief, not a finished plan. The recommendations are deliberately opinionated so they can be argued with.

---

## 1. Original prompt (from Jack)

> Lets rethink this project. These are the main goals,
>
> 1. 2 agents work together in a reviewer / lead relationship
> 2. the communication between agents is handled in a file allowing them to communicate without spending additional api tokens.
>
> Those goals are mostly achieved. We still have some bugs related to drift and the handoff getting out of sync, but its mostly working well
>
> 3. a graphical interface that allows the user to start the process of picking agent roles, and kicking off a handoff cycle, monitored in a GUI, game type interface — this is secondary, partially working but needs lots of work. Its not a high priority right now.
>
> Is there a better way to do this? If we think of a 2.0 version of this project? Should I be building an mcp server to handle this? Should we be sending data into a memory system of some kind to help us track? Can we be more token efficient? (I think we burn up a lot of tokens with this)
>
> Use multiple agents to research similar projects, and see if there are things we can learn, whether we are adding to this project, or building a new one to more efficiently accomplish the same goals. I don't know how unique this project is, but suspect other people are doing something similar, and it may or may not be better. one project i learned about is https://ax-platform.com/ which has https://ax-platform.com/mcp/

---

## 2. Headline recommendations

1. **Do not rebuild as an MCP server.** MCP is a host↔server protocol, not an inter-agent message bus, and tool calls cost the same tokens as `Read`-ing a file. An optional, *thin* MCP adapter that exposes the existing file-based state (`handoff_send`, `handoff_check`, `phase_status`) is a defensible future addition if non–Claude-Code agents ever need to participate. The file remains canonical.
2. **Do not adopt a memory service** (Mem0, Letta/MemGPT, Zep, LangGraph state). At a 10-round-per-phase cap with human arbitration, the corpus is bounded and the failure modes (network dep, embedding bill, memory-service-down → handoff blocked) outweigh the gains.
3. **The real 2.0 wins are token-efficiency improvements to the file format and read patterns.** Specifically: tail-only reads, writer-emitted summaries, structured fields, and (optionally) per-round files. These are small, focused changes with large payoff and no architectural rewrite.
4. **Steal one idea from ax-platform**: their `messages(wait=true, wait_mode=mentions)` blocking pattern. Even in a pure file-based world, replacing the watcher's poll loop with `fswatch`/`watchdog` event-driven blocking is a quality-of-life win.
5. **Sharpen positioning**, not architecture. The tagteam combination — two peer Claude Code subprocesses + symmetric `handoff-state.json` + per-phase JSONL + 10-round cap + human tie-break, packaged as `pip install` — is a *well-chosen recombination*, not a new pattern. Lean hard into the token-economy + bounded-rounds-with-human-arbiter story; that is the actual differentiation.

---

## 3. Key fact worth flagging

**Anthropic's prompt cache is not shared across separate Claude Code processes.** Lead and reviewer run as different conversations, so neither benefits from the other's cache. This is why re-reading the growing JSONL log is genuinely expensive: each agent pays full input cost on every read. This single fact is the strongest argument for the token-efficiency changes in §6.

(Caveat: this is true today; cache scoping could change. Verify before relying on it long-term.)

---

## 4. Comparison with ax-platform.com

**What ax-platform actually is.** A hosted multi-tenant SaaS — FastAPI on Google Cloud Run, PostgreSQL 15 with row-level security, OAuth 2.1 / GitHub SSO. Bills itself as "the first MCP-native collaboration platform for AI agents." Heterogeneous agents (Claude, ChatGPT, Gemini, custom) connect to the same backend over MCP and message, assign tasks, and share context as if they were teammates in a Slack-like workspace. The GitHub org is `ax-platform`; the MCP wrapper repo `ax-platform-mcp` is MIT-licensed but the server appears closed-source. No public pricing tiers (gated behind login at paxai.app). No HN/Reddit traction located.

**Their MCP surface.** Streamable-HTTP transport (stdio fallback via `mcp-remote`). Six action-dispatched tools:

- `messages` — `send`, `check`, `react`, `edit`, `delete`, `stop`; supports `wait: true` + `wait_mode: 'mentions'` so an agent blocks until @mentioned (synchronous A2A without polling).
- `tasks` — `create`, `assign`, `update`, `release`, `current`, `suggestions`, `search`.
- `search` — semantic across messages/tasks/agents.
- `spaces` — multi-tenant workspace switching.
- `agents` — discovery with `scope: my|team|public|all`.
- `context` — ephemeral KV (`set/get/list/delete`).

Topology is flexible — demos show 3-agent self-coordinating swarms. Claude Code docs additionally show hierarchical parent/sub-agent workflows. **No first-class lead/reviewer pattern.**

**Overlap with tagteam.** Both want N agents to coordinate without re-reading each other's output. ax-platform's `messages(wait_mode=mentions)` + `tasks` + `context` is roughly the network-service version of `handoff-state.json` + JSONL round logs. Differences:

| Axis | tagteam | ax-platform |
|---|---|---|
| Topology | Exactly two roles + human arbiter | Generic N-agent chatroom |
| Locality | Local files, zero network dep | Hosted SaaS, network-required |
| Vendor scope | Claude Code only | Cross-vendor (Claude, ChatGPT, Gemini, custom) |
| Cost model | Free / OSS | SaaS, gated pricing |
| Token efficiency | Explicit design goal | Implicit at best |
| Phase structure | First-class (roadmap-driven) | Not present |

**What tagteam should learn:**

- **`wait=true / wait_mode=mentions` pattern** — clean alternative to the watcher's poll loop, worth porting to a file-based impl using `fswatch`/`watchdog`.
- **Action-dispatched tool design** — one `tasks` tool with an `action` enum keeps the surface small. Useful template if a thin MCP adapter is ever added.
- **`context` (ephemeral KV) split from durable `messages`** — a useful structural distinction worth mirroring inside `handoff-state.json` (transient state vs. round log).

**Should tagteam rebuild as MCP to compete?** No. ax-platform's value is *cross-vendor agent collab over a network*; tagteam's value is *local, opinionated, free, lead/reviewer-specific*. Rebuilding as a hosted MCP throws away the moat.

---

## 5. Prior-art survey — how unique is tagteam?

### Closest hits

**Aider Architect/Editor mode** — Two LLMs in one loop: an "Architect" reasons, an "Editor" applies edits. <https://aider.chat/2024/09/26/architect.html>
*vs tagteam:* Aider's "reviewer" is a translator (plan → diff), not an adversarial critic. Single process, no file IPC, no tie-break. Closest in spirit, different pattern.

**Cline Plan/Act + Roo Code modes** — Plan mode drafts, Act mode executes; Roo adds Architect/Code/QA personas. <https://cline.bot/blog/plan-smarter-code-faster-clines-plan-act-is-the-paradigm-for-agentic-coding>, <https://github.com/RooCodeInc/Roo-Code>
*vs tagteam:* Same model switches hats inside one session. No second agent reviewing the first; no persistent inter-agent file.

**claude-squad (smtg-ai)** — Terminal multiplexer for parallel Claude/Codex/Aider in git worktrees. <https://github.com/smtg-ai/claude-squad>
*vs tagteam:* Parallel, not paired. Agents don't talk.

**Overstory (jayminwest)** — Tmux + git-worktree agents communicating via SQLite "mail." <https://github.com/jayminwest/overstory>
*vs tagteam:* Closest on file-based-IPC philosophy, but message-passing (SQLite WAL), not structured handoff state. Generic worker pool, not lead/reviewer.

**ccswarm, claude-swarm, metaswarm, ruflo, OpenAgentsControl** — Orchestrator + specialist subagents for Claude Code. <https://github.com/nwiizo/ccswarm>, <https://github.com/affaan-m/claude-swarm>, <https://github.com/dsifry/metaswarm>
*vs tagteam:* Hierarchical decomposition (1 lead → N specialists), not symmetric two-agent debate with bounded rounds. State usually in-memory.

**MetaGPT / ChatDev / AutoGen** — Role-based "AI software company." MetaGPT deliberately uses structured documents as inter-agent comms. <https://github.com/FoundationAgents/MetaGPT>
*vs tagteam:* Closest precedent for file-as-channel, but the files are SDLC artifacts (PRDs, designs), not a live handoff state machine. No human-arbiter tie-break.

**mcp-handoff-server (dazeb)** — MCP server with create/read/update/complete_handoff tools, JSON state with workingOn/status/nextStep. <https://github.com/dazeb/mcp-handoff-server>
*vs tagteam:* **Strikingly similar data model** (`currentState`, `nextStep`) but designed for sequential session-to-session handoff (one agent now, another later), not two concurrent agents iterating with tie-break. Worth reading carefully — closest schematic neighbor.

**Reviewer/critique research** — Multi-Agent Reflection, Multi-Agent Debate (Du et al.), LLM-as-judge. <https://arxiv.org/abs/2305.19118>, <https://composable-models.github.io/llm_debate/>
*vs tagteam:* Academic basis for the pattern. Bounded-rounds-then-human-arbiter is standard in MAD literature. Tagteam is essentially a productionized two-agent reflection loop applied to Claude Code subprocesses.

**spencermarx/open-code-review, calimero/ai-code-reviewer, Cloudflare's coordinator pattern** — Multi-agent PR review. <https://blog.cloudflare.com/ai-code-review/>
*vs tagteam:* Post-hoc review of finished PRs, not in-loop iteration during implementation.

### Verdict: partially novel

Each ingredient is prior art. The specific combination — **(a) two peer Claude Code subprocesses, (b) symmetric `handoff-state.json` + per-phase JSONL designed for token economy, (c) phase-by-phase roadmap progression, (d) hard 10-round cap with human tie-break, (e) packaged as `pip install`** — was not located as an existing product. It is a well-chosen recombination, not a new pattern.

**Action items:**
- Read Aider's architect post and the MAD paper to articulate why two peer reviewers > one architect+editor.
- Study `dazeb/mcp-handoff-server`'s schema since it is eerily close.
- Lean hard into the token-economy + bounded-rounds-with-human-arbiter story when positioning.

---

## 6. Token-efficiency analysis (the actually-actionable part)

### The problem, restated

Lead and reviewer each read the growing per-phase JSONL log every turn. Over 10 rounds with two readers, that's O(N²) reads — re-reading a 10-round log 10 times is ~55 round-equivalents of input tokens. With 50KB logs, this can be 150–200K tokens per cycle of pure re-read overhead. And because the two agents are separate Claude Code processes, **prompt caching does not amortize across them**.

### Concrete patterns from production multi-agent systems

- **Append-only / tail reads.** Track byte offset or last-seen `round` per agent; read only `rounds.jsonl` from that offset. JSONL is already append-friendly. **Lowest-effort, biggest win.** Collapses O(N²) to O(N).
- **Summarization handoffs.** Each writer emits a `summary` field (≤200 tokens) alongside `content`. Reader consumes summaries by default, drills into `content` on demand. Pattern used by AutoGen `Society of Mind`, CrewAI hierarchical mode, LangGraph's `summarize_conversation`. ~5–10× compression on long threads.
- **Structured scratch vs. free-form transcript.** Add `decision`, `blockers[]`, `unresolved_threads[]`, `resolved: true` fields. Reader can skip prose entirely. Pattern in OpenAI Swarm context variables and MetaGPT SOP artifacts.
- **Prompt caching (Anthropic `cache_control`).** Helps within one process — re-reading a stable file in one Claude Code session can hit cache at ~10% input cost (5-min TTL). **Across separate Claude Code processes, caches are not shared.** Within a single agent's own session, appended JSONL invalidates the suffix but the prefix can still cache. Per-round files (one file per round) make this caching more effective because earlier files never change.
- **Context compaction / sliding windows** (LangGraph, Letta). Overkill at tagteam's scale.

### Memory systems considered

| System | Tagteam fit | Cost | Save |
|---|---|---|---|
| Mem0 | Cloud vector + graph memory; replace JSONL reads with semantic search | Hosted SaaS + embeddings; ~50ms/query | Big on long horizons; near-zero at 10-round cap |
| Letta (MemGPT) | Core + archival memory with paging | Self-host, nontrivial | Wrong shape — tagteam doesn't need persistent agent identity |
| Zep | Temporal knowledge graph + facts extraction | Self-host or cloud | Useful for cross-cycle learning ("reviewer always flags X"); not for one phase |
| LangGraph state + checkpointing | Native state object passed between graph nodes | Refactor: tagteam becomes a graph, not two CLI subprocesses | Eliminates file-based handoff entirely. Largest architectural change. |
| OpenAI Assistants threads | Wrong vendor | — | — |

**Verdict:** Memory services earn their keep when (i) state spans dozens of sessions, (ii) cross-phase learning matters, or (iii) semantic retrieval over hundreds of rounds is needed. None apply to tagteam today. Tail reads + summaries solve ~95% of the savings without the network dep.

### Prioritized 2.0 changes

1. **Tail-only reads.** Track `last_round_seen` per agent in `handoff-state.json`; reader loads `rounds[last_seen+1:]`. Single-day implementation, biggest win.
2. **Writer-produced `summary` field.** Add `summary` (≤200 tokens) to each round record. Reader prefers summary; fetches `content` on demand. Cuts re-read cost ~5–10×.
3. **Structured fields.** `decision`, `blockers[]`, `unresolved_threads[]`, `resolved: true`. Lets reader skip narration.
4. **Per-round files** (`rounds/001.json`, `002.json`, …). Maximizes within-process prompt-cache effectiveness because earlier files are immutable. Slightly more files, mirrors append-only semantics cleanly.

**Defer:** Mem0 / Letta / Zep / LangGraph rewrite.

---

## 7. MCP analysis

### What MCP is and isn't

MCP is a host↔server protocol for **one client at a time**. Each client connection is isolated; servers don't see the full conversation and can't orchestrate across clients. This is a security feature, not a workaround target. **MCP is not a message bus between agents.**

### Could MCP replace the file-based handoff?

Technically yes — a server could hold `handoff-state.json` and expose `claim_turn()`, `submit_round()`, `read_state()` tools. But:

- **Token cost.** A tool call returning the round log incurs the same token cost as the `Read` tool, plus JSON-RPC envelope. **MCP saves zero tokens vs. files.**
- **Turn-taking.** Two separate Claude Code processes connecting to one MCP server and competing to call tools is messier than the current approach. MCP has no built-in turn-taking primitive — the server would need custom logic to reject the non-active agent, logic the watcher already implements.

### Where MCP genuinely helps (and whether tagteam needs it)

| MCP feature | Tagteam need? |
|---|---|
| Standardizing API across Cursor / Codex / Claude Code | Not today (Claude-Code-only) |
| Resources for selective reading | Not the bottleneck |
| Server-driven prompts that nudge agents into modes | Watcher already does this |
| Non-Claude agents participating | Not a current requirement |

### Recommendation

**Skip MCP for v2.0. Polish the file format + watcher.** If a non-Claude-Code agent ever needs to participate, add a thin optional MCP adapter alongside (file canonical, MCP a thin read/write API). Don't lead with it.

---

## 8. Proposed v2.0 scope

In priority order. Each is a candidate phase for the roadmap; none assumes the others.

### Phase A — Tail-only reads (highest leverage)
- Add `last_round_seen` per agent in `handoff-state.json`.
- Reader loads only new rounds since last seen.
- Update agent prompts/skills to consume the slice, not the whole log.
- Acceptance: re-read cost over a 10-round phase drops from O(N²) to O(N), measurable in input tokens per phase.

### Phase B — Summary field on every round
- Writer emits a ≤200-token `summary` alongside `content`.
- Reader consumes summaries by default; drills into `content` only when flagged.
- Update lead and reviewer prompts to produce summaries that capture decisions and unresolved threads, not vibes.

### Phase C — Structured round schema
- Add `decision`, `blockers[]`, `unresolved_threads[]`, `resolved: true` fields.
- Reader can render a "current open threads" view without LLM-summarizing prose.
- Helps both token efficiency *and* the drift / out-of-sync bugs Jack mentioned.

### Phase D — Per-round files (optional, defer if A–C suffice)
- Split `<phase>_rounds.jsonl` into `rounds/001.json`, `002.json`, …
- Earlier files are immutable → maximal within-process prompt-cache reuse.
- Trade-off: more file-system noise; might complicate the watcher.

### Phase E — Event-driven watcher (optional polish)
- Replace polling with `watchdog` (cross-platform) or `fswatch` events.
- Reduces wall-clock latency between turns; orthogonal to token costs.
- Inspired by ax-platform's `wait_mode=mentions`.

### Phase F — Drift / out-of-sync bug audit (Jack flagged this in the prompt)
- Not architectural; just a cycle of focused fixes against the existing failure modes.
- Probably benefits from C (structured fields) being done first.

### Deferred / explicit non-goals

- MCP server (revisit only if a non–Claude-Code consumer appears).
- Memory service (Mem0 / Letta / Zep) — wrong fit at current scale.
- LangGraph rewrite — would discard the two-CLI-subprocess primitive that is core to the project.
- GUI / launchpad work — Jack flagged as low priority. Don't let it pull focus.

---

## 9. Open questions for the handoff cycle

These are the points where the lead and reviewer should push back during review:

1. Is tail-only reads worth it if the agent's prompt already trims aggressively? Measure first?
2. Should `summary` be human-written or LLM-written? If LLM-written, by which agent (writer, or the next reader)?
3. Are the drift / out-of-sync bugs actually *caused by* the unstructured round log, or are they orthogonal? Phase F might be its own thing.
4. Is the prompt-cache claim (no sharing across Claude Code processes) verified, or assumed? Worth a small experiment before betting Phase D on it.
5. Should the thin MCP adapter be a v2.0 phase or a "nice to have, no roadmap slot"?
6. Does any of this break the existing `pip install tagteam` UX or the auto-publish flow?

---

## 10. SQLite revisit (2026-05-01)

The original proposal stayed file-based by default. After a follow-up
discussion focused on operational pain — git-status clutter, status
drift, cross-day resumption confusion — the file-vs-DB question was
revisited and resolved in favor of SQLite.

### What changed in the analysis

The original §6 framed storage as a *token-efficiency* question, which
correctly led to "stay with files, just iterate on the format." It did
not engage with the *consistency* dimension. The pain Jack actually
hits in long-running projects is multi-file drift: `handoff-state.json`,
`docs/handoffs/<phase>_<type>_status.json`, and the round log can
disagree, and the only thing keeping them aligned is the
`_update_handoff_state` helper plus hope. Phase F (drift audit) exists
*because* this surface is too large.

A SQLite store collapses that surface to zero by construction. Specifically:

- Drift is impossible — `state` is a singleton row and cycle status
  derives from rounds.
- Cross-record writes are atomic — `BEGIN/COMMIT` instead of two
  hopeful file rewrites.
- Health queries are one SELECT instead of walking N files.
- Schema enforcement catches bad writes at the boundary.

The thing the file-based design correctly cared about — git-visible,
PR-reviewable conversation history — is preserved by auto-rendering
markdown on every DB write. The DB is canonical for runtime; the
markdown is the human/PR export.

### Spike result

A spike (2026-05-01, code in `tagteam/db_spike.py`, findings in
`docs/phases/sqlite-spike-findings.md`) tested the round-trip against
the rankr corpus (24 cycles, 85 rounds). Result: **24/24 cycles render
byte-identical** between SQLite and current files. Auto-export to
markdown is not a regression. Verdict: go.

### Effect on §8 phasing

This consolidates the proposal's six phases into roughly two:

| Original §8 phase | Status after revisit |
|---|---|
| A (tail-only reads) | Absorbed by SQLite — `WHERE round > ?` |
| B (summary field) | Absorbed by SQLite — already a column |
| C (structured round schema) | Absorbed by SQLite — native columns |
| D (per-round files) | Obviated by SQLite — schema is flat |
| E (event-driven watcher) | Independent — orthogonal to storage |
| F (drift audit) | Absorbed by SQLite — drift impossible |

The execution path becomes: Phase 28 (SQLite production port,
documented in roadmap.md) → Phase E (event-driven watcher, when
ready). The prompt-cache experiment in §9 question 4 is no longer
load-bearing on D, since D is dropped.

### What did *not* change

- No MCP server rewrite. §7 stands.
- No memory service (Mem0/Letta/Zep). §6.4 stands.
- Two-CLI-subprocess primitive remains the runtime model. The 130 ms
  Python startup cost dominates per-call latency regardless of storage,
  so storage choice is invisible to agents at the latency level — the
  case for SQLite rests on architectural simplicity, not raw speed.

### Why this revision and not just "stick with the original plan"

The original §6 was correct given the framing it had. It asked "how do
we make file-based handoffs token-efficient?" and answered well. It
did not ask "are file-based handoffs the right shape?" The follow-up
discussion surfaced the consistency-not-performance argument and the
spike validated it. Treat §1–§9 as the *token-efficiency* analysis
and §10 as the *operational-shape* analysis; both are correct in
their own frames, and §10 ends up dominating because it eliminates a
class of bugs rather than reducing a cost.

---

## 11. Sources

- AX Platform: <https://ax-platform.com/>, <https://ax-platform.com/mcp/>, <https://ax-platform.com/docs/claude-code-multi-agent/>
- ax-platform-mcp: <https://github.com/ax-platform/ax-platform-mcp>
- Aider Architect/Editor: <https://aider.chat/2024/09/26/architect.html>
- Cline Plan/Act: <https://cline.bot/blog/plan-smarter-code-faster-clines-plan-act-is-the-paradigm-for-agentic-coding>
- Roo Code: <https://github.com/RooCodeInc/Roo-Code>
- claude-squad: <https://github.com/smtg-ai/claude-squad>
- Overstory: <https://github.com/jayminwest/overstory>
- ccswarm: <https://github.com/nwiizo/ccswarm>
- claude-swarm: <https://github.com/affaan-m/claude-swarm>
- metaswarm: <https://github.com/dsifry/metaswarm>
- MetaGPT: <https://github.com/FoundationAgents/MetaGPT>
- mcp-handoff-server: <https://github.com/dazeb/mcp-handoff-server>
- Multi-Agent Debate (Du et al.): <https://arxiv.org/abs/2305.19118>
- LLM Debate: <https://composable-models.github.io/llm_debate/>
- Cloudflare AI code review: <https://blog.cloudflare.com/ai-code-review/>
- MCP Specification (Architecture): <https://modelcontextprotocol.io/specification/2025-11-25/architecture>
- MCP Specification (Server Features): <https://modelcontextprotocol.io/specification/2025-11-25/server>
