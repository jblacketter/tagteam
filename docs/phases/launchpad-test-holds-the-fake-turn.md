# Phase 74a: Launchpad test holds the fake turn

## Status
- [x] Planning: none needed. This is a test-only fix, so it goes straight to an impl cycle, as the arbiter's rule for small fixes says.
- [x] Implementation: branch `phase-74a-launchpad-test-holds-the-fake-turn`
- [x] Implementation Review: approved round 2 (2026-09-24) at `3e94057`; gate 2,611 passed, 5 skipped (r1 was a gate bounce on the second race)
- [x] Complete: PR #63 merged 2026-09-24 (rebase); its CI was green on the first run.

## Closeout
```
Phase report: launchpad-test-holds-the-fake-turn — impl approved r2
  impl   2 rounds · 0 change requests · 1 bounce · gate 2 runs, 18m 04s
  time   start→approve 20m 57s
         lead 1m 37s (1 span, 1 unknown) · reviewer 1m 16s (1 span) · gate 18m 04s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 3 · no token data 0 · unmatched 2 · unknown 1
```

## Summary
`tests/test_launchpad.py::TestServerEndpoints::test_watch_session_and_launch_endpoints`
failed three times on CI with `assert conv["slot"]["held"]` false:
- on `main` at `d49c632`;
- on PR #61 (run 35960741817);
- on PR #62 (run 35963725788).

Each time the same code also passed, once for PRs #61 and #62.

## Cause
The test launches a lead turn through `POST /api/start/launch`. It then
checks, among other things, that the turn is **still running**:
- the row reads `running`;
- the slot is held;
- an SSE line arrives;
- a repeat launch returns the same turn.

It relied on the fake agent's turn lasting about 2 s
(`FAKE_AGENT_SLEEP=1.0` between events). On a loaded runner the POST and
the GET can take that long, and the turn ends first.

`lead_chat.run_turn` releases the slot in a `finally` a few milliseconds
**before** `finish_conversation_turn` marks the row finished. A read in
that gap sees `status: running` with the slot free, which is exactly the
failure.

**Demonstrated:** with the sleep cut to 0.01 s, a copy of the test fails
every time on the first "still running" assertion. That shows the timing
dependence. The exact gap CI hit was not reproduced locally; it is the only
state that fits the failing assertion.

## Fix
This is test-only, and no product code changes.
- **`tests/fixtures/fake_agent.py`:** a new `FAKE_AGENT_HOLD=<path>`. In
  `chat` mode, the fake emits its first event and then waits until that file
  exists before it replies and exits. The wait polls every 20 ms and is
  capped at 30 s, so a broken test can't hang the suite.
- **The test:** it sets the hold file's path before the launch, and creates
  the file only when it reaches the completion section. Every "still
  running" assertion now runs while the turn is provably held. The
  1-second sleep goes (the fixture's 0.05 s applies), so the test is also
  faster.

**Not changed:** the release-before-finish ordering in `run_turn`. The gap
is milliseconds, and it is harmless to the product: the next launch just
sees a free slot. Reordering it would change the slot lifecycle, which is
outside a test fix.

## Verification (recorded)
- **Held test:** it passes and takes about 2 s. Before, it took at least
  2 s on the sleeps alone, and relied on luck.
- **Under load:** 30 runs in a row, with 4 busy loops on the 8-core
  machine: **30/30 passed** in 57 s.
- **The failure is demonstrated:** with the old test and the sleep cut to
  0.01 s, the test fails every time on its first "still running"
  assertion, before this fix.

## Round 1: a second flake, found by the gate
The r1 gate run failed once on a different test:
`tests/test_jobs.py::TestLifecycle::test_fast_completion_keeps_the_runners_result`
(from Phase 72). It is a test race of the same kind.
- The helper `_wait_terminal` returned the record as soon as the job was
  terminal.
- But the job runner adds the `delivery` field just *after* it commits the
  result. That is by design: delivery is at most once, and recorded after
  the commit.
- A byte snapshot taken between the two writes then differed. The byte
  diff lands at the sorted `delivery` key.
- The cancel test beside it read `rec["delivery"]` on the same timing, so
  it was exposed to the same race.

**Fix:** `_wait_terminal` now returns only when the job is terminal **and**
its runner has released `runner.lock` (`probe_runner != busy`), so the
record is settled. Under load (4 busy loops on 8 cores) the two real-runner
tests passed **20/20**.

## Success criteria
1. The test passes with the hold in place, and fails as before if the hold
   is removed and the sleep shortened (checked by hand; not a committed
   test).
2. Run 30 times in a row locally under CPU load, it passes every time.
3. The fake agent without `FAKE_AGENT_HOLD` behaves exactly as before, and
   the rest of the suite is unchanged (the gate's full run).
