# Phase 72: Jobs: background tasks and ci-watch

## Status
- [ ] Planning: DRAFT — plan cycle not opened yet
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
**The rule: deterministic first, a cheap model second, the top model last.**
Today, waiting for CI or a release is done by the top model polling by hand.

- On 2026-09-20, the lead polled five release runs one `gh` call at a time.
- `gh run list --limit 1` right after a tag push returns the *previous* run.
  That was learned the hard way.
- PyPI's JSON endpoint lags about 20 s behind `/simple/`.

None of this needs a model. It needs a recorded background task that polls
until it knows the answer, then says it once.

Add **`tagteam job`**: a recorded, detached background task with a status, a
log and a short result. The first kind is **`ci-watch`**, and it involves no
model. The result reaches the arbiter as a desktop notification (a phone
push via Remote Control) and in the cockpit, and reaches the lead too if
asked (`--to-lead` delivers it as an interjection). The cockpit gets a
**Jobs strip**.

## Scope
**In**
- A new module, `tagteam/jobs.py`: the job record, the runner and the
  `ci-watch` kind.
- A CLI: `tagteam job start|list|status|log|cancel`.
- `GET /api/jobs` and `POST /api/jobs/cancel`.
- The Jobs strip in the cockpit.
- Tests, with a fake `gh` and a fake PyPI.

**Out**
- **A model-written failure summary.** The roadmap entry allows "a model
  only to summarise a failure". This phase ships the *deterministic* failure
  result: the failing check names, their URLs, and the tail of
  `gh run view --log-failed`. That is usually enough to act on. A cheap-model
  summary is a flag for a later phase, measured first (the rule above).
- Starting jobs from the cockpit. Jobs are started by the CLI, by the lead
  or the arbiter. The cockpit shows and cancels them.
- Job kinds other than `ci-watch`, and scheduling or recurrence.

## Technical approach

### The job record: files, not the database
A job lives in `.tagteam/jobs/<id>/`:
- `job.json`: `id`, `kind`, `target`, `status`, `created_at`, `started_at`,
  `finished_at`, `pid`, `ident`, `by`, `deliver`, `result`, `attempts`,
  `last_poll_at`, `error`;
- `log.txt`: one line per poll, bounded like the watcher log.

Files keep `/api/jobs` and the Jobs strip **database-free**, as Phase 68 made
the turn facts, so any project and `tagteam job list` under read-only work
and create nothing. Writes are atomic (temp file + replace) under the guarded
`O_NOFOLLOW` rules that `watchlog` uses. The runner is the only writer of
its own job, and a cancel writes only a `cancel` marker file that the runner
honours.

**Statuses:** `running → succeeded | failed | timed-out | cancelled | error`.
- `failed`: CI said red.
- `error`: the watch itself could not work, e.g. `gh` missing or
  unauthenticated, or the target not found.
- A `running` job whose recorded pid+ident is gone reads as **`lost`**. This
  is the same liveness rule the in-flight turn uses (`procs.identity`),
  derived on read and never written by the reader.

### The runner
`tagteam job start ci-watch …` validates the target, writes `job.json`, and
spawns `python -m tagteam job run <id>`, detached (a new session, stdout to
`log.txt`), recording its pid and identity. The runner:
- polls every `--interval` seconds (default 20, minimum 5), up to
  `--timeout` (default 60 min);
- stops at the first definite answer, a cancel marker, or the timeout;
- writes the result and delivers it.

No model is involved, and no turn slot is taken, because a job is not a turn
and must never block one.

### `ci-watch` targets: each with the one right question
| Target | How it is answered (all through `gh … --json`, parsed; no text scraping) |
|---|---|
| `--pr N` | `gh pr checks N --json name,state,bucket,link`. Done when no check is pending: green if every bucket is `pass` or `skipping`, red otherwise. |
| `--run ID` | `gh run view ID --json status,conclusion,name,url,jobs` |
| `--workflow NAME --ref REF` | `gh run list --workflow NAME --json databaseId,headBranch,headSha,event,createdAt,status,conclusion`, filtered to `headBranch == REF` and **created at or after the job's start** (minus a 2-minute skew). It waits for such a run to *appear* before watching it, so the run that existed before the push is never mistaken for the new one. This is the 2026-09-20 lesson, now a test. |
| `--pypi PKG==VER` | `GET https://pypi.org/simple/PKG/` (PEP 691 JSON). Done when VER is listed. The JSON API is deliberately not used, because it lags. No `gh`. |

- On red, the result gets the failing check or job names, their URLs, and
  the last 40 lines of `gh run view <id> --log-failed`.
- `gh` exiting non-zero (not logged in, not found) is an `error` with its
  stderr's first line, and is never retried as if it were CI still running.

### Delivery: one short result, said once
- **`job.json` + `log.txt`:** always.
- **A desktop notification** (`notify_macos`, the watcher's). It reaches the
  phone when Remote Control is on, e.g. "CI green — PR #59: 2/2 checks
  passed" / "CI failed — PR #59: pytest (ubuntu) · <url>". It is on by
  default, with `--quiet` to turn it off.
- **`--to-lead`:** the one-line result is recorded as an interjection for
  the lead (`tagteam interject --to lead`, with `--by job:<id>`), so the
  lead's next turn reads it instead of polling. If no cycle is active, the
  interjection is skipped, and `job.json` says so.

### CLI
```
tagteam job start ci-watch (--pr N | --run ID | --workflow NAME --ref REF | --pypi PKG==VER)
                  [--interval S] [--timeout M] [--to-lead] [--quiet]
tagteam job list [--all] [--json]      # running + the last 24 h (--all: everything)
tagteam job status ID [--json]
tagteam job log ID [-n N]
tagteam job cancel ID
```
- `list`, `status` and `log` go into `READ_ONLY_COMMANDS`. `start` and
  `cancel` are refused under read-only.
- `start` prints the job id and a copy-paste `tagteam job status <id>`.
- Retention: finished jobs older than 7 days are pruned by the next `start`.

### Cockpit: the Jobs strip
- `GET /api/jobs` (file-only) returns running jobs and those finished in
  the last 24 h, newest first.
- A slim strip under the turn bar shows **only when there is a job**. Each
  chip reads, for example, "ci-watch · PR #59 · running 3m" or "✓ green" /
  "✗ failed". A chip expands to the result and the log tail. A running job
  has **Cancel**, confirmed with its CLI line (`POST /api/jobs/cancel`,
  through the existing action pattern).
- A `lost` job says so, and Cancel clears it. It is a job, not a turn, so
  nothing is killed without an identity check.
- The strip is refreshed by the existing SSE signature plus the live tick.
  The signature gains the max `jobs` mtime.

## Files
- `tagteam/jobs.py` (new): the record, the runner and `ci_watch`.
- `tagteam/cli.py`: dispatch, help and `READ_ONLY_COMMANDS`.
- `tagteam/cockpit_api.py`: `jobs_payload`, the cancel action, and the
  signature.
- `tagteam/server.py`: the routes.
- `cockpit.html` / `.js` / `.css`: a "Phase 72" Jobs-strip slice.
- Docs: README, how-tagteam-works, and the workflows Steering list.
- Tests: `tests/test_jobs.py`, with a fake `gh` script on PATH that replays
  canned JSON sequences, and a local HTTP stub for PyPI.

## Success criteria
1. **Each target reaches the right answer from canned `gh` sequences.**
   - A PR goes from pending to green, and from pending to red with the
     failing names, URLs and log tail.
   - A run succeeds or fails.
   - PyPI goes from absent to listed.
2. **The previous-run trap.** With `--workflow … --ref v1.2.3`, a run for
   the same ref created *before* the job started is ignored. The job waits
   until the new run appears, then watches that one.
3. **`error` is not `failed`.** A missing `gh`, an unauthenticated `gh` and
   a not-found target each end as `error` with the first line of the reason,
   without polling until the timeout.
4. **The lifecycle:**
   - timeout → `timed-out`;
   - cancel → `cancelled` within one interval;
   - a killed runner → `lost` on read, and the reader writes nothing;
   - `list`, `status` and `log` create nothing and work read-only.
5. **Delivery:**
   - exactly one notification per finished job (a stubbed `notify_macos`);
   - `--quiet` sends none;
   - `--to-lead` records one interjection for the lead with `by: job:<id>`,
     or records that it skipped it when no cycle is active.
6. **A job never touches the turn slot.** A job running beside a headless
   turn does not change `slot_status`, `launch_availability` or the
   headline.
7. **The cockpit:**
   - the strip is absent with no jobs;
   - a running job shows its chip and a cancel with its CLI line;
   - a finished job shows its result;
   - a lost job says so.

   Driven in a real page with a fake `gh`.
8. **Used on the real thing once:** after this phase merges, the 3.14.9
   release is watched with `tagteam job start ci-watch --workflow "Publish
   to PyPI" --ref v3.14.9` and `--pypi tagteam==3.14.9`, instead of by hand.
   The result is recorded in the closeout.

## Risks and open questions for the reviewer
- **Files, not the database**, for job records. This keeps `/api/jobs`
  database-free and each runner the single writer of its own record. The
  cost: no SQL queries over job history, which the strip does not need.
- **The model summary is deferred** (see Out). The deterministic failure
  result ships first; whether a cheap-model summary earns its tokens is
  measured later.
- **`notify_macos` on by default.** The arbiter asked for phone pushes when
  something is ready. A job's end is exactly that.
