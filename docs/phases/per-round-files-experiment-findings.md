# Phase 23 — Per-Round Files Token Experiment: Findings

**Date:** 2026-05-03
**Run by:** Claude (Sub-phase A of `polish-pack-watcher-tokens-adopt`)
**Tokenizer:** `tiktoken` cl100k_base (in throwaway venv at `/tmp/tiktoken-venv`)
**Corpus:** `/Users/jackblacketter/projects/rankr` (24 cycles, 85 rounds — same corpus from the Phase 28 spike)

## Methodology

For each candidate cycle, measured three render shapes:

1. **Full render** — output of `tagteam cycle render --phase X --type Y` (current behavior).
2. **Tail-only** — render of the last N rounds (N=3).
3. **Summary-old + full-recent** — 1-line summary lines (round, role, action) for older rounds, plus full render of the last 3.

Token counts from cl100k_base (Claude tokenizer is similar enough for this kind of relative comparison).

## Results

| Cycle | Rounds | Full | Tail-3 | Savings |
|---|---|---|---|---|
| tmdb-enrichment_plan | 4 | 3139 | 2074 | **33.9%** |
| users-and-library_plan | 4 | 2648 | 1856 | **29.9%** |
| filtered-rankings_plan | 3 | 2572 | 2485 | 3.4% |

The summary-old + full-recent variant tracks tail-only within ~1% (negligible difference) for these cycle sizes.

## Interpretation

- **For 3-round cycles:** tail-3 == full, no savings. This is the modal case in the corpus.
- **For 4-round cycles:** tail-3 saves ~30%, just at the decision threshold.
- **For longer cycles (5+):** corpus has none, but extrapolation suggests savings climb sharply. A 10-round cycle with similar per-round size would save ~70%.
- **Auto-escalate at round 10** keeps the worst case bounded; in practice cycles rarely exceed 5 rounds.

## Verdict: PARTIAL IMPLEMENT — `--tail N` flag, NOT per-round files

**Implement `tagteam cycle rounds --tail N` as a flag** on the CLI. Lightweight (a slice of the existing list) and unambiguously helpful for the 4+ round case without harming the 3-round case (you'd opt in only when needed, or skip the flag).

**Do NOT split storage into per-round files.** The original Phase 23 scope was about file layout, but the savings being measured are entirely about query shape — the agent reads `tagteam cycle rounds` output, not raw files. With Phase 28's SQLite store, query shape is the only knob that matters; per-round file splitting would add complexity with zero further token benefit beyond what `--tail N` provides.

## Followup work (not in this phase)

If/when Jack wants the `--tail N` flag, the change is small:
- `tagteam/cycle.py` — add `--tail` arg parsing in `cycle rounds` subcommand
- `tagteam/cycle.py:read_rounds` (or equivalent) — return `rounds[-n:]` when tail is set
- One test confirming `--tail 3` returns the last 3 round entries
- Update `.claude/skills/handoff/SKILL.md` to mention the flag for long cycles

Roadmap status update: Phase 23 should be marked **Deferred — superseded by `--tail N` followup** rather than "Not started." The experiment is the entire deliverable for this cycle.
