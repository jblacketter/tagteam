"""Phase 71b: safe `tagteam.yaml` edits.

A closed set of keys (`SAFE_KEYS`) can be changed from the CLI
(`tagteam config set`) and the cockpit's Rules tab. An edit is a TARGETED LINE
EDIT: a PyYAML load-and-dump would drop the file's comments, blank lines and
key order. The locator is deliberately narrow — anything unusual (flow style,
duplicates, block scalars, tabs, anchors, several documents) is refused, never
guessed at.

Nothing is written unless:
1. the edited text parses;
2. the parsed result equals the old config with ONLY the target set (the
   target's own parent normalised when absent/empty) — a backstop for the
   locator itself;
3. for an enable and for `resend_minutes`, the engine's own resolver on the
   new config yields the requested value (a disable needs no agreement).

No-ops are decided on the SAVED value (absent | invalid | set), never on the
effective one: disabling a gate that an invalid block already keeps OFF still
writes `enabled: false`.

Writes: refused read-only; guarded (no symlinks, regular file, bounded read,
O_EXCL|O_NOFOLLOW temp file, mode kept, os.replace); the whole
read/validate/re-read/replace section under the project's writer lock (thread
RLock + flock — every tagteam writer takes it); optionally bound to a
preview's `base` (sha256 of the bytes it diffed). An external editor that
does not take the lock can still race the final re-read and the replace; that
window is narrowed, not closed.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "tagteam.yaml"
MAX_BYTES = 256 * 1024

# key → (kind, human label of the rule)
SAFE_KEYS = {
    "gatekeeper.enabled": ("bool", "gate"),
    "gatekeeper.on_submit": ("bool", "gate runs at submission"),
    "panel.enabled": ("bool", "reviewer panel"),
    "briefer.enabled": ("bool", "escalation brief"),
    "watcher.resend_minutes": ("minutes", "re-send a stuck turn"),
}

_TEST_HOOK = None          # tests: called inside the locked critical section


class Refused(Exception):
    """An edit that is not made. `kind`: refused | stale | usage."""

    def __init__(self, message: str, kind: str = "refused"):
        super().__init__(message)
        self.kind = kind


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render(kind: str, value) -> str:
    if kind == "bool":
        return "true" if value else "false"
    return str(int(value))


def parse_cli_value(key: str, raw: str):
    kind = SAFE_KEYS[key][0]
    if kind == "bool":
        if raw.lower() in ("true", "on", "yes"):
            return True
        if raw.lower() in ("false", "off", "no"):
            return False
        raise Refused(f"{key} takes true or false, not {raw!r}", "usage")
    if not raw.isdigit():
        raise Refused(f"{key} takes a whole number of minutes >= 0 (0 = never), not {raw!r}", "usage")
    return int(raw)


def check_key(key: str) -> str:
    if key not in SAFE_KEYS:
        raise Refused(f"{key!r} is not one of the keys tagteam edits ({', '.join(SAFE_KEYS)}); "
                      f"edit {CONFIG_FILE} by hand", "usage")
    return SAFE_KEYS[key][0]


# ---------------------------------------------------------------------------
# Saved values (what the file says, typed) — the basis of every no-op
# ---------------------------------------------------------------------------

def saved(config: dict | None, key: str) -> tuple[str, object]:
    """(absent | invalid | set, value)."""
    block, leaf = key.split(".", 1)
    kind = SAFE_KEYS[key][0]
    cfg = config if isinstance(config, dict) else {}
    if block not in cfg or cfg[block] is None:
        return "absent", None
    blk = cfg[block]
    if not isinstance(blk, dict):
        return "invalid", blk
    if leaf not in blk:
        return "absent", None
    v = blk[leaf]
    if kind == "bool":
        return ("set", v) if isinstance(v, bool) else ("invalid", v)
    ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
    return ("set", v) if ok else ("invalid", v)


# ---------------------------------------------------------------------------
# Effective values — the engine's own resolvers
# ---------------------------------------------------------------------------

def effective(config: dict, key: str, root: Path) -> tuple[object, list[str]]:
    """(the value the engine would use, its problems)."""
    if key.startswith("gatekeeper."):
        from tagteam.gatekeeper import resolve_gatekeeper
        g = resolve_gatekeeper(config)
        return (g.enabled if key.endswith(".enabled") else g.on_submit), list(g.problems)
    if key == "panel.enabled":
        from tagteam.panel import resolve_panel
        p = resolve_panel(config, root)
        return p.enabled, list(p.problems)
    if key == "briefer.enabled":
        from tagteam.briefer import resolve_briefer
        b = resolve_briefer(config, root)
        return b.enabled, list(b.problems)
    from tagteam.config import resolve_watcher
    minutes, problems = resolve_watcher(config)
    return minutes, list(problems)


def describe_effective(key: str, value) -> str:
    label = SAFE_KEYS[key][1]
    if SAFE_KEYS[key][0] == "minutes":
        return f"{label}: {'never' if value == 0 else f'{value} min'}"
    return f"{label}: {'ON' if value else 'OFF'}"


# ---------------------------------------------------------------------------
# The locator: a targeted line edit, or a refusal
# ---------------------------------------------------------------------------

_SCALAR = re.compile(r"""^(?P<val>"[^"\n]*"|'[^'\n]*'|[^\s#'"][^\s#]*)(?P<trail>(\s+#.*)?\s*)$""")


def _content(line: str) -> str:
    return line.rstrip("\r\n")


def _eol_of(line: str) -> str:
    return line[len(_content(line)):]


def _node_lines(text: str, block: str, leaf: str) -> tuple[int | None, int | None]:
    """The parser's view (impl r1 review): `yaml.compose` keeps every mapping
    key — duplicates and quoting included — where `safe_load` has already
    collapsed them. Returns the 0-based lines of the target block's key and
    of the leaf's key (None when absent); refuses every spelling the line
    locator does not handle."""
    import yaml
    by_hand = f"edit {CONFIG_FILE} by hand"
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as e:
        raise Refused(f"{CONFIG_FILE} does not parse ({str(e).splitlines()[0]}) — fix it by hand")
    if root is None:
        return None, None
    if not isinstance(root, yaml.MappingNode):
        raise Refused(f"{CONFIG_FILE} is not a mapping — fix it by hand")
    hits = [(k, v) for k, v in root.value if isinstance(k, yaml.ScalarNode) and k.value == block]
    if any(k.style for k, _ in hits):
        raise Refused(f"`{block}` is written as a quoted key — {by_hand}")
    if len(hits) > 1:
        raise Refused(f"`{block}:` appears {len(hits)} times — {by_hand}")
    if not hits:
        return None, None
    k, v = hits[0]
    if isinstance(v, yaml.ScalarNode) and v.tag == "tag:yaml.org,2002:null" and v.value in ("", "~", "null"):
        return k.start_mark.line, None
    if not isinstance(v, yaml.MappingNode) or v.flow_style:
        raise Refused(f"`{block}:` is not a plain block mapping — {by_hand}")
    leaves = [lk for lk, _ in v.value if isinstance(lk, yaml.ScalarNode) and lk.value == leaf]
    if any(lk.style for lk in leaves):
        raise Refused(f"`{block}.{leaf}` is written as a quoted key — {by_hand}")
    if len(leaves) > 1:
        raise Refused(f"`{block}.{leaf}` appears {len(leaves)} times — {by_hand}")
    return k.start_mark.line, (leaves[0].start_mark.line if leaves else None)


def edit_text(text: str, key: str, value) -> str:
    """Return `text` with ONLY the target changed, or raise Refused."""
    block, leaf = key.split(".", 1)
    kind = SAFE_KEYS[key][0]
    rendered = render(kind, value)
    lines = text.splitlines(keepends=True)
    eol = next((_eol_of(l) for l in lines if _eol_of(l)), "\n")
    by_hand = f"edit {CONFIG_FILE} by hand"

    for l in lines:
        c = _content(l)
        if c.strip() in ("---", "...") or c.startswith("--- "):
            raise Refused(f"{CONFIG_FILE} has YAML document markers — {by_hand}")

    node_block, node_leaf = _node_lines(text, block, leaf)
    header = re.compile(r"^" + re.escape(block) + r"\s*:(?P<rest>.*)$")
    heads = [i for i, l in enumerate(lines) if header.match(_content(l))]
    if (heads[0] if len(heads) == 1 else None) != node_block and len(heads) <= 1:
        raise Refused(f"the `{block}` block is laid out in a way tagteam does not edit — {by_hand}")
    if len(heads) > 1:
        raise Refused(f"`{block}:` appears {len(heads)} times — {by_hand}")

    if not heads:                                   # append a new block
        out = text
        if out and not out.endswith(("\n", "\r")):
            out += eol
        if out and _content(lines[-1]).strip() != "":
            out += eol
        tail = eol if (not text or text.endswith(("\n", "\r"))) else ""
        return out + f"{block}:{eol}  {leaf}: {rendered}" + tail

    h = heads[0]
    rest = header.match(_content(lines[h])).group("rest").strip()
    if rest and not rest.startswith("#"):
        raise Refused(f"`{block}:` is not a plain block mapping (`{block}: {rest}`) — {by_hand}")

    body: list[int] = []
    j = h + 1
    while j < len(lines):
        c = _content(lines[j])
        if c.strip() == "" or c.lstrip().startswith("#"):
            j += 1
            continue
        if c[0] not in " \t":
            break
        body.append(j)
        j += 1
    for b in body:
        c = _content(lines[b])
        lead = c[:len(c) - len(c.lstrip())]
        if "\t" in lead:
            raise Refused(f"a tab indents the `{block}` block — {by_hand}")
        unquoted = re.sub(r"\"[^\"]*\"|'[^']*'", "", c.split(" #", 1)[0])
        if re.search(r"(^\s*|:\s+|-\s+)[&*][^\s]", unquoted):     # a node that starts with an anchor/alias
            raise Refused(f"the `{block}` block uses anchors or aliases — {by_hand}")
    ci = (len(_content(lines[body[0]])) - len(_content(lines[body[0]]).lstrip())) if body else 2

    keyline = re.compile(r"^(?P<ind> {" + str(ci) + r"})" + re.escape(leaf) + r"(?P<sp1>\s*):(?P<sp2>\s*)(?P<rest>.*)$")
    hits = [b for b in body if keyline.match(_content(lines[b]))]
    if len(hits) > 1:
        raise Refused(f"`{key}` appears {len(hits)} times — {by_hand}")
    if (hits[0] if hits else None) != node_leaf:
        raise Refused(f"`{key}` is laid out in a way tagteam does not edit — {by_hand}")

    if not hits:                                    # insert right after the header
        new_line = " " * ci + f"{leaf}: {rendered}"
        hl = lines[h]
        if not _eol_of(hl):                         # the header is the file's last line
            lines[h] = hl + eol
            lines.insert(h + 1, new_line)
        else:
            lines.insert(h + 1, new_line + eol)
        return "".join(lines)

    k = hits[0]
    m = keyline.match(_content(lines[k]))
    raw = m.group("rest")
    if raw.strip() == "" or raw.lstrip().startswith("#"):
        nxt = k + 1
        while nxt < len(lines) and (_content(lines[nxt]).strip() == "" or _content(lines[nxt]).lstrip().startswith("#")):
            nxt += 1
        if nxt < len(lines) and nxt in body:
            c = _content(lines[nxt])
            if len(c) - len(c.lstrip()) > ci:
                raise Refused(f"`{key}` has a multi-line value — {by_hand}")
        trail = (" " + raw.strip()) if raw.strip() else ""
        new = f"{m.group('ind')}{leaf}{m.group('sp1')}: {rendered}{trail}"
    else:
        sm = _SCALAR.match(raw)
        if not sm or sm.group("val")[0] in "[{&*!|>":
            raise Refused(f"`{key}` is not a plain one-line value (`{raw.strip()}`) — {by_hand}")
        new = f"{m.group('ind')}{leaf}{m.group('sp1')}:{m.group('sp2')}{rendered}{sm.group('trail')}"
    lines[k] = new + _eol_of(lines[k])
    return "".join(lines)


def _expected(old: dict, key: str, value) -> dict:
    block, leaf = key.split(".", 1)
    exp = copy.deepcopy(old)
    if exp.get(block) is None:                      # absent, or `block:` with nothing under it
        exp[block] = {}
    if not isinstance(exp[block], dict):
        raise Refused(f"`{block}` is not a mapping — edit {CONFIG_FILE} by hand")
    exp[block][leaf] = value
    return exp


# ---------------------------------------------------------------------------
# Reading / planning / writing
# ---------------------------------------------------------------------------

def _read(root: Path) -> bytes:
    from tagteam import safe_read
    r = safe_read.read_bounded(root, CONFIG_FILE, MAX_BYTES, truncate=False)
    if r.state == "absent":
        raise Refused(f"no {CONFIG_FILE} here — run `tagteam init` first; nothing written")
    if r.state != "file" or r.data is None:
        raise Refused(f"{CONFIG_FILE}: {r.detail or r.state} — nothing written")
    return r.data


def _load(text: str) -> dict:
    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise Refused(f"{CONFIG_FILE} does not parse ({str(e).splitlines()[0]}) — fix it by hand")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise Refused(f"{CONFIG_FILE} is not a mapping — fix it by hand")
    return data


@dataclass
class Plan:
    key: str
    value: object
    base: str
    noop: bool
    old_text: str
    new_text: str
    diff: str
    engine: str
    notes: list = field(default_factory=list)


def plan(data: bytes, key: str, value, root: Path) -> Plan:
    """Compute (never write) the edit of `data`. Raises Refused."""
    kind = check_key(key)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{CONFIG_FILE} is not UTF-8 — fix it by hand")
    old = _load(text)
    state, current = saved(old, key)
    base = _sha(data)
    notes: list[str] = []
    # the layout refusals come FIRST and always apply — a duplicated key or an alias must
    # not slip through as "already set" just because it parses to the requested value
    new_text = edit_text(text, key, value)
    if state == "set" and current == value:
        eff, problems = effective(old, key, root)
        if eff != value:
            notes.append(f"the engine still uses {describe_effective(key, eff)}: " + "; ".join(problems))
        return Plan(key, value, base, True, text, text, "", describe_effective(key, eff), notes)
    new = _load(new_text)
    if new != _expected(old, key, value):
        raise Refused(f"internal: the edit would change more than `{key}` — nothing written; "
                      f"please report this with your {CONFIG_FILE}")
    eff, problems = effective(new, key, root)
    must_honour = (kind == "minutes") or value is True
    if must_honour and eff != value:
        raise Refused(f"`{key}: {render(kind, value)}` is refused: the engine would still use "
                      f"{describe_effective(key, eff)} — " + ("; ".join(problems) or "no reason given")
                      + "; nothing written")
    from tagteam.config import validate_config
    try:
        # the expected-tree check means the edit introduced none of these
        notes += [f"{CONFIG_FILE}: {p}" for p in validate_config(new)]
    except Exception:
        pass
    diff = render_diff(text, new_text)
    return Plan(key, value, base, False, text, new_text, diff, describe_effective(key, eff), notes)


_NO_EOL = "\\ No newline at end of file"


def _diff_lines(text: str) -> list[str]:
    """Lines for the diff, without their endings; a final line with no
    newline carries git's marker, so a changed last line reads as its own
    `-` / `+` lines (impl r1 review)."""
    lines = text.splitlines()
    if lines and not text.endswith(("\n", "\r")):
        lines[-1] = lines[-1] + "\n" + _NO_EOL
    return lines


def render_diff(old: str, new: str) -> str:
    out = list(difflib.unified_diff(_diff_lines(old), _diff_lines(new),
                                    fromfile=CONFIG_FILE, tofile=f"{CONFIG_FILE} (edited)", lineterm=""))
    return ("\n".join(out) + "\n") if out else ""


def _write(root: Path, data: bytes) -> None:
    from tagteam import safe_read
    target = root / CONFIG_FILE
    probe = safe_read._lstat_chain(root, CONFIG_FILE)
    if probe.state != "file":
        raise Refused(f"{CONFIG_FILE}: {probe.detail or 'not a regular file'} — nothing written")
    mode = stat.S_IMODE(os.lstat(target).st_mode)
    tmp = root / f".{CONFIG_FILE}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def preview(root: str | Path, key: str, value) -> Plan:
    root = Path(root)
    return plan(_read(root), key, value, root)


def apply(root: str | Path, key: str, value, expect: str | None = None) -> Plan:
    """Make the edit under the project's writer lock. Raises Refused."""
    from tagteam import dualwrite
    root = Path(root)
    dualwrite.refuse_if_read_only(f"`tagteam config set` refused: {CONFIG_FILE}")
    with dualwrite.writer_lock(str(root)):
        data = _read(root)
        if expect is not None and _sha(data) != expect:
            raise Refused(f"{CONFIG_FILE} changed since the preview — preview again; nothing written", "stale")
        p = plan(data, key, value, root)
        if p.noop:
            return p
        if _TEST_HOOK is not None:
            _TEST_HOOK()
        if _read(root) != data:                    # an external editor wrote meanwhile
            raise Refused(f"{CONFIG_FILE} changed while editing — nothing written; run it again", "stale")
        _write(root, p.new_text.encode("utf-8"))
    return p


def _watcher_note(root: Path) -> str | None:
    try:
        from tagteam import watchlog
        if watchlog.beat_view(root).get("state") in ("fresh", "in-turn", "stale"):
            beat = watchlog.read_beat(root) or {}
            return (f"a running watcher (pid {beat.get('pid')}) keeps the settings it started with "
                    f"until it is restarted")
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

USAGE = f"""\
Usage: tagteam config keys [--json]
       tagteam config set KEY VALUE --preview [--json]
       tagteam config set KEY VALUE [--expect SHA] [--by NAME]

Edits a small safe set of {CONFIG_FILE} keys as a targeted line edit (comments,
blank lines and order are kept), and only when the engine would then do what
was asked. Keys: {', '.join(SAFE_KEYS)}.
--preview prints the diff and `base: <sha256>`; --expect SHA refuses unless the
file still has exactly the previewed bytes. Anything else: edit {CONFIG_FILE} by hand."""


def keys_rows(root: str | Path) -> list[dict]:
    """Saved and effective value of every safe key (a read; nothing created)."""
    root = Path(root)
    try:
        cfg = _load(_read(root).decode("utf-8"))
    except (Refused, UnicodeDecodeError):
        cfg = {}
    rows = []
    for key, (kind, label) in SAFE_KEYS.items():
        state, value = saved(cfg, key)
        eff, problems = effective(cfg, key, root)
        rows.append({"key": key, "kind": kind, "label": label, "saved_state": state,
                     "saved": value if state == "set" else None, "effective": eff, "problems": problems})
    return rows


def _fmt_plan(p: Plan, *, preview_only: bool, by: str | None, root: Path) -> str:
    lines = []
    if p.noop:
        lines.append(f"{p.key}: already {render(SAFE_KEYS[p.key][0], p.value)} in {CONFIG_FILE}; nothing written.")
    else:
        lines.append(p.diff.rstrip("\n"))
        lines.append(("Would write" if preview_only else "Written")
                     + f" {p.key}: {render(SAFE_KEYS[p.key][0], p.value)}" + (f" (by {by})" if by and not preview_only else "") + ".")
    lines.append(f"engine: {p.engine}")
    lines += [f"note: {n}" for n in p.notes]
    w = _watcher_note(root)
    if w and not p.noop:
        lines.append(f"note: {w}")
    if preview_only:
        lines.append(f"base: {p.base}")
    return "\n".join(lines)


def config_command(args: list[str], project_root: str | Path | None = None, out=None) -> int:
    from tagteam.state import _resolve_project_root
    err = out if out is not None else sys.stderr
    out = out if out is not None else sys.stdout
    root = Path(project_root) if project_root else Path(_resolve_project_root())
    args = list(args)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE, file=out)
        return 0 if args else 2
    sub, rest = args[0], args[1:]
    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]
    if sub == "keys":
        rows = keys_rows(root)
        if as_json:
            print(json.dumps(rows, indent=2, default=str), file=out)
        else:
            for r in rows:
                sv = r["saved"] if r["saved_state"] == "set" else r["saved_state"]
                print(f"{r['key']:<24} saved: {str(sv).lower():<8} engine: {describe_effective(r['key'], r['effective'])}", file=out)
        return 0
    if sub != "set":
        print(USAGE, file=err)
        return 2
    opts: dict = {"--preview": False, "--expect": None, "--by": None}
    pos: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--preview":
            opts[a] = True
        elif a in ("--expect", "--by"):
            if i + 1 >= len(rest):
                print(f"{a} needs a value", file=err)
                return 2
            opts[a] = rest[i + 1]
            i += 1
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        print(USAGE, file=err)
        return 2
    try:
        check_key(pos[0])
        value = parse_cli_value(pos[0], pos[1])
        if opts["--preview"]:
            p = preview(root, pos[0], value)
            if as_json:
                print(json.dumps({"ok": True, "noop": p.noop, "diff": p.diff, "base": p.base,
                                  "engine": p.engine, "notes": p.notes,
                                  "message": _fmt_plan(p, preview_only=True, by=None, root=root)}), file=out)
            else:
                print(_fmt_plan(p, preview_only=True, by=None, root=root), file=out)
            return 0
        p = apply(root, pos[0], value, expect=opts["--expect"])
        print(_fmt_plan(p, preview_only=False, by=opts["--by"], root=root), file=out)
        return 0
    except Refused as e:
        print(str(e), file=err)
        return 2 if e.kind == "usage" else 1
