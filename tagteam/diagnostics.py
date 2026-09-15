"""Phase 53: ``tagteam doctor`` — legacy workflow diagnostics and capability
visibility. Report-only.

Two questions a package upgrade does not answer:

* **Legacy workflow artifacts.** Does the project still carry pre-plugin
  skills or instructions that name a fixed role holder ("Claude is always the
  lead") or use retired command syntax (the pre-plugin ``handoff-*`` slash commands)? Findings need
  content evidence — a file name or a provider mention alone is never one —
  and every finding is a *candidate*: tagteam holds no provenance for these
  files. Paths Phase 52 manages are excluded; their state is the framework
  section's.
* **What each role's process can reach.** Launch executable, headless
  provider and executable (observed separately), contract entry points,
  instruction sources, tool configuration by name, and which protections are
  instructions rather than enforcement. States: ``configured`` / ``found`` /
  ``missing`` / ``unknown``; a check that could not run is ``unknown``, never
  ``missing``.

Reading is not printing. Every file goes through :func:`read_bounded`
(lstat from the project root down, regular files only, never a symlink,
opened ``O_NOFOLLOW|O_NONBLOCK``, size-bounded). Config parsing reads
secret-bearing bytes into memory; the guarantee is the output: no config
values, command arguments, ``env``, ``headers`` or ``args`` are emitted.
Nothing is written, logged, probed or authenticated. The one subprocess is
Phase 48's ``claude plugin list``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tagteam import config as config_mod
from tagteam.config import HEADLESS_PROVIDERS, get_headless_spec
from tagteam.framework import SKILL_REL
from tagteam.plugin import (PLUGIN_KEY, SKILL_IN_PLUGIN, PluginStatus, _same_path,
                            legacy_handoff_skill_candidates, list_plugins)

SCHEMA = 1
MARKDOWN_LIMIT = 256 * 1024
JSON_LIMIT = 64 * 1024
EXCERPT_CHARS = 120
CONFIG_NAME = "tagteam.yaml"
ROLES = ("lead", "reviewer")
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
CLAUDE_SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
PROVENANCE = "candidate: content match, no provenance"

#: Skill names of the retired pre-plugin workflow (flat ``plan.md`` … shipped
#: in Jan 2026, later ``handoff-*``). A name alone is never a finding.
LEGACY_SKILL_NAMES = ("plan", "review", "implement", "decide", "escalate", "phase", "status", "sync")

_RETIRED = re.compile(
    r"(?<![\w/.-])/handoff-(?:cycle|plan|review|implement|decide|escalate|phase|status|sync|handoff)\b"
    r"|\bai_handoff\b")


def _fixed_role_patterns(name: str) -> list[tuple[re.Pattern, str | None]]:
    """(pattern, role) for one provider/agent name; role None = the match's
    group 1. "final decision/say" is the legacy lead's authority."""
    n = re.escape(name)
    return [
        (re.compile(rf"\b{n}\b\s+is\s+(?:always\s+)?the\s+(lead|reviewer)\b", re.I), None),
        (re.compile(rf"\b{n}\s*\((lead|reviewer)\)", re.I), None),
        (re.compile(rf"\b{n}\b\s+has\s+(?:the\s+)?final\s+(?:decision|say)\b", re.I), "lead"),
    ]


RULES = {
    "retired-command": "Retired command syntax: the /handoff-* commands and the ai_handoff package "
                       "were removed. Use /tagteam:handoff (Claude Code) or `tagteam contract` "
                       "(any shell agent).",
    "fixed-role": "Roles come from tagteam.yaml ({roles}). Reword to 'the lead' / 'the reviewer' "
                  "or point at tagteam.yaml.",
    "fixed-role-match": "Matches tagteam.yaml today ({roles}) but goes stale on a role switch; "
                        "consider role-neutral wording.",
    "legacy-skill-shape": "Probably a pre-plugin tagteam workflow skill. Review it and retire it by "
                          "hand in favour of /tagteam:handoff (or `tagteam contract`).",
}


# ---------------------------------------------------------------------------
# Bounded, shape-checked reads
# ---------------------------------------------------------------------------

@dataclass
class Read:
    state: str                  # absent | file | unsupported | skipped | unreadable
    detail: str = ""
    data: bytes | None = None
    size: int | None = None
    truncated: bool = False


def _lstat_chain(root: Path, rel: str) -> Read:
    parts = Path(rel).parts
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        shown = cur.relative_to(root).as_posix()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return Read("absent")
        except OSError as e:
            return Read("unreadable", f"{shown} unreadable ({e.__class__.__name__})")
        if stat.S_ISLNK(st.st_mode):
            return Read("unsupported", f"{shown} is a symlink")
        if i < len(parts) - 1:
            if not stat.S_ISDIR(st.st_mode):
                return Read("unsupported", f"{shown} is not a directory")
        elif stat.S_ISDIR(st.st_mode):
            return Read("unsupported", f"{shown} is a directory")
        elif not stat.S_ISREG(st.st_mode):
            return Read("unsupported", f"{shown} is not a regular file")
        else:
            return Read("file", size=st.st_size)
    return Read("absent")


def _dir_chain(root: Path, rel: str) -> tuple[str, str]:
    """(``dir`` | ``absent`` | ``unsupported``, detail) — every component a
    plain directory, never a symlink."""
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        shown = cur.relative_to(root).as_posix()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return "absent", ""
        except OSError as e:
            return "unsupported", f"{shown} unreadable ({e.__class__.__name__})"
        if stat.S_ISLNK(st.st_mode):
            return "unsupported", f"{shown} is a symlink"
        if not stat.S_ISDIR(st.st_mode):
            return "unsupported", f"{shown} is not a directory"
    return "dir", ""


def read_bounded(root: Path, rel: str, limit: int, *, truncate: bool) -> Read:
    """Read ``rel`` under ``root`` only if every component is a plain
    directory and the leaf a regular file. Over ``limit``: the first
    ``limit`` bytes when ``truncate``, else ``skipped`` and nothing read."""
    r = _lstat_chain(root, rel)
    if r.state != "file":
        return r
    if r.size is not None and r.size > limit and not truncate:
        return Read("skipped", f"over size limit ({limit // 1024} KB)", size=r.size)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(root / rel, flags)
    except OSError as e:
        return Read("unreadable", f"{rel} unreadable ({e.__class__.__name__})")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return Read("unsupported", f"{rel} is not a regular file")
        chunks, left = [], limit + 1
        while left > 0:
            b = os.read(fd, min(65536, left))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
    except OSError as e:
        return Read("unreadable", f"{rel} unreadable ({e.__class__.__name__})")
    finally:
        os.close(fd)
    if len(data) > limit:
        if not truncate:
            return Read("skipped", f"over size limit ({limit // 1024} KB)", size=st.st_size)
        return Read("file", data=data[:limit], size=st.st_size, truncated=True)
    return Read("file", data=data, size=st.st_size)


def _read_json(root: Path, rel: str) -> tuple[Read, object]:
    r = read_bounded(root, rel, JSON_LIMIT, truncate=False)
    if r.state != "file":
        return r, None
    try:
        return r, json.loads(r.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return Read("unknown", f"malformed {rel}", size=r.size), None


# ---------------------------------------------------------------------------
# Roles: desktop launch and headless, observed separately
# ---------------------------------------------------------------------------

def _provider_of(token: str) -> str | None:
    base = token.lower()
    for prov in HEADLESS_PROVIDERS:
        if base == prov or base.startswith(prov):
            return prov
    return None


def _which(exe: str) -> tuple[str, str]:
    path = shutil.which(exe)
    return ("found", path) if path else ("missing", "")


def _agent(config: dict, role: str) -> dict:
    agents = config.get("agents") if isinstance(config, dict) else None
    agent = agents.get(role) if isinstance(agents, dict) else None
    return agent if isinstance(agent, dict) else {}


def _desktop(agent: dict) -> dict:
    name = agent.get("name") if isinstance(agent.get("name"), str) else ""
    command = agent.get("command") if isinstance(agent.get("command"), str) and agent.get("command").strip() \
        else name.lower()
    out = {"executable": "", "state": "unknown", "path": "", "reason": "", "provider": None}
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    if not tokens:
        out["reason"] = "launch command not parsed"
        return out
    first = tokens[0]
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first):
        out["reason"] = "launch command not parsed"
        return out
    out["executable"] = Path(first).name
    out["state"], out["path"] = _which(first)
    out["provider"] = _provider_of(Path(first).name)
    return out


def _headless(config: dict, role: str, agent: dict) -> dict:
    spec = get_headless_spec(config, role)
    provider = spec["provider"]
    block = agent.get("headless") if isinstance(agent.get("headless"), dict) else {}
    if block.get("provider") is not None:
        source = "explicit"
    elif provider is None:
        source = "unresolved"
    else:
        cmd = agent.get("command")
        first = ""
        if isinstance(cmd, str) and cmd.strip():
            first = Path(cmd.strip().split()[0]).name.lower()
        source = "inferred from command" if first and _provider_of(first) == provider else "inferred from name"
    out = {"provider": provider, "source": source, "executable": "", "state": "unknown",
           "path": "", "reason": ""}
    if provider not in HEADLESS_PROVIDERS:
        out["reason"] = "unresolved provider" if provider is None else "unknown provider"
        return out
    exe = spec["executable"] if isinstance(spec["executable"], str) and spec["executable"] else provider
    out["executable"] = exe
    out["state"], out["path"] = _which(exe)
    return out


def _autoload(root: Path, provider: str | None) -> dict:
    from tagteam.headless import PROVIDER_AUTOLOADS
    name = PROVIDER_AUTOLOADS.get(provider) if provider else None
    if not name:
        return {"file": None, "state": "unknown"}
    r = _lstat_chain(root, name)
    return {"file": name, "state": {"file": "found", "absent": "missing"}.get(r.state, "unknown"),
            "detail": r.detail}


def _injection(root: Path, provider: str | None) -> dict:
    """What a headless turn injects, by the engine's own selection."""
    from tagteam.headless import PROJECT_CONTEXT_MAX_CHARS, select_context_file
    if provider not in HEADLESS_PROVIDERS:
        return {"file": None, "state": "unknown", "truncates": None}
    path = select_context_file(root, provider)
    if path is None:
        return {"file": None, "state": "none", "truncates": False}
    r = read_bounded(root, path.name, MARKDOWN_LIMIT, truncate=True)
    if r.state != "file":
        return {"file": path.name, "state": "unknown", "truncates": None,
                "detail": r.detail or r.state}
    try:
        text = r.data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return {"file": path.name, "state": "none", "truncates": False, "detail": "not valid UTF-8"}
    if not text:
        return {"file": path.name, "state": "none", "truncates": False, "detail": "empty"}
    return {"file": path.name, "state": "injected",
            "truncates": r.truncated or len(text) > PROJECT_CONTEXT_MAX_CHARS,
            "chars": len(text), "limit": PROJECT_CONTEXT_MAX_CHARS}


def observe_roles(root: Path, config: dict | None) -> list[dict]:
    # Names are the only requirement: a config the engine would reject (say
    # an unknown headless provider) is still worth describing, as `unknown`.
    if not isinstance(config, dict) or not all(
            isinstance(_agent(config, r).get("name"), str) and _agent(config, r).get("name").strip()
            for r in ROLES):
        return []
    out = []
    for role in ROLES:
        agent = _agent(config, role)
        desk = _desktop(agent)
        desk["autoloads"] = _autoload(root, desk.pop("provider"))
        head = _headless(config, role, agent)
        head["injects"] = _injection(root, head["provider"])
        out.append({"role": role, "name": agent.get("name"), "desktop": desk, "headless": head})
    return out


def _identities(role: dict) -> set[str]:
    ids = {str(role.get("name") or "").lower()}
    exe = role["desktop"].get("executable")
    if exe:
        ids.add(exe.lower())
        prov = _provider_of(exe)
        if prov:
            ids.add(prov)
    if role["headless"].get("provider"):
        ids.add(str(role["headless"]["provider"]).lower())
    return ids - {""}


# ---------------------------------------------------------------------------
# Plugin availability — not PluginStatus.installed
# ---------------------------------------------------------------------------

def plugin_availability(root: Path) -> dict:
    """``found`` / ``missing`` / ``configured, disabled`` / ``configured,
    broken`` / ``unknown``, with the reason. Uses the Phase 48 reader; unlike
    :func:`tagteam.plugin.plugin_status` a failed discovery is ``unknown``."""
    records, err = list_plugins(root)
    if records is None:
        return {"state": "unknown", "reason": err}
    applicable = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("id") != PLUGIN_KEY:
            continue
        scope = rec.get("scope")
        if scope == "user":
            applicable.append(rec)
        elif scope in ("project", "local"):
            pp = rec.get("projectPath")
            if isinstance(pp, str) and _same_path(pp, root):
                applicable.append(rec)
        else:
            return {"state": "unknown", "reason": f"{PLUGIN_KEY}: unsupported scope {scope!r}"}
    if not applicable:
        return {"state": "missing", "reason": f"no applicable {PLUGIN_KEY} record"}
    if len({rec.get("enabled") for rec in applicable}) > 1:
        return {"state": "unknown", "reason": f"{PLUGIN_KEY}: applicable records disagree on enabled"}
    rec = applicable[0]
    scope = rec.get("scope")
    if rec.get("enabled") is not True:
        return {"state": "configured, disabled", "reason": f"{scope} scope"}
    ip = rec.get("installPath")
    if not isinstance(ip, str) or not ip:
        return {"state": "configured, broken", "reason": "install record has no installPath"}
    if not (Path(ip) / SKILL_IN_PLUGIN).is_file():
        return {"state": "configured, broken", "reason": "handoff skill missing from the install"}
    return {"state": "found", "reason": f"{scope} scope"}


# ---------------------------------------------------------------------------
# Framework section (Phase 52 classification, read-only)
# ---------------------------------------------------------------------------

_FRAMEWORK_KINDS = ("file", "skill", "skill-handover", "extra", "legacy")


def observe_framework(root: Path, plugin: dict) -> dict:
    from tagteam import framework
    from tagteam.setup import get_data_dir
    status = PluginStatus(plugin["state"] == "found", plugin.get("reason", ""))
    plan = framework.build_plan(root, data_dir=get_data_dir(), plugin=status)
    m = plan.manifest
    manifest = (f"{m['tagteam']}" + (f" (written {m['written_at']})" if m["written_at"] else "")) \
        if m else plan.manifest_state
    items = [{"path": it.rel, "action": it.action, "reason": it.reason}
             for it in plan.items if it.kind in _FRAMEWORK_KINDS]
    return {"package": plan.package_version, "manifest": manifest, "items": items}


# ---------------------------------------------------------------------------
# Legacy workflow findings
# ---------------------------------------------------------------------------

def _scan_targets(root: Path, managed: set[str]) -> tuple[list[str], list[dict]]:
    """Candidate relpaths to scan, and notes for entries not scanned."""
    targets: list[str] = []
    notes: list[dict] = []
    for base, dirs_ok in ((".claude/skills", True), (".claude/commands", False)):
        kind, detail = _dir_chain(root, base)
        if kind == "absent":
            continue
        if kind != "dir":
            notes.append({"path": base, "detail": f"not scanned: unsupported filesystem shape ({detail})"})
            continue
        try:
            entries = sorted(os.scandir(root / base), key=lambda e: e.name)
        except OSError as e:
            notes.append({"path": base, "detail": f"not scanned ({e.__class__.__name__})"})
            continue
        for e in entries:
            rel = f"{base}/{e.name}"
            if e.is_symlink():
                if e.name.endswith(".md") or dirs_ok:
                    notes.append({"path": rel, "detail": "not scanned: unsupported filesystem shape "
                                                         f"({rel} is a symlink)"})
                continue
            if dirs_ok and e.is_dir(follow_symlinks=False):
                if e.name == "handoff":
                    continue
                cand = f"{rel}/SKILL.md"
                if cand not in managed and _lstat_chain(root, cand).state != "absent":
                    targets.append(cand)
            elif e.name.endswith(".md") and rel not in managed:
                targets.append(rel)
    for name in INSTRUCTION_FILES:
        if name not in managed and _lstat_chain(root, name).state != "absent":
            targets.append(name)
    return targets, notes


def _skill_name(rel: str) -> str | None:
    parts = Path(rel).parts
    if len(parts) >= 3 and parts[:2] == (".claude", "skills"):
        return parts[2][:-3] if parts[2].endswith(".md") else parts[2]
    return None


def _roles_text(roles: list[dict]) -> str:
    if not roles:
        return "roles not configured"
    return ", ".join(f"{r['role']}: {r['name']}" for r in roles)


def scan_file(rel: str, text: str, roles: list[dict]) -> list[dict]:
    names = {"claude", "codex"} | {str(r.get("name") or "").lower() for r in roles}
    names.discard("")
    ids = {r["role"]: _identities(r) for r in roles}
    rtext = _roles_text(roles)
    findings: list[dict] = []
    seen: set[tuple] = set()

    def add(rule, severity, line, excerpt, remediation):
        key = (rule, line, remediation)
        if key in seen:
            return
        seen.add(key)
        findings.append({"path": rel, "rule": rule, "severity": severity, "line": line,
                         "excerpt": excerpt, "provenance": PROVENANCE, "remediation": remediation})

    for lineno, line in enumerate(text.splitlines(), 1):
        # Evidence is the recognised match alone, never the surrounding line:
        # arguments and values next to a command must not reach the output.
        for m in _RETIRED.finditer(line):
            add("retired-command", "warn", lineno, m.group(0)[:EXCERPT_CHARS], RULES["retired-command"])
        for name in sorted(names):
            for pattern, fixed in _fixed_role_patterns(name):
                for m in pattern.finditer(line):
                    role = fixed or m.group(1).lower()
                    excerpt = m.group(0)[:EXCERPT_CHARS]
                    if not roles:
                        add("fixed-role", "info", lineno, excerpt, RULES["fixed-role"].format(roles=rtext))
                    elif name in ids.get(role, set()):
                        add("fixed-role", "info", lineno, excerpt,
                            RULES["fixed-role-match"].format(roles=rtext))
                    else:
                        add("fixed-role", "warn", lineno, excerpt, RULES["fixed-role"].format(roles=rtext))
    skill = _skill_name(rel)
    if skill in LEGACY_SKILL_NAMES and findings:
        sev = "warn" if any(f["severity"] == "warn" for f in findings) else "info"
        findings.insert(0, {"path": rel, "rule": "legacy-skill-shape", "severity": sev, "line": 0,
                            "excerpt": f"skill name '{skill}' with {len(findings)} content hit(s)",
                            "provenance": PROVENANCE, "remediation": RULES["legacy-skill-shape"]})
    return findings


def scan_legacy(root: Path, roles: list[dict], managed: set[str]) -> tuple[list[dict], list[dict]]:
    targets, notes = _scan_targets(root, managed)
    findings: list[dict] = []
    for rel in targets:
        r = read_bounded(root, rel, MARKDOWN_LIMIT, truncate=True)
        if r.state != "file":
            notes.append({"path": rel, "detail": f"not scanned: unsupported filesystem shape ({r.detail})"
                          if r.state == "unsupported" else f"not scanned: {r.detail or r.state}"})
            continue
        if r.truncated:
            notes.append({"path": rel, "detail": f"truncated scan (first {MARKDOWN_LIMIT // 1024} KB)"})
        findings += scan_file(rel, r.data.decode("utf-8", errors="replace"), roles)
    return findings, notes


# ---------------------------------------------------------------------------
# Instruction sources, tool configuration, protections
# ---------------------------------------------------------------------------

def observe_instructions(root: Path) -> list[dict]:
    out = []
    for name in INSTRUCTION_FILES:
        r = _lstat_chain(root, name)
        out.append({"file": name, "state": {"file": "found", "absent": "missing"}.get(r.state, "unknown"),
                    "size": r.size, "detail": r.detail})
    return out


def observe_tools(root: Path) -> dict:
    r, data = _read_json(root, ".mcp.json")
    mcp = {"file": ".mcp.json", "state": r.state, "servers": [], "detail": r.detail}
    if r.state == "file":
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            mcp.update(state="configured (not probed)", servers=sorted(str(k) for k in servers))
        else:
            mcp.update(state="unknown", detail="malformed .mcp.json (no mcpServers object)")
    settings = []
    for rel in CLAUDE_SETTINGS:
        r, data = _read_json(root, rel)
        entry = {"file": rel, "state": r.state, "hooks": [], "detail": r.detail}
        if r.state == "file":
            hooks = data.get("hooks", {}) if isinstance(data, dict) else None
            if not isinstance(hooks, dict):
                entry.update(state="unknown", detail=f"malformed {rel} (hooks is not an object)")
            else:
                found = []
                for event in sorted(hooks):
                    groups = hooks[event] if isinstance(hooks[event], list) else []
                    matchers = sorted({g.get("matcher") for g in groups
                                       if isinstance(g, dict) and isinstance(g.get("matcher"), str)
                                       and g.get("matcher")})
                    found.append(f"{event}[{'|'.join(matchers)}]" if matchers else str(event))
                entry.update(state="configured", hooks=found)
        settings.append(entry)
    c = _lstat_chain(root, ".codex/config.toml")
    codex = {"file": ".codex/config.toml",
             "state": {"file": "configured (not parsed)", "absent": "absent"}.get(c.state, "unknown"),
             "detail": c.detail}
    return {"mcp": mcp, "claude_settings": settings, "codex_config": codex}


def protection_notes(roles: list[dict], tools: dict) -> list[str]:
    notes = []
    hooked = [s["file"] for s in tools["claude_settings"] if s["hooks"]]
    if hooked:
        others = []
        for r in roles:
            provs = {p for p in (_provider_of(r["desktop"].get("executable") or ""),
                                 r["headless"].get("provider")) if p}
            if provs - {"claude"}:
                others.append(f"{r['name']} ({', '.join(sorted(provs - {'claude'}))})")
        tail = f"; they do not bind {', '.join(others)}" if others else ""
        notes.append(f"Claude Code hooks in {', '.join(hooked)} apply to Claude processes only{tail}.")
    notes += [
        "TAGTEAM_READ_ONLY=1 blocks tagteam's own writes for delegated helpers; it does not stop "
        "arbitrary filesystem or API mutation.",
        "One cycle-writing call per turn is enforced by the tagteam CLI and the orchestrator, not by "
        "agent instructions alone.",
        "A desktop session and a headless child can differ in tools, credentials, working directory "
        "and approvals; configuration alone does not show a connection is usable.",
    ]
    return notes


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

@dataclass
class Report:
    root: str
    roles: list = field(default_factory=list)
    roles_configured: bool = False
    contract: dict = field(default_factory=dict)
    framework: dict = field(default_factory=dict)
    instructions: list = field(default_factory=list)
    tools: dict = field(default_factory=dict)
    protections: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    user_level: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def counts(self) -> dict:
        return {"warn": sum(f["severity"] == "warn" for f in self.findings),
                "info": sum(f["severity"] == "info" for f in self.findings)}

    def to_json(self) -> dict:
        d = asdict(self)
        d = {"schema": SCHEMA, **d, "counts": self.counts}
        return d


def read_role_config(root: Path) -> tuple[dict | None, dict | None]:
    """``tagteam.yaml`` through :func:`read_bounded` and parsed from those
    bytes — the path is never reopened. Returns (config, note); a link,
    FIFO, oversized or malformed file is a note, and the report goes on
    without roles."""
    r = read_bounded(root, CONFIG_NAME, JSON_LIMIT, truncate=False)
    if r.state == "absent":
        return None, None
    if r.state != "file":
        why = f"unsupported filesystem shape ({r.detail})" if r.state == "unsupported" else (r.detail or r.state)
        return None, {"path": CONFIG_NAME, "detail": f"not read: {why}"}
    try:
        text = r.data.decode("utf-8")
        if config_mod.HAS_YAML:
            data = config_mod.yaml.safe_load(text)
        else:
            data = config_mod._read_config_fallback(text)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return None, {"path": CONFIG_NAME, "detail": f"not read: malformed {CONFIG_NAME}"}
    return data, None


def _managed(framework: dict) -> set[str]:
    return {i["path"] for i in framework.get("items", [])}


def legacy_findings(root: str | Path) -> list[dict]:
    """Project findings only, for the setup/upgrade pointer. No plugin call:
    the managed set is taken with the plugin treated as absent, which only
    widens the exclusion to the vendored skill path (excluded anyway)."""
    root = Path(root).resolve()
    config, _ = read_role_config(root)
    roles = observe_roles(root, config)
    fw = observe_framework(root, {"state": "missing", "reason": "not checked"})
    findings, _ = scan_legacy(root, roles, _managed(fw) | {SKILL_REL})
    return findings


def build_report(target: str | Path) -> Report:
    root = Path(target).resolve()
    config, config_note = read_role_config(root)
    roles = observe_roles(root, config)
    plugin = plugin_availability(root)
    fw = observe_framework(root, plugin)
    skill = next((i for i in fw["items"] if i["path"] == SKILL_REL), None)
    tools = observe_tools(root)
    findings, notes = scan_legacy(root, roles, _managed(fw) | {SKILL_REL})
    if config_note:
        notes.insert(0, config_note)
    return Report(
        root=str(root), roles=roles, roles_configured=bool(roles),
        contract={"shell": {"entry": "tagteam contract", "state": "found"}, "plugin": plugin,
                  "vendored_skill": skill},
        framework=fw, instructions=observe_instructions(root), tools=tools,
        protections=protection_notes(roles, tools), findings=findings,
        user_level=[str(p) for p in legacy_handoff_skill_candidates()], notes=notes)


def _obs(state: str, path: str, reason: str) -> str:
    if state == "found":
        return f"found {path}"
    return state + (f" ({reason})" if reason else "")


def format_report(rep: Report) -> str:
    L = [f"Tagteam doctor — {rep.root}", "=" * 40, "", "Roles"]
    config_note = next((n for n in rep.notes if n["path"] == CONFIG_NAME), None)
    if config_note:
        L.append(f"  {CONFIG_NAME} {config_note['detail']}")
    elif not rep.roles:
        L.append("  not configured; run tagteam init")
    for r in rep.roles:
        d, h = r["desktop"], r["headless"]
        L.append(f"  {r['role'].capitalize()}: {r['name']}")
        al = d["autoloads"]
        auto = f"auto-loads {al['file']} ({al['state']})" if al["file"] else "auto-loads unknown"
        L.append(f"    desktop   {d['executable'] or '?'} — {_obs(d['state'], d['path'], d['reason'])} · {auto}")
        inj = h["injects"]
        if inj["state"] == "injected":
            itext = f"injects {inj['file']} ({inj['chars']} chars" + \
                (f", truncated at {inj['limit']})" if inj["truncates"] else ")")
        elif inj["state"] == "none":
            itext = "injects nothing" + (f" ({inj['file']}: {inj['detail']})" if inj.get("detail") else "")
        else:
            itext = "injects unknown" + (f" ({inj['file']}: {inj.get('detail', '')})" if inj.get("file") else "")
        prov = f"{h['provider']} ({h['source']})" if h["provider"] else f"? ({h['source']})"
        exe = f"{h['executable']} — " if h["executable"] and h["executable"] != h["provider"] else ""
        L.append(f"    headless  {prov} — {exe}{_obs(h['state'], h['path'], h['reason'])} · {itext}")
    L += ["", "Contract", "  shell     tagteam contract — found"]
    p = rep.contract["plugin"]
    L.append(f"  plugin    {p['state']}" + (f" ({p['reason']})" if p.get("reason") else ""))
    s = rep.contract.get("vendored_skill")
    L.append(f"  vendored  {s['path']} — {s['action']} ({s['reason']})" if s
             else "  vendored  none")
    fw = rep.framework
    L += ["", f"Framework   package {fw['package']} · manifest {fw['manifest']} · plugin: {p['state']}"]
    tally: dict[str, int] = {}
    for i in fw["items"]:
        tally[i["action"]] = tally.get(i["action"], 0) + 1
    L.append("  " + " · ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    for i in fw["items"]:
        if i["action"] not in ("current", "none"):
            L.append(f"    {i['action']:<8} {i['path']} — {i['reason']}")
    L += ["", "Instruction sources"]
    for i in rep.instructions:
        extra = f", {i['size']} bytes" if i["state"] == "found" else (f" ({i['detail']})" if i["detail"] else "")
        L.append(f"  {i['file']:<10} {i['state']}{extra}")
    t = rep.tools
    L += ["", "Tool configuration"]
    m = t["mcp"]
    mtext = m["state"] + (f": {', '.join(m['servers'])}" if m["servers"] else "") + \
        (f" ({m['detail']})" if m["detail"] else "")
    L.append(f"  {'.mcp.json':<28} {mtext}")
    for c in t["claude_settings"]:
        ctext = c["state"] + (f": hooks {', '.join(c['hooks'])}" if c["hooks"] else "") + \
            (f" ({c['detail']})" if c["detail"] else "")
        L.append(f"  {c['file']:<28} {ctext}")
    cx = t["codex_config"]
    L.append(f"  {cx['file']:<28} {cx['state']}" + (f" ({cx['detail']})" if cx["detail"] else ""))
    L += ["", "Protections"] + [f"  - {n}" for n in rep.protections]
    L += ["", "Legacy workflow findings"]
    if not rep.findings and not rep.user_level:
        L.append("  none")
    for f in rep.findings:
        where = f"{f['path']}:{f['line']}" if f["line"] else f["path"]
        L.append(f"  {f['severity']:<5} {where}  {f['rule']} — {f['excerpt']}")
        L.append(f"        {f['provenance']}")
        L.append(f"        → {f['remediation']}")
    for u in rep.user_level:
        L.append(f"  user-level candidate  {u} (not read; tagteam never modifies it)")
    if rep.notes:
        L += ["", "Not read"] + [f"  {n['path']} — {n['detail']}" for n in rep.notes]
    c = rep.counts
    L += ["", f"findings: {c['warn']} warn, {c['info']} info"]
    return "\n".join(L)


def summary_line(findings: list[dict], target: str | Path) -> str:
    if not findings:
        return ""
    warn = sum(f["severity"] == "warn" for f in findings)
    n = len(findings)
    return (f"note: {n} legacy workflow finding{'s' if n != 1 else ''} ({warn} warn) — "
            f"run: tagteam doctor {target}")


def doctor_command(args: list[str]) -> int:
    as_json = "--json" in args
    rest = [a for a in args if a != "--json"]
    unknown = [a for a in rest if a.startswith("-")]
    if unknown or len(rest) > 1:
        print(f"doctor: unexpected argument {(unknown or rest[1:])[0]}")
        print("usage: tagteam doctor [DIR] [--json]")
        return 2
    target = Path(rest[0] if rest else ".")
    try:
        ok = target.is_dir()
    except OSError:
        ok = False
    if not ok:
        print(f"doctor: not a directory: {target}")
        return 2
    rep = build_report(target)
    if as_json:
        print(json.dumps(rep.to_json(), indent=2, default=str))
    else:
        print(format_report(rep))
    return 0
