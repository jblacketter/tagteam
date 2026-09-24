"""Phase 69: the roadmap board — `roadmap.classify` / `roadmap.board`, the
dependency-aware launch intent, the board-wide launch guard, and the Roadmap
tab (the shipped slice, run in real Chromium)."""

import json
import os
import re
import threading
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import launch as L
from tagteam import roadmap as rm
from tagteam.config import read_config
from tagteam.state import write_state

from tests.test_launchpad import _cycle, _proj, fake_path  # noqa: F401  (fixture)

WEB = Path(__file__).resolve().parent.parent / "tagteam" / "data" / "web"

# document order ≠ dependency order: B depends on C
GRAPH = """# Roadmap

### Phase 1: A
- **Status:** Not started

### Phase 2: B
- **Status:** Not started
- **Depends on:** C

### Phase 3: C
- **Status:** Not started

### Phase 4: D
- **Status:** ✅ Complete

### Phase 5: E
- **Status:** In review — plan approved
"""


def _phases(tmp_path, text):
    p = tmp_path / "roadmap.md"
    p.write_text(text, encoding="utf-8")
    return rm.parse_roadmap(p)


def _groups(g):
    return {k: [e["phase"].slug for e in v] for k, v in g.items()}


# ---------------------------------------------------------------------------
# classify: exhaustive, first match wins (criteria 4, 9)
# ---------------------------------------------------------------------------

class TestClassify:
    def test_table(self, tmp_path):
        ph = _phases(tmp_path, GRAPH)
        g = _groups(rm.classify(ph))
        assert g == {"done": ["d"], "in_progress": ["e"], "ready": ["a", "c"], "blocked": ["b"]}

    def test_every_phase_exactly_once_in_every_fixture(self, tmp_path):
        states = [None, {"phase": "a", "type": "plan"}, {"phase": "c", "type": "impl"}]
        cycles = [None, {"state": "in-progress"}, {"state": "approved"}, {"state": "aborted", "round": 2}]
        for st in states:
            for cs in cycles:
                for completed in ([], ["a"], ["c", "b"]):
                    ph = _phases(tmp_path, GRAPH)
                    g = rm.classify(ph, state=st, cycle_status=cs, completed=completed)
                    slugs = [e["phase"].slug for v in g.values() for e in v]
                    assert sorted(slugs) == sorted(p.slug for p in ph), (st, cs, completed)

    def test_completed_in_the_run_with_a_stale_status_while_another_is_current(self, tmp_path):
        ph = _phases(tmp_path, GRAPH)
        g = rm.classify(ph, state={"phase": "b", "type": "plan", "status": "ready"},
                        cycle_status={"state": "in-progress"}, completed=["c"])
        by = {e["phase"].slug: (k, e) for k, v in g.items() for e in v}
        assert by["c"][0] == "done" and by["c"][1]["done_by"] == "run"
        assert by["b"][0] == "in_progress" and by["b"][1]["why"] == "current"

    def test_an_aborted_current_cycle_is_ready_with_a_note(self, tmp_path):
        ph = _phases(tmp_path, GRAPH)
        g = rm.classify(ph, state={"phase": "a", "type": "plan"}, cycle_status={"state": "aborted", "round": 3})
        a = [e for e in g["ready"] if e["phase"].slug == "a"][0]
        assert "aborted" in a["note"] and "round 3" in a["note"]

    def test_approved_impl_and_declared(self, tmp_path):
        ph = _phases(tmp_path, GRAPH)
        g = rm.classify(ph, state={"phase": "a", "type": "impl"}, cycle_status={"state": "approved"})
        whys = {e["phase"].slug: e["why"] for e in g["in_progress"]}
        assert whys == {"a": "approved", "e": "declared"}

    def test_ready_is_in_topological_order(self, tmp_path):
        text = "# R\n\n### Phase 1: X\n- **Status:** Not started\n- **Depends on:** Z\n\n" \
               "### Phase 2: Y\n- **Status:** Not started\n\n### Phase 3: Z\n- **Status:** ✅ Complete\n"
        g = rm.classify(_phases(tmp_path, text))
        assert [e["phase"].slug for e in g["ready"]] == ["x", "y"]


# ---------------------------------------------------------------------------
# the launch intent: dependency-aware; a chosen phase (criteria 1, 2, 3)
# ---------------------------------------------------------------------------

class TestIntent:
    def test_default_is_the_first_ready_phase_not_document_order(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH)
        assert L.launch_intent(p)["command"].endswith("start a")
        _cycle(p, "a", "plan", "approved")
        _cycle(p, "a", "impl", "approved")
        # the old `_next_after` offered B (next in the document) although B waits for C
        assert L.launch_intent(p)["command"].endswith("start c")

    def test_a_chosen_phase(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH)
        assert L.launch_intent(p, phase="c")["command"].endswith("start c")
        it = L.launch_intent(p, phase="b")
        assert it["command"] is None and "waits for c" in it["reason"]
        assert "in progress" in L.launch_intent(p, phase="e")["reason"]
        assert "is done" in L.launch_intent(p, phase="d")["reason"]
        assert "not a phase" in L.launch_intent(p, phase="zzz")["reason"]

    def test_plan_approved_only_implementation(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH)
        _cycle(p, "a", "plan", "approved")
        assert L.launch_intent(p, phase="c")["command"] is None
        assert "implement it first" in L.launch_intent(p, phase="c")["reason"]
        assert L.launch_intent(p, phase="a")["command"].endswith("start a impl")

    def test_graph_problems_offer_nothing(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH + "\n### Phase 6: F\n- **Status:** Not started\n- **Depends on:** Nowhere\n")
        it = L.launch_intent(p)
        assert it["command"] is None and "problems" in it["reason"]
        b = rm.board(p)
        assert b["problems"] and all(r["start"] is None or r["start"]["command"] is None
                                     for rows in b["groups"].values() for r in rows)

    def test_an_aborted_cycle_can_be_started_again(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        _cycle(p, "a", "plan", "in-progress")
        st = json.loads((p / "docs" / "handoffs" / "a_plan_status.json").read_text())
        st["state"] = "aborted"; st["ready_for"] = None
        (p / "docs" / "handoffs" / "a_plan_status.json").write_text(json.dumps(st))
        it = L.launch_intent(p, phase="a")
        assert it["command"] and it["command"].endswith("start a")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0:
                            {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        code, res = L.launch(p, intent=it, config=read_config(p / "tagteam.yaml"), by="t",
                             send=lambda: {"n": 1, "status": "ok"})
        assert code == 200 and res["launched"]


# ---------------------------------------------------------------------------
# board / CLI / payload (criteria 5, 6)
# ---------------------------------------------------------------------------

class TestBoardAndCli:
    def test_cli_and_json_match_board(self, tmp_path, monkeypatch, capsys):
        p = _proj(tmp_path, roadmap=GRAPH)
        monkeypatch.chdir(p)
        before = sorted(str(x.relative_to(p)) for x in p.rglob("*"))
        assert rm.roadmap_command(["board", "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out == json.loads(json.dumps(rm.board(p), default=str))
        assert [r["slug"] for r in out["groups"]["blocked"]] == ["b"] and out["groups"]["blocked"][0]["unmet"] == ["c"]
        assert rm.roadmap_command(["board"]) == 0
        text = capsys.readouterr().out
        assert "Up next — blocked (1)" in text and "waits for: c" in text and "handoff start c" in text
        assert sorted(str(x.relative_to(p)) for x in p.rglob("*")) == before        # a read creates nothing

    def test_read_only(self, tmp_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        assert rm.board(p)["groups"]["ready"]
        from tagteam.cli import read_only_refusal
        assert read_only_refusal(["roadmap", "board"]) is None

    def test_start_payload_ready_count(self, tmp_path, fake_path):
        p = _proj(tmp_path, roadmap=GRAPH)
        assert L.start_payload(p, read_config(p / "tagteam.yaml"))["ready_count"] == 2

    def test_missing_roadmap(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH)
        (p / "docs" / "roadmap.md").unlink()
        assert "no docs/roadmap.md" in rm.board(p)["problems"][0]


# ---------------------------------------------------------------------------
# the board-wide launch guard (criterion 10)
# ---------------------------------------------------------------------------

class TestLaunchGuard:
    def test_availability_from_the_facts(self):
        assert capi.launch_availability(".", {"inflight": {"kind": "conversation"}})["reason"].startswith("the lead is busy")
        assert capi.launch_availability(".", {"inflight": {"kind": "cycle"}})["available"] is False
        assert "a start is in progress" in capi.launch_availability(".", {"launch": {"status": "pending"}})["reason"]
        for hl in ("working", "starting", "launching"):           # incl. the 68a automatic plan→impl hand-off
            assert capi.launch_availability(".", {"headline": {"state": hl}})["available"] is False
        assert capi.launch_availability(".", {"headline": {"state": "idle"}}) == {"available": True, "reason": ""}

    def test_a_pending_start_blocks_every_other_start(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        cfg = read_config(p / "tagteam.yaml")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0:
                            {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        board0 = rm.board(p)
        it_a = [r for r in board0["groups"]["ready"] if r["slug"] == "a"][0]["start"]
        it_c = [r for r in board0["groups"]["ready"] if r["slug"] == "c"][0]["start"]
        inside, release, sends = threading.Event(), threading.Event(), []

        def slow_send():
            sends.append("a")
            inside.set()
            assert release.wait(20)
            return {"n": 1, "status": "ok"}
        res = {}
        t = threading.Thread(target=lambda: res.update(a=L.launch(p, intent=it_a, config=cfg, by="t", send=slow_send)))
        t.start()
        assert inside.wait(20)
        # the board: nothing startable while A is pending
        payload = capi.roadmap_payload(p)
        assert payload["launch"]["available"] is False
        # a stale Start for C: refused, no row, no second lead turn
        code, body = L.launch(p, intent=it_c, config=cfg, by="t", send=lambda: sends.append("c") or {"n": 1})
        assert code == 409 and "another start is in progress" in body["error"]
        from tagteam import db
        conn = db.connect(project_dir=str(p))
        try:
            rows = conn.execute("SELECT json_extract(intent_json, '$.phase') FROM launches").fetchall()
        finally:
            conn.close()
        assert [r[0] for r in rows] == ["a"]
        release.set(); t.join(20)
        assert sends == ["a"] and res["a"][0] == 200

    def test_an_abandoned_pending_row_never_blocks_forever(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        from tagteam import db
        conn = db.connect(project_dir=str(p))
        try:
            db.claim_launch(conn, key="dead", ts="2026-01-01T00:00:00+00:00",
                            intent_json=json.dumps({"phase": "a"}), owner_pid=999999, owner_ident="gone")
        finally:
            conn.close()
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0:
                            {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        it = L.launch_intent(p, phase="c")
        code, res = L.launch(p, intent=it, config=read_config(p / "tagteam.yaml"), by="t",
                             send=lambda: {"n": 1, "status": "ok"})
        assert code == 200, res
        conn = db.connect(project_dir=str(p))
        try:
            assert db.get_launch(conn, "dead")["status"] == "failed"
        finally:
            conn.close()

    def test_a_held_turn_slot_refuses_a_new_start(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        from tagteam import headless
        monkeypatch.setattr(headless, "slot_status", lambda root: {"held": True, "marker": {}, "reason": "alive"})
        code, body = L.launch(p, intent=L.launch_intent(p, phase="a"), config=read_config(p / "tagteam.yaml"),
                              by="t", send=lambda: pytest.fail("must not send"))
        assert code == 409 and "turn slot is held" in body["error"]


# ---------------------------------------------------------------------------
# the tab in REAL Chromium (criteria 6, 7, 10)
# ---------------------------------------------------------------------------

def _slice() -> str:
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    return js[js.index("// ---------- Phase 69: Roadmap board"):js.index("// ---------- end Phase 69")]


def test_the_slice_presents_and_never_derives():
    block = _slice()
    assert "innerHTML" not in block and "insertAdjacentHTML" not in block
    for derived in ("depends_on.every", "is_terminal", "unmet.length ===", "status.indexOf", "Complete"):
        assert derived not in block, derived
    assert "launch.available" in block


def test_markup_regroup():
    html = (WEB / "cockpit.html").read_text(encoding="utf-8")
    tabs = re.findall(r'data-tab="([a-z]+)"', html[html.index('<nav class="tabs"'):html.index("</nav>")])
    assert tabs == ["now", "roadmap", "rules", "history", "usage"]
    for kept in ('id="panel-feed"', 'id="panel-diff"', 'id="panel-notes"', 'id="all-activity"', 'id="notes-count"'):
        assert kept in html
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    assert "var TAB_ALIASES = { feed: 'history', diff: 'history', notes: 'now', lead: 'now' };" in js
    assert "cardShell('start'" not in js                      # the Start card is gone


_BOARD = {
    "problems": [], "warnings": [],
    "groups": {
        "in_progress": [{"slug": "e", "number": "5", "name": "E", "status": "In review", "depends_on": [], "unmet": [],
                         "why": "declared", "done_by": None, "note": None, "start": None}],
        "ready": [{"slug": "a", "number": "1", "name": "A", "status": "Not started", "depends_on": [], "unmet": [],
                   "why": None, "done_by": None, "note": None,
                   "start": {"phase": "a", "type": "plan", "command": "/h start a", "observed": {}}},
                  {"slug": "c", "number": "3", "name": "C", "status": "Not started", "depends_on": [], "unmet": [],
                   "why": None, "done_by": None, "note": "last cycle aborted (plan, round 2)",
                   "start": {"phase": "c", "type": "plan", "command": "/h start c", "observed": {}}}],
        "blocked": [{"slug": "b", "number": "2", "name": "B", "status": "Not started", "depends_on": ["c"],
                     "unmet": ["c"], "why": None, "done_by": None, "note": None, "start": None}],
        "done": [{"slug": "d", "number": "4", "name": "D", "status": "Not started", "depends_on": [], "unmet": [],
                  "why": None, "done_by": "run", "note": None, "start": None}]},
    "launch": {"available": True, "reason": ""},
}


def _run_board_in_chromium(scenario_js: str) -> dict:
    import html as _html
    import subprocess
    import tempfile
    from tests.test_cockpit_activity import _find_chromium
    chrome = _find_chromium()
    if not chrome:
        pytest.skip("no Chromium/Chrome found (set TAGTEAM_TEST_CHROME) — the Roadmap tab test needs one")
    page_html = (WEB / "cockpit.html").read_text(encoding="utf-8")
    css = (WEB / "cockpit.css").read_text(encoding="utf-8")
    panel = page_html[page_html.index('<div class="panel" id="panel-roadmap"'):page_html.index('<div class="panel" id="panel-history"')]
    panel = panel.replace('class="panel" id="panel-roadmap"', 'class="panel active" id="panel-roadmap"')
    shims = r"""
      function $(id) { return document.getElementById(id); }
      function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
      function typeWord(t) { return t === 'impl' ? 'implementation' : 'plan'; }
      function plainError(e) { return String(e || ''); }
      function toast() {}
      var POSTS = [], NOW = { agents: { lead: 'claude' } }, LEAD = {}, START = { headless: { ok: true } };
      function loadLead() {}
      function getJSON() { return Promise.resolve({ ok: true, body: PAYLOAD }); }
      function act(btn, url, data, opts) { POSTS.push({ url: url, data: data, title: opts.confirm.title }); }
    """
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + css + "</style></head><body>"
            + "<main class='main' style='width:900px'>" + panel + "</main>"
            + "<pre id='RESULT'></pre><script>var PAYLOAD = " + json.dumps(_BOARD) + ";" + shims
            + _slice() + "\n" + scenario_js
            + "\ndocument.getElementById('RESULT').textContent = JSON.stringify(RESULT);</script></body></html>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "board.html"
        f.write_text(page, encoding="utf-8")
        r = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            "--window-size=1000,1400", f"--user-data-dir={d}/profile", "--dump-dom", f.as_uri()],
                           capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    m = re.search(r"<pre id=\"RESULT\">(.*?)</pre>", r.stdout, re.S)
    assert m and m.group(1).strip(), "the page produced no RESULT — a script error? " + r.stderr[-1500:]
    return json.loads(_html.unescape(m.group(1)))


class TestRoadmapTabInARealBrowser:
    def test_groups_rows_and_starts(self):
        r = _run_board_in_chromium(r"""
          renderRoadmap(PAYLOAD);
          function rows(g) { return Array.prototype.map.call(document.querySelectorAll('.rm-group[data-group="' + g + '"] .rm-row'),
                             function (x) { return x.dataset.slug + ':' + Array.prototype.map.call(x.querySelectorAll('button'), function (b) { return b.textContent; }).join('|'); }); }
          var RESULT = { order: Array.prototype.map.call(document.querySelectorAll('.rm-group'), function (g) { return g.dataset.group; }),
                         ready: rows('ready'), blocked: rows('blocked'), inprog: rows('in_progress'),
                         doneTag: document.querySelector('.rm-group[data-group="done"]').tagName,
                         meta: Array.prototype.map.call(document.querySelectorAll('.rm-meta'), function (m) { return m.textContent; }),
                         guardHidden: $('roadmap-guard').classList.contains('hidden') };
          document.querySelector('.rm-row[data-slug="c"] .rm-start').click();
          RESULT.posts = POSTS;
          RESULT.overflow = document.documentElement.scrollWidth > window.innerWidth;
        """)
        assert r["order"] == ["in_progress", "ready", "blocked", "done"] and r["doneTag"] == "DETAILS"
        assert r["ready"] == ["a:Start|Copy command", "c:Start|Copy command"]
        assert r["blocked"] == ["b:"] and r["inprog"] == ["e:"]
        assert any("waits for: c" in m for m in r["meta"])
        assert any("change its status to start it again" in m for m in r["meta"])
        assert any("completed in this run — the roadmap still says: Not started" in m for m in r["meta"])
        assert any("last cycle aborted" in m for m in r["meta"])
        assert r["guardHidden"] is True
        assert r["posts"] == [{"url": "/api/start/launch", "title": "Start c — plan?",
                               "data": {"intent": {"phase": "c", "type": "plan", "command": "/h start c", "observed": {}},
                                        "ensure_watcher": True}}]
        assert r["overflow"] is False

    def test_no_start_anywhere_while_the_guard_is_down(self):
        r = _run_board_in_chromium(r"""
          PAYLOAD.launch = { available: false, reason: 'a start is in progress — wait for it to finish' };
          PAYLOAD.groups.in_progress.push({ slug: 'a', number: '1', name: 'A', status: 'Not started', depends_on: [], unmet: [],
            why: 'current', start: { phase: 'a', type: 'impl', command: '/h start a impl', observed: {} } });
          renderRoadmap(PAYLOAD);
          var RESULT = { starts: document.querySelectorAll('.rm-start').length,
                         guard: $('roadmap-guard').textContent,
                         reasons: Array.prototype.map.call(document.querySelectorAll('.rm-guard'), function (x) { return x.textContent; }) };
        """)
        assert r["starts"] == 0
        assert r["guard"] == "No Start right now: a start is in progress — wait for it to finish"
        assert len(r["reasons"]) == 3                       # a, c and the in-progress "Start implementation" slot


# ---------------------------------------------------------------------------
# impl r1 review
# ---------------------------------------------------------------------------

def _abort(p, phase, ctype):
    f = p / "docs" / "handoffs" / f"{phase}_{ctype}_status.json"
    st = json.loads(f.read_text()); st["state"] = "aborted"; st["ready_for"] = None
    f.write_text(json.dumps(st))


class TestReviewR1:
    @pytest.mark.parametrize("status", ["In progress", "In review — plan cycle open", "✅ Approved — awaiting merge"])
    def test_an_aborted_current_cycle_beats_a_declared_status(self, tmp_path, fake_path, monkeypatch, status):
        p = _proj(tmp_path, roadmap=GRAPH.replace("### Phase 1: A\n- **Status:** Not started",
                                                  f"### Phase 1: A\n- **Status:** {status}"))
        _cycle(p, "a", "plan", "in-progress"); _abort(p, "a", "plan")
        b = rm.board(p)
        a = [r for r in b["groups"]["ready"] if r["slug"] == "a"]
        assert a and "aborted" in a[0]["note"] and a[0]["start"]["command"].endswith("start a")
        monkeypatch.setattr(L, "start_watcher", lambda root, mode="headless", wait_s=5.0:
                            {"ok": True, "pid": os.getpid(), "mode": mode, "message": "fake"})
        code, res = L.launch(p, intent=a[0]["start"], config=read_config(p / "tagteam.yaml"), by="t",
                             send=lambda: {"n": 1, "status": "ok"})
        assert code == 200 and res["launched"]

    def test_an_aborted_current_cycle_with_unmet_dependencies_is_blocked(self, tmp_path):
        p = _proj(tmp_path, roadmap=GRAPH.replace("### Phase 2: B\n- **Status:** Not started",
                                                  "### Phase 2: B\n- **Status:** In progress"))
        _cycle(p, "b", "plan", "in-progress"); _abort(p, "b", "plan")
        b = rm.board(p)
        blocked = [r for r in b["groups"]["blocked"] if r["slug"] == "b"]
        assert blocked and blocked[0]["unmet"] == ["c"] and "aborted" in blocked[0]["note"]
        assert "waits for c" in L.launch_intent(p, phase="b")["reason"]

    def test_an_invalid_roadmap_refuses_start_implementation_too(self, tmp_path, fake_path, monkeypatch):
        p = _proj(tmp_path, roadmap=GRAPH)
        _cycle(p, "a", "plan", "approved")
        stale = L.launch_intent(p)
        assert stale["command"].endswith("start a impl")
        (p / "docs" / "roadmap.md").write_text(GRAPH + "\n### Phase 6: F\n- **Status:** Not started\n- **Depends on:** Nowhere\n")
        b = rm.board(p)
        assert b["problems"] and sum(len(v) for v in b["groups"].values()) == 6        # groups still shown
        assert all(not (r.get("start") or {}).get("command") for v in b["groups"].values() for r in v)
        sends = []
        monkeypatch.setattr(L, "start_watcher", lambda *a, **k: pytest.fail("must not start a watcher"))
        code, res = L.launch(p, intent=stale, config=read_config(p / "tagteam.yaml"), by="t",
                             send=lambda: sends.append(1) or {"n": 1})
        assert code == 409 and sends == []
        from tagteam import db
        conn = db.connect(project_dir=str(p))
        try:
            assert conn.execute("SELECT COUNT(*) FROM launches").fetchone()[0] == 0
        finally:
            conn.close()

    def test_what_the_reader_opened_survives_a_refresh(self):
        r = _run_board_in_chromium(r"""
          PAYLOAD.current = { phase: 'a', type: 'plan', round: 2, state: 'in-progress' };
          PAYLOAD.groups.in_progress.push({ slug: 'a', number: '1', name: 'A', why: 'current', depends_on: [], unmet: [],
            status: 'In review — ' + new Array(30).join('a long status line '), start: { command: null, reason: 'a cycle is in progress' } });
          renderRoadmap(PAYLOAD);
          document.querySelector('.rm-group[data-group="done"]').open = true;
          document.querySelector('.rm-group[data-group="done"]').dispatchEvent(new Event('toggle'));
          var sd = document.querySelector('.rm-row[data-slug="a"] .rm-status-full');
          sd.open = true; sd.dispatchEvent(new Event('toggle'));
          renderRoadmap(PAYLOAD);                                            // SSE / the live tick
          var RESULT = {
            doneOpen: document.querySelector('.rm-group[data-group="done"]').open,
            statusOpen: document.querySelector('.rm-row[data-slug="a"] .rm-status-full').open,
            fullText: document.querySelector('.rm-row[data-slug="a"] .rm-status-text').textContent.length > 100,
            cycle: document.querySelector('.rm-row[data-slug="a"] .rm-cycle').textContent };
        """)
        assert r == {"doneOpen": True, "statusOpen": True, "fullText": True, "cycle": "plan · round 2 · in-progress"}

    def test_done_starts_collapsed(self):
        r = _run_board_in_chromium(r"""
          renderRoadmap(PAYLOAD);
          var RESULT = { open: document.querySelector('.rm-group[data-group="done"]').open };
        """)
        assert r == {"open": False}
