"""
Web server for the Tagteam dashboard.

Serves the steam engine dashboard HTML and provides a JSON API
for reading/updating handoff state.

Usage:
    python -m tagteam serve                       # port 8080, current dir
    python -m tagteam serve --port 3000           # custom port
    python -m tagteam serve --dir ~/projects/foo  # explicit project dir
"""

import json
import os
import re
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from tagteam.config import read_config as _read_config_file, get_agent_names
from tagteam.cycle import list_cycles, read_status as read_cycle_status, render_cycle
from tagteam.parser import extract_all_rounds, format_rounds_html, read_cycle_rounds
from tagteam.state import read_state, update_state


CONFIG_TEMPLATE = """\
# Tagteam Configuration
# Defines the two AI agents and their roles in the collaboration workflow.

agents:
  lead:
    name: "{lead_name}"
  reviewer:
    name: "{reviewer_name}"
"""

SAFE_AGENT_NAME = re.compile(r'^[\w\s\-\.]+$')
# Stricter pattern for /api/launch: the name becomes a shell command fallback
# when tagteam.yaml has no explicit command, so it must be a single token.
SAFE_LAUNCH_AGENT_NAME = re.compile(r'^[a-zA-Z][a-zA-Z0-9_\-]{0,31}$')
_SLUG_STRIP = re.compile(r'[^a-z0-9]+')
_SLUG_TRIM = re.compile(r'^-+|-+$')


def _slugify(text: str, max_len: int = 40) -> str:
    """Derive a phase slug from free-text. Returns '' if nothing usable remains."""
    s = _SLUG_STRIP.sub('-', text.lower())
    s = _SLUG_TRIM.sub('', s)
    s = s[:max_len]
    s = _SLUG_TRIM.sub('', s)
    if not s or not s[0].isalnum():
        return ''
    return s


def _initial_phase_markdown(slug: str, first_prompt: str) -> str:
    """Scaffold a phase markdown file from the user's launch prompt."""
    title = slug.replace('-', ' ').title()
    return (
        f"# Phase: {title}\n\n"
        "## Summary\n"
        f"{first_prompt}\n\n"
        "## Scope\n"
        "_Lead to fill in during plan review._\n\n"
        "## Technical Approach\n"
        "_Lead to fill in during plan review._\n\n"
        "## Success Criteria\n"
        "_Lead to fill in during plan review._\n"
    )


def _detect_backend_safe() -> str:
    try:
        from tagteam.session import default_backend
        return default_backend()
    except Exception:
        return "unknown"


def _read_config(project_dir: str) -> dict | None:
    """Read tagteam.yaml from project directory."""
    config_path = Path(project_dir) / "tagteam.yaml"
    return _read_config_file(config_path)


_WEB_DIR = Path(__file__).parent / "data" / "web"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _get_dashboard_html() -> bytes:
    """Load the dashboard HTML from package data."""
    return (_WEB_DIR / "index.html").read_bytes()


def _get_static_file(filename: str) -> tuple[bytes, str] | None:
    """Load a static file from the web data directory. Returns (content, content_type) or None."""
    safe = Path(filename).name
    path = _WEB_DIR / safe
    if not path.is_file():
        return None
    suffix = path.suffix.lower()
    content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
    return path.read_bytes(), content_type


def _list_dir_md(project_dir: str, subdir: str) -> list[str]:
    """List .md files in a docs subdirectory."""
    d = Path(project_dir) / "docs" / subdir
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix == ".md")


def _read_doc(project_dir: str, subdir: str, filename: str) -> str | None:
    """Read a markdown file from docs/<subdir>/. Returns None if missing."""
    # Sanitize filename to prevent path traversal
    safe = Path(filename).name
    path = Path(project_dir) / "docs" / subdir / safe
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _get_phases(project_dir: str) -> list[dict]:
    """Build structured phase list with status from cycle documents."""
    phase_files = _list_dir_md(project_dir, "phases")
    current_state = read_state(project_dir) or {}

    # Build a lookup of cycles by phase
    all_cycles = list_cycles(project_dir)
    cycle_lookup: dict[str, dict] = {}  # phase_name -> {plan: cycle, impl: cycle}
    for c in all_cycles:
        phase = c["phase"]
        if phase not in cycle_lookup:
            cycle_lookup[phase] = {}
        cycle_lookup[phase][c["type"]] = c

    phases = []
    for pf in phase_files:
        phase_name = pf.rsplit(".", 1)[0]  # strip .md
        phase_cycles = cycle_lookup.get(phase_name, {})
        has_impl = "impl" in phase_cycles
        has_plan = "plan" in phase_cycles

        step_type = "impl" if has_impl else "plan" if has_plan else ""
        status = "pending"

        # Get status from the most relevant cycle
        if has_impl or has_plan:
            c = phase_cycles.get("impl") or phase_cycles.get("plan")
            if c["format"] == "jsonl":
                cs = read_cycle_status(c["phase"], c["type"], project_dir)
                if cs:
                    state_val = cs.get("state", "pending")
                    status = "done" if state_val == "approved" else state_val
            else:
                cycle_file = f"{c['phase']}_{c['type']}_cycle.md"
                status = _extract_cycle_state(project_dir, cycle_file)

        # Override with current active state if this is the active phase
        if current_state.get("phase") == phase_name:
            active_status = current_state.get("status", "")
            if active_status in ("ready", "working", "escalated"):
                status = active_status
            elif current_state.get("result") == "approved":
                status = "done"

        phases.append({
            "phase": phase_name,
            "type": step_type,
            "status": status,
            "result": "approved" if status == "done" else "",
        })

    return phases


def _extract_cycle_state(project_dir: str, cycle_filename: str) -> str:
    """Extract STATE from a cycle document's CYCLE_STATUS section."""
    path = Path(project_dir) / "docs" / "handoffs" / cycle_filename
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return "pending"
    match = re.search(r"STATE:\s*(\S+)", content)
    if match:
        state_val = match.group(1).strip()
        if state_val == "approved":
            return "done"
        return state_val
    return "pending"


def _get_watcher_status(project_dir: str) -> dict:
    """Check if a watcher daemon is running for this project."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"tagteam.*watch.*{project_dir}"],
            capture_output=True, text=True, timeout=5,
        )
        pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
        if pids and pids[0]:
            return {"running": True, "pid": int(pids[0])}
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return {"running": False, "pid": None}


def _get_session_status(project_dir: str) -> dict:
    """Check for a tmux session associated with this project."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        sessions = result.stdout.strip().split("\n") if result.stdout.strip() else []
        # Look for tagteam session
        for s in sessions:
            if "handoff" in s.lower() or "tagteam" in s.lower():
                return {"active": True, "session": s}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return {"active": False, "session": None}


def _unavailable(reason: str) -> dict:
    return {"available": False, "reason": reason}


def _available(content: str) -> dict:
    return {"available": True, "content": content}


def _get_pane_logs(project_dir: str, n: int = 50) -> dict:
    """Return log-tail content for lead/watcher/reviewer panes.

    Response shape:
        {"lead": {...}, "watcher": {...}, "reviewer": {...}, "backend": "..."}
    Each pane slot is either
        {"available": True, "content": "..."}
    or
        {"available": False, "reason": "..."}.
    """
    try:
        from tagteam.session import default_backend
    except Exception:
        return {
            "backend": "unknown",
            "lead": _unavailable("backend-detection-failed"),
            "watcher": _unavailable("backend-detection-failed"),
            "reviewer": _unavailable("backend-detection-failed"),
        }

    backend = default_backend()
    if backend == "manual":
        reason = "backend=manual"
        return {
            "backend": backend,
            "lead": _unavailable(reason),
            "watcher": _unavailable(reason),
            "reviewer": _unavailable(reason),
        }

    if backend == "tmux":
        from tagteam.watcher import capture_pane, pane_exists
        slots = {}
        for role, target in (
            ("lead", "tagteam:0.0"),
            ("watcher", "tagteam:0.1"),
            ("reviewer", "tagteam:0.2"),
        ):
            if not pane_exists(target):
                slots[role] = _unavailable("no-session")
            else:
                slots[role] = _available(capture_pane(target, last_n_lines=n))
        return {"backend": backend, **slots}

    # iTerm2
    from tagteam.iterm import (
        _read_session_file,
        get_session_contents,
        session_id_is_valid,
    )
    data = _read_session_file(project_dir)
    if not data:
        reason = "no-session"
        return {
            "backend": backend,
            "lead": _unavailable(reason),
            "watcher": _unavailable(reason),
            "reviewer": _unavailable(reason),
        }

    tabs = data.get("tabs", {})
    slots = {}
    for role in ("lead", "watcher", "reviewer"):
        sid = (tabs.get(role) or {}).get("session_id")
        if not sid:
            slots[role] = _unavailable("no-session")
        elif not session_id_is_valid(sid):
            slots[role] = _unavailable("dead-session")
        else:
            slots[role] = _available(get_session_contents(sid, last_n_lines=n))
    return {"backend": backend, **slots}


_STATE_FIELD_VALIDATORS = {
    "turn": (str, {"lead", "reviewer"}),
    "status": (str, {"ready", "working", "done", "escalated", "aborted"}),
    "command": (str, None),
    "phase": (str, None),
    "type": (str, {"plan", "impl"}),
    "round": (int, None),
    "result": (str, None),
    "reason": (str, None),
    "updated_by": (str, None),
    "run_mode": (str, {"single-phase", "full-roadmap"}),
    "roadmap": (dict, None),
}


def _validate_state_post(updates: dict) -> str | None:
    """Validate state POST payload. Returns error message or None."""
    for key, value in updates.items():
        if key not in _STATE_FIELD_VALIDATORS:
            return f"Unknown field: {key}"
        expected_type, allowed_values = _STATE_FIELD_VALIDATORS[key]
        if not isinstance(value, expected_type):
            return (f"Field '{key}' must be {expected_type.__name__},"
                    f" got {type(value).__name__}")
        if allowed_values and value not in allowed_values:
            return (f"Invalid value for '{key}': {value!r}."
                    f" Must be one of: {', '.join(sorted(allowed_values))}")
    return None


def make_handler(project_dir: str):
    """Create a request handler class bound to a specific project directory."""

    class HandoffHandler(BaseHTTPRequestHandler):

        def _send_json(self, data, status=200):
            body = json.dumps(data, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text, status=200):
            body = text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html_bytes, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def _send_404(self, msg="Not found"):
            self._send_json({"error": msg}, 404)

        def do_OPTIONS(self):
            """Handle CORS preflight."""
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/":
                self._send_html(_get_dashboard_html())

            elif path == "/api/state":
                state = read_state(project_dir)
                self._send_json(state or {})

            elif path == "/api/config":
                config = _read_config(project_dir)
                self._send_json(config or {})

            elif path == "/api/cycles":
                cycles = list_cycles(project_dir)
                self._send_json([c["id"] for c in cycles])

            elif path.startswith("/api/cycle/"):
                cycle_id = path[len("/api/cycle/"):]
                # Try JSONL-backed render first
                parts = cycle_id.rsplit("_", 1)
                rendered = None
                if len(parts) == 2:
                    phase, cycle_type = parts
                    rendered = render_cycle(phase, cycle_type, project_dir)
                if rendered:
                    self._send_text(rendered)
                else:
                    # Fall back to legacy .md (try with and without .md suffix)
                    filename = cycle_id if cycle_id.endswith(".md") else cycle_id + "_cycle.md"
                    content = _read_doc(project_dir, "handoffs", filename)
                    if content is None:
                        self._send_404(f"Cycle not found: {cycle_id}")
                    else:
                        self._send_text(content)

            elif path == "/api/phases":
                phases = _get_phases(project_dir)
                self._send_json(phases)

            elif path.startswith("/api/rounds/"):
                cycle_id = path[len("/api/rounds/"):]
                # Try format dispatcher (handles JSONL and legacy .md)
                rounds = None
                parts = cycle_id.rsplit("_", 1)
                if len(parts) == 2:
                    phase, cycle_type = parts
                    rounds = read_cycle_rounds(phase, cycle_type, project_dir)
                if rounds is None:
                    # Try legacy filename directly
                    filename = cycle_id if cycle_id.endswith(".md") else cycle_id + "_cycle.md"
                    safe = Path(filename).name
                    cycle_path = Path(project_dir) / "docs" / "handoffs" / safe
                    if cycle_path.is_file():
                        rounds = extract_all_rounds(cycle_path)
                if rounds is None:
                    self._send_json({"rounds": [], "html": ""})
                else:
                    self._send_json({
                        "rounds": rounds,
                        "html": format_rounds_html(rounds),
                    })

            elif path == "/api/watcher/status":
                status = _get_watcher_status(project_dir)
                self._send_json(status)

            elif path == "/api/watcher/logs":
                from urllib.parse import parse_qs
                qs = parse_qs(parsed.query or "")
                try:
                    n = int((qs.get("n") or ["50"])[0])
                except ValueError:
                    n = 50
                n = max(1, min(n, 500))
                self._send_json(_get_pane_logs(project_dir, n=n))

            elif path == "/api/session/status":
                status = _get_session_status(project_dir)
                self._send_json(status)

            elif path == "/api/dialogue":
                state = read_state(project_dir)
                if not state or not state.get("phase") or not state.get("type"):
                    self._send_json({"lines": []})
                else:
                    # Import dialogue builder (requires textual optional dep)
                    try:
                        from tagteam.tui.review_dialogue import build_state_dialogue
                        from tagteam.tui.state_watcher import HandoffState
                        hs = HandoffState.from_dict(state)
                        lines = build_state_dialogue(hs, None, project_dir=project_dir)
                        self._send_json({"lines": [{"speaker": s, "text": t} for s, t in lines]})
                    except ImportError:
                        # textual not installed — return empty
                        self._send_json({"lines": [], "error": "TUI package not installed"})

            elif path.startswith("/") and not path.startswith("/api/"):
                # Serve static files (CSS, JS) from web directory
                filename = path.lstrip("/")
                result = _get_static_file(filename)
                if result:
                    content, content_type = result
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self._send_404()

            else:
                self._send_404()

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/api/state":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    updates = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    self._send_json({"error": "Invalid JSON"}, 400)
                    return

                if not isinstance(updates, dict):
                    self._send_json({"error": "Expected JSON object"}, 400)
                    return

                # Validate fields: names, types, and allowed values
                err = _validate_state_post(updates)
                if err:
                    self._send_json({"error": err}, 400)
                    return

                new_state = update_state(updates, project_dir)
                self._send_json(new_state)

            elif path == "/api/config":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    self._send_json({"error": "Invalid JSON"}, 400)
                    return

                if not isinstance(data, dict):
                    self._send_json({"error": "Expected JSON object"}, 400)
                    return

                lead = (data.get("lead") or "").strip()
                reviewer = (data.get("reviewer") or "").strip()
                overwrite = data.get("overwrite") is True

                if not lead or not reviewer:
                    self._send_json({"error": "Both 'lead' and 'reviewer' are required"}, 400)
                    return
                if not SAFE_AGENT_NAME.match(lead) or not SAFE_AGENT_NAME.match(reviewer):
                    self._send_json({"error": "Agent names must contain only letters, numbers, spaces, hyphens, and dots"}, 400)
                    return

                config_path = Path(project_dir) / "tagteam.yaml"
                if config_path.exists() and not overwrite:
                    existing = _read_config(project_dir)
                    self._send_json({
                        "error": "Config already exists",
                        "existing": existing or {}
                    }, 409)
                    return

                content = CONFIG_TEMPLATE.format(lead_name=lead, reviewer_name=reviewer)
                config_path.write_text(content, encoding="utf-8")
                self._send_json(_read_config(project_dir) or {})

            elif path == "/api/launch":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    self._send_json({"error": "Invalid JSON"}, 400)
                    return

                if not isinstance(data, dict):
                    self._send_json({"error": "Expected JSON object"}, 400)
                    return

                lead = (data.get("lead") or "").strip()
                reviewer = (data.get("reviewer") or "").strip()
                first_prompt = (data.get("first_prompt") or "").strip()

                if not lead or not reviewer:
                    self._send_json({"error": "Both 'lead' and 'reviewer' are required"}, 400)
                    return
                if (not SAFE_LAUNCH_AGENT_NAME.match(lead)
                        or not SAFE_LAUNCH_AGENT_NAME.match(reviewer)):
                    self._send_json({
                        "error": ("Agent names must start with a letter and contain only "
                                  "letters, digits, hyphens, or underscores (max 32 chars)."),
                    }, 400)
                    return
                if not first_prompt:
                    self._send_json({"error": "'first_prompt' is required"}, 400)
                    return
                if len(first_prompt) > 2000:
                    self._send_json({"error": "'first_prompt' must be 2000 chars or fewer"}, 400)
                    return

                slug = _slugify(first_prompt)
                if not slug:
                    self._send_json({"error": "Could not derive a phase slug from first_prompt"}, 400)
                    return

                config_path = Path(project_dir) / "tagteam.yaml"
                if config_path.exists():
                    existing = _read_config(project_dir)
                    if existing is None:
                        self._send_json({
                            "error": "tagteam.yaml exists but could not be parsed. "
                                     "Fix or remove it before launching from the saloon.",
                        }, 409)
                        return
                    ex_lead, ex_reviewer = get_agent_names(existing)
                    if not ex_lead or not ex_reviewer:
                        self._send_json({
                            "error": "tagteam.yaml exists but is missing a lead or reviewer. "
                                     "Fix or remove it before launching from the saloon.",
                            "existing": {"lead": ex_lead, "reviewer": ex_reviewer},
                        }, 409)
                        return
                    if ex_lead != lead or ex_reviewer != reviewer:
                        self._send_json({
                            "error": "tagteam.yaml exists with different agent names",
                            "existing": {"lead": ex_lead, "reviewer": ex_reviewer},
                        }, 409)
                        return
                else:
                    try:
                        from tagteam.cli import write_config
                        write_config(project_dir, lead, reviewer)
                    except Exception as exc:
                        self._send_json({"error": f"Failed to write tagteam.yaml: {exc}"}, 500)
                        return

                phases_dir = Path(project_dir) / "docs" / "phases"
                phases_dir.mkdir(parents=True, exist_ok=True)
                phase_file = phases_dir / f"{slug}.md"
                if phase_file.exists():
                    self._send_json({
                        "error": f"Phase file already exists: docs/phases/{slug}.md",
                    }, 409)
                    return
                phase_content = _initial_phase_markdown(slug, first_prompt)
                try:
                    phase_file.write_text(phase_content, encoding="utf-8")
                except OSError as exc:
                    self._send_json({"error": f"Failed to write phase file: {exc}"}, 500)
                    return

                try:
                    from tagteam.cycle import init_cycle
                    summary = first_prompt.splitlines()[0][:280]
                    init_cycle(
                        phase=slug,
                        cycle_type="plan",
                        lead=lead,
                        reviewer=reviewer,
                        content=summary,
                        project_dir=project_dir,
                        updated_by="saloon",
                    )
                except Exception as exc:
                    self._send_json({
                        "error": f"Failed to initialize cycle: {exc}",
                        "partial": True,
                        "phase": slug,
                    }, 500)
                    return

                try:
                    from tagteam.session import ensure_session
                    result = ensure_session(
                        project_dir,
                        launch=True,
                        attach_existing=False,
                    )
                except Exception as exc:
                    self._send_json({
                        "error": f"Failed to launch session: {exc}",
                        "partial": True,
                        "phase": slug,
                    }, 500)
                    return

                # ensure_session reports backend/session failures by returning
                # the sentinel string "error" rather than raising. Config, phase
                # file, and cycle have already been written at this point;
                # surface a non-2xx so the browser doesn't transition to live
                # mode, but leave the partial artifacts in place (recoverable
                # via the CLI).
                if result == "error":
                    self._send_json({
                        "error": ("Session launch failed. Config, phase, and cycle were "
                                  "created; start the session manually with "
                                  "`tagteam session start --launch`."),
                        "partial": True,
                        "phase": slug,
                        "backend": _detect_backend_safe(),
                    }, 500)
                    return

                self._send_json({
                    "status": result if result in ("created", "exists", "manual") else "ok",
                    "session": result,
                    "phase": slug,
                    "backend": _detect_backend_safe(),
                })

            elif path == "/api/start-phase":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    data = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    self._send_json({"error": "Invalid JSON"}, 400)
                    return

                if not isinstance(data, dict):
                    self._send_json({"error": "Expected JSON object"}, 400)
                    return

                phase = (data.get("phase") or "").strip()
                phase_type = (data.get("type") or "").strip()

                if not phase:
                    self._send_json({"error": "'phase' is required"}, 400)
                    return
                if phase_type not in ("plan", "impl"):
                    self._send_json({"error": "'type' must be 'plan' or 'impl'"}, 400)
                    return

                current = read_state(project_dir)
                if current and current.get("status") in ("ready", "working", "escalated"):
                    self._send_json({
                        "error": "Active handoff in progress",
                        "state": current
                    }, 409)
                    return

                new_state = update_state({
                    "phase": phase,
                    "type": phase_type,
                    "turn": "lead",
                    "status": "ready",
                    "round": 1,
                    "command": f"/handoff-cycle {phase}",
                    "result": None,
                    "reason": None,
                }, project_dir)
                self._send_json(new_state)

            else:
                self._send_404()

        def log_message(self, format, *args):
            """Quieter logging — single line per request."""
            print(f"  {self.address_string()} {format % args}")

    return HandoffHandler


def serve_command(args: list[str]) -> int:
    """Handle `python -m tagteam serve [--port N] [--dir PATH]`."""
    port = 8080
    project_dir = "."

    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                print(f"Invalid port: {args[i + 1]}")
                return 1
            i += 2
        elif args[i] == "--dir" and i + 1 < len(args):
            project_dir = os.path.expanduser(args[i + 1])
            i += 2
        elif args[i] in ("-h", "--help"):
            print("Usage: python -m tagteam serve [--port PORT] [--dir DIR]")
            print()
            print("  --port PORT  Port to listen on (default: 8080)")
            print("  --dir DIR    Project directory (default: current directory)")
            return 0
        else:
            print(f"Unknown argument: {args[i]}")
            return 1

    project_dir = os.path.abspath(project_dir)

    # Verify project directory looks valid
    config_path = Path(project_dir) / "tagteam.yaml"
    if not config_path.exists():
        print(f"  No tagteam.yaml yet — the Mayor will help you set up.")
        print()

    handler = make_handler(project_dir)
    server = HTTPServer(("", port), handler)

    print(f"Tagteam Dashboard")
    print(f"  Project: {project_dir}")
    print(f"  URL:     http://localhost:{port}")
    print()
    print("Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

    return 0
