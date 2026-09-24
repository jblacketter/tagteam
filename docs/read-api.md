# The read API other dashboards may rely on

Tagteam serves JSON to its own pages: the cockpit (`tagteam serve`, one
project) and the hub (`tagteam hub`, every registered project). This page
declares the part of it **another dashboard may rely on**: which endpoints,
which fields, their types, and what "stable" means.

Everything not listed here is private. It may change in any release.

The same declaration is data in `tagteam/read_api.py` (`STABLE`). A test
keeps this page and that module identical, and another checks every listed
field against real payloads.

## Finding it
- **`GET /api/cockpit/info`** (a cockpit) and **`GET /api/hub/info`** (the
  hub; `/api/info` is the same) return `api_version`, the `tagteam` package
  version, and `stable`: the endpoints that server promises.
- **The hub mounts each project's cockpit at `/p/<id>/`**, so
  `/p/<id>/api/now` is the same contract as a standalone cockpit's
  `/api/now`. Take `<id>` from a hub row's `id`.
- **No `api_version`** in the info endpoint means a tagteam older than this
  promise (before 3.14.9). Nothing is promised there.

## What "stable" means
- **Within one `api_version`, changes are additive only.**
  - A listed path is never removed, renamed or re-typed.
  - Fields may be added anywhere, so **ignore fields you don't know**.
  - Enum-like strings (`headline.state`, `headline.tone`, `group`,
    `kind`, `shown`, …) may gain values, so **handle values you don't
    know**.
- **Anything else is a new `api_version`:** a removal, a rename, or a type
  change. The old version is not served beside it. Check `api_version`, and
  refuse or adapt.
- **The type column.**
  - `or null`: the value may be null.
  - **optional**: the key may be absent, and absent means "unknown". For
    example, `state.turn` is left out while nothing is owed.
  - A null or absent object has no children to read.
  - `name[]` means each element of the list `name`.
  - `number` may be an integer or a float.

## Limits
- **No CORS** (the arbiter's decision, 2026-09-24). The cockpit is a local
  control surface, and it sends no `Access-Control-Allow-Origin`. So a web
  page on another origin can't read it from the browser. A server-side
  reader, or a page proxied through its own backend, can.
- **Reads only.** The write endpoints (`POST /api/…`) take a per-run token
  and are not part of this contract.
- **SSE payloads are not promised.** `/api/events` and `/api/hub/events`
  promise only that an event named `change` means "re-read".
- **A hub row's `watcher` may be null**: that means *unknown*, not "not
  running". A project whose files can't be read is isolated into a row
  (with an `error` message) instead of failing the whole hub response, and
  its watcher is not inspected.
- **`usage.cost_usd` is an estimate**: what the recorded tokens would cost
  at API prices. The agents run on subscriptions, so it is not a charge.
- **Show the headline; don't re-derive it.** `headline` in `/api/now` is
  tagteam's one decision about who has the ball (Phase 68). Show its
  `text`, with `tone` for colour. The facts it is derived from are
  deliberately not promised.

## Not promised
- `state.history`, `inflight`, `launch`, `last_turn`, and the `intent`
  and `worktree` on hub rows.
- Log paths and process ids.
- Every other endpoint: `/api/activity`, `/api/tail`, `/api/lead*`,
  `/api/rules`, `/api/usage`, `/api/scope-diff/*`, `/api/briefs*`,
  `/api/interjections`, `/api/start`, and the legacy Saloon endpoints.

## Endpoints (api_version 1)

## Hub

### `GET /api/hub/info`

| path | type | |
|---|---|---|
| `app` | `string` |  |
| `kind` | `string` |  |
| `api_version` | `int` |  |
| `tagteam` | `string` |  |
| `stable` | `list` |  |
| `stable[]` | `string` |  |
| `mounted` | `list` |  |
| `mounted[]` | `string` |  |

### `GET /api/hub`

| path | type | |
|---|---|---|
| `ts` | `string` |  |
| `groups` | `object` |  |
| `groups.needs_you` | `list` |  |
| `groups.waiting` | `list` |  |
| `groups.quiet` | `list` |  |
| `groups.hidden` | `list` |  |
| `groups.hidden[].id` | `string` |  |
| `groups.hidden[].path` | `string` |  |
| `groups.hidden[].kind` | `string` |  |

#### Project row: each element of `groups.needs_you[]`, `groups.waiting[]`, `groups.quiet[]`

| path | type | |
|---|---|---|
| `id` | `string` |  |
| `name` | `string` |  |
| `path` | `string` |  |
| `group` | `string` |  |
| `why` | `string|null` |  |
| `phase` | `string|null` |  |
| `type` | `string|null` |  |
| `round` | `int|null` |  |
| `turn` | `string|null` |  |
| `status` | `string|null` |  |
| `cycle_state` | `string or null` |  |
| `live` | `bool` |  |
| `stale` | `bool` |  |
| `last_activity` | `string or null` |  |
| `last_activity_age_s` | `number or null` |  |
| `paused` | `object|null` |  |
| `paused.reason` | `string or null` |  |
| `watcher` | `object or null` |  |
| `watcher.running` | `bool` |  |
| `usage` | `object|null` |  |
| `usage.turns` | `int` |  |
| `usage.input_tokens` | `int` |  |
| `usage.output_tokens` | `int` |  |
| `usage.cost_usd` | `number` |  |

### `GET /api/hub/events`

An SSE stream. An event named `change` means "re-read what you show". Its payload is not promised.

## Cockpit

### `GET /api/cockpit/info`

| path | type | |
|---|---|---|
| `api_version` | `int` |  |
| `tagteam` | `string` |  |
| `stable` | `list` |  |
| `stable[]` | `string` |  |
| `project_dir` | `string` |  |

### `GET /api/now`

| path | type | |
|---|---|---|
| `ts` | `string` |  |
| `state` | `object` |  |
| `state.phase` | `string or null` | optional |
| `state.type` | `string or null` | optional |
| `state.round` | `int or null` | optional |
| `state.status` | `string or null` | optional |
| `state.turn` | `string or null` | optional |
| `state.run_mode` | `string or null` | optional |
| `headline` | `object` |  |
| `headline.state` | `string` |  |
| `headline.tone` | `string` |  |
| `headline.text` | `string` |  |
| `headline.age_s` | `number or null` |  |
| `headline.role` | `string or null` |  |
| `headline.agent` | `string or null` |  |
| `watcher` | `object` |  |
| `watcher.running` | `bool` |  |
| `watcher.mode` | `string or null` |  |
| `watcher.beat` | `object` |  |
| `watcher.beat.state` | `string` |  |
| `paused` | `object|null` |  |
| `paused.reason` | `string or null` |  |
| `agents` | `object` |  |
| `agents.lead` | `string or null` |  |
| `agents.reviewer` | `string or null` |  |
| `pending_notes` | `int` |  |

### `GET /api/roadmap`

| path | type | |
|---|---|---|
| `current` | `object|null` |  |
| `current.phase` | `string` |  |
| `current.type` | `string` |  |
| `current.round` | `int or null` |  |
| `current.state` | `string or null` |  |
| `groups` | `object` |  |
| `groups.done` | `list` |  |
| `groups.in_progress` | `list` |  |
| `groups.ready` | `list` |  |
| `groups.blocked` | `list` |  |
| `problems` | `list` |  |
| `problems[]` | `string` |  |
| `warnings` | `list` |  |
| `warnings[]` | `string` |  |
| `launch` | `object` |  |
| `launch.available` | `bool` |  |
| `launch.reason` | `string or null` |  |

#### Phase row: each element of `groups.done[]`, `groups.in_progress[]`, `groups.ready[]`, `groups.blocked[]`

| path | type | |
|---|---|---|
| `slug` | `string` |  |
| `number` | `string` |  |
| `name` | `string` |  |
| `status` | `string` |  |
| `depends_on` | `list` |  |
| `depends_on[]` | `string` |  |
| `unmet` | `list` |  |
| `unmet[]` | `string` |  |

### `GET /api/watcher/events`

| path | type | |
|---|---|---|
| `events` | `list` |  |
| `events[].ts` | `string` |  |
| `events[].kind` | `string` |  |
| `events[].msg` | `string` |  |
| `events[].phase` | `string or null` | optional |
| `events[].type` | `string or null` | optional |
| `events[].round` | `int or null` | optional |
| `events[].turn` | `string or null` | optional |
| `events[].repeat` | `int` | optional |
| `events[].last_ts` | `string` | optional |

### `GET /api/jobs`

| path | type | |
|---|---|---|
| `jobs` | `list` |  |
| `jobs[].id` | `string` |  |
| `jobs[].kind` | `string` |  |
| `jobs[].label` | `string` |  |
| `jobs[].status` | `string` |  |
| `jobs[].shown` | `string` |  |
| `jobs[].summary` | `string` |  |
| `jobs[].created_at` | `string` |  |
| `jobs[].finished_at` | `string or null` |  |
| `jobs[].age_s` | `int` |  |
| `running` | `int` |  |

### `GET /api/events`

An SSE stream. An event named `change` means "re-read what you show". Its payload is not promised.
