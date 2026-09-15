"""
`tagteam usage` (Phase 32): per-turn token usage for this project from the
`usage` table (written by the headless engine since Phase 31), with
roll-ups by role, by cycle (phase+type), and totals. `--json` for scripts.
No cross-project mode here — that is the Phase 35 hub.

Phase 55: `--by role|cycle|model|kind`; no dollar figures in the text view
(`--json` keeps `cost_usd`); read through `db.connect_for_read`, so the
command never creates or migrates the database.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _empty_bucket() -> dict:
    return {"turns": 0, "ok": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0,
            "cost_known_turns": 0, "duration_ms_total": 0, "duration_known_turns": 0,
            "mean_duration_ms": None}


def _add(bucket: dict, row: dict) -> None:
    bucket["turns"] += 1
    if row.get("status") == "ok":
        bucket["ok"] += 1
    else:
        bucket["failed"] += 1
    for k in _TOKEN_KEYS:
        v = row.get(k)
        if isinstance(v, (int, float)):
            bucket[k] += int(v)
    c = row.get("cost_usd")
    if isinstance(c, (int, float)):
        bucket["cost_usd"] += float(c)
        bucket["cost_known_turns"] += 1
    d = row.get("duration_ms")
    if isinstance(d, (int, float)):
        bucket["duration_ms_total"] += int(d)
        bucket["duration_known_turns"] += 1


def _finish(bucket: dict) -> dict:
    if bucket["duration_known_turns"]:
        bucket["mean_duration_ms"] = int(bucket["duration_ms_total"] / bucket["duration_known_turns"])
    bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return bucket


BY_KEYS = ("role", "cycle", "model", "kind")
DEFAULT_BY = ("role", "cycle")


def model_label(row: dict) -> str:
    return row.get("model") or f"{row.get('provider') or '?'} (model not reported)"


def model_parts(row: dict) -> list[tuple[str, dict]]:
    """(model, token-row) pairs for one usage row: one per `model_usage`
    entry when the row has a per-model split, else the row itself under its
    own model label. Status and duration ride along for the turn counts."""
    mu = row.get("model_usage")
    if isinstance(mu, dict) and mu:
        parts = []
        for name, tokens in mu.items():
            if not isinstance(tokens, dict):
                continue
            part = {k: tokens.get(k) for k in _TOKEN_KEYS}
            part.update(status=row.get("status"), duration_ms=row.get("duration_ms"))
            parts.append((str(name), part))
        if parts:
            return parts
    return [(model_label(row), row)]


def aggregate(rows: list[dict], by: tuple[str, ...] | list[str] = DEFAULT_BY) -> dict:
    """Pure roll-up: {"turns": rows, "by_<key>": {...} for each key in `by`,
    "totals": {...}}. `by_model` buckets count a turn once per model it used
    ("turns using this model"); totals are always row-based. `cost_usd` stays
    in the buckets for JSON consumers; the text view never prints it."""
    buckets: dict[str, dict[str, dict]] = {k: {} for k in by}
    totals = _empty_bucket()
    for r in rows:
        if "role" in buckets:
            _add(buckets["role"].setdefault(r.get("role") or "?", _empty_bucket()), r)
        if "cycle" in buckets:
            cyc = f"{r.get('phase') or '?'}/{r.get('type') or '?'}"
            _add(buckets["cycle"].setdefault(cyc, _empty_bucket()), r)
        if "kind" in buckets:
            _add(buckets["kind"].setdefault(r.get("kind") or "turn", _empty_bucket()), r)
        if "model" in buckets:
            for name, part in model_parts(r):
                _add(buckets["model"].setdefault(name, _empty_bucket()), part)
        _add(totals, r)
    out: dict = {"turns": list(rows)}
    for k in by:
        out[f"by_{k}"] = {name: _finish(b) for name, b in buckets[k].items()}
    out["totals"] = _finish(totals)
    return out


def _fmt_int(v) -> str:
    return f"{int(v):,}" if isinstance(v, (int, float)) else "-"


_BLOCK_TITLES = {"role": "By role:", "cycle": "By cycle:", "kind": "By kind:",
                 "model": "By model (turns using this model):"}


def render_text(agg: dict) -> str:
    lines = []
    rows = agg["turns"]
    if not rows:
        return "No usage rows yet (headless turns record them; see `tagteam watch --mode headless`)."
    lines.append(f"{'ts':<26} {'cycle':<34} {'role':<8} {'prov':<6} {'status':<12} "
                 f"{'dur':>7} {'in':>10} {'out':>8} {'cache_r':>10} {'cache_w':>9}")
    for r in rows:
        cyc = f"{r.get('phase') or '?'}/{r.get('type') or '?'} r{r.get('round')}"
        dur = r.get("duration_ms")
        dur_s = f"{dur/1000:.0f}s" if isinstance(dur, (int, float)) else "-"
        lines.append(f"{(r.get('ts') or '')[:25]:<26} {cyc[:34]:<34} {str(r.get('role'))[:8]:<8} "
                     f"{str(r.get('provider'))[:6]:<6} {str(r.get('status'))[:12]:<12} {dur_s:>7} "
                     f"{_fmt_int(r.get('input_tokens')):>10} {_fmt_int(r.get('output_tokens')):>8} "
                     f"{_fmt_int(r.get('cache_read_tokens')):>10} "
                     f"{_fmt_int(r.get('cache_write_tokens')):>9}")

    def summary(b: dict) -> str:
        mean = (f"{b['mean_duration_ms']/1000:.0f}s"
                if b["mean_duration_ms"] is not None else "-")
        return (f"turns={b['turns']} (ok {b['ok']}, failed {b['failed']})  "
                f"in={_fmt_int(b['input_tokens'])} out={_fmt_int(b['output_tokens'])} "
                f"cache_r={_fmt_int(b['cache_read_tokens'])} "
                f"cache_w={_fmt_int(b['cache_write_tokens'])} "
                f"mean={mean}")

    def block(title: str, buckets: dict):
        lines.append("")
        lines.append(title)
        for k, b in buckets.items():
            lines.append(f"  {k:<40} {summary(b)}")

    for k in BY_KEYS:
        if f"by_{k}" in agg:
            block(_BLOCK_TITLES[k], agg[f"by_{k}"])
    lines.append("")
    lines.append("Totals: " + summary(agg["totals"]))
    return "\n".join(lines)


def usage_command(args: list[str], project_root: str | Path | None = None,
                  out=None) -> int:
    out = out or sys.stdout
    phase = ctype = role = None
    limit = None
    as_json = False
    by: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--phase" and i + 1 < len(args):
            phase = args[i + 1]; i += 2
        elif a == "--type" and i + 1 < len(args):
            ctype = args[i + 1]; i += 2
        elif a == "--role" and i + 1 < len(args):
            role = args[i + 1]; i += 2
        elif a == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                print(f"--limit must be an integer, got {args[i+1]!r}", file=out); return 1
            i += 2
        elif a == "--by" and i + 1 < len(args):
            if args[i + 1] not in BY_KEYS:
                print(f"--by must be one of {', '.join(BY_KEYS)}, got {args[i+1]!r}", file=out)
                return 1
            if args[i + 1] not in by:
                by.append(args[i + 1])
            i += 2
        elif a == "--json":
            as_json = True; i += 1
        elif a in ("-h", "--help"):
            print("Usage: tagteam usage [--phase P] [--type plan|impl] [--role lead|reviewer] "
                  "[--by role|cycle|model|kind]... [--limit N] [--json]", file=out)
            return 0
        else:
            print(f"Unknown argument: {a}", file=out); return 1
    if project_root is None:
        from tagteam.state import _resolve_project_root
        project_root = _resolve_project_root()
    from tagteam import db
    # Phase 55: never creates or migrates, in either mode; an older schema
    # stays readable. No DB (or one unreadable without writing) → no rows.
    conn, note = db.connect_for_read(project_dir=str(project_root))
    if conn is None and note != "no database":
        # Rows exist but cannot be read without writing (e.g. a WAL without
        # its index): say so instead of printing an empty table.
        print(f"usage: cannot read the database without changing it: {note}", file=out)
        return 2
    if conn is None:
        rows = []
    else:
        try:
            rows = db.get_usage(conn, phase=phase, cycle_type=ctype)
        finally:
            conn.close()
    if role:
        rows = [r for r in rows if r.get("role") == role]
    if limit is not None:
        rows = rows[-limit:] if limit > 0 else []
    agg = aggregate(rows, tuple(by) or DEFAULT_BY)
    if as_json:
        print(json.dumps(agg, indent=2), file=out)
    else:
        print(render_text(agg), file=out)
    return 0
