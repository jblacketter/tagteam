"""
Centralized configuration handling for Tagteam.

This module provides a single source of truth for reading and validating
tagteam.yaml configuration files.
"""

from pathlib import Path

# PyYAML is optional
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def _indent(line: str) -> int:
    """Return leading whitespace width for fallback YAML parsing."""
    return len(line) - len(line.lstrip(" \t"))


def _parse_simple_value(raw: str) -> str:
    """Parse a simple scalar value from the fallback YAML parser."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


# Headless providers known to `tagteam.headless` (Phase 31). Kept here so
# `validate_config` can reject unknown providers without importing the
# adapter module.
HEADLESS_PROVIDERS = ("claude", "codex")


def _read_config_fallback(content: str) -> dict | None:
    """Parse the simple tagteam.yaml shape without PyYAML.

    This intentionally supports only the configuration shape TagTeam writes:
    ``agents -> lead/reviewer -> name/command/model_patterns/headless``
    (``headless`` being a mapping of ``provider``/``executable``/``args``).
    """
    if content.strip() == "{}":
        return {}

    lines = content.splitlines()
    agents_index = None
    agents_indent = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "agents:":
            agents_index = i
            agents_indent = _indent(line)
            break
        return None

    if agents_index is None:
        return None

    agents: dict[str, dict] = {}
    i = agents_index + 1
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        line_indent = _indent(line)
        if line_indent <= agents_indent:
            break

        role = stripped[:-1] if stripped.endswith(":") else None
        if role not in {"lead", "reviewer"}:
            i += 1
            continue

        role_indent = line_indent
        role_data: dict[str, str | list[str]] = {}
        i += 1
        while i < len(lines):
            sub_line = lines[i]
            sub = sub_line.strip()
            if not sub or sub.startswith("#"):
                i += 1
                continue

            sub_indent = _indent(sub_line)
            if sub_indent <= role_indent:
                break

            if sub.startswith("name:"):
                role_data["name"] = _parse_simple_value(sub.split(":", 1)[1])
                i += 1
                continue
            if sub.startswith("command:"):
                role_data["command"] = _parse_simple_value(sub.split(":", 1)[1])
                i += 1
                continue
            if sub == "model_patterns:":
                patterns: list[str] = []
                pattern_indent = sub_indent
                i += 1
                while i < len(lines):
                    item_line = lines[i]
                    item = item_line.strip()
                    if not item or item.startswith("#"):
                        i += 1
                        continue
                    item_indent = _indent(item_line)
                    if item_indent <= pattern_indent:
                        break
                    if item.startswith("- "):
                        patterns.append(_parse_simple_value(item[2:]))
                    i += 1
                role_data["model_patterns"] = patterns
                continue
            if sub == "headless:":
                headless: dict = {}
                headless_indent = sub_indent
                i += 1
                while i < len(lines):
                    h_line = lines[i]
                    h = h_line.strip()
                    if not h or h.startswith("#"):
                        i += 1
                        continue
                    h_indent = _indent(h_line)
                    if h_indent <= headless_indent:
                        break
                    if h.startswith("provider:"):
                        headless["provider"] = _parse_simple_value(h.split(":", 1)[1])
                        i += 1
                        continue
                    if h.startswith("executable:"):
                        headless["executable"] = _parse_simple_value(h.split(":", 1)[1])
                        i += 1
                        continue
                    if h.startswith("timeout_minutes:"):
                        raw_t = _parse_simple_value(h.split(":", 1)[1])
                        try:
                            headless["timeout_minutes"] = int(raw_t)
                        except (TypeError, ValueError):
                            try:
                                headless["timeout_minutes"] = float(raw_t)
                            except (TypeError, ValueError):
                                headless["timeout_minutes"] = raw_t
                        i += 1
                        continue
                    if h.startswith("args:"):
                        rest = h.split(":", 1)[1].strip()
                        args_list: list[str] = []
                        if rest.startswith("[") and rest.endswith("]"):
                            inner = rest[1:-1].strip()
                            if inner:
                                args_list = [_parse_simple_value(x) for x in inner.split(",")]
                            headless["args"] = args_list
                            i += 1
                            continue
                        if rest and not rest.startswith("#"):
                            # Scalar `args: "--model opus"` — preserve the raw
                            # string so validate_config rejects it (must be a
                            # list); never coerce to [] here.
                            headless["args"] = _parse_simple_value(rest)
                            i += 1
                            continue
                        args_indent = h_indent
                        i += 1
                        while i < len(lines):
                            a_line = lines[i]
                            a = a_line.strip()
                            if not a or a.startswith("#"):
                                i += 1
                                continue
                            if _indent(a_line) <= args_indent:
                                break
                            if a.startswith("- "):
                                args_list.append(_parse_simple_value(a[2:]))
                            i += 1
                        headless["args"] = args_list
                        continue
                    # Unknown key under headless: keep it (raw) so
                    # validate_config reports "unknown keys" instead of the
                    # fallback parser silently discarding it.
                    if ":" in h and not h.startswith("- "):
                        key, val = h.split(":", 1)
                        headless[key.strip()] = _parse_simple_value(val) if val.strip() else None
                    i += 1
                role_data["headless"] = headless
                continue

            i += 1

        agents[role] = role_data

    return {"agents": agents} if agents else None


def read_config(config_path: Path | str) -> dict | None:
    """Read and parse tagteam.yaml.

    Args:
        config_path: Path to config file

    Returns:
        Parsed config dict, or None if file doesn't exist or is invalid
    """
    path = Path(config_path)
    if not path.exists():
        return None

    try:
        content = path.read_text(encoding="utf-8")
        if HAS_YAML:
            result = yaml.safe_load(content)
            # Only return if it's a dict (not [], "foo", or other valid YAML)
            return result if isinstance(result, dict) else None

        return _read_config_fallback(content)
    except Exception:
        pass
    return None


def validate_config(config: dict) -> list[str]:
    """Validate tagteam.yaml structure.

    Args:
        config: Parsed config dict

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not isinstance(config, dict):
        return ["Config must be a YAML mapping"]

    agents = config.get("agents")
    if not isinstance(agents, dict):
        errors.append("Missing 'agents' section")
        return errors

    # Validate lead
    lead = agents.get("lead")
    if not isinstance(lead, dict) or not lead.get("name"):
        errors.append("Missing or invalid 'agents.lead.name'")

    # Validate reviewer
    reviewer = agents.get("reviewer")
    if not isinstance(reviewer, dict) or not reviewer.get("name"):
        errors.append("Missing or invalid 'agents.reviewer.name'")

    # Validate command if present
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if isinstance(agent, dict):
            command = agent.get("command")
            if command is not None and not isinstance(command, str):
                errors.append(f"'agents.{role}.command' must be a string")
            elif isinstance(command, str) and not command.strip():
                errors.append(f"'agents.{role}.command' is empty")

    # Validate model_patterns if present
    all_patterns: list[tuple[str, list[str]]] = []
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            continue
        patterns = agent.get("model_patterns")
        if patterns is not None:
            if not isinstance(patterns, list):
                errors.append(f"'agents.{role}.model_patterns' must be a list")
            elif not all(isinstance(p, str) and p for p in patterns):
                errors.append(f"'agents.{role}.model_patterns' must contain non-empty strings")
            else:
                all_patterns.append((role, [p.lower() for p in patterns]))

    # Validate headless block if present (Phase 31). Structural checks only;
    # option-level validation of `args` lives in `tagteam.headless.build_argv`.
    for role in ["lead", "reviewer"]:
        agent = agents.get(role, {})
        if not isinstance(agent, dict):
            continue
        headless = agent.get("headless")
        if headless is None:
            continue
        if not isinstance(headless, dict):
            errors.append(f"'agents.{role}.headless' must be a mapping")
            continue
        provider = headless.get("provider")
        if provider is not None and provider not in HEADLESS_PROVIDERS:
            errors.append(
                f"'agents.{role}.headless.provider' must be one of: "
                f"{', '.join(HEADLESS_PROVIDERS)} (got {provider!r})"
            )
        executable = headless.get("executable")
        if executable is not None and (
                not isinstance(executable, str) or not executable.strip()):
            errors.append(f"'agents.{role}.headless.executable' must be a non-empty string")
        args = headless.get("args")
        if args is not None:
            if not isinstance(args, list):
                errors.append(
                    f"'agents.{role}.headless.args' must be a list of strings "
                    f"(got {type(args).__name__}); shell strings are never tokenized"
                )
            elif not all(isinstance(a, str) for a in args):
                errors.append(f"'agents.{role}.headless.args' must contain only strings")
        tmo = headless.get("timeout_minutes")
        if tmo is not None and (isinstance(tmo, bool) or not isinstance(tmo, (int, float))
                                or tmo <= 0):
            errors.append(f"'agents.{role}.headless.timeout_minutes' must be a positive number")
        unknown = set(headless) - {"provider", "executable", "args", "timeout_minutes"}
        if unknown:
            errors.append(
                f"'agents.{role}.headless' has unknown keys: {sorted(unknown)}"
            )

    # Check for pattern overlap (error, not warning)
    if len(all_patterns) == 2:
        role1, patterns1 = all_patterns[0]
        role2, patterns2 = all_patterns[1]
        for p1 in patterns1:
            for p2 in patterns2:
                if p1 in p2 or p2 in p1:
                    errors.append(
                        f"Pattern overlap: '{p1}' ({role1}) and '{p2}' ({role2}) "
                        f"could match the same model identifier"
                    )

    return errors


def get_launch_commands(config: dict) -> tuple[str, str]:
    """Extract launch commands for lead and reviewer agents.

    Uses the optional 'command' field from each agent config,
    falling back to the lowercase agent name.

    Args:
        config: Parsed config dict

    Returns:
        (lead_command, reviewer_command) tuple
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {}) if isinstance(agents.get("lead"), dict) else {}
    reviewer = agents.get("reviewer", {}) if isinstance(agents.get("reviewer"), dict) else {}

    if not lead.get("name") or not reviewer.get("name"):
        raise ValueError("Configure both lead and reviewer in tagteam.yaml before launching agents")
    lead_cmd = lead.get("command") or lead["name"].lower()
    reviewer_cmd = reviewer.get("command") or reviewer["name"].lower()

    return lead_cmd, reviewer_cmd


def get_agent_names(config: dict) -> tuple[str | None, str | None]:
    """Extract lead and reviewer names from config.

    Args:
        config: Parsed config dict

    Returns:
        (lead_name, reviewer_name) tuple, with None for missing values
    """
    agents = config.get("agents", {})
    lead = agents.get("lead", {})
    reviewer = agents.get("reviewer", {})

    lead_name = lead.get("name") if isinstance(lead, dict) else None
    reviewer_name = reviewer.get("name") if isinstance(reviewer, dict) else None

    return lead_name, reviewer_name


def infer_headless_provider(config: dict, role: str) -> str | None:
    """Pick the headless provider for a role.

    Order: explicit ``agents.<role>.headless.provider`` → basename of the
    first whitespace token of the interactive ``command`` (inference only;
    the command string is never executed or tokenized for headless) →
    the agent ``name`` lowercased. Returns None if nothing matches a
    known provider.
    """
    agents = config.get("agents", {}) if isinstance(config, dict) else {}
    agent = agents.get(role, {}) if isinstance(agents, dict) else {}
    if not isinstance(agent, dict):
        return None
    headless = agent.get("headless") or {}
    explicit = headless.get("provider") if isinstance(headless, dict) else None
    if explicit is not None:
        # An explicit provider is authoritative: return it even when it is
        # not a known provider so callers report it instead of silently
        # falling back to inference (reviewer finding, Phase 31 impl r1).
        return explicit if isinstance(explicit, str) else str(explicit)
    candidates: list[str] = []
    command = agent.get("command")
    if isinstance(command, str) and command.strip():
        first = command.strip().split()[0]
        candidates.append(Path(first).name.lower())
    name = agent.get("name")
    if isinstance(name, str) and name.strip():
        candidates.append(name.strip().lower())
    for cand in candidates:
        for prov in HEADLESS_PROVIDERS:
            if cand == prov or cand.startswith(prov):
                return prov
    return None


def get_headless_spec(config: dict, role: str) -> dict:
    """Return ``{"provider", "executable", "args"}`` for a role.

    ``provider`` may be None (uninferable) or an unknown explicit value —
    the caller reports either; ``executable`` is the configured value or
    None (caller resolves the provider name via ``shutil.which``);
    ``args`` is the raw configured value (a list when valid; anything
    else is passed through so `headless.build_argv` can reject it) or [].
    """
    agents = config.get("agents", {}) if isinstance(config, dict) else {}
    agent = agents.get(role, {}) if isinstance(agents, dict) else {}
    headless = agent.get("headless") if isinstance(agent, dict) else None
    headless = headless if isinstance(headless, dict) else {}
    args = headless.get("args")
    return {
        "provider": infer_headless_provider(config, role),
        "executable": headless.get("executable") or None,
        "args": list(args) if isinstance(args, list) else (args if args is not None else []),
        "timeout_minutes": headless.get("timeout_minutes"),
    }


# ---------------------------------------------------------------------------
# Phase 33: escalation briefer config (top-level `briefer:` block).
# Opt-in: an absent block, or `enabled` absent/false, means disabled and
# 0.9.0 escalation behavior. Validation is deliberately SEPARATE from the
# fatal `validate_config()` — briefer problems warn and disable, never block.
# ---------------------------------------------------------------------------

BRIEFER_KEYS = {"enabled", "provider", "executable", "args", "timeout_minutes"}
BRIEFER_DEFAULT_TIMEOUT_MINUTES = 15


def validate_briefer_config(config: dict) -> list[str]:
    """Return problems with the `briefer:` block (empty when absent/valid)."""
    if not isinstance(config, dict):
        return []
    block = config.get("briefer")
    if block is None:
        return []
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["'briefer' must be a mapping"]
    enabled = block.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("'briefer.enabled' must be true or false")
    provider = block.get("provider")
    if provider is not None and provider not in HEADLESS_PROVIDERS:
        errors.append(f"'briefer.provider' must be one of: {', '.join(HEADLESS_PROVIDERS)} "
                      f"(got {provider!r})")
    executable = block.get("executable")
    if executable is not None and (not isinstance(executable, str) or not executable.strip()):
        errors.append("'briefer.executable' must be a non-empty string")
    args = block.get("args")
    if args is not None:
        if not isinstance(args, list):
            errors.append("'briefer.args' must be a list of strings (never a shell string)")
        elif not all(isinstance(a, str) for a in args):
            errors.append("'briefer.args' must contain only strings")
    tmo = block.get("timeout_minutes")
    if tmo is not None and (isinstance(tmo, bool) or not isinstance(tmo, (int, float)) or tmo <= 0):
        errors.append("'briefer.timeout_minutes' must be a positive number")
    unknown = set(block) - BRIEFER_KEYS
    if unknown:
        errors.append(f"'briefer' has unknown keys: {sorted(unknown)}")
    return errors


def get_briefer_spec(config: dict) -> dict:
    """{"enabled", "provider", "executable", "args", "timeout_minutes"}.

    `enabled` is True only when `briefer.enabled: true` is explicit;
    `provider` defaults to the lead's headless provider (inference), or
    None if uninferable. Callers must run `validate_briefer_config` first;
    this function does not validate.
    """
    block = config.get("briefer") if isinstance(config, dict) else None
    block = block if isinstance(block, dict) else {}
    provider = block.get("provider") or infer_headless_provider(config, "lead")
    args = block.get("args")
    tmo = block.get("timeout_minutes")
    # Never let an invalid value propagate as a raise: fall back to the
    # default (validate_briefer_config reports the problem separately).
    if isinstance(tmo, bool) or not isinstance(tmo, (int, float)) or tmo <= 0:
        tmo = BRIEFER_DEFAULT_TIMEOUT_MINUTES
    return {
        "enabled": block.get("enabled") is True,
        "provider": provider,
        "executable": block.get("executable") or None,
        "args": list(args) if isinstance(args, list) else (args if args is not None else []),
        "timeout_minutes": tmo,
    }


# ---------------------------------------------------------------------------
# Phase 38: gatekeeper (deterministic pre-checks before the reviewer's turn)
# ---------------------------------------------------------------------------

def _norm_on_key(block):
    """PyYAML (YAML 1.1) parses a bare `on:` key as boolean True. Both the
    gatekeeper and the panel blocks use `on: [plan|impl]`, so accept the
    boolean-key spelling (and the explicit alias `cycles:`) as `on`."""
    if not isinstance(block, dict):
        return block
    out = {}
    for k, v in block.items():
        if k is True or k == "cycles":
            k = "on"
        out[k] = v
    return out


GATEKEEPER_KEYS = {"enabled", "on", "tests", "scope", "max_bounces", "max_output_chars", "on_submit"}
GATEKEEPER_TESTS_KEYS = {"command", "timeout_minutes"}
GATEKEEPER_DEFAULTS = {
    "on": ["impl"], "scope": True, "max_bounces": 2, "max_output_chars": 4000,
    "tests_timeout_minutes": 15,
}


def validate_gatekeeper_config(config: dict) -> list[str]:
    """Return problems with the `gatekeeper:` block (empty when absent/valid)."""
    if not isinstance(config, dict):
        return []
    block = config.get("gatekeeper")
    if block is None:
        return []
    errors: list[str] = []
    if not isinstance(block, dict):
        return ["'gatekeeper' must be a mapping"]
    block = _norm_on_key(block)
    enabled = block.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("'gatekeeper.enabled' must be true or false")
    on = block.get("on")
    if on is not None:
        if not isinstance(on, list) or not all(isinstance(t, str) for t in on):
            errors.append("'gatekeeper.on' must be a list of cycle types (plan, impl)")
        elif any(t not in ("plan", "impl") for t in on):
            errors.append("'gatekeeper.on' entries must be plan or impl")
    tests = block.get("tests")
    if tests is not None:
        if not isinstance(tests, dict):
            errors.append("'gatekeeper.tests' must be a mapping (command, timeout_minutes)")
        else:
            cmd = tests.get("command")
            if cmd is not None:
                if isinstance(cmd, str):
                    if not cmd.strip():
                        errors.append("'gatekeeper.tests.command' must be a non-empty string or a list of strings")
                elif not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) and a for a in cmd):
                    errors.append("'gatekeeper.tests.command' must be a non-empty string or a list of strings")
            tmo = tests.get("timeout_minutes")
            if tmo is not None and (isinstance(tmo, bool) or not isinstance(tmo, (int, float)) or tmo <= 0):
                errors.append("'gatekeeper.tests.timeout_minutes' must be a positive number")
            unknown = set(tests) - GATEKEEPER_TESTS_KEYS
            if unknown:
                errors.append(f"'gatekeeper.tests' has unknown keys: {sorted(unknown)}")
    scope = block.get("scope")
    if scope is not None and not isinstance(scope, bool):
        errors.append("'gatekeeper.scope' must be true or false")
    on_submit = block.get("on_submit")
    if on_submit is not None and not isinstance(on_submit, bool):
        errors.append("'gatekeeper.on_submit' must be true or false")
    for key in ("max_bounces", "max_output_chars"):
        v = block.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            errors.append(f"'gatekeeper.{key}' must be a non-negative integer")
    unknown = set(block) - GATEKEEPER_KEYS
    if unknown:
        errors.append(f"'gatekeeper' has unknown keys: {sorted(unknown)}")
    return errors


def get_gatekeeper_spec(config: dict) -> dict:
    """{"enabled", "on", "tests_command", "tests_timeout_s", "scope",
    "max_bounces", "max_output_chars", "on_submit"}. `enabled` is True only
    when `gatekeeper.enabled: true` is explicit; `on_submit` (Phase 41) is
    True only when explicit. Callers validate first."""
    block = config.get("gatekeeper") if isinstance(config, dict) else None
    block = _norm_on_key(block) if isinstance(block, dict) else {}
    tests = block.get("tests") if isinstance(block.get("tests"), dict) else {}
    tmo = tests.get("timeout_minutes")
    return {
        "enabled": block.get("enabled") is True,
        "on": list(block.get("on") or GATEKEEPER_DEFAULTS["on"]),
        "tests_command": tests.get("command"),
        "tests_timeout_s": float(tmo if tmo is not None else GATEKEEPER_DEFAULTS["tests_timeout_minutes"]) * 60.0,
        "scope": block.get("scope") if block.get("scope") is not None else GATEKEEPER_DEFAULTS["scope"],
        "max_bounces": int(block.get("max_bounces") if block.get("max_bounces") is not None else GATEKEEPER_DEFAULTS["max_bounces"]),
        "max_output_chars": int(block.get("max_output_chars") if block.get("max_output_chars") is not None else GATEKEEPER_DEFAULTS["max_output_chars"]),
        "on_submit": block.get("on_submit") is True,
    }


# ---------------------------------------------------------------------------
# Phase 41: watcher block (opt-in tuning of the watchdog re-send)
# ---------------------------------------------------------------------------

WATCHER_KEYS = {"resend_minutes"}
WATCHER_DEFAULT_RESEND_MINUTES = 15


def validate_watcher_config(config: dict) -> list[str]:
    """Return problems with the top-level `watcher:` block (empty when
    absent/valid). `resend_minutes`: integer >= 0 (0 = never re-send)."""
    if not isinstance(config, dict):
        return []
    block = config.get("watcher")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["'watcher' must be a mapping"]
    errors: list[str] = []
    v = block.get("resend_minutes")
    if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
        errors.append("'watcher.resend_minutes' must be a non-negative integer (0 = never re-send)")
    unknown = set(block) - WATCHER_KEYS
    if unknown:
        errors.append(f"'watcher' has unknown keys: {sorted(unknown)}")
    return errors


def resolve_watcher(config: dict | None) -> tuple[int, list[str]]:
    """Phase 71: the re-send interval a watcher actually uses, and why.
    Any problem with the block → the default, as `_watch_locked` has always
    done (it now calls this, so the cockpit's Rules tab cannot drift from
    it). Never raises."""
    try:
        problems = validate_watcher_config(config or {})
        if problems:
            return WATCHER_DEFAULT_RESEND_MINUTES, problems
        return get_watcher_spec(config or {})["resend_minutes"], []
    except Exception as e:
        return WATCHER_DEFAULT_RESEND_MINUTES, [f"watcher config unreadable: {e}"]


def get_watcher_spec(config: dict) -> dict:
    """{"resend_minutes"} with defaults applied. Callers validate first."""
    block = config.get("watcher") if isinstance(config, dict) else None
    block = block if isinstance(block, dict) else {}
    v = block.get("resend_minutes")
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        v = WATCHER_DEFAULT_RESEND_MINUTES
    return {"resend_minutes": int(v)}


# ---------------------------------------------------------------------------
# Phase 39: reviewer panels (opt-in)
# ---------------------------------------------------------------------------

PANEL_KEYS = {"enabled", "on", "phases", "lenses"}
PANEL_LENS_KEYS = {"name", "brief"}
PANEL_DEFAULT_LENSES = ["correctness", "scope", "verification"]
PANEL_MIN_LENSES, PANEL_MAX_LENSES = 2, 3
_LENS_NAME_RE = r"^[a-z][a-z0-9_-]{0,31}$"


def _lens_entries(block: dict) -> list[dict] | None:
    """Normalise `panel.lenses` to [{name, brief|None}] or None if invalid."""
    import re
    lenses = block.get("lenses")
    if lenses is None:
        return [{"name": n, "brief": None} for n in PANEL_DEFAULT_LENSES]
    if not isinstance(lenses, list):
        return None
    out = []
    for item in lenses:
        if isinstance(item, str):
            out.append({"name": item, "brief": None})
        elif isinstance(item, dict):
            out.append({"name": item.get("name"), "brief": item.get("brief")})
        else:
            return None
    for e in out:
        if not isinstance(e["name"], str) or not re.match(_LENS_NAME_RE, e["name"]):
            return None
    return out


def validate_panel_config(config: dict) -> list[str]:
    """Return problems with the `panel:` block (empty when absent/valid)."""
    if not isinstance(config, dict):
        return []
    block = config.get("panel")
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["'panel' must be a mapping"]
    block = _norm_on_key(block)
    errors: list[str] = []
    enabled = block.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("'panel.enabled' must be true or false")
    on = block.get("on")
    if on is not None:
        if not isinstance(on, list) or not all(isinstance(t, str) for t in on):
            errors.append("'panel.on' must be a list of cycle types (plan, impl)")
        elif any(t not in ("plan", "impl") for t in on):
            errors.append("'panel.on' entries must be plan or impl")
    phases = block.get("phases")
    if phases is not None and (not isinstance(phases, list) or not all(isinstance(p, str) and p for p in phases)):
        errors.append("'panel.phases' must be a list of phase slugs")
    lenses = block.get("lenses")
    if lenses is not None:
        entries = _lens_entries(block)
        if entries is None:
            errors.append("'panel.lenses' must be a list of lens names or {name, brief} mappings "
                          "(names: lowercase letters, digits, - or _)")
        else:
            names = [e["name"] for e in entries]
            if not (PANEL_MIN_LENSES <= len(names) <= PANEL_MAX_LENSES):
                errors.append(f"'panel.lenses' must list {PANEL_MIN_LENSES}–{PANEL_MAX_LENSES} lenses (got {len(names)})")
            if len(set(names)) != len(names):
                errors.append("'panel.lenses' names must be unique")
            for item in lenses:
                if isinstance(item, dict):
                    unknown = set(item) - PANEL_LENS_KEYS
                    if unknown:
                        errors.append(f"'panel.lenses' entry has unknown keys: {sorted(unknown)}")
                    brief = item.get("brief")
                    if brief is not None and (not isinstance(brief, str) or not brief.strip()):
                        errors.append("'panel.lenses[].brief' must be a non-empty path")
    unknown = set(block) - PANEL_KEYS
    if unknown:
        errors.append(f"'panel' has unknown keys: {sorted(unknown)}")
    return errors


def get_panel_spec(config: dict) -> dict:
    """{"enabled", "on", "phases", "lenses": [{name, brief}]}. `enabled` is
    True only when `panel.enabled: true` is explicit. Callers validate first."""
    block = config.get("panel") if isinstance(config, dict) else None
    block = _norm_on_key(block) if isinstance(block, dict) else {}
    lenses = _lens_entries(block) or [{"name": n, "brief": None} for n in PANEL_DEFAULT_LENSES]
    return {
        "enabled": block.get("enabled") is True,
        "on": list(block.get("on") or ["impl"]),
        "phases": list(block.get("phases") or []),
        "lenses": lenses,
    }
