"""Phase 34 tests: the dashboard server in BOTH modes over a real
`TagteamHTTPServer` on an ephemeral port.

Legacy (bare `tagteam serve`): Saloon at `/`, binds all interfaces, the
four legacy POSTs accepted without a token, `*` CORS, no meta token, every
new endpoint 404 — 0.10.0-identical. Cockpit (`--theme cockpit` /
`serve.theme`): cockpit page at `/`, Saloon at `/?theme=saloon` with the
token embedded, loopback default + `--host` override, token + Origin
required on every POST (incl. the legacy four), no `*` CORS, read/action
endpoints routed, SSE (two clients, heartbeat, cap → 503), threading."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from tagteam import controls, db, headless as h, server as srv
from tagteam import cycle as cycle_mod
from tagteam import state as state_mod

from tests.test_headless import project, fake_path, _init_cycle  # noqa: F401


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port

    def request(self, method: str, path: str, body=None, headers=None, timeout=10.0):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        out = {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()}, "raw": raw}
        try:
            out["json"] = json.loads(raw) if raw else None
        except ValueError:
            out["json"] = None
        conn.close()
        return out

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body if body is not None else {}, **kw)


class Served:
    """Context manager: a real server thread in `mode`, torn down after."""

    def __init__(self, project_dir, mode="legacy", host="127.0.0.1", **handler_kw):
        self.project_dir = str(project_dir)
        self.mode = mode
        self.host = host
        self.handler_kw = handler_kw

    def __enter__(self):
        token = srv.new_token() if self.mode == "cockpit" else None
        handler = srv.make_handler(self.project_dir, mode=self.mode, token=token, **self.handler_kw)
        self.server = srv.TagteamHTTPServer((self.host, 0), handler)
        self.port = self.server.server_address[1]
        self.token = handler.TOKEN
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()
        self.client = Client("127.0.0.1", self.port)
        return self

    def __exit__(self, *exc):
        self.server.stop()
        self.thread.join(timeout=5)

    def auth(self, origin=True):
        hdrs = {"X-Tagteam-Token": self.token}
        if origin:
            hdrs["Origin"] = f"http://127.0.0.1:{self.port}"
        return hdrs


class SSEReader:
    """Raw socket SSE consumer (frames + comments), non-blocking-ish."""

    def __init__(self, port: int, headers: dict | None = None, path: str = "/api/events"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n"
        for k, v in (headers or {}).items():
            req += f"{k}: {v}\r\n"
        req += "\r\n"
        self.sock.sendall(req.encode())
        self.buf = b""
        self.status = None
        self.frames: list[dict] = []
        self.comments: list[str] = []
        self.headers_done = False

    def pump(self, timeout: float):
        deadline = time.monotonic() + timeout
        self.sock.settimeout(0.2)
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buf += chunk
            self._parse()
        return self

    def _parse(self):
        if not self.headers_done:
            if b"\r\n\r\n" not in self.buf:
                return
            head, self.buf = self.buf.split(b"\r\n\r\n", 1)
            self.status = int(head.split(b" ")[1])
            self.head = head.decode("latin-1")
            self.headers_done = True
        while b"\n\n" in self.buf:
            block, self.buf = self.buf.split(b"\n\n", 1)
            text = block.decode("utf-8", "replace")
            if text.startswith(":"):
                self.comments.append(text)
                continue
            frame = {}
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    frame[k.strip()] = v.strip()
            if "data" in frame:
                frame["data"] = json.loads(frame["data"])
            self.frames.append(frame)

    def wait_frames(self, n: int, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while len(self.frames) < n and time.monotonic() < deadline:
            self.pump(0.3)
        return self.frames

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


LEGACY_NEW_ENDPOINTS = ["/api/now", "/api/usage", "/api/briefs", "/api/brief/current",
                        "/api/interjections", "/api/scope-diff/feat-x_plan", "/api/tail",
                        "/api/events", "/api/cockpit/info", "/api/rules"]
NEW_POSTS = ["/api/pause", "/api/resume", "/api/interject", "/api/interject/retire",
             "/api/cancel-turn", "/api/brief/generate", "/api/rule", "/api/orders"]


# ---------------------------------------------------------------------------
# legacy mode: 0.10.0-identical
# ---------------------------------------------------------------------------

class TestLegacyMode:
    def test_index_is_saloon_verbatim_no_token(self, project):
        with Served(project) as s:
            r = s.client.get("/")
            assert r["status"] == 200
            assert r["raw"] == (srv._WEB_DIR / "index.html").read_bytes()
            assert b"tagteam-token" not in r["raw"]
            assert r["headers"]["access-control-allow-origin"] == "*"
            assert s.client.get("/app.js")["headers"]["access-control-allow-origin"] == "*"
            # ?theme=saloon is ignored in legacy mode (same bytes)
            assert s.client.get("/?theme=cockpit")["raw"] == r["raw"]

    def test_new_endpoints_404(self, project):
        with Served(project) as s:
            for p in LEGACY_NEW_ENDPOINTS:
                r = s.client.get(p)
                assert r["status"] == 404, p
                assert r["json"] == {"error": "Not found"}, p
            for p in NEW_POSTS:
                assert s.client.post(p, {}).get("status") == 404, p

    def test_legacy_posts_without_token(self, project, tmp_path):
        with Served(project) as s:
            c = s.client
            # /api/state
            r = c.post("/api/state", {"turn": "lead", "status": "ready", "phase": "p", "type": "plan", "round": 1})
            assert r["status"] == 200 and r["json"]["turn"] == "lead"
            assert r["headers"]["access-control-allow-origin"] == "*"
            # /api/config (exists → 409, overwrite → 200)
            r = c.post("/api/config", {"lead": "A", "reviewer": "B"})
            assert r["status"] == 409
            r = c.post("/api/config", {"lead": "A", "reviewer": "B", "overwrite": True})
            assert r["status"] == 200 and r["json"]["agents"]["lead"]["name"] == "A"
            # /api/start-phase (active handoff → 409, the same validation as before)
            r = c.post("/api/start-phase", {"phase": "x", "type": "plan"})
            assert r["status"] == 409
            # /api/launch validation path (no token needed to reach it)
            r = c.post("/api/launch", {"lead": "A", "reviewer": "B", "first_prompt": ""})
            assert r["status"] == 400 and "first_prompt" in r["json"]["error"]
            # OPTIONS preflight unchanged
            r = c.request("OPTIONS", "/api/state")
            assert r["status"] == 204 and r["headers"]["access-control-allow-headers"] == "Content-Type"

    def test_legacy_rounds_shape_unchanged(self, project):
        _init_cycle(project)
        with Served(project) as s:
            r = s.client.get("/api/rounds/feat-x_plan")
            assert r["status"] == 200
            assert set(r["json"]) == {"rounds", "html"}
            rd = r["json"]["rounds"][0]
            assert "interjections" not in rd            # cockpit-only additive field
            assert "entries" in rd and "rulings" in rd  # pre-existing fields

    def test_saloon_theme_binds_all_interfaces_and_bare_is_cockpit(self, project):
        # 3.1: bare `serve` is the cockpit (loopback); `--theme saloon` is the legacy path
        opts = srv.resolve_serve_options(["--dir", str(project)])
        assert opts["mode"] == "cockpit" and opts["host"] == "127.0.0.1"
        opts = srv.resolve_serve_options(["--dir", str(project), "--theme", "saloon"])
        assert opts["mode"] == "legacy" and opts["host"] == ""
        opts = srv.resolve_serve_options(["--dir", str(project), "--theme", "saloon", "--host", "10.0.0.5"])
        assert opts["host"] == "10.0.0.5"


# ---------------------------------------------------------------------------
# cockpit mode
# ---------------------------------------------------------------------------

class TestCockpitPages:
    def test_pages_and_assets(self, project):
        with Served(project, "cockpit") as s:
            r = s.client.get("/")
            assert r["status"] == 200
            html = r["raw"].decode()
            assert f'<meta name="tagteam-token" content="{s.token}">' in html
            assert "access-control-allow-origin" not in r["headers"]
            assert r["headers"].get("cache-control") == "no-store"
            # zones + tabs present (round-3 IA)
            for anchor in ('id="now"', 'id="needs-you"', 'id="watch"', 'data-tab="now"',   # Phase 69 regroup
                           'data-tab="roadmap"', 'data-tab="history"', 'data-tab="usage"', 'id="conn"'):
                assert anchor in html, anchor
            # referenced assets resolve — and carry no wildcard CORS in cockpit mode
            for asset in ("/cockpit.css", "/cockpit.js"):
                a = s.client.get(asset)
                assert a["status"] == 200 and len(a["raw"]) > 1000, asset
                assert "access-control-allow-origin" not in a["headers"], asset
            for path in ("/api/state", "/api/now", "/api/rounds/x_plan", "/api/nope"):
                assert "access-control-allow-origin" not in s.client.get(path)["headers"], path
            assert s.client.get("/cockpit.js")["headers"]["content-type"].startswith("application/javascript")
            # Saloon theme with the token embedded; its assets resolve
            r = s.client.get("/?theme=saloon")
            assert r["status"] == 200
            saloon = r["raw"].decode()
            assert "The Handoff Saloon" in saloon and f'content="{s.token}"' in saloon
            for asset in ("/app.js", "/styles.css", "/sprites.js", "/conversation.js"):
                assert s.client.get(asset)["status"] == 200, asset
            assert "tagteamFetch" in s.client.get("/app.js")["raw"].decode()

    def test_info_and_404_still_json(self, project):
        with Served(project, "cockpit") as s:
            r = s.client.get("/api/cockpit/info")
            assert r["json"]["mode"] == "cockpit" and r["json"]["max_sse"] == srv.DEFAULT_MAX_SSE
            assert s.client.get("/api/nope")["status"] == 404


class TestCockpitAuth:
    def test_token_required_on_every_post_incl_legacy_four(self, project):
        with Served(project, "cockpit") as s:
            c = s.client
            legacy = [("/api/state", {"turn": "lead"}), ("/api/config", {"lead": "A", "reviewer": "B"}),
                      ("/api/launch", {}), ("/api/start-phase", {"phase": "x", "type": "plan"})]
            for path, body in legacy + [(p, {}) for p in NEW_POSTS]:
                r = c.post(path, body)
                assert r["status"] == 403, path
                assert r["json"]["ok"] is False and "X-Tagteam-Token" in r["json"]["error"]
                assert "access-control-allow-origin" not in r["headers"]
                r = c.post(path, body, headers={"X-Tagteam-Token": "wrong"})
                assert r["status"] == 403, path
                # right token, wrong origin
                r = c.post(path, body, headers={"X-Tagteam-Token": s.token, "Origin": "http://evil.example"})
                assert r["status"] == 403 and "Origin" in r["json"]["error"], path
                # right token, wrong referer
                r = c.post(path, body, headers={"X-Tagteam-Token": s.token,
                                                "Referer": "http://127.0.0.1:1/"})
                assert r["status"] == 403, path

    def test_legacy_four_accepted_as_the_pages_send_them(self, project):
        with Served(project, "cockpit") as s:
            c = s.client
            r = c.post("/api/state", {"turn": "lead", "status": "ready", "phase": "p", "type": "plan", "round": 1},
                       headers=s.auth())
            assert r["status"] == 200 and r["json"]["turn"] == "lead"
            assert "access-control-allow-origin" not in r["headers"]
            r = c.post("/api/config", {"lead": "A", "reviewer": "B", "overwrite": True}, headers=s.auth())
            assert r["status"] == 200
            r = c.post("/api/start-phase", {"phase": "x", "type": "plan"}, headers=s.auth())
            assert r["status"] == 409  # active handoff — reached the route's own validation
            r = c.post("/api/launch", {"lead": "A", "reviewer": "B", "first_prompt": ""}, headers=s.auth())
            assert r["status"] == 400 and "first_prompt" in r["json"]["error"]
            # token without any Origin/Referer (non-browser client that read the page) is fine
            r = c.post("/api/state", {"turn": "reviewer"}, headers=s.auth(origin=False))
            assert r["status"] == 200

    def test_options_advertises_token_header(self, project):
        with Served(project, "cockpit") as s:
            r = s.client.request("OPTIONS", "/api/pause")
            assert r["status"] == 204
            assert "X-Tagteam-Token" in r["headers"]["access-control-allow-headers"]
            assert "access-control-allow-origin" not in r["headers"]

    def test_default_bind_loopback_and_host_override(self, project):
        opts = srv.resolve_serve_options(["--dir", str(project), "--theme", "cockpit"])
        assert opts["mode"] == "cockpit" and opts["host"] == "127.0.0.1"
        opts = srv.resolve_serve_options(["--dir", str(project), "--theme", "cockpit", "--host", "0.0.0.0"])
        assert opts["host"] == "0.0.0.0"
        assert isinstance(srv.resolve_serve_options(["--theme", "neon"]), str)
        assert isinstance(srv.resolve_serve_options(["--max-sse", "x"]), str)
        assert srv.resolve_serve_options(["--max-sse", "3", "--theme", "cockpit"])["max_sse"] == 3

    def test_config_gate(self, project):
        (project / "tagteam.yaml").write_text(
            "agents:\n  lead:\n    name: Claude\n  reviewer:\n    name: Codex\nserve:\n  theme: cockpit\n")
        opts = srv.resolve_serve_options(["--dir", str(project)])
        assert opts["mode"] == "cockpit" and opts["host"] == "127.0.0.1"
        # explicit flag wins over config
        opts = srv.resolve_serve_options(["--dir", str(project), "--theme", "saloon"])
        assert opts["mode"] == "legacy" and opts["host"] == ""


class TestCockpitReads:
    def test_read_endpoints_shapes(self, project):
        _init_cycle(project)
        controls.interject_command(["n1"], project_root=project)
        with Served(project, "cockpit") as s:
            c = s.client
            now = c.get("/api/now")["json"]
            assert now["owed"]["role"] == "reviewer" and now["state"]["phase"] == "feat-x"
            r = c.get("/api/rounds/feat-x_plan")["json"]
            assert r["rounds"][0]["interjections"][0]["note"] == "n1"
            assert "entries" in r["rounds"][0] and "rulings" in r["rounds"][0]
            i = c.get("/api/interjections")["json"]           # defaults to the state's cycle
            assert i["pending"] == 1 and i["interjections"][0]["status"] == "pending"
            assert c.get("/api/interjections?phase=feat-x&type=plan")["json"]["pending"] == 1
            assert c.get("/api/briefs")["json"] == {"briefs": []}
            bc = c.get("/api/brief/current")["json"]
            assert bc["event"] is None and "not escalated" in bc["reason"]
            assert c.get("/api/brief/999")["status"] == 404
            assert c.get("/api/brief/abc")["status"] == 404
            u = c.get("/api/usage?phase=feat-x&type=plan")["json"]
            assert set(u) >= {"by_role", "by_cycle", "by_agent", "totals", "series", "rate_limits"}
            sd = c.get("/api/scope-diff/feat-x_plan")["json"]
            assert set(sd) >= {"paths", "files", "truncated", "error"}
            assert c.get("/api/scope-diff/bogus")["status"] == 400
            t = c.get("/api/tail?lines=5")["json"]
            assert t["path"] is None and "No headless turn logs" in t["message"]

    def test_brief_current_after_escalation(self, project):
        _init_cycle(project)
        cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                            updated_by="Codex")
        with Served(project, "cockpit") as s:
            bc = s.client.get("/api/brief/current")["json"]
            assert bc["event"]["cycle_state"] == "escalated" and bc["brief"] is None


class TestCockpitWrites:
    def test_pause_resume_records_web_user(self, project, monkeypatch):
        monkeypatch.setenv("TAGTEAM_ARBITER", "jack")
        with Served(project, "cockpit") as s:
            r = s.client.post("/api/pause", {"reason": "hold"}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["ok"] is True and "Paused" in r["json"]["message"]
            assert r["json"]["cli"] == "tagteam pause --reason hold --by web:jack"
            assert h.read_pause(project)["by"] == "web:jack"
            r = s.client.post("/api/resume", {}, headers=s.auth())
            assert r["json"]["ok"] is True and h.read_pause(project) is None
            r = s.client.post("/api/resume", {}, headers=s.auth())
            assert r["status"] == 409 and r["json"]["ok"] is False and "Not paused" in r["json"]["message"]

    def test_interject_retire_and_dry_run(self, project):
        _init_cycle(project)
        with Served(project, "cockpit") as s:
            r = s.client.post("/api/interject", {"note": "web note", "to": "lead"}, headers=s.auth())
            assert r["json"]["ok"] and "Interjection #1" in r["json"]["message"]
            conn = db.connect(project_dir=str(project))
            try:
                row = db.get_interjections(conn)[0]
            finally:
                conn.close()
            assert row["note"] == "web note" and row["by"].startswith("web:")
            r = s.client.post("/api/interject", {"note": ""}, headers=s.auth())
            assert r["status"] == 400 and r["json"]["ok"] is False
            r = s.client.post("/api/interject/retire", {"id": 1, "dry_run": True}, headers=s.auth())
            assert r["json"]["dry_run"] is True and r["json"]["cli"].startswith("tagteam interject --retire 1")
            r = s.client.post("/api/interject/retire", {"id": 1}, headers=s.auth())
            assert r["json"]["ok"] and "Retired" in r["json"]["message"]
            r = s.client.post("/api/interject/retire", {"id": 1}, headers=s.auth())
            assert r["status"] == 409 and r["json"]["ok"] is False
            r = s.client.post("/api/pause", None, headers=dict(s.auth(), **{"Content-Type": "application/json"}))
            assert r["status"] == 200  # empty body is an empty object

    def test_invalid_json_body(self, project):
        with Served(project, "cockpit") as s:
            conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=5)
            conn.request("POST", "/api/pause", body=b"{not json", headers=dict(s.auth(), **{"Content-Type": "application/json"}))
            r = conn.getresponse(); body = json.loads(r.read())
            assert r.status == 400 and body["ok"] is False and body["message"] == "Invalid JSON"

    def test_rule_on_non_escalated_and_then_escalated(self, project):
        _init_cycle(project)
        with Served(project, "cockpit") as s:
            r = s.client.post("/api/rule", {"ruling": "approve"}, headers=s.auth())
            assert r["status"] == 409 and r["json"]["ok"] is False and "Nothing to rule on" in r["json"]["message"]
            r = s.client.post("/api/rule", {"ruling": "nope"}, headers=s.auth())
            assert r["status"] == 400
            cycle_mod.add_round("feat-x", "plan", "reviewer", "ESCALATE", 1, "stuck", str(project),
                                updated_by="Codex")
            r = s.client.post("/api/rule", {"ruling": "approve", "content": "ship it"}, headers=s.auth())
            assert r["status"] == 200 and r["json"]["ok"] and "approved" in r["json"]["message"]
            st = cycle_mod.read_status("feat-x", "plan", str(project))
            assert st["state"] == "approved"
            rounds = cycle_mod.read_rounds("feat-x", "plan", str(project))
            assert rounds[-1]["content"].startswith("[ARBITER RULING by web:")
            # rounds endpoint shows the ruling
            rr = s.client.get("/api/rounds/feat-x_plan")["json"]["rounds"][0]
            assert rr["rulings"] and rr["rulings"][0]["action"] == "APPROVE"

    def test_cancel_turn_and_brief_generate_paths(self, project):
        _init_cycle(project)
        with Served(project, "cockpit") as s:
            r = s.client.post("/api/cancel-turn", {}, headers=s.auth())
            assert r["status"] == 409 and "Nothing in flight" in r["json"]["message"]
            r = s.client.post("/api/brief/generate", {}, headers=s.auth())
            assert r["json"]["ok"] is False and ("not enabled" in r["json"]["message"]
                                                 or "No current escalation" in r["json"]["message"])

    def test_handler_exception_is_json_500(self, project, monkeypatch):
        from tagteam import cockpit_api as capi
        monkeypatch.setattr(capi, "now_payload", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        with Served(project, "cockpit") as s:
            r = s.client.get("/api/now")
            assert r["status"] == 500 and "boom" in r["json"]["error"]


# ---------------------------------------------------------------------------
# SSE + threading
# ---------------------------------------------------------------------------

class TestSSE:
    def test_snapshot_change_two_clients_heartbeat_last_event_id(self, project):
        _init_cycle(project)
        with Served(project, "cockpit", sse_interval=0.2, sse_heartbeat=0.6) as s:
            a = SSEReader(s.port); b = SSEReader(s.port, {"Last-Event-ID": "stale-id"})
            a.wait_frames(1); b.wait_frames(1)
            assert a.status == 200 and "text/event-stream" in a.head.lower()
            assert a.frames[0]["event"] == "change" and "id" in a.frames[0]
            assert a.frames[0]["data"]["phase"] == "feat-x" and a.frames[0]["data"]["paused"] is False
            assert b.frames[0]["id"] == a.frames[0]["id"]      # Last-Event-ID → current snapshot
            first_id = a.frames[0]["id"]
            # a change: cycle add
            cycle_mod.add_round("feat-x", "plan", "reviewer", "REQUEST_CHANGES", 1, "x", str(project),
                                updated_by="Codex")
            a.wait_frames(2, 3.0); b.wait_frames(2, 3.0)
            assert len(a.frames) >= 2 and len(b.frames) >= 2
            assert a.frames[-1]["id"] != first_id
            assert a.frames[-1]["data"]["turn"] == "lead"
            # interjection, pause, usage, inflight each fire
            n = len(a.frames)
            controls.interject_command(["n"], project_root=project); a.wait_frames(n + 1, 3.0); assert len(a.frames) > n
            n = len(a.frames)
            controls.pause_command([], project_root=project); a.wait_frames(n + 1, 3.0); assert a.frames[-1]["data"]["paused"] is True
            n = len(a.frames)
            conn = db.connect(project_dir=str(project))
            try:
                db.add_usage(conn, ts="t", status="ok")
            finally:
                conn.close()
            a.wait_frames(n + 1, 3.0); assert a.frames[-1]["data"]["usage"] == 1
            n = len(a.frames)
            h.turns_dir(project).mkdir(parents=True, exist_ok=True)
            h.inflight_path(project).write_text(json.dumps({"stem": "s9", "pid": 1, "started_at": h._now_iso()}))
            a.wait_frames(n + 1, 3.0); assert a.frames[-1]["data"]["inflight"]["stem"] == "s9"
            # heartbeat while idle
            a.comments.clear(); a.pump(2.5)
            assert any(c.startswith(": heartbeat") for c in a.comments)
            a.close(); b.close()

    def test_cap_returns_503(self, project):
        with Served(project, "cockpit", max_sse=2, sse_interval=0.2) as s:
            a = SSEReader(s.port); b = SSEReader(s.port)
            a.wait_frames(1); b.wait_frames(1)
            c = SSEReader(s.port); c.pump(1.0)
            assert c.status == 503
            assert b"max 2" in c.buf or b"Too many" in c.buf
            a.close(); b.close(); c.close()
            # slot freed after disconnect
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if s.client.get("/api/cockpit/info")["json"]["sse_active"] == 0:
                    break
                time.sleep(0.1)
            d = SSEReader(s.port); d.wait_frames(1)
            assert d.status == 200; d.close()

    def test_open_stream_does_not_block_other_requests(self, project):
        with Served(project, "cockpit", sse_interval=0.2) as s:
            a = SSEReader(s.port); a.wait_frames(1)
            t0 = time.monotonic()
            r = s.client.get("/api/state")
            assert r["status"] == 200 and time.monotonic() - t0 < 2.0
            r = s.client.get("/api/now")
            assert r["status"] == 200
            a.close()

    def test_legacy_mode_is_threaded_too(self, project):
        with Served(project) as s:
            # two overlapping requests complete
            results = []
            def go():
                results.append(s.client.get("/api/state")["status"])
            ts = [threading.Thread(target=go) for _ in range(4)]
            [t.start() for t in ts]; [t.join(5) for t in ts]
            assert results == [200, 200, 200, 200]


class TestServeCommand:
    def test_help_and_bad_args(self, capsys):
        assert srv.serve_command(["--help"]) == 0
        out = capsys.readouterr().out
        assert "--theme" in out and "--host" in out and "--max-sse" in out
        assert srv.serve_command(["--port", "x"]) == 1
        assert srv.serve_command(["--bogus"]) == 1


class TestStartPhaseCommand:
    """Phase 49: /api/start-phase writes the project-aware handoff command,
    not the dead `/handoff-cycle`."""

    def _migrate(self, project):
        import pathlib, shutil
        shutil.rmtree(pathlib.Path(project) / ".claude" / "skills" / "handoff", ignore_errors=True)

    def test_migrated_project_gets_namespaced_command(self, project):
        self._migrate(project)
        with Served(project) as s:
            r = s.client.post("/api/start-phase", {"phase": "gamma", "type": "plan"})
            assert r["status"] == 200, r
            assert r["json"]["command"] == "/tagteam:handoff start gamma"
            assert r["json"]["turn"] == "lead" and r["json"]["round"] == 1

    def test_impl_type_appends_impl(self, project):
        self._migrate(project)
        with Served(project) as s:
            r = s.client.post("/api/start-phase", {"phase": "gamma", "type": "impl"})
            assert r["status"] == 200 and r["json"]["command"] == "/tagteam:handoff start gamma impl"

    def test_vendored_project_keeps_slash_handoff(self, project):
        import pathlib
        d = pathlib.Path(project) / ".claude" / "skills" / "handoff"; d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("x")
        with Served(project) as s:
            r = s.client.post("/api/start-phase", {"phase": "gamma", "type": "plan"})
            assert r["status"] == 200 and r["json"]["command"] == "/handoff start gamma"
