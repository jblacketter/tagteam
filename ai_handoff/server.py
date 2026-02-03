"""
Web server for the AI Handoff dashboard.

Serves the steam engine dashboard HTML and provides a JSON API
for reading/updating handoff state.

Usage:
    python -m ai_handoff serve                       # port 8080, current dir
    python -m ai_handoff serve --port 3000           # custom port
    python -m ai_handoff serve --dir ~/projects/foo  # explicit project dir
"""

import json
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from ai_handoff.config import read_config as _read_config_file, get_agent_names
from ai_handoff.state import read_state, update_state


CONFIG_TEMPLATE = """\
# AI Handoff Configuration
# Defines the two AI agents and their roles in the collaboration workflow.

agents:
  lead:
    name: "{lead_name}"
  reviewer:
    name: "{reviewer_name}"
"""

SAFE_AGENT_NAME = re.compile(r'^[\w\s\-\.]+$')


def _read_config(project_dir: str) -> dict | None:
    """Read ai-handoff.yaml from project directory."""
    config_path = Path(project_dir) / "ai-handoff.yaml"
    return _read_config_file(config_path)


def _get_dashboard_html() -> bytes:
    """Load the dashboard HTML from package data."""
    html_path = Path(__file__).parent / "data" / "web" / "index.html"
    return html_path.read_bytes()


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
    return path.read_text()


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
                files = _list_dir_md(project_dir, "handoffs")
                self._send_json(files)

            elif path.startswith("/api/cycle/"):
                filename = path[len("/api/cycle/"):]
                content = _read_doc(project_dir, "handoffs", filename)
                if content is None:
                    self._send_404(f"Cycle doc not found: {filename}")
                else:
                    self._send_text(content)

            elif path == "/api/phases":
                files = _list_dir_md(project_dir, "phases")
                self._send_json(files)

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

                config_path = Path(project_dir) / "ai-handoff.yaml"
                if config_path.exists() and not overwrite:
                    existing = _read_config(project_dir)
                    self._send_json({
                        "error": "Config already exists",
                        "existing": existing or {}
                    }, 409)
                    return

                content = CONFIG_TEMPLATE.format(lead_name=lead, reviewer_name=reviewer)
                config_path.write_text(content)
                self._send_json(_read_config(project_dir) or {})

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
    """Handle `python -m ai_handoff serve [--port N] [--dir PATH]`."""
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
            print("Usage: python -m ai_handoff serve [--port PORT] [--dir DIR]")
            print()
            print("  --port PORT  Port to listen on (default: 8080)")
            print("  --dir DIR    Project directory (default: current directory)")
            return 0
        else:
            print(f"Unknown argument: {args[i]}")
            return 1

    project_dir = os.path.abspath(project_dir)

    # Verify project directory looks valid
    config_path = Path(project_dir) / "ai-handoff.yaml"
    if not config_path.exists():
        print(f"  No ai-handoff.yaml yet — the Mayor will help you set up.")
        print()

    handler = make_handler(project_dir)
    server = HTTPServer(("", port), handler)

    print(f"AI Handoff Dashboard")
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
