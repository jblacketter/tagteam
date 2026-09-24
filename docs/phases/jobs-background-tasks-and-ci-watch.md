# Phase 72: Jobs: background tasks and ci-watch

## Status
- [ ] Planning: in review (plan cycle opened 2026-09-23)
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
model. The result reaches the arbiter as a best-effort desktop
notification and in the cockpit, and reaches the lead too if
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
`O_NOFOLLOW` rules that `watchlog` uses.

**Who may write: two `O_EXCL` tokens decide it** (plan review r1). Each is a
file created with `O_CREAT|O_EXCL`, so exactly one process ever holds it:
- **`claim`** (the runner token), written with `{pid, ident, claimed_at}`.
  - `start` writes the initial `job.json` (`status: starting`, no pid)
    **before** it spawns, and never writes `job.json` again.
  - The spawned `job run ID` creates `claim` first. It then records its own
    pid/ident in `job.json`. A second `job run ID` finds `claim` taken and
    exits non-zero having written nothing. So a fast child's result can't be
    overwritten by the parent, and a duplicate runner can't poll or deliver.
  - `start` waits up to 5 s for `claim` to appear. If it hasn't, `start`
    tries to create `claim` itself (content `{"by": "start"}`). If `start`
    wins, the child never ran: `start` takes `final` (below), records
    `error: runner did not start`, and exits non-zero. If `start` loses, the
    child owns the job.
- **`final`** (the terminal token). Whoever creates it writes the terminal
  `job.json` and does the delivery, so a job is finished and delivered
  **exactly once**. That is the runner in every normal case, and the
  canceller only for a confirmed-dead runner (below).

**Statuses:** `starting → running → succeeded | failed | timed-out |
cancelled | error`.
- `failed`: CI said red.
- `error`: the watch itself could not work, e.g. `gh` missing or
  unauthenticated, the target not found, or the runner did not start.

**Liveness is derived on read, and the reader writes nothing.** For a
non-terminal job, the reader looks at `claim`'s pid and ident:
- **alive:** `pid_alive(pid)` and `identity(pid) == ident`.
- **`lost` (confirmed dead):** the pid is not alive, or `identity(pid)` is a
  *different* identity (the pid was reused).
- **`unknown`:** the pid answers but `identity()` returns None (unverifiable).
  It is shown as running with a caveat and is **never** treated as dead.
- A `starting` job with no `claim` after 30 s reads as `lost`.

**Cancel:**
- `tagteam job cancel ID` always writes a `cancel` marker first.
- **Live runner:** the runner sees the marker within one interval, takes
  `final` and writes `cancelled`.
- **Confirmed-dead (`lost`) runner:** no one else will ever finish the job,
  so cancel takes `final` itself. It writes `status: cancelled` with
  `error: "runner lost"` and delivers nothing (no CI answer is known). This is
  durable: the job is now an ordinary finished job for `list` and retention.
- **`unknown` runner:** cancel writes the marker only, and says it could not
  confirm the runner is dead, so the job was not finalised.
- An uncancelled `lost` job stays `lost` in `list`. Retention prunes it,
  like a finished one, 7 days after `created_at`.

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
| `--pr N` | `gh pr view N --json number,state,headRefOid,statusCheckRollup` (not `gh pr checks`, which exits non-zero when no checks are registered yet; see "PR checks" below). |
| `--run ID` | `gh run view ID --json status,conclusion,name,url,jobs` |
| `--workflow NAME (--ref REF \| --sha SHA)` | Bound to a **commit**, not a time window (see "Selecting the run" below). `gh run list --workflow NAME --commit SHA --json databaseId,headSha,event,createdAt,status,conclusion,url`, then `gh run view ID --json …` once a run is pinned. |
| `--pypi PKG==VER` | `GET https://pypi.org/simple/PKG/` (PEP 691 JSON). Done when VER is listed. The JSON API is deliberately not used, because it lags. No `gh`. |

**Selecting the run (`--workflow`)** (plan review r1). A time window can't
tell a previous run from the new one. A run 60 s old passes a "start minus 2
minutes" filter. So the job binds to the **commit** that was pushed:
- `--ref REF` is resolved **once, at `start`, synchronously**, with
  `git rev-parse --verify REF^{commit}` in the project. For a release, the tag
  is created locally before it is pushed, so this works whether the watch is
  armed before or after the push. `--sha SHA` gives the commit directly. If
  the ref doesn't resolve, `start` fails at once, and no job is written.
- The runner polls `gh run list --workflow NAME --commit SHA`:
  - **No run for that commit yet:** it logs "waiting for a run of <sha7>"
    and keeps polling until the timeout. It is never green.
  - **One run:** the job pins it.
  - **Several runs** (e.g. a re-dispatch): it pins the newest, the highest
    `databaseId`, and logs the others.
  - **Pinned is final:** the pinned run id is written to `job.json`
    (`pinned_run`), and every later poll is `gh run view <pinned>`.
- The run that existed before the push belongs to a different commit, so it
  can never match, however recent it is. A run for the same commit that
  finished before the watch started is the right answer, and it is reported.
- Tag pushes and branch pushes are the same case. A bare `--ref` for a branch
  means the local tip, which is what was just pushed.

**PR checks (`--pr`)** (plan review r1). `gh pr view --json
statusCheckRollup` separates the two cases by *structure*, not by stderr
text. An existing PR returns exit 0 with a rollup list, which may be
**empty**. A missing or inaccessible PR exits non-zero, which is an `error`.
The rollup holds `CheckRun` items (`status`, `conclusion`, `name`,
`detailsUrl`) and `StatusContext` items (`state`, `context`, `targetUrl`).
Both are normalised to `pending | pass | skip | fail`.
- **Empty rollup:** "no checks registered yet". The job keeps waiting, and
  never calls it green.
- **Any check pending:** the job waits.
- **All complete:** green if every check is pass or skip, red otherwise.
- **The head sha is recorded with each poll.** If it changes (a new push),
  the log says so and the judgement follows the new head. The result names
  the sha it judged.
- **`--expect-checks N`** (optional): don't judge until at least N checks
  are registered. This covers a PR whose workflows register one at a time.
  Without it, the first complete-and-non-empty set is judged, and the plan
  names that as a known limit.

- On red, the result gets the failing check or job names, their URLs, and
  the last 40 lines of `gh run view <id> --log-failed`.
- `gh` exiting non-zero (not logged in, not found) is an `error` with its
  stderr's first line, and is never retried as if it were CI still running.

### Delivery: one short result, said once
- **`job.json` + `log.txt`:** always.
- **A best-effort desktop notification** through `tagteam.notify`, which
  uses osascript, Windows toast or notify-send. For example: "CI green —
  PR #59: 2/2 checks passed" / "CI failed — PR #59: pytest (ubuntu) · <url>".
  It is on by default, with `--quiet` to turn it off. A notifier failure is
  logged and never changes the job's result. **There is no phone
  guarantee** (plan review r1): tagteam has no phone transport. A push to
  the arbiter's phone is Claude Code's Remote Control and is out of scope;
  the `--to-lead` interjection is how a live Claude session hears of it.
- **`--to-lead`:** the one-line result is recorded as an interjection for
  the lead (`tagteam interject --to lead`, with `--by job:<id>`), so the
  lead's next turn reads it instead of polling. If no cycle is active, the
  interjection is skipped, and `job.json` says so.

### CLI
```
tagteam job start ci-watch (--pr N [--expect-checks N] | --run ID
                             | --workflow NAME (--ref REF | --sha SHA) | --pypi PKG==VER)
                  [--interval S] [--timeout M] [--to-lead] [--quiet]
tagteam job run ID                     # internal: the runner (claims or exits)
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
   The fixtures pair each JSON body with the exit code the real command
   gives.
   - A PR goes from no checks (an empty rollup) to pending to green, and
     from pending to red with the failing names, URLs and log tail.
   - An empty rollup is never green.
   - `--expect-checks 2` doesn't judge one completed check.
   - A head-sha change mid-watch is followed.
   - A run succeeds or fails.
   - PyPI goes from absent to listed.
2. **The previous-run trap.** With `--workflow … --ref v1.2.3`:
   - a successful run for the same workflow and branch/ref name, created
     **60 s** before the job started but for a different commit, is
     ignored; the job waits;
   - the run for the resolved sha then appears and is watched;
   - with two runs for that sha, the higher id is pinned, and `pinned_run`
     stays fixed across later polls;
   - a ref that doesn't resolve fails `start` with no job written.
3. **`error` is not `failed`.** A missing `gh`, an unauthenticated `gh` and
   a not-found target each end as `error` with the first line of the reason,
   without polling until the timeout.
4. **The lifecycle:**
   - timeout → `timed-out`;
   - cancel → `cancelled` within one interval;
   - a killed runner → `lost` on read, and the reader writes nothing;
   - **fast completion:** a runner that finishes before `start` returns
     keeps its terminal result, and `start` never rewrites `job.json`;
   - **duplicate runner:** a second `job run ID` exits non-zero, writes
     nothing and delivers nothing;
   - **cancel after runner death:** the job becomes `cancelled` /
     `runner lost` durably, it is pruned by retention, and nothing is
     delivered;
   - **unverifiable identity** (`identity()` stubbed to None on a live pid)
     reads `unknown`, and cancel doesn't finalise it;
   - **runner never claims:** `start` takes the claim and records
     `error: runner did not start`;
   - `list`, `status` and `log` create nothing and work read-only.
5. **Delivery** (local calls only — this proves the notifier is called,
   not that anything reaches a phone):
   - exactly one notification per finished job (a stubbed `tagteam.notify`),
     even with a duplicate runner or a racing cancel;
   - a notifier that raises doesn't change the result;
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
- **Desktop notification on by default** (accepted in r1). It is
  best-effort and desktop-only; the phone push is not tagteam's.
- **`--ref` resolves locally.** A watch on a ref that exists only on the
  remote needs `--sha`. That is deliberate: resolving through the API would
  bring back "not found yet or not found at all" ambiguity.
