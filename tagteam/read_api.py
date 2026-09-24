"""The versioned read API other dashboards may rely on (Phase 74).

Tagteam's pages read tagteam's JSON endpoints, and they ship in the same
wheel as the server, so their shapes could change in one commit without
anyone noticing. Another dashboard (superdash) consumes tagteam as a package
and needs a promise. This module IS that promise: which endpoints, which
fields, their JSON types, and the version of the promise a server keeps.

**Stability rule.** Within one ``API_VERSION`` changes are additive only:
a promised path is never removed, renamed or re-typed. Fields may be added
anywhere (readers ignore unknown fields), and enum-like strings may gain
values (readers handle unknown values). Anything else is a new
``API_VERSION``; the old version is not served beside it. A server with no
``api_version`` in its info endpoint predates the promise.

**Declaration syntax.** ``STABLE[kind][endpoint]`` is a list of
``(path, type)`` or ``(path, type, "optional")``:

- ``path`` uses dots; ``name[]`` means "each element of the list ``name``".
- ``type`` is ``string | int | number | bool | object | list``, optionally
  ``|null``. ``number`` accepts ints; ``int`` rejects bools.
- ``optional``: the key may be absent, and absent means "unknown". A path
  that is not optional must be present (possibly null, if nullable).
- A null or absent parent ends the check below it: children of a nullable
  or optional object are checked only when it is there.

Anything not declared here is private. `docs/read-api.md` states the same
contract for readers, and a test keeps the two identical.

Imports nothing from the server, so a reader can use ``check()`` to
validate what it receives.
"""
from __future__ import annotations

import hashlib
import json

API_VERSION = 1

_ROW = [  # one project row in the hub's visible groups
    ("id", "string"), ("name", "string"), ("path", "string"), ("group", "string"), ("why", "string|null"),
    ("phase", "string|null"), ("type", "string|null"), ("round", "int|null"), ("turn", "string|null"),
    ("status", "string|null"), ("cycle_state", "string|null"), ("live", "bool"), ("stale", "bool"),
    ("last_activity", "string|null"), ("last_activity_age_s", "number|null"),
    ("paused", "object|null"), ("paused.reason", "string|null"),
    ("watcher", "object"), ("watcher.running", "bool"),
    ("usage", "object|null"), ("usage.turns", "int"), ("usage.input_tokens", "int"),
    ("usage.output_tokens", "int"),
    # an API-equivalent estimate of what the tokens would cost; the agents run on
    # subscriptions (arbiter's decision, 2026-09-24: shown, labelled as an estimate)
    ("usage.cost_usd", "number"),
]
_HIDDEN = [("id", "string"), ("path", "string"), ("kind", "string")]
_PHASE = [("slug", "string"), ("number", "string"), ("name", "string"), ("status", "string"),
          ("depends_on", "list"), ("depends_on[]", "string"), ("unmet", "list"), ("unmet[]", "string")]
_INFO = [("api_version", "int"), ("tagteam", "string"), ("stable", "list"), ("stable[]", "string")]


def _under(prefix: str, rows: list) -> list:
    return [(f"{prefix}.{r[0]}",) + tuple(r[1:]) for r in rows]


STABLE: dict[str, dict[str, list[tuple]]] = {
    "hub": {
        "/api/hub/info": [("app", "string"), ("kind", "string")] + _INFO
                         + [("mounted", "list"), ("mounted[]", "string")],
        "/api/hub": [("ts", "string"), ("groups", "object"),
                     ("groups.needs_you", "list"), ("groups.waiting", "list"), ("groups.quiet", "list"),
                     ("groups.hidden", "list")]
                    + _under("groups.needs_you[]", _ROW) + _under("groups.waiting[]", _ROW)
                    + _under("groups.quiet[]", _ROW) + _under("groups.hidden[]", _HIDDEN),
        # SSE: an event named `change` means "re-read /api/hub"; the payload is not promised
        "/api/hub/events": [],
    },
    "cockpit": {
        "/api/cockpit/info": _INFO + [("project_dir", "string")],
        "/api/now": [
            ("ts", "string"), ("state", "object"),
            ("state.phase", "string|null", "optional"), ("state.type", "string|null", "optional"),
            ("state.round", "int|null", "optional"), ("state.status", "string|null", "optional"),
            ("state.turn", "string|null", "optional"), ("state.run_mode", "string|null", "optional"),
            # the one server-side sentence of Phase 68 — show it; don't re-derive it
            ("headline", "object"), ("headline.state", "string"), ("headline.tone", "string"),
            ("headline.text", "string"), ("headline.age_s", "number|null"),
            ("headline.role", "string|null"), ("headline.agent", "string|null"),
            ("watcher", "object"), ("watcher.running", "bool"), ("watcher.mode", "string|null"),
            ("watcher.beat", "object"), ("watcher.beat.state", "string"),
            ("paused", "object|null"), ("paused.reason", "string|null"),
            ("agents", "object"), ("agents.lead", "string|null"), ("agents.reviewer", "string|null"),
            ("pending_notes", "int"),
        ],
        "/api/roadmap": [
            ("current", "object|null"), ("current.phase", "string"), ("current.type", "string"),
            ("current.round", "int|null"), ("current.state", "string|null"),
            ("groups", "object"), ("groups.done", "list"), ("groups.in_progress", "list"),
            ("groups.ready", "list"), ("groups.blocked", "list"),
            ("problems", "list"), ("problems[]", "string"), ("warnings", "list"), ("warnings[]", "string"),
            ("launch", "object"), ("launch.available", "bool"), ("launch.reason", "string|null"),
        ] + _under("groups.done[]", _PHASE) + _under("groups.in_progress[]", _PHASE)
          + _under("groups.ready[]", _PHASE) + _under("groups.blocked[]", _PHASE),
        "/api/watcher/events": [
            ("events", "list"), ("events[].ts", "string"), ("events[].kind", "string"),
            ("events[].msg", "string"),
            ("events[].phase", "string|null", "optional"), ("events[].type", "string|null", "optional"),
            ("events[].round", "int|null", "optional"), ("events[].turn", "string|null", "optional"),
            ("events[].repeat", "int", "optional"), ("events[].last_ts", "string", "optional"),
        ],
        "/api/jobs": [
            ("jobs", "list"), ("jobs[].id", "string"), ("jobs[].kind", "string"), ("jobs[].label", "string"),
            ("jobs[].status", "string"), ("jobs[].shown", "string"), ("jobs[].summary", "string"),
            ("jobs[].created_at", "string"), ("jobs[].finished_at", "string|null"), ("jobs[].age_s", "int"),
            ("running", "int"),
        ],
        # SSE: an event named `change` means "re-read what you show"; the payload is not promised
        "/api/events": [],
    },
}

# The digest of STABLE, updated in the same commit as any change to STABLE. It
# is a REVIEW TRIGGER, not a compatibility check: a hash can't tell an
# additive change from a breaking one. `test_changing_the_promise_needs_a_version_decision`
# fails until it is updated, and its message states the rule (additive → update
# the digest; removal / rename / retype → bump API_VERSION and say so in
# docs/read-api.md). The decision itself is part of review.
STABLE_DIGEST = "be8c9689ed45eb4b"


def stable_endpoints(kind: str) -> list[str]:
    """The endpoint paths a server of ``kind`` (hub | cockpit) promises."""
    return sorted(STABLE[kind])


def info_fields(kind: str) -> dict:
    """What the info endpoints add: the version of the promise and its endpoints."""
    from tagteam import __version__
    return {"api_version": API_VERSION, "tagteam": __version__, "stable": stable_endpoints(kind)}


def digest() -> str:
    raw = json.dumps({"v": API_VERSION, "stable": STABLE}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

_JSON_TYPES = {"string": str, "object": dict, "list": list, "bool": bool}


def _type_ok(value, spec: str) -> bool:
    for t in spec.split("|"):
        if t == "null" and value is None:
            return True
        if t == "int" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        py = _JSON_TYPES.get(t)
        if py is not None and isinstance(value, py) and not (t != "bool" and isinstance(value, bool)):
            return True
    return False


def _tree(decls: list[tuple]) -> dict:
    """{name: node}; node = {type, optional, path, children, item}. Undeclared
    intermediate segments default to a required object (or list)."""
    root: dict = {"type": "object", "optional": False, "path": "", "children": {}, "item": None}
    for d in decls:
        path, typ = d[0], d[1]
        optional = len(d) > 2 and d[2] == "optional"
        node, walked = root, []
        segs = path.split(".")
        for i, seg in enumerate(segs):
            is_list = seg.endswith("[]")
            name = seg[:-2] if is_list else seg
            walked.append(seg)
            child = node["children"].get(name)
            if child is None:
                child = {"type": "list" if is_list else "object", "optional": False,
                         "path": ".".join(walked)[:-2] if is_list else ".".join(walked),
                         "children": {}, "item": None}
                node["children"][name] = child
            last = i == len(segs) - 1
            if is_list:
                if child["item"] is None:
                    child["item"] = {"type": "object", "optional": False, "path": ".".join(walked),
                                     "children": {}, "item": None}
                if last:
                    child["item"].update(type=typ, optional=False)
                node = child["item"]
            else:
                if last:
                    child.update(type=typ, optional=optional)
                node = child
    return root


def check(payload, kind: str, endpoint: str, seen: set | None = None) -> list[str]:
    """Problems with ``payload`` against the promise for ``endpoint``, each
    naming the path. ``seen`` collects the declared paths found PRESENT and
    non-null, so tests can prove every declaration was exercised."""
    problems: list[str] = []
    tree = _tree(STABLE[kind][endpoint])

    def walk(value, node, shown):
        if not _type_ok(value, node["type"]):
            problems.append(f"{endpoint} {shown or '<root>'}: expected {node['type']}, got {type(value).__name__}")
            return
        if value is None:
            return
        if seen is not None and node["path"]:
            seen.add(node["path"])
        if isinstance(value, dict):
            for name, child in node["children"].items():
                if name not in value:
                    if not child["optional"]:
                        problems.append(f"{endpoint} {(shown + '.' if shown else '') + name}: missing")
                    continue
                walk(value[name], child, (shown + "." if shown else "") + name)
        if isinstance(value, list) and node["item"] is not None:
            for i, el in enumerate(value):
                walk(el, node["item"], f"{shown}[{i}]")

    walk(payload, tree, "")
    return problems


def declared_paths(kind: str, endpoint: str) -> set[str]:
    return {d[0] for d in STABLE[kind][endpoint]}
