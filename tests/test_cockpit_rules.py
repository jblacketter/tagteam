"""Phase 71: the cockpit's Rules tab — `GET /api/rules`, `POST /api/orders`,
and the tab itself (the shipped markup/CSS/JS slice, run in real Chromium)."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tagteam import cockpit_api as capi
from tagteam import orders
from tagteam.config import resolve_watcher
from tagteam.state import read_state, write_state

from tests.test_server_cockpit import Served

WEB = Path(__file__).resolve().parent.parent / "tagteam" / "data" / "web"

BASE = "agents:\n  lead:\n    name: claude\n  reviewer:\n    name: codex\n"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "docs" / "handoffs").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TAGTEAM_READ_ONLY", raising=False)
    monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
    return tmp_path


def _cfg(p: Path, extra: str = "") -> None:
    (p / "tagteam.yaml").write_text(BASE + extra, encoding="utf-8")


def _rows(payload) -> dict:
    return {r["key"]: r for r in payload["enforced"]}


def _o(p: Path, *args) -> int:
    return orders.orders_command(list(args), project_root=p)


# ---------------------------------------------------------------------------
# The payload: engine rows describe what RUNS (criterion 8)
# ---------------------------------------------------------------------------

class TestEngineRows:
    def test_every_rule_is_there_with_text_source_and_applies(self, proj):
        _cfg(proj)
        rows = _rows(capi.rules_payload(proj))
        assert list(rows) == ["stop", "gate", "panel", "briefer", "resend", "stale"]
        for r in rows.values():
            assert r["label"] and r["text"] and r["source"] and r["applies"]

    def test_invalid_gate_block_is_off_even_when_enabled(self, proj):
        _cfg(proj, "gatekeeper:\n  enabled: true\n  scope: invalid\n")
        p = capi.rules_payload(proj)
        gate = _rows(p)["gate"]
        assert gate["on"] is False and gate["value"] == "off" and "OFF" in gate["text"]
        assert any(w.startswith("gatekeeper:") for w in p["warnings"])

    def test_watcher_block_with_an_unknown_key_uses_the_default(self, proj):
        _cfg(proj, "watcher:\n  resend_minutes: 3\n  bogus: 1\n")
        rows = _rows(capi.rules_payload(proj))
        assert rows["resend"]["value"] == "15 min" and rows["resend"]["source"] == "default"
        # …which is exactly what the watcher itself resolves (one shared resolver)
        from tagteam.config import read_config
        assert resolve_watcher(read_config(proj / "tagteam.yaml"))[0] == 15

    def test_valid_watcher_block(self, proj):
        _cfg(proj, "watcher:\n  resend_minutes: 0\n")
        r = _rows(capi.rules_payload(proj))["resend"]
        assert r["value"] == "never" and r["on"] is False and r["source"] == "tagteam.yaml watcher"

    def test_panel_with_a_missing_lens_brief_is_off(self, proj):
        _cfg(proj, "panel:\n  enabled: true\n  lenses:\n    - name: nosuchlens\n    - name: scope\n")
        p = capi.rules_payload(proj)
        assert _rows(p)["panel"]["on"] is False
        assert any("nosuchlens" in w for w in p["warnings"])

    def test_briefer_without_a_resolvable_provider_is_off(self, proj):
        (proj / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: someone\n  reviewer:\n    name: other\nbriefer:\n  enabled: true\n")
        p = capi.rules_payload(proj)
        assert _rows(p)["briefer"]["on"] is False
        assert any(w.startswith("briefer:") for w in p["warnings"])

    def test_an_invalid_block_never_hides_a_valid_one(self, proj):
        _cfg(proj, "gatekeeper:\n  enabled: true\n  scope: invalid\nwatcher:\n  resend_minutes: 7\n")
        rows = _rows(capi.rules_payload(proj))
        assert rows["gate"]["on"] is False
        assert rows["resend"]["value"] == "7 min" and rows["resend"]["source"] == "tagteam.yaml watcher"

    def test_gate_prose_follows_the_resolved_spec(self, proj):
        _cfg(proj, "gatekeeper:\n  enabled: true\n  on_submit: true\n  tests:\n    command: \"pytest -q --secret-flag\"\n")
        g = _rows(capi.rules_payload(proj))["gate"]
        assert "implementation reviews only" in g["value"] and "inside the lead's submission" in g["text"]
        assert "the test suite" in g["text"] and g["applies"] == "each submission"
        assert "pytest" not in json.dumps(capi.rules_payload(proj))          # never the command
        _cfg(proj, "gatekeeper:\n  enabled: true\n  on: [plan, impl]\n  scope: false\n")
        g = _rows(capi.rules_payload(proj))["gate"]
        assert "every review" in g["value"] and "no tests (no test command configured)" in g["text"]
        assert "no scope check" in g["text"] and "in the watcher" in g["text"]
        assert g["applies"] == "when the watcher starts"

    @pytest.mark.parametrize("block, key", [
        ("panel: invalid\n", "panel"), ("panel:\n  enabled: \"true\"\n", "panel"),
        ("briefer: invalid\n", "briefer"), ("briefer:\n  enabled: \"yes\"\n", "briefer")])
    def test_malformed_panel_or_briefer_is_a_warning_even_when_not_enabled(self, proj, block, key):
        """impl r1 review: a malformed mapping or enable value is shown, beside a valid row."""
        _cfg(proj, block + "watcher:\n  resend_minutes: 7\n")
        p = capi.rules_payload(proj)
        rows = _rows(p)
        assert rows[key]["on"] is False and rows[key]["warnings"]
        assert any(w.startswith(key + ":") for w in p["warnings"])
        assert rows["resend"]["value"] == "7 min"

    @pytest.mark.parametrize("block", ["", "panel:\n  enabled: false\n", "briefer:\n  enabled: false\n"])
    def test_absent_or_valid_disabled_blocks_are_quiet(self, proj, block):
        _cfg(proj, block)
        assert capi.rules_payload(proj)["warnings"] == []

    def test_unreadable_config_is_a_warning_and_defaults(self, proj):
        (proj / "tagteam.yaml").write_text("agents: [unclosed\n")
        p = capi.rules_payload(proj)
        assert any("tagteam.yaml could not be read" in w for w in p["warnings"])
        assert _rows(p)["gate"]["on"] is False


# ---------------------------------------------------------------------------
# Saved vs effective stop (criterion 9)
# ---------------------------------------------------------------------------

class TestStop:
    def _state(self, p, **kw):
        write_state({"phase": "a", "type": "plan", "status": "ready", "turn": "reviewer",
                     "run_mode": "single-phase", **kw}, str(p))

    def test_a_project_phase_run_roadmap(self, proj):
        self._state(proj)
        _o(proj, "stop", "phase")
        _o(proj, "stop", "roadmap", "--run")
        s = capi.rules_payload(proj)["stop"]
        assert (s["effective"], s["source"], s["project"], s["run"]) == ("roadmap", "run", "phase", "roadmap")
        assert s["project_shadowed_by"] == "run" and "applies from the next run" in s["shadow_note"]
        _o(proj, "stop", "roadmap")                      # the project edit is shown as SAVED
        s = capi.rules_payload(proj)["stop"]
        assert s["project"] == "roadmap" and s["project_shadowed_by"] is None

    def test_b_full_roadmap_run_without_a_stop_override(self, proj):
        self._state(proj, run_mode="full-roadmap", roadmap={"queue": ["a"], "current_index": 0, "completed": []})
        _o(proj, "stop", "phase")
        s = capi.rules_payload(proj)["stop"]
        assert (s["effective"], s["source"], s["project"], s["run"]) == ("roadmap", "run-mode", "phase", None)
        assert s["run_mode_roadmap"] is True and s["project_shadowed_by"] == "run-mode"
        assert "full-roadmap run" in s["effective_text"]

    def test_c_unsetting_the_run_stop_keeps_the_run_notes(self, proj):
        self._state(proj)
        _o(proj, "stop", "roadmap", "--run")
        _o(proj, "add", "open the PR", "--run")
        res = capi.run_action("orders", {"op": "stop", "value": "unset", "run": True}, proj, by="web:jack")
        assert res["ok"], res
        p = capi.rules_payload(proj)
        assert p["stop"]["run"] is None and p["stop"]["has_run_override"] is True
        assert [(n["scope"], n["text"]) for n in p["advisory"]] == [("run", "open the PR")]

    def test_d_clear_run_removes_stop_and_notes(self, proj):
        self._state(proj)
        _o(proj, "stop", "roadmap", "--run")
        _o(proj, "add", "open the PR", "--run")
        assert capi.run_action("orders", {"op": "clear-run"}, proj, by="web:jack")["ok"]
        p = capi.rules_payload(proj)
        assert p["stop"]["has_run_override"] is False and p["advisory"] == []
        assert "orders" not in read_state(str(proj))

    def test_malformed_orders_file_locks_project_editing(self, proj):
        (proj / orders.ORDERS_FILE).write_text("{nope")
        p = capi.rules_payload(proj)
        assert p["project_orders_ok"] is False
        assert any("not valid JSON" in w for w in p["warnings"])


# ---------------------------------------------------------------------------
# Configured vs running (criterion 10)
# ---------------------------------------------------------------------------

class TestWatcherStaleConfig:
    def _beat(self, p, started):
        (p / ".tagteam").mkdir(exist_ok=True)
        now = datetime.now(timezone.utc)
        (p / ".tagteam" / "watcher-beat.json").write_text(json.dumps(
            {"pid": os.getpid(), "mode": "headless", "started_at": started.isoformat(),
             "ts": now.isoformat(), "every_s": 2}))

    def test_config_changed_after_the_watcher_started(self, proj):
        _cfg(proj)
        self._beat(proj, datetime.now(timezone.utc) - timedelta(hours=1))
        sc = capi.rules_payload(proj)["watcher_stale_config"]
        assert sc and sc["config_mtime"] > sc["started_at"]

    def test_watcher_started_after_the_config(self, proj):
        _cfg(proj)
        self._beat(proj, datetime.now(timezone.utc) + timedelta(minutes=1))
        assert capi.rules_payload(proj)["watcher_stale_config"] is None

    def test_no_running_watcher(self, proj):
        _cfg(proj)
        assert capi.rules_payload(proj)["watcher_stale_config"] is None


# ---------------------------------------------------------------------------
# File-only, read-only (criterion 2)
# ---------------------------------------------------------------------------

class TestReadOnlyPayload:
    def test_creates_nothing_and_works_read_only(self, proj, monkeypatch):
        _cfg(proj)
        before = sorted(str(x.relative_to(proj)) for x in proj.rglob("*"))
        monkeypatch.setenv("TAGTEAM_READ_ONLY", "1")
        p = capi.rules_payload(proj)
        assert p["enforced"]
        assert sorted(str(x.relative_to(proj)) for x in proj.rglob("*")) == before


# ---------------------------------------------------------------------------
# The orders action (criterion 3) — through the real server
# ---------------------------------------------------------------------------

class TestOrdersAction:
    def test_plan_maps_every_op(self):
        by = "web:jack"
        assert capi._plan("orders", {"op": "stop", "value": "roadmap"}, by=by)[1] == ["stop", "roadmap", "--by", by]
        assert capi._plan("orders", {"op": "stop", "value": "unset", "run": True}, by=by)[1] == \
            ["stop", "--unset", "--run", "--by", by]
        assert capi._plan("orders", {"op": "add", "text": " hold the PR ", "run": True}, by=by)[1] == \
            ["add", "hold the PR", "--run", "--by", by]
        assert capi._plan("orders", {"op": "remove", "id": "2"}, by=by)[1] == ["remove", "2"]
        assert capi._plan("orders", {"op": "clear-run"}, by=by)[1] == ["clear", "--run"]

    @pytest.mark.parametrize("params", [
        {"op": "stop", "value": "sometimes"}, {"op": "add", "text": ""}, {"op": "add", "text": "two\nlines"},
        {"op": "add", "text": "x" * 501}, {"op": "remove", "id": "x"}, {"op": "nope"}, {},
        {"op": "stop", "value": "roadmap", "run": "true"}, {"op": "add", "text": "t", "run": 1},
        {"op": "remove", "id": 1.9}, {"op": "remove", "id": True}, {"op": "remove", "id": "-1"}])
    def test_bad_params_are_rejected(self, params):
        with pytest.raises(ValueError):
            capi._plan("orders", params, by="web:jack")

    def test_through_the_server(self, proj):
        _cfg(proj, "serve:\n  theme: cockpit\n")
        with Served(proj, "cockpit") as s:
            r = s.client.post("/api/orders", {"op": "add", "text": "hold the PR", "dry_run": True}, headers=s.auth())
            assert r["json"]["ok"] and r["json"]["cli"].startswith("tagteam orders add 'hold the PR'")
            assert not (proj / orders.ORDERS_FILE).exists()                  # a dry run writes nothing
            r = s.client.post("/api/orders", {"op": "add", "text": "hold the PR"}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["ok"], r
            r = s.client.post("/api/orders", {"op": "stop", "value": "roadmap"}, headers=s.auth())
            assert r["json"]["ok"]
            p = s.client.get("/api/rules")["json"]
            assert p["stop"]["project"] == "roadmap" and p["advisory"][0]["text"] == "hold the PR"
            body = json.loads((proj / orders.ORDERS_FILE).read_text())
            assert body["advisory"][0]["by"] == "web:jack"
            r = s.client.post("/api/orders", {"op": "remove", "id": 9}, headers=s.auth())
            assert r["status"] == 409 and r["json"]["ok"] is False and "no advisory note #9" in r["json"]["message"]
            r = s.client.post("/api/orders", {"op": "bogus"}, headers=s.auth())
            assert r["status"] == 400
            assert s.client.post("/api/orders", {"op": "clear-run"})["status"] in (401, 403)   # token required

    def test_bad_scope_or_id_is_400_and_changes_nothing(self, proj):
        """impl r1 review: `run: "true"` must not save the PROJECT stop, and
        `id: 1.9` must not delete note #1."""
        _cfg(proj, "serve:\n  theme: cockpit\n")
        write_state({"phase": "a", "type": "plan", "status": "ready", "turn": "reviewer"}, str(proj))
        _o(proj, "add", "keep me")
        before = ((proj / orders.ORDERS_FILE).read_bytes(), (proj / "handoff-state.json").read_bytes())
        with Served(proj, "cockpit") as s:
            for body in ({"op": "stop", "value": "roadmap", "run": "true"},
                         {"op": "remove", "id": 1.9}, {"op": "remove", "id": True}):
                r = s.client.post("/api/orders", body, headers=s.auth())
                assert r["status"] == 400 and r["json"]["ok"] is False, body
        assert ((proj / orders.ORDERS_FILE).read_bytes(), (proj / "handoff-state.json").read_bytes()) == before

    def test_rules_is_cockpit_only(self, proj):
        with Served(proj) as s:
            assert s.client.get("/api/rules")["status"] == 404


# ---------------------------------------------------------------------------
# The tab (criteria 1, 3, 6) — the shipped slice in REAL Chromium
# ---------------------------------------------------------------------------

def _slice() -> str:
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    return js[js.index("// ---------- Phase 71: Rules tab"):js.index("// ---------- end Phase 71")]


def _slice_71b() -> str:
    js = (WEB / "cockpit.js").read_text(encoding="utf-8")
    return js[js.index("// ---------- Phase 71b: safe tagteam.yaml edits"):js.index("// ---------- end Phase 71b")]


class TestSliceIsPresentationOnly:
    def test_no_html_injection_and_no_derivation(self):
        block = _slice()
        assert "innerHTML" not in block and "insertAdjacentHTML" not in block
        # the page never works out precedence or which value is effective itself
        for derived in ("run_mode", "stop_source", "=== 'run-mode'", "effective ==="):
            assert derived not in block, derived
        assert "stop.effective_text" in block and "stop.shadow_note" in block


_PAYLOAD = {
    "enforced": [
        {"key": "stop", "label": "When a run stops for you", "on": True, "value": "roadmap", "text": "x",
         "source": "standing order (this run)", "change": "orders", "applies": "each approval"},
        {"key": "gate", "label": "Gate before the reviewer's turn", "on": True, "value": "on · implementation reviews only",
         "text": "GATE-TEXT", "source": "tagteam.yaml gatekeeper", "change": "gatekeeper.enabled", "applies": "each submission"},
        {"key": "panel", "label": "Reviewer panel", "on": False, "value": "off", "text": "One reviewer.",
         "source": "tagteam.yaml panel", "change": "panel.enabled", "applies": "when the watcher starts"}],
    "advisory": [{"id": 1, "scope": "project", "text": "hold the PR", "by": "jack"},
                 {"id": 1, "scope": "run", "text": "open the PR", "by": "jack"}],
    "stop": {"effective": "roadmap", "source": "run", "project": "phase", "run": "roadmap", "run_mode_roadmap": False,
             "project_shadowed_by": "run", "has_run_override": True,
             "effective_text": "Now: the run goes on through the roadmap (set for this run).",
             "shadow_note": "SHADOW-NOTE"},
    "last_decision": "converted to a roadmap run",
    "presets": {"stop": [{"value": "phase", "label": "Stop after each phase", "recommended": True},
                         {"value": "roadmap", "label": "Run the roadmap", "recommended": False}],
                "advisory": [{"text": "PRESET-A", "recommended": True}]},
    "warnings": ["WARN-1"], "watcher_stale_config": None, "orders_file": "tagteam-orders.json",
    "text_max": 500, "project_orders_ok": True,
}


def _run_rules_in_chromium(scenario_js: str) -> dict:
    import html as _html
    import subprocess
    import tempfile
    from tests.test_cockpit_activity import _find_chromium
    chrome = _find_chromium()
    if not chrome:
        pytest.skip("no Chromium/Chrome found (set TAGTEAM_TEST_CHROME) — the Rules tab test needs one")
    page_html = (WEB / "cockpit.html").read_text(encoding="utf-8")
    css = (WEB / "cockpit.css").read_text(encoding="utf-8")
    panel = page_html[page_html.index('<div class="panel" id="panel-rules"'):page_html.index("  </section>\n</main>")]
    panel = panel.replace('class="panel" id="panel-rules"', 'class="panel active" id="panel-rules"')
    shims = r"""
      function $(id) { return document.getElementById(id); }
      function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
      function fmtTs(ts) { return String(ts || ''); }
      var POSTS = [], TOASTS = [], DONE = null;
      function postJSON() { return Promise.resolve({ ok: false, status: 500, body: {} }); }
      function confirmModal() {}
      function toast(k, m) { TOASTS.push(k + ':' + m); }
      function getJSON() { return Promise.resolve({ ok: true, body: PAYLOAD }); }
      var ACTS = [];
      function act(btn, url, data, opts) { POSTS.push({ url: url, data: data, title: opts.confirm.title, body: opts.confirm.body }); ACTS.push(opts); }
    """
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>" + css + "</style></head><body>"
            + "<main class='main' style='width:900px'>" + panel + "</main>"
            + "<pre id='RESULT'></pre><script>var PAYLOAD = " + json.dumps(_PAYLOAD) + ";" + shims
            + _slice() + "\n" + _slice_71b() + "\n" + scenario_js
            + "\nPromise.resolve(DONE).then(function () { document.getElementById('RESULT').textContent = JSON.stringify(RESULT); });"
            + "</script></body></html>")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "rules.html"
        f.write_text(page, encoding="utf-8")
        r = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                            "--window-size=1000,1400", "--virtual-time-budget=5000",
                            f"--user-data-dir={d}/profile", "--dump-dom", f.as_uri()],
                           capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    m = re.search(r"<pre id=\"RESULT\">(.*?)</pre>", r.stdout, re.S)
    assert m and m.group(1).strip(), "the page produced no RESULT — a script error? " + r.stderr[-1500:]
    return json.loads(_html.unescape(m.group(1)))


class TestRulesTabInARealBrowser:
    def test_render_scopes_and_writes(self):
        r = _run_rules_in_chromium(r"""
          renderRules(PAYLOAD);
          var RESULT = {};
          var enf = $('panel-rules').querySelector('.rules-enforced'), adv = $('panel-rules').querySelector('.rules-advisory');
          RESULT.groups = [enf.querySelector('.rg-mark').textContent, adv.querySelector('.rg-mark').textContent];
          // apart by more than colour: a different border STYLE
          RESULT.borders = [getComputedStyle(enf).borderLeftStyle, getComputedStyle(adv).borderLeftStyle];
          RESULT.rows = Array.prototype.map.call($('rules-enforced').children, function (x) { return x.dataset.key; });
          RESULT.now = document.querySelector('#rules-stop .r-now').textContent;
          RESULT.projectChecked = document.querySelector('#rules-stop input:checked').value;
          RESULT.shadow = (document.querySelector('.rules-shadow') || {}).textContent;
          document.querySelector('#rules-stop .rules-scope button[data-scope="run"]').click();
          RESULT.runChecked = document.querySelector('#rules-stop input:checked').value;
          RESULT.shadowOnRun = !!document.querySelector('#rules-stop .rules-shadow');
          document.querySelector('#rules-stop input[value="unset"]').click();
          RESULT.afterClickChecked = document.querySelector('#rules-stop input:checked').value;   // back to saved
          document.querySelector('#rules-stop .link-btn').click();                               // clear this run
          Array.prototype.filter.call(document.querySelectorAll('.rule-note button'), function (b) { return true; })[1].click();
          document.querySelector('.rules-add .rules-scope button[data-scope="run"]').click();
          document.querySelector('.rules-chips button').click();
          $('rules-free-text').value = '  my own note ';
          document.querySelector('.rules-free button').click();
          RESULT.posts = POSTS.map(function (p) { return p.data; });
          RESULT.titles = POSTS.map(function (p) { return p.title; });
          RESULT.clearBody = POSTS[1].body;
          RESULT.notes = Array.prototype.map.call(document.querySelectorAll('.rule-note .n-scope'), function (x) { return x.textContent; });
          RESULT.warn = $('rules-warnings').textContent;
          RESULT.warnHidden = $('rules-warnings').classList.contains('hidden');
          RESULT.last = $('rules-last').textContent;
          RESULT.meta = document.querySelector('.rule-row[data-key="gate"] .r-meta').textContent;
          RESULT.overflow = document.documentElement.scrollWidth > window.innerWidth;
        """)
        assert r["groups"] == ["ENFORCED", "ADVISORY"]
        assert r["borders"] == ["solid", "dashed"]
        assert r["rows"] == ["gate", "panel"]                  # the stop order is the editor, not a second row
        assert r["now"] == "Now: the run goes on through the roadmap (set for this run)."
        assert r["projectChecked"] == "phase" and r["shadow"] == "SHADOW-NOTE"
        assert r["runChecked"] == "roadmap" and r["shadowOnRun"] is False
        assert r["afterClickChecked"] == "roadmap"
        assert r["posts"] == [
            {"op": "stop", "value": "unset", "run": True},
            {"op": "clear-run"},
            {"op": "remove", "id": 1, "run": True},
            {"op": "add", "text": "PRESET-A", "run": True},
            {"op": "add", "text": "my own note", "run": True}]
        assert "This run’s notes stay" not in r["clearBody"] and "AND its notes" in r["clearBody"]
        assert r["notes"] == ["project", "this run"]
        assert r["warn"] == "WARN-1" and r["warnHidden"] is False
        assert r["last"] == "Last approval: converted to a roadmap run"
        assert "to change: gatekeeper.enabled in tagteam.yaml" in r["meta"]
        assert r["overflow"] is False

    def test_locked_project_editing_when_the_orders_file_is_bad(self):
        r = _run_rules_in_chromium(r"""
          PAYLOAD.project_orders_ok = false;
          renderRules(PAYLOAD);
          var RESULT = {
            disabled: Array.prototype.map.call(document.querySelectorAll('#rules-stop input'), function (i) { return i.disabled; }),
            checked: !!document.querySelector('#rules-stop input:checked'),
            chips: Array.prototype.every.call(document.querySelectorAll('.rules-chips button'), function (b) { return b.disabled; }),
            free: $('rules-free-text').disabled };
          document.querySelector('#rules-stop .rules-scope button[data-scope="run"]').click();
          RESULT.runEditable = Array.prototype.every.call(document.querySelectorAll('#rules-stop input'), function (i) { return !i.disabled; });
        """)
        assert r == {"disabled": [True, True, True], "checked": False, "chips": True, "free": True,
                     "runEditable": True}

    def test_a_draft_survives_refreshes_and_is_cleared_only_by_its_own_success(self):
        """impl r1 review: SSE / the live tick / a write re-render the tab; an
        unfinished note must keep its text, focus and caret."""
        r = _run_rules_in_chromium(r"""
          renderRules(PAYLOAD);
          var inp = $('rules-free-text');
          inp.focus(); inp.value = 'half a thought'; inp.dispatchEvent(new Event('input'));
          inp.setSelectionRange(4, 6);
          renderRules(PAYLOAD);                                   // a background refresh
          var a = $('rules-free-text');
          var RESULT = { refreshed: { value: a.value, focused: document.activeElement === a,
                                      sel: [a.selectionStart, a.selectionEnd] } };
          document.querySelector('.rules-add .rules-scope button[data-scope="run"]').click();   // scope change
          RESULT.afterScope = $('rules-free-text').value;
          document.querySelector('.rules-free button').click();   // submit → the write is REFUSED
          ACTS[ACTS.length - 1].onDone({ ok: false });
          renderRules(PAYLOAD);                                   // the reload after a refused write
          RESULT.afterRefused = $('rules-free-text').value;
          // a cancelled confirm never calls onDone: nothing to do, the text is simply still there
          RESULT.afterCancel = $('rules-free-text').value;
          document.querySelector('.rules-free button').click();   // submit again → succeeds
          ACTS[ACTS.length - 1].onDone({ ok: true });            // its loadRules() re-renders asynchronously…
          renderRules(PAYLOAD);                                   // …so render the reload's result here
          RESULT.afterSuccess = $('rules-free-text').value;
          RESULT.posted = POSTS.map(function (p) { return p.data; });
        """)
        assert r["refreshed"] == {"value": "half a thought", "focused": True, "sel": [4, 6]}
        assert r["afterScope"] == "half a thought"
        assert r["afterRefused"] == "half a thought" and r["afterCancel"] == "half a thought"
        assert r["afterSuccess"] == ""
        assert r["posted"] == [{"op": "add", "text": "half a thought", "run": True}] * 2


# ---------------------------------------------------------------------------
# Phase 71b: editable rows, the preview, and preview-bound writes
# ---------------------------------------------------------------------------

class TestConfigEditsApi:
    def test_rows_carry_saved_values_and_not_in_effect(self, proj):
        _cfg(proj, "gatekeeper:\n  enabled: true\n  scope: invalid\nwatcher:\n  resend_minutes: 3\n  bogus: 1\n")
        rows = _rows(capi.rules_payload(proj))
        g = {e["key"]: e for e in rows["gate"]["edits"]}
        assert g["gatekeeper.enabled"]["saved"] is True and rows["gate"]["on"] is False
        assert g["gatekeeper.enabled"]["not_in_effect"].startswith("saved: true · not in effect:")
        assert g["gatekeeper.on_submit"]["saved_state"] == "absent"
        w = rows["resend"]["edits"][0]
        assert w["saved"] == 3 and rows["resend"]["value"] == "15 min" and "bogus" in w["not_in_effect"]
        assert rows["panel"]["edits"][0]["saved_state"] == "absent" and "edits" not in rows["stale"]

    @pytest.mark.parametrize("params", [
        {"key": "agents.lead.name", "value": "x", "preview": True},
        {"key": "gatekeeper.enabled", "value": "true", "preview": True},
        {"key": "gatekeeper.enabled", "value": 1, "preview": True},
        {"key": "watcher.resend_minutes", "value": True, "preview": True},
        {"key": "watcher.resend_minutes", "value": -1, "preview": True},
        {"key": "watcher.resend_minutes", "value": 2.5, "preview": True},
        {"key": "gatekeeper.enabled", "value": True},                       # a write without `expect`
        {"key": "gatekeeper.enabled", "value": True, "expect": "abc"}])
    def test_bad_params_are_400(self, params):
        with pytest.raises(ValueError):
            capi._plan("config/set", params, by="web:jack")

    def test_preview_then_stale_write_through_the_server(self, proj):
        _cfg(proj, "serve:\n  theme: cockpit\ngatekeeper:\n  enabled: false  # keep\n")
        f = proj / "tagteam.yaml"
        with Served(proj, "cockpit") as s:
            before = f.read_bytes()
            pv = s.client.post("/api/config/set", {"key": "gatekeeper.enabled", "value": True, "preview": True},
                               headers=s.auth())["json"]
            assert pv["ok"] and "+  enabled: true  # keep" in pv["diff"] and pv["engine"] == "gate: ON"
            assert pv["cli"].endswith("--expect " + pv["base"]) and f.read_bytes() == before
            f.write_text(f.read_text() + "# hand edit\n")                 # after the preview
            edited = f.read_bytes()
            r = s.client.post("/api/config/set", {"key": "gatekeeper.enabled", "value": True, "expect": pv["base"]},
                              headers=s.auth())
            assert r["status"] == 409 and "changed since the preview" in r["json"]["message"]
            assert f.read_bytes() == edited
            pv2 = s.client.post("/api/config/set", {"key": "gatekeeper.enabled", "value": True, "preview": True},
                                headers=s.auth())["json"]
            r = s.client.post("/api/config/set", {"key": "gatekeeper.enabled", "value": True, "expect": pv2["base"]},
                              headers=s.auth())
            assert r["status"] == 200 and r["json"]["ok"], r
            assert f.read_text().endswith("enabled: true  # keep\n# hand edit\n")
            assert s.client.post("/api/config/set", {"key": "gatekeeper.enabled", "value": False,
                                                     "preview": True})["status"] in (401, 403)

    def test_a_refused_preview_is_409_with_the_reason(self, proj):
        (proj / "tagteam.yaml").write_text("agents:\n  lead:\n    name: someone\n  reviewer:\n    name: other\n")
        res = capi.run_action("config/set", {"key": "briefer.enabled", "value": True, "preview": True}, proj)
        assert res["ok"] is False and "escalation brief: OFF" in res["message"]


class TestConfigEditsInARealBrowser:
    def test_preview_confirm_and_a_stale_refusal_is_not_retried(self):
        r = _run_rules_in_chromium(r"""
          PAYLOAD.enforced[1].edits = [
            { key: 'gatekeeper.enabled', kind: 'bool', label: 'Gate', saved_state: 'set', saved: false, not_in_effect: null },
            { key: 'gatekeeper.on_submit', kind: 'bool', label: 'Runs at submission', saved_state: 'absent', saved: null, not_in_effect: null }];
          PAYLOAD.enforced[2].edits = [
            { key: 'panel.enabled', kind: 'bool', label: 'Panel', saved_state: 'set', saved: true,
              not_in_effect: 'saved: true · not in effect: panel: lens brief not found' }];
          var CALLS = [], MODAL = null;
          postJSON = function (url, data) {
            CALLS.push(data);
            if (data.preview) return Promise.resolve({ ok: true, status: 200, body: { ok: true, noop: false,
              diff: '-  enabled: false\n+  enabled: true\n', base: 'b'.repeat(64), engine: 'gate: ON', notes: [],
              cli: 'tagteam config set gatekeeper.enabled true --expect ' + 'b'.repeat(64) } });
            return Promise.resolve({ ok: false, status: 409, body: { ok: false, message: 'changed since the preview' } });
          };
          confirmModal = function (title, body, cli, onOk, labels) { MODAL = { title: title, body: body, cli: cli, diff: labels.diff, ok: onOk }; };
          act = function (btn, url, data, opts) { CALLS.push({ act: data }); return postJSON(url, data); };
          renderRules(PAYLOAD);
          var RESULT = {
            saved: Array.prototype.map.call(document.querySelectorAll('.rule-bool .r-saved'), function (x) { return x.textContent; }),
            buttons: Array.prototype.map.call(document.querySelectorAll('.rule-switch'), function (x) { return x.textContent; }),
            shadow: (document.querySelector('.rule-row[data-key="panel"] .rules-shadow') || {}).textContent,
            gateHint: document.querySelector('.rule-row[data-key="gate"] .r-meta').textContent };
          document.querySelector('.rule-switch[data-key="gatekeeper.enabled"]').click();
          DONE = new Promise(function (res) { setTimeout(res, 50); }).then(function () {
            RESULT.modal = { title: MODAL.title, diff: MODAL.diff, cli: MODAL.cli };
            MODAL.ok();                                              // confirm → the stale write is refused
            return new Promise(function (res) { setTimeout(res, 50); });
          }).then(function () {
            RESULT.calls = CALLS;
          });
        """)
        assert r["saved"] == ["Gate — saved: off", "Runs at submission — saved: not set", "Panel — saved: on"]
        assert r["buttons"] == ["Turn on", "Turn on", "Turn off"]
        assert r["shadow"].startswith("saved: true · not in effect")
        assert "to change:" not in r["gateHint"]
        assert r["modal"]["title"] == "Change tagteam.yaml — gatekeeper.enabled"
        assert r["modal"]["diff"].startswith("-  enabled: false") and r["modal"]["cli"].endswith("b" * 64)
        writes = [c for c in r["calls"] if "act" in c]
        assert r["calls"][0] == {"key": "gatekeeper.enabled", "value": True, "preview": True}
        assert writes == [{"act": {"key": "gatekeeper.enabled", "value": True, "expect": "b" * 64}}]
        assert len([c for c in r["calls"] if c.get("expect")]) == 1            # exactly one write POST, no retry
