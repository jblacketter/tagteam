# Phase 74: Versioned read API for other dashboards

## Status
- [ ] Planning: in review (plan cycle opened 2026-09-24)
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
Today only tagteam's own pages read tagteam's JSON endpoints. `cockpit.js`
and the hub page ship in the same wheel as the server, so a field can be
renamed in one commit and nothing breaks. Nothing promises the shape to
anyone else.

The arbiter decided on 2026-09-20 that tagteam stays a standalone package,
which another dashboard (superdash) consumes. That dashboard needs a promise.

This phase **declares** a small read surface other dashboards may rely on:
- which endpoints;
- which fields in each, and their JSON types;
- what "stable" means;
- an `api_version` that says which promise a server keeps.

A test pins the declaration against real payloads. **No new data**, and no
superdash- or Aegis-specific glue: this is a contract over what the pages
already read.

## Scope
**In**
- A new module, `tagteam/read_api.py`. It holds `API_VERSION = 1` and the
  declaration `STABLE`: endpoint → the field paths promised, with their JSON
  types and whether they may be null.
- `api_version` and the list of stable endpoints in `GET /api/cockpit/info`
  and `GET /api/hub/info` (`/api/info` on the hub is the same handler).
- `docs/read-api.md`: the contract for a reader. The endpoints, the fields,
  the stability rules, how to find a project's cockpit through the hub, and
  what is deliberately **not** promised.
- Tests. Every declared path is present with the declared type in real
  payloads built from fixture projects, covering the states that matter
  (no cycle, working, approved, escalated, a job, a watcher event).
  Separately, the declaration is pinned to its version.

**Out**
- **New fields or endpoints.** If a dashboard needs something not already
  served, that is a later phase.
- **CORS.** Cockpit mode sends no `Access-Control-Allow-Origin`, and this
  phase does not add one (see Risks). A browser page on another origin can't
  read the API; a server-side reader, or a page proxied through its own
  backend, can.
- **Write endpoints.** They stay cockpit-internal, token-guarded and
  unversioned.
- The SSE streams' payloads. Only the fact that they signal a change is
  promised (below).
- The legacy Saloon endpoints (`/api/state`, `/api/cycles`, …).

## Technical approach

### The stable surface, v1
Chosen by one question: *what would another dashboard need to show "who has
the ball" and "what is next" for each project?* Every field below is already
served today.

**Hub** (`tagteam hub`, one server for all registered projects):

| Endpoint | Promised |
|---|---|
| `GET /api/hub/info` | `app` (`"tagteam"`), `kind` (`"hub"`), `api_version` (int), `tagteam` (package version), `stable` (list of endpoint paths), `mounted` (list of project ids with a mounted cockpit) |
| `GET /api/hub` | `ts`; `groups.needs_you` / `waiting` / `quiet` (lists of project rows) and `groups.hidden`. Row: `id`, `name`, `path`, `group`, `why`, `phase`, `type`, `round`, `turn`, `status`, `cycle_state`, `live`, `stale`, `last_activity`, `last_activity_age_s`, `paused` (object or null), `watcher.running` |
| `GET /api/hub/events` | SSE. An event named `change` means "re-read `/api/hub`". The payload is not promised. |

**Cockpit** (`tagteam serve`, one project; also mounted by the hub at
`/p/<id>/`, so `/p/<id>/api/now` is the same contract):

| Endpoint | Promised |
|---|---|
| `GET /api/cockpit/info` | `api_version`, `tagteam`, `stable`, `project_dir` |
| `GET /api/now` | `ts`; `state.phase` / `type` / `round` / `status` / `turn` / `run_mode` (each **optional**: the state file omits a key it has no value for, e.g. `turn` while nothing is owed); `headline.state` / `tone` / `text` / `age_s` / `role` / `agent` (the one server-side sentence of Phase 68, and the thing to show); `watcher.running`, `watcher.mode`, `watcher.beat.state`; `paused` (object or null); `agents.lead` / `agents.reviewer`; `pending_notes` |
| `GET /api/roadmap` | `current` (object or null: `phase`, `type`, `round`, `state`); `groups.done` / `in_progress` / `ready` / `blocked` (lists of `slug`, `number`, `name`, `status`, `depends_on`, `unmet`); `problems`, `warnings` (lists of strings); `launch.available`, `launch.reason` |
| `GET /api/watcher/events?n=&chatter=0` | `events` (list of `ts`, `kind`, `msg`, with `phase` / `type` / `round` / `turn` present when known, and `repeat` / `last_ts` on folded runs). `kind` is from the closed `watchlog.KINDS` vocabulary. |
| `GET /api/jobs` | `jobs` (list of `id`, `kind`, `label`, `status`, `shown`, `summary`, `created_at`, `finished_at`, `age_s`), `running` (int). (Phase 72, merged in PR #60.) |
| `GET /api/events` | SSE. A `change` event means "re-read what you show". The payload is not promised. |

**Deliberately not promised:**
- `usage.cost_usd` on hub rows. The arbiter's rule is no dollar figures in
  anything tagteam presents, and a promise would make it permanent. (It
  stays in the payload for now; removing it is a separate decision.)
- `state.history`, `inflight`, `launch` and `last_turn` internals.
- Log paths and pids (process details, not facts).
- Every other endpoint (`/api/activity`, `/api/tail`, `/api/lead*`,
  `/api/rules`, `/api/usage`, `/api/scope-diff/*`, `/api/briefs*`, …).

A reader should treat anything not in `STABLE` as private.

### What "stable" means (in `docs/read-api.md` and enforced by tests)
- **Within one `api_version`, changes are additive only.** Promised paths
  are never removed, renamed or re-typed. A path marked nullable may be
  null. A path marked optional may be absent, and absent means "unknown".
  Fields may be added anywhere, so readers must ignore unknown fields.
  Enum-like strings (`headline.state`, `tone`, `kind`, `group`) may gain
  values, and readers must handle unknown values.
- **Anything else is a new `api_version`:** a removal, a rename, a type
  change, or a nullable field becoming non-null in a way that changes its
  meaning. The old version is not served alongside it. A reader checks
  `api_version` at `/api/*/info` and refuses or adapts.
- A **missing** `api_version` means a tagteam older than this phase, with no
  promise.

### How the declaration is kept honest
- **`STABLE` is data**, not prose:
  `{"/api/now": [("headline.text", "string"), ("state.turn", "string|null", "optional"), …], …}`.
  Paths use dots, and `[]` means "each element of this list". The info
  endpoints' `stable` list is derived from it. `docs/read-api.md` lists the
  same paths, and a test compares the doc's tables with `STABLE`, so the
  doc can't drift.
- **The shape test** builds real payloads from fixture projects, through the
  same functions the server calls. It walks every declared path and checks
  presence and type, in each of these states:
  - no cycle;
  - a plan in review with the reviewer working;
  - an approved impl;
  - an escalation;
  - paused;
  - a watcher event;
  - a job;
  - a hub with a needs-you project and a quiet one.
- **The version pin** (`test_changing_the_promise_needs_a_version_decision`)
  stores a digest of `STABLE` beside `API_VERSION`. Editing `STABLE` fails
  the test until the digest is updated in the same commit. The message says:
  "additive → update the digest; removal/rename/retype → bump API_VERSION
  and write the change in docs/read-api.md". Adding a path is allowed
  without a bump, and removing one is caught (a test that removes a path
  from a copy asserts the check fails).
- **A served test:** `Served(proj, "cockpit")` and a hub server return
  `api_version` and `stable` from the info endpoints, and `/p/<id>/api/now`
  through the hub equals the project's own `/api/now` on the promised paths.

## Files
- `tagteam/read_api.py` (new): `API_VERSION`, `STABLE`, `digest()`,
  `check(payload, endpoint)` (used by the tests; importable by a reader who
  wants to validate).
- `tagteam/server.py`: `/api/cockpit/info` gains `api_version`, `tagteam`
  and `stable`.
- `tagteam/hub.py`: `/api/hub/info` gains the same.
- `docs/read-api.md` (new); a line each in the README and
  `how-tagteam-works.md` (the Hub and Cockpit sections).
- `tests/test_read_api.py` (new).

## Success criteria
1. Both info endpoints return `api_version: 1`, `tagteam`, and `stable`,
   which equals the keys of `STABLE` for that server kind.
2. Every declared path is present with its declared type in every fixture
   state listed above, for every stable endpoint. A deliberately broken
   payload (a renamed field) fails the check with the path named.
3. Editing `STABLE` without updating the digest fails the version-pin test
   with the rule in its message. Adding a path is allowed with a digest
   update and no version bump.
4. `docs/read-api.md` lists exactly the paths in `STABLE` (a test compares
   them) and states the stability rules, the not-promised list (including
   why `cost_usd` is excluded) and the no-CORS limit.
5. `/p/<id>/api/now` through the hub equals the project's own `/api/now` on
   every promised path.
6. No payload loses or renames anything: the full suite passes unchanged
   apart from the new tests.

## Risks and open questions for the reviewer
- **CORS stays off.** Cockpit mode is a local control surface with a POST
  token. Opening reads to any origin would let any web page the arbiter
  visits read project state from localhost. superdash's stack is undecided,
  and a server-side reader needs nothing. If it turns out to be a
  browser-only page, a later phase can add an explicit allow-list
  (`serve.read_origins`).
- **`cost_usd` in the hub payload.** It is excluded from the promise but
  still served. Removing it now would be a change to a surface nobody
  depends on yet; the arbiter may want it gone.
- **The granularity of the promise.** `headline` is promised, not the facts
  it is derived from. That is deliberate: Phase 68 made the headline the one
  place "who has the ball" is decided, and a second dashboard re-deriving it
  is exactly what that phase removed.
