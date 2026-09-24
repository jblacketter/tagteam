"""Phase 74: the versioned read API other dashboards may rely on.

Every declared path is checked against REAL payloads, built through the same
functions the servers call, across the states that matter. The union of what
the fixtures exercised must cover every declared path, so a nested
declaration can't pass just because its parent was empty or null. The
declaration is pinned to its version (a review trigger) and to
docs/read-api.md.
"""

import copy
import json
import os
import re
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import cycle as cycle_mod
from tagteam import db, headless as h, hub_api, jobs, read_api, watchlog
from tagteam import watcher as watcher_mod
from tagteam.state import write_state

REPO = Path(__file__).resolve().parent.parent
YAML = "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\n"

ROADMAP = """# Roadmap

## Phases
### Phase 1: Base
- **Status:** ✅ Complete
- **Description:** done.

### Phase 2: Current
- **Status:** In progress
- **Description:** being worked on.
- **Depends on:** Phase 1

### Phase 3: Next
- **Status:** Not started
- **Description:** ready.
- **Depends on:** Phase 1

### Phase 4: Later
- **Status:** Not started
- **Description:** blocked on 3.
- **Depends on:** Phase 3

### Phase five without a colon
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)
    monkeypatch.setenv("TAGTEAM_NO_NOTIFY", "1")


def _proj(root: Path, name: str) -> Path:
    d = root / name
    (d / "docs" / "handoffs").mkdir(parents=True)
    (d / ".tagteam").mkdir()
    (d / "tagteam.yaml").write_text(YAML)
    return d


def _fixtures(tmp_path) -> dict[str, Path]:
    """The states that matter, one project each."""
    out = {}
    out["bare"] = _proj(tmp_path, "bare")

    d = out["reviewer_owed"] = _proj(tmp_path, "owed")
    (d / "docs" / "roadmap.md").write_text(ROADMAP)
    cycle_mod.init_cycle("current", "plan", "Claude", "Codex", "first", str(d), updated_by="Claude")
    st = json.loads((d / "handoff-state.json").read_text())
    st["run_mode"] = "single-phase"
    write_state(st, str(d))
    sink = watchlog.Sink(d, "headless", 10)
    for _ in range(2):                                   # folded into one row with `repeat`
        sink.event(">> Codex's turn", kind="turn", phase="current", type="plan", round=1, turn="reviewer")
    sink.beat(st, force=True)
    watcher_mod.write_pidfile(d, "headless")
    jid = jobs.create(d, "ci-watch", {"pr": 7}, interval_s=5, timeout_s=60, to_lead=False, quiet=True, by="t")
    jobs.update_record(d, jid, lambda r: dict(r, status="running"))
    jobs._commit(d, jid, "succeeded", {"summary": "PR #7: 2/2 checks passed"})
    conn = db.connect(project_dir=str(d))
    try:
        db.add_usage(conn, ts="2026-09-24T00:00:00+00:00", status="ok", phase="current", type="plan", round=1,
                     role="reviewer", agent="Codex", input_tokens=1000, output_tokens=200, cost_usd=0.12)
    finally:
        conn.close()

    d = out["approved"] = _proj(tmp_path, "approved")
    cycle_mod.init_cycle("feat", "impl", "Claude", "Codex", "first", str(d), updated_by="Claude")
    cycle_mod.add_round("feat", "impl", "reviewer", "APPROVE", 1, "ok", str(d), updated_by="Codex")

    d = out["escalated"] = _proj(tmp_path, "escalated")
    cycle_mod.init_cycle("feat", "plan", "Claude", "Codex", "first", str(d), updated_by="Claude")
    cycle_mod.add_round("feat", "plan", "reviewer", "ESCALATE", 1, "stuck", str(d), updated_by="Codex")

    d = out["paused"] = _proj(tmp_path, "paused")
    cycle_mod.init_cycle("feat", "plan", "Claude", "Codex", "first", str(d), updated_by="Claude")
    h.write_pause(d, {"reason": "reviewing by hand", "by": "jack", "source": "cli",
                      "ts": "2026-09-24T00:00:00+00:00", "log_path": None})
    return out


_GROUP = re.compile(r"^groups\.[a-z_]+\[\]\.")


def _row_shape(paths: set) -> set:
    """Rows are ONE declaration applied to several group lists (the doc shows
    each row table once), so coverage is per row shape: a path under any
    `groups.<g>[]` counts for all of them. A `ready` phase can't have an unmet
    dependency, but the phase row's `unmet[]` is exercised by a blocked one."""
    return {_GROUP.sub("groups.*[].", x) for x in paths}


COCKPIT = {"/api/now": capi.now_payload, "/api/roadmap": capi.roadmap_payload,
           "/api/watcher/events": lambda d: capi.watcher_events_payload(d, 50, chatter=False),
           "/api/jobs": jobs.jobs_payload}


class TestEveryDeclaredPathAgainstRealPayloads:
    def test_cockpit_endpoints_in_every_state_and_every_path_exercised(self, tmp_path):
        fx = _fixtures(tmp_path)
        seen: dict[str, set] = {ep: set() for ep in COCKPIT}
        for name, d in fx.items():
            for ep, build in COCKPIT.items():
                problems = read_api.check(build(d), "cockpit", ep, seen[ep])
                assert problems == [], (name, problems)
        for ep in COCKPIT:
            missing = _row_shape(read_api.declared_paths("cockpit", ep)) - _row_shape(seen[ep])
            assert not missing, f"{ep}: declared but never seen present in a fixture: {sorted(missing)}"

    def test_hub_payload_with_every_group_and_every_path_exercised(self, tmp_path):
        fx = _fixtures(tmp_path)
        paths = [str(p) for p in fx.values()] + [str(tmp_path / "gone")]     # a missing dir → hidden
        p = hub_api.hub_payload(paths, procs_snapshot=[], scratch_prefixes=())
        assert p["groups"]["needs_you"] and p["groups"]["waiting"] and p["groups"]["quiet"] and p["groups"]["hidden"]
        owed = next(r for r in p["groups"]["waiting"] if r["name"] == "owed")
        assert owed["usage"]["turns"] == 1 and owed["usage"]["cost_usd"] == pytest.approx(0.12)
        seen: set = set()
        assert read_api.check(p, "hub", "/api/hub", seen) == []
        missing = _row_shape(read_api.declared_paths("hub", "/api/hub")) - _row_shape(seen)
        assert not missing, sorted(missing)


class TestAnUnreadableProjectInTheHub:
    def test_a_malformed_project_beside_a_healthy_one_satisfies_the_contract(self, tmp_path):
        """impl review r1: an unreadable project is isolated into a row with
        `error` and `watcher: null` (unknown) — the whole payload still holds."""
        good = _proj(tmp_path, "good")
        cycle_mod.init_cycle("feat", "plan", "Claude", "Codex", "first", str(good), updated_by="Claude")
        bad = _proj(tmp_path, "bad")
        (bad / "handoff-state.json").write_text("{broken")
        p = hub_api.hub_payload([str(good), str(bad)], procs_snapshot=[], scratch_prefixes=())
        rows = {r["name"]: r for g in ("needs_you", "waiting", "quiet") for r in p["groups"][g]}
        assert rows["bad"]["error"] and rows["bad"]["watcher"] is None
        assert isinstance(rows["good"]["watcher"], dict)
        assert read_api.check(p, "hub", "/api/hub") == []


class TestTheCheck:
    def _now(self, tmp_path):
        import uuid
        base = tmp_path / uuid.uuid4().hex[:8]
        base.mkdir()
        return capi.now_payload(_fixtures(base)["reviewer_owed"])

    def test_a_renamed_field_fails_with_its_path(self, tmp_path):
        p = self._now(tmp_path)
        p["headline"]["words"] = p["headline"].pop("text")
        assert read_api.check(p, "cockpit", "/api/now") == ["/api/now headline.text: missing"]

    def test_a_retyped_field_fails_with_its_path(self, tmp_path):
        p = self._now(tmp_path)
        p["pending_notes"] = "0"
        assert read_api.check(p, "cockpit", "/api/now") == ["/api/now pending_notes: expected int, got str"]
        p = self._now(tmp_path)
        p["pending_notes"] = True                        # a bool is not an int
        assert read_api.check(p, "cockpit", "/api/now")

    def test_optional_absent_versus_nullable_parent(self, tmp_path):
        p = self._now(tmp_path)
        p["state"] = {}                                  # optional keys may be absent
        p["paused"] = None                               # a nullable parent ends the check below it
        assert read_api.check(p, "cockpit", "/api/now") == []
        p["paused"] = {}                                 # present parent: its required children are checked
        assert read_api.check(p, "cockpit", "/api/now") == ["/api/now paused.reason: missing"]
        p = self._now(tmp_path)
        del p["paused"]                                  # nullable is not optional
        assert read_api.check(p, "cockpit", "/api/now") == ["/api/now paused: missing"]

    def test_list_elements_are_each_checked(self):
        p = {"jobs": [{"id": "x"}], "running": 0}
        problems = read_api.check(p, "cockpit", "/api/jobs")
        assert "/api/jobs jobs[0].kind: missing" in problems and len(problems) == 8

    def test_hidden_entries_have_their_own_smaller_shape(self):
        p = {"ts": "t", "groups": {"needs_you": [], "waiting": [], "quiet": [],
                                   "hidden": [{"id": "a", "path": "/x", "kind": "missing"}]}}
        assert read_api.check(p, "hub", "/api/hub") == []


class TestThePromiseIsPinned:
    RULE = ("STABLE changed. Additive (a new path) → update STABLE_DIGEST in the same commit. "
            "Removal, rename or retype → bump API_VERSION, update STABLE_DIGEST, and write the change "
            "in docs/read-api.md. The decision is part of review.")

    def test_changing_the_promise_needs_a_version_decision(self):
        assert read_api.digest() == read_api.STABLE_DIGEST, self.RULE + f" (digest now {read_api.digest()})"
        assert read_api.API_VERSION == 1

    def test_the_digest_trips_on_an_addition_and_on_a_removal(self, monkeypatch):
        base = read_api.digest()
        added = copy.deepcopy(read_api.STABLE)
        added["cockpit"]["/api/now"].append(("new_field", "string"))
        monkeypatch.setattr(read_api, "STABLE", added)
        assert read_api.digest() != base              # additive: digest update, no bump
        removed = copy.deepcopy(read_api.STABLE)
        removed["cockpit"]["/api/now"] = [d for d in removed["cockpit"]["/api/now"] if d[0] != "headline.text"]
        monkeypatch.setattr(read_api, "STABLE", removed)
        assert read_api.digest() != base              # removal: caught; the bump is the reviewer's call

    def test_the_doc_lists_exactly_the_declaration(self):
        doc = (REPO / "docs" / "read-api.md").read_text(encoding="utf-8")
        got: dict[str, set] = {}
        endpoint, prefixes = None, [""]
        for line in doc.splitlines():
            m = re.match(r"^### `GET (/api/[^`]+)`", line)
            if m:
                endpoint, prefixes = m.group(1), [""]
                got.setdefault(endpoint, set())
                continue
            m = re.match(r"^#### .*: each element of (.+)$", line)
            if m:
                prefixes = [p + "." for p in re.findall(r"`([^`]+)`", m.group(1))]
                continue
            m = re.match(r"^\| `([^`]+)` \| `([^`]+)` \| *(optional)? *\|$", line)
            if m and endpoint:
                typ = m.group(2).replace(" or ", "|")
                for pre in prefixes:
                    got[endpoint].add((pre + m.group(1), typ, bool(m.group(3))))
        want = {ep: {(d[0], d[1], len(d) > 2) for d in decls}
                for kind in read_api.STABLE for ep, decls in read_api.STABLE[kind].items()}
        assert got == want
        for words in ("No CORS", "is an estimate", "additive only", "ignore fields you don't know"):
            assert words in doc, words


class TestServed:
    def test_the_cockpit_info_endpoint(self, tmp_path):
        from tests.test_server_cockpit import Served
        d = _fixtures(tmp_path)["reviewer_owed"]
        with Served(d, "cockpit") as s:
            info = s.client.get("/api/cockpit/info")["json"]
        assert info["api_version"] == 1 and info["stable"] == read_api.stable_endpoints("cockpit")
        from tagteam import __version__
        assert info["tagteam"] == __version__
        assert read_api.check(info, "cockpit", "/api/cockpit/info") == []
        assert set(info["stable"]) == {"/api/cockpit/info", "/api/now", "/api/roadmap", "/api/watcher/events",
                                       "/api/jobs", "/api/events"}

    def test_the_hub_info_and_a_mounted_now_equals_the_projects_own(self, tmp_path):
        from tests.test_hub_server import HubServed
        from tests.test_server_cockpit import Served
        d = _fixtures(tmp_path)["reviewer_owed"]
        reg = tmp_path / "projects.json"
        reg.write_text(json.dumps([str(d)]))
        pid = hub_api.project_id(str(d))
        with HubServed(reg) as hub_s, Served(d, "cockpit") as own:
            mounted = hub_s.client.get(f"/p/{pid}/api/now")["json"]
            info = hub_s.client.get("/api/hub/info")["json"]
            alias = hub_s.client.get("/api/info")["json"]
            direct = own.client.get("/api/now")["json"]
        assert info["api_version"] == 1 and info["stable"] == read_api.stable_endpoints("hub")
        assert info["mounted"] == [pid] and alias["api_version"] == 1
        assert read_api.check(info, "hub", "/api/hub/info") == []
        assert _promised(mounted, "/api/now") == _promised(direct, "/api/now")


_CLOCKED = re.compile(r"(^|\.)(ts|age_s)$")


def _promised(payload, endpoint) -> dict:
    """The promised leaf values, clock-dependent ones (ts, *age_s) frozen."""
    out = {}
    for d in read_api.STABLE["cockpit"][endpoint]:
        if d[1].split("|")[0] in ("object", "list"):
            continue                                     # compare leaves; containers hold clocked fields
        cur = payload
        for seg in d[0].split("."):
            cur = cur.get(seg) if isinstance(cur, dict) else None
        out[d[0]] = "<clock>" if _CLOCKED.search(d[0]) else json.dumps(cur, sort_keys=True)
    return out
