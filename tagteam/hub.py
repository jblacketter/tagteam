"""
`tagteam hub` (Phase 35): one surface over every registered project.

    tagteam hub                     # server on http://localhost:8090 (loopback)
    tagteam hub --list [--json] [--all]
    tagteam hub --port N --host H --interval S --max-sse N --registry PATH --all

The hub page ranks projects by what needs the human (Needs you → Waiting →
Quiet), aggregates burn and the shared subscription window, and MOUNTS each
project's Phase 34 cockpit at `/p/<id>/` (one `CockpitRouter` per project,
one per-run token, loopback default) so acting is two clicks away.

Nothing here runs unless `tagteam hub` is invoked; the hub never mutates a
project (read-only DB access, see `hub_api`) or the registry.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from tagteam import hub_api
from tagteam import registry as registry_mod
from tagteam.server import (CockpitRouter, TagteamHTTPServer, _ResponseMixin, _get_static_file,
                            _WEB_DIR, new_token, DEFAULT_MAX_SSE)

DEFAULT_PORT = 8090
DEFAULT_INTERVAL_S = 3.0
HUB_HEARTBEAT_S = 15.0
_TOKEN_META = b'<meta name="tagteam-token" content="%s">'


def _get_hub_html(token: str) -> bytes:
    html = (_WEB_DIR / "hub.html").read_bytes()
    marker = b'<meta charset="UTF-8">'
    meta = _TOKEN_META % token.encode("ascii")
    return html.replace(marker, marker + b"\n" + meta, 1) if marker in html else html


class HubContext:
    """Immutable-ish hub configuration + the per-project router cache."""

    def __init__(self, *, registry_reader, registry_file: Path | None, token: str,
                 max_sse: int = DEFAULT_MAX_SSE, interval_s: float = DEFAULT_INTERVAL_S,
                 heartbeat_s: float = HUB_HEARTBEAT_S, show_all: bool = False,
                 scratch_prefixes: tuple[str, ...] = hub_api.SCRATCH_PREFIXES):
        self.registry_reader = registry_reader
        self.registry_file = registry_file
        self.token = token
        self.max_sse = max_sse
        self.interval_s = interval_s
        self.heartbeat_s = heartbeat_s
        self.show_all = show_all
        self.scratch_prefixes = scratch_prefixes
        self.routers: dict[str, CockpitRouter] = {}
        self.lock = threading.Lock()
        self.sse_lock = threading.Lock()
        self.sse_state = {"active": 0}

    def paths(self) -> list[str]:
        try:
            return list(self.registry_reader())
        except Exception:
            return []

    def router_for(self, project_id: str) -> CockpitRouter | None:
        """The mounted cockpit for a registry project id (created lazily,
        cached — routers are context, not handlers). Membership is
        re-validated against the CURRENT registry on every call: an id no
        longer registered (or whose path changed / disappeared) is evicted
        and answers None, so an unregistered project's write surface goes
        away immediately, not at restart."""
        with self.lock:
            current = None
            for raw in self.paths():
                if hub_api.project_id(raw) == project_id:
                    current = raw
                    break
            r = self.routers.get(project_id)
            if current is None or not Path(current).is_dir():
                if r is not None:
                    del self.routers[project_id]
                return None
            if r is not None and r.project_dir != current:
                del self.routers[project_id]      # id reused for another path — rebuild
                r = None
            if r is None:
                r = CockpitRouter(current, mode="cockpit", token=self.token, max_sse=self.max_sse,
                                  base_path=f"/p/{project_id}")
                self.routers[project_id] = r
            return r

    def payload(self, show_all: bool | None = None) -> dict:
        return hub_api.hub_payload(self.paths(), show_all=self.show_all if show_all is None else show_all,
                                   scratch_prefixes=self.scratch_prefixes)

    def signature(self, procs_snapshot=None) -> dict:
        return hub_api.hub_signature(self.paths(), self.registry_file, procs_snapshot=procs_snapshot)


def make_hub_handler(ctx: HubContext):
    """Handler class: hub routes at `/`, `/api/hub…`; mounted cockpits at
    `/p/<id>/…` (prefix stripped, delegated to that project's router)."""

    class _HubOwnRouter:
        """Just enough router-shaped context for the hub's own responses
        (cockpit mode → no wildcard CORS)."""
        cockpit = True
        mode = "cockpit"
        token = ctx.token

    hub_router = _HubOwnRouter()

    class HubHandler(_ResponseMixin, BaseHTTPRequestHandler):
        CTX = ctx
        TOKEN = ctx.token

        def __init__(self, *a, **kw):
            self.router = hub_router
            super().__init__(*a, **kw)

        # ---- mounts
        def _mount(self, path: str):
            """('/p/<id>', router, rest_path) or None."""
            if not path.startswith("/p/"):
                return None
            rest = path[3:]
            pid, _, sub = rest.partition("/")
            if not pid:
                return None
            router = ctx.router_for(pid)
            return (f"/p/{pid}", router, "/" + sub)

        def _check_hub_write_auth(self) -> str | None:
            import secrets
            supplied = self.headers.get("X-Tagteam-Token") or ""
            if not supplied or not secrets.compare_digest(supplied, ctx.token):
                return "Missing or invalid X-Tagteam-Token (reload the page to get the current token)"
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if origin:
                host = (self.headers.get("Host") or "").strip()
                netloc = urlparse(origin).netloc
                if not host or netloc != host:
                    return f"Origin {netloc!r} does not match this server ({host!r})"
            return None

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tagteam-Token")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            m = self._mount(parsed.path)
            if m is not None:
                base, router, sub = m
                if router is None:
                    self._send_404(f"Unknown project mount: {base}")
                    return
                self.router = router
                sub = sub.rstrip("/") or "/"
                router.handle_get(self, parsed, sub)
                return
            q = {k: v[0] for k, v in parse_qs(parsed.query or "").items() if v}
            try:
                if path == "/":
                    self._send_html(_get_hub_html(ctx.token))
                elif path == "/api/hub":
                    show_all = q.get("all") in ("1", "true", "yes") or ctx.show_all
                    self._send_json(ctx.payload(show_all=show_all))
                elif path == "/api/hub/usage":
                    win = q.get("window", "24h")
                    agg = hub_api.aggregate_usage([e["path"] for e in hub_api.classify_registry(
                        ctx.paths(), scratch_prefixes=ctx.scratch_prefixes) if not e["hidden"]])
                    self._send_json({"window": win, "usage": agg.get(win, agg), "all": agg})
                elif path in ("/api/hub/info", "/api/info"):
                    from tagteam import read_api  # Phase 74: the version of the promise + its endpoints
                    self._send_json({"app": "tagteam", "kind": "hub", "project": "hub",
                                     "mode": "hub", "max_sse": ctx.max_sse, "sse_active": ctx.sse_state["active"],
                                     "interval_s": ctx.interval_s, "registry": str(ctx.registry_file) if ctx.registry_file else None,
                                     "mounted": sorted(ctx.routers), **read_api.info_fields("hub")})
                elif path == "/api/hub/events":
                    self._hub_sse()
                elif path.startswith("/api/"):
                    self._send_404()
                else:
                    result = _get_static_file(path.lstrip("/"))
                    if result:
                        content, content_type = result
                        self.send_response(200)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Content-Length", str(len(content)))
                        self.end_headers()
                        self.wfile.write(content)
                    else:
                        self._send_404()
            except Exception as exc:  # never a traceback to the browser
                self.log_message("hub GET %s failed: %s", path, exc)
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self):
            parsed = urlparse(self.path)
            m = self._mount(parsed.path)
            if m is not None:
                base, router, sub = m
                if router is None:
                    self._send_404(f"Unknown project mount: {base}")
                    return
                self.router = router
                router.handle_post(self, parsed, sub.rstrip("/"))
                return
            # The hub itself has no write endpoints.
            why = self._check_hub_write_auth()
            if why:
                self._send_json({"error": why, "ok": False}, 403)
                return
            self._send_404()

        def _client_gone(self) -> bool:
            import select, socket
            try:
                r, _, _ = select.select([self.connection], [], [], 0)
                if r:
                    return self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True
            return False

        def _hub_sse(self):
            from tagteam import procs, cockpit_api as capi
            with ctx.sse_lock:
                if ctx.sse_state["active"] >= ctx.max_sse:
                    self._send_json({"error": f"Too many live connections (max {ctx.max_sse})"}, 503)
                    return
                ctx.sse_state["active"] += 1
            stop = getattr(self.server, "stop_event", None)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def snapshot():
                    try:
                        snap = procs.list_processes(capi.WATCH_ARGV_RE.pattern)
                    except Exception:
                        snap = []
                    sig = ctx.signature(procs_snapshot=snap)
                    return sig, hub_api.signature_id(sig)

                def frame(sid, sig):
                    data = json.dumps({"projects": len(sig.get("projects") or {}), "id": sid})
                    self.wfile.write(f"id: {sid}\nevent: change\ndata: {data}\n\n".encode())
                    self.wfile.flush()

                sig, last_id = snapshot()
                frame(last_id, sig)
                last_beat = time.monotonic()
                while True:
                    if stop is not None:
                        if stop.wait(ctx.interval_s):
                            break
                    else:
                        time.sleep(ctx.interval_s)
                    if self._client_gone():
                        break
                    sig, sid = snapshot()
                    now = time.monotonic()
                    if sid != last_id:
                        frame(sid, sig); last_id = sid; last_beat = now
                    elif now - last_beat >= ctx.heartbeat_s:
                        self.wfile.write(b": heartbeat\n\n"); self.wfile.flush(); last_beat = now
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with ctx.sse_lock:
                    ctx.sse_state["active"] -= 1

    return HubHandler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolve_hub_options(args: list[str]) -> dict | str:
    opts = {"port": DEFAULT_PORT, "host": "127.0.0.1", "registry": None, "interval": DEFAULT_INTERVAL_S,
            "max_sse": DEFAULT_MAX_SSE, "all": False, "list": False, "json": False, "help": False}
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            opts["help"] = True; i += 1
        elif a == "--list":
            opts["list"] = True; i += 1
        elif a == "--json":
            opts["json"] = True; i += 1
        elif a == "--all":
            opts["all"] = True; i += 1
        elif a == "--port" and i + 1 < len(args):
            try:
                opts["port"] = int(args[i + 1])
            except ValueError:
                return f"Invalid port: {args[i + 1]}"
            i += 2
        elif a == "--host" and i + 1 < len(args):
            opts["host"] = args[i + 1]; i += 2
        elif a == "--registry" and i + 1 < len(args):
            opts["registry"] = os.path.expanduser(args[i + 1]); i += 2
        elif a == "--interval" and i + 1 < len(args):
            try:
                opts["interval"] = max(0.5, float(args[i + 1]))
            except ValueError:
                return f"Invalid --interval: {args[i + 1]}"
            i += 2
        elif a == "--max-sse" and i + 1 < len(args):
            try:
                opts["max_sse"] = max(1, int(args[i + 1]))
            except ValueError:
                return f"Invalid --max-sse: {args[i + 1]}"
            i += 2
        else:
            return f"Unknown argument: {a}"
    return opts


def _registry_reader(path: str | None):
    """(reader, registry_file). Default: `~/.tagteam/projects.json` via the
    NON-mutating raw read; `--registry PATH` reads that file the same way."""
    if path is None:
        return registry_mod.read_registry_raw, registry_mod.registry_path()
    p = Path(path)

    def reader():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []
    return reader, p


def hub_command(args: list[str], out=None, *,
                scratch_prefixes: tuple[str, ...] = hub_api.SCRATCH_PREFIXES) -> int:
    out = out or sys.stdout
    opts = resolve_hub_options(args)
    if isinstance(opts, str):
        print(opts, file=out); return 1
    if opts["help"]:
        print("Usage: tagteam hub [--port 8090] [--host 127.0.0.1] [--interval 3] [--max-sse 8]"
              " [--registry PATH] [--all]", file=out)
        print("       tagteam hub --list [--json] [--all]", file=out)
        print("  One surface over every registered project: Needs you → Waiting → Quiet, burn and", file=out)
        print("  the shared subscription window; each project's cockpit is mounted at /p/<id>/.", file=out)
        print("  --list prints the triage once (text; --json for scripts). --all includes hidden", file=out)
        print("  entries (missing dirs, scratch paths, dirs without tagteam.yaml).", file=out)
        print("  Read-only: the hub never migrates a project DB or rewrites the registry.", file=out)
        return 0
    reader, reg_file = _registry_reader(opts["registry"])
    if opts["list"]:
        payload = hub_api.hub_payload(reader(), show_all=opts["all"], scratch_prefixes=scratch_prefixes)
        if opts["json"]:
            print(json.dumps(payload, indent=2, default=str), file=out)
        else:
            print(hub_api.render_text(payload), file=out)
        return 0
    token = new_token()
    ctx = HubContext(registry_reader=reader, registry_file=reg_file, token=token, max_sse=opts["max_sse"],
                     interval_s=opts["interval"], show_all=opts["all"], scratch_prefixes=scratch_prefixes)
    handler = make_hub_handler(ctx)
    from tagteam import portlease
    try:
        lease = portlease.acquire(opts["port"], host=opts["host"], project="hub", kind="hub")
    except portlease.PortHeld as held:
        print(held.reason, file=out)
        return 2
    if portlease.probe_occupied(opts["host"], opts["port"]):
        print(portlease.occupied_message(opts["host"], opts["port"]), file=out)
        lease.release()
        return 2
    try:
        server = TagteamHTTPServer((opts["host"], opts["port"]), handler)
    except OSError as e:
        print(portlease.occupied_message(opts["host"], opts["port"]) + f" ({e.strerror or e})", file=out)
        lease.release()
        return 2
    n = len(reader())
    print("Tagteam Hub", file=out)
    print(f"  Registry: {reg_file} ({n} project(s))", file=out)
    shown = "localhost" if opts["host"] in ("127.0.0.1", "localhost") else (opts["host"] or "0.0.0.0")
    print(f"  URL:      http://{shown}:{opts['port']}   (each project's cockpit at /p/<id>/)", file=out)
    if opts["host"] not in ("127.0.0.1", "localhost"):
        print("  WARNING: reachable from other hosts; the page token is the only write guard", file=out)
    print(file=out); print("Press Ctrl+C to stop.", file=out); print(file=out)
    from tagteam.server import _install_sigterm_as_interrupt
    _install_sigterm_as_interrupt()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=out)
    finally:
        server.stop_event.set()
        server.server_close()
        lease.release()
    return 0


def registry_command(args: list[str], out=None, *,
                     scratch_prefixes: tuple[str, ...] = hub_api.SCRATCH_PREFIXES) -> int:
    """`tagteam registry list [--json]` (raw, non-mutating) and
    `tagteam registry unregister PATH` (the only mutation)."""
    out = out or sys.stdout
    if not args or args[0] in ("-h", "--help"):
        print("Usage: tagteam registry list [--json]", file=out)
        print("       tagteam registry unregister PATH", file=out)
        print("  The registry (~/.tagteam/projects.json) is written by `tagteam setup`; the hub", file=out)
        print("  reads it without pruning. `list` shows every entry with a marker (ok / legacy /", file=out)
        print("  missing / no-yaml / scratch); `unregister` removes one entry.", file=out)
        return 0 if args else 1
    sub = args[0]
    if sub == "list":
        entries = hub_api.classify_registry(registry_mod.read_registry_raw(), scratch_prefixes=scratch_prefixes)
        if "--json" in args[1:]:
            print(json.dumps(entries, indent=2), file=out); return 0
        if not entries:
            print("No projects registered (run `tagteam setup` in a project).", file=out); return 0
        for e in entries:
            print(f"{e['kind']:<8} {e['path']}", file=out)
        return 0
    if sub == "unregister":
        if len(args) < 2:
            print("unregister needs a PATH", file=out); return 1
        target = str(Path(os.path.expanduser(args[1])).resolve())
        before = registry_mod.read_registry_raw()
        if target not in before:
            print(f"Not registered: {target}", file=out); return 1
        registry_mod.unregister_project(target)
        print(f"Unregistered {target}", file=out)
        return 0
    print(f"Unknown registry subcommand: {sub}", file=out)
    return 1
