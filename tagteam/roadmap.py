"""
Roadmap parser for extracting phase information from docs/roadmap.md.

Parses the markdown roadmap file to produce an ordered list of phases
with their completion status, used by full-roadmap execution mode.

Phase 40 (roadmap as a DAG): a phase block may carry an optional
``- **Depends on:** a, Phase 3, Exact Name`` line. The roadmap is then a
directed acyclic graph: `build_queue` is a stable topological order
(byte-identical to the flat list for a well-formed roadmap without edges),
`ready_phases`/`blocked_phases` say what can start now, and
`check_graph` reports every identity/edge problem at once. There is exactly
ONE dependency-satisfaction rule (`dep_satisfied`): a dependency is
satisfied when its roadmap disposition is terminal on disk OR it was
approved in the active full-roadmap run (`state.roadmap.completed`).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class RoadmapPhase:
    """A single phase extracted from the roadmap."""

    slug: str
    name: str
    status: str
    number: int | None = None
    # Phase 40: dependency references as resolved slugs. A reference that
    # could not be resolved is kept verbatim so `check_graph` can report it;
    # `parse_roadmap` itself stays lenient.
    depends_on: list[str] = field(default_factory=list)
    # Phase 59: an optional single-letter heading suffix (`### Phase 58a:`),
    # normalized to lowercase. Appended last so positional construction of the
    # earlier fields keeps working. Phase identity is the (number, suffix)
    # pair: `Phase 5` and `Phase 5b` are different phases, and neither is a
    # duplicate of the other.
    suffix: str = ""


class RoadmapGraphError(ValueError):
    """The roadmap is not a usable graph (identity or edge problems).
    `problems` lists every problem found; the message joins them."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("roadmap invalid: " + "; ".join(self.problems))


# Pattern: ### Phase N: <name>
_PHASE_HEADING_RE = re.compile(
    r"^###[ \t]+Phase[ \t]+(\d+)([A-Za-z]?):[ \t]+(.+)$", re.MULTILINE
)
# Lenient heading scan for identity validation: also matches a bare
# `### Phase N:` (no name) that the strict pattern skips.
_PHASE_HEADING_LENIENT_RE = re.compile(
    r"^###[ \t]+Phase[ \t]+(\d+)([A-Za-z]?):[ \t]*(.*)$", re.MULTILINE
)

# Pattern: - **Status:** <status>
_STATUS_RE = re.compile(
    r"^-\s+\*\*Status:\*\*\s+(.+)$", re.MULTILINE
)

# Pattern: - **Depends on:** a, b   (also `**Depends on**:`; several lines merge)
_DEPENDS_RE = re.compile(
    r"^-\s+\*\*Depends on:?\*\*:?\s*(.*)$", re.MULTILINE | re.IGNORECASE
)
_DEP_NONE_WORDS = frozenset({"", "none", "nothing", "-", "—", "n/a", "na"})
_PHASE_NUM_REF_RE = re.compile(
    r"^phase[\s_-]*(\d+)([A-Za-z]?)$", re.IGNORECASE)
_PHASE_NUM_SLUG_REF_RE = re.compile(
    r"^phase-(\d+)([A-Za-z]?)-(.+)$", re.IGNORECASE)


def _slugify(name: str) -> str:
    """Convert a phase name to a URL/file-safe slug.

    Example: 'Configurable Agents Init' -> 'configurable-agents-init'
    """
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def parse_roadmap(roadmap_path: Path) -> list[RoadmapPhase]:
    """Parse docs/roadmap.md and return all phases in order.

    Raises:
        FileNotFoundError: If roadmap_path does not exist.
        ValueError: If no phase headings are found.
    """
    if not roadmap_path.exists():
        raise FileNotFoundError(
            f"{roadmap_path} not found. Create it before using --roadmap mode."
        )

    content = roadmap_path.read_text(encoding="utf-8")

    # Split content into sections by phase heading
    headings = list(_PHASE_HEADING_RE.finditer(content))
    if not headings:
        raise ValueError(
            f"No phases found in {roadmap_path}. "
            "Expected '### Phase N: <name>' headings."
        )

    phases: list[RoadmapPhase] = []
    raw_deps: list[list[str]] = []
    for i, match in enumerate(headings):
        number = int(match.group(1))
        suffix = match.group(2).lower()
        name = match.group(3).strip()
        slug = _slugify(name)

        # Extract the section text between this heading and the next
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        section = content[start:end]

        # Look for status line in this section
        status_match = _STATUS_RE.search(section)
        status = status_match.group(1).strip() if status_match else "Unknown"

        phases.append(RoadmapPhase(slug=slug, name=name, status=status,
                                   number=number, suffix=suffix))
        raw_deps.append(_split_dep_refs(section))

    # Second pass: resolve dependency references against the full phase list.
    for phase, refs in zip(phases, raw_deps):
        resolved: list[str] = []
        for ref in refs:
            target = _resolve_ref(ref, phases)
            value = target.slug if target is not None else ref
            if value not in resolved:
                resolved.append(value)
        phase.depends_on = resolved

    return phases


def _split_dep_refs(section: str) -> list[str]:
    """All `Depends on:` references in a phase section, in order, de-duplicated."""
    refs: list[str] = []
    for m in _DEPENDS_RE.finditer(section):
        for part in re.split(r"[,;]", m.group(1)):
            ref = part.strip().strip("`\"'").strip()
            if ref.casefold() in _DEP_NONE_WORDS:
                continue
            if ref not in refs:
                refs.append(ref)
    return refs


def _resolve_ref(ref: str, phases: list[RoadmapPhase]) -> RoadmapPhase | None:
    """Resolve one dependency reference: `Phase N`/`phase-N` (heading number,
    only when unique), an exact name (case-insensitive), a slug, or the
    `phase-N-slug` state format. None when nothing matches."""
    text = ref.strip()
    m = _PHASE_NUM_REF_RE.match(text)
    if m:
        want = (int(m.group(1)), m.group(2).lower())
        hits = [p for p in phases if (p.number, p.suffix) == want]
        return hits[0] if len(hits) == 1 else None
    folded = text.casefold()
    for p in phases:
        if p.name.casefold() == folded:
            return p
    slug = _slugify(text)
    for p in phases:
        if slug and p.slug == slug:
            return p
    m = _PHASE_NUM_SLUG_REF_RE.match(text)
    if m:
        inner = _slugify(m.group(3))
        for p in phases:
            if inner and p.slug == inner:
                return p
    return None


# Phase 37: terminal dispositions. A status line like "✅ Complete — impl
# approved …", "Complete (2026-05-03).", "Absorbed — see …", "Deferred (…)",
# "Superseded by …" all mean "nothing left to run here". Matched on the
# normalized prefix (emoji/decoration stripped, case-folded) so free-text
# after the word does not matter, and only on the *first* word so
# "Not started" and "In progress" stay actionable.
_TERMINAL_STATUS_WORDS = ("complete", "completed", "done", "absorbed", "deferred",
                          "superseded", "shipped", "closed", "decided")
_STATUS_DECORATION_RE = re.compile(r"^[^A-Za-z]+")


def normalize_status(status: str | None) -> str:
    text = (status or "").strip()
    text = _STATUS_DECORATION_RE.sub("", text)      # strip emoji / bullets / spaces
    return text.casefold()


def is_terminal_status(status: str | None) -> bool:
    """True when the roadmap disposition means "not actionable"."""
    norm = normalize_status(status)
    if not norm:
        return False
    first = re.split(r"[^a-z]+", norm, maxsplit=1)[0]
    return first in _TERMINAL_STATUS_WORDS


def get_incomplete_phases(roadmap_path: Path) -> list[RoadmapPhase]:
    """Parse roadmap and return only actionable phases (any disposition
    that is not terminal — see `is_terminal_status`).

    Raises:
        FileNotFoundError: If roadmap_path does not exist.
        ValueError: If no phases found, or all phases are complete.
    """
    all_phases = parse_roadmap(roadmap_path)
    incomplete = [p for p in all_phases if not is_terminal_status(p.status)]

    if not incomplete:
        raise ValueError(
            "All roadmap phases are complete. Nothing to run."
        )

    return incomplete


# ── Phase 40: graph identity + edges ────────────────────────────


def validate_identities(roadmap_text: str) -> list[str]:
    """Every identity problem in the raw roadmap text: empty heading names
    (including a bare `### Phase N:` the strict parser skips), duplicate
    heading numbers, duplicate normalized slugs. Empty list = clean."""
    problems: list[str] = []
    # Phase 59: keyed on the (number, suffix) pair. Keying on the bare number
    # would report a roadmap carrying both `Phase 5` and `Phase 5b` as a
    # duplicate and refuse it.
    numbers: dict[tuple[int, str], list[str]] = {}
    slugs: dict[str, list[str]] = {}
    for m in _PHASE_HEADING_LENIENT_RE.finditer(roadmap_text):
        number = int(m.group(1))
        suffix = m.group(2).lower()
        name = m.group(3).strip()
        label = f"Phase {number}{suffix}"
        if not name:
            problems.append(f"{label}: empty name")
        numbers.setdefault((number, suffix), []).append(name or "(empty)")
        if name:
            slug = _slugify(name)
            if not slug:
                problems.append(f"{label}: empty normalized slug ({name!r})")
            else:
                slugs.setdefault(slug, []).append(label)
    for (number, suffix), names in sorted(numbers.items()):
        if len(names) > 1:
            problems.append(
                f"duplicate phase number {number}{suffix}: " + ", ".join(names))
    for slug, labels in slugs.items():
        if len(labels) > 1:
            problems.append(
                f"duplicate slug '{slug}': " + ", ".join(labels))
    return problems


def dependency_graph(phases: list[RoadmapPhase]) -> dict[str, list[str]]:
    """{slug: [dependency slugs]} in roadmap order (references as resolved
    by `parse_roadmap`; unresolved ones are kept verbatim)."""
    return {p.slug: list(p.depends_on) for p in phases}


def validate_graph(phases: list[RoadmapPhase]) -> list[str]:
    """Edge problems: unknown references, self-dependencies, cycles."""
    problems: list[str] = []
    by_slug = {p.slug: p for p in phases}
    for p in phases:
        for dep in p.depends_on:
            if dep == p.slug:
                problems.append(f"{p.slug}: depends on itself")
            elif dep not in by_slug:
                problems.append(f"{p.slug}: unknown dependency '{dep}'")
    # Cycle detection (DFS, three colours), only over resolvable edges.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {p.slug: WHITE for p in phases}
    reported: set[tuple[str, ...]] = set()

    def visit(slug: str, stack: list[str]) -> None:
        colour[slug] = GREY
        stack.append(slug)
        for dep in by_slug[slug].depends_on:
            if dep not in by_slug or dep == slug:
                continue
            if colour[dep] == GREY:
                cyc = tuple(stack[stack.index(dep):] + [dep])
                key = tuple(sorted(set(cyc)))
                if key not in reported:
                    reported.add(key)
                    problems.append("cycle: " + " -> ".join(cyc))
            elif colour[dep] == WHITE:
                visit(dep, stack)
        stack.pop()
        colour[slug] = BLACK

    for p in phases:
        if colour[p.slug] == WHITE:
            visit(p.slug, [])
    return problems


def graph_problems(roadmap_path: Path) -> tuple[list[RoadmapPhase], list[str]]:
    """Parse + validate: (phases, every identity and edge problem)."""
    text = roadmap_path.read_text(encoding="utf-8") if roadmap_path.exists() else ""
    identity = validate_identities(text) if text else []
    phases = parse_roadmap(roadmap_path)
    problems = identity + validate_graph(phases)
    return phases, problems


def check_graph(roadmap_path: Path) -> list[RoadmapPhase]:
    """Parse the roadmap and raise `RoadmapGraphError` (listing every
    problem) unless it is a valid DAG with unique identities."""
    phases, problems = graph_problems(roadmap_path)
    if problems:
        raise RoadmapGraphError(problems)
    return phases


def has_edges(phases: list[RoadmapPhase]) -> bool:
    return any(p.depends_on for p in phases)


# ── Phase 40: the ONE dependency-satisfaction rule ──────────────


def _normalize_completed(completed: Iterable[str] | None) -> set[str]:
    from tagteam.state import normalize_phase_key
    return {normalize_phase_key(c) for c in (completed or []) if c}


def dep_satisfied(dep: str, by_slug: dict[str, RoadmapPhase],
                  completed: Iterable[str] | None = None) -> bool:
    """A dependency is satisfied when its roadmap disposition on disk is
    terminal OR it was approved in the active full-roadmap run
    (`state.roadmap.completed`). Used identically by `roadmap ready`, the
    watcher's advance / `roadmap resume` and `roadmap worktree`."""
    target = by_slug.get(dep)
    if target is not None and is_terminal_status(target.status):
        return True
    return dep in _normalize_completed(completed)


def unmet_dependencies(phase: RoadmapPhase, by_slug: dict[str, RoadmapPhase],
                       completed: Iterable[str] | None = None) -> list[str]:
    done = _normalize_completed(completed)
    return [d for d in phase.depends_on
            if not (dep_satisfied(d, by_slug, done))]


def _actionable(phases: list[RoadmapPhase],
                completed: Iterable[str] | None = None) -> list[RoadmapPhase]:
    done = _normalize_completed(completed)
    return [p for p in phases
            if not is_terminal_status(p.status) and p.slug not in done]


def ready_phases(phases: list[RoadmapPhase],
                 completed: Iterable[str] | None = None) -> list[RoadmapPhase]:
    """Incomplete, not-completed phases whose dependencies are all satisfied."""
    by_slug = {p.slug: p for p in phases}
    done = _normalize_completed(completed)
    return [p for p in _actionable(phases, done)
            if not unmet_dependencies(p, by_slug, done)]


def blocked_phases(phases: list[RoadmapPhase],
                   completed: Iterable[str] | None = None,
                   ) -> list[tuple[RoadmapPhase, list[str]]]:
    """Incomplete phases with at least one unmet dependency, with the list."""
    by_slug = {p.slug: p for p in phases}
    done = _normalize_completed(completed)
    out = []
    for p in _actionable(phases, done):
        unmet = unmet_dependencies(p, by_slug, done)
        if unmet:
            out.append((p, unmet))
    return out


def topological_queue(phases: list[RoadmapPhase], start: str | None = None,
                      completed: Iterable[str] | None = None,
                      ) -> tuple[list[str], list[str]]:
    """Stable Kahn order of the actionable phases: among candidates whose
    dependencies are all emitted or satisfied, always the earliest in
    roadmap order. With `start`, the queue covers `start` plus the
    actionable phases after it in roadmap order plus every unmet dependency
    ancestor of those (pulled in ahead of the phase that needs it);
    actionable phases before `start` that nothing needs are dropped.
    Returns (queue, pulled_in_ancestors). Edge-free roadmap → today's list.
    Assumes a validated graph (see `check_graph`)."""
    by_slug = {p.slug: p for p in phases}
    done = _normalize_completed(completed)
    order = {p.slug: i for i, p in enumerate(phases)}
    actionable = _actionable(phases, done)
    act_slugs = {p.slug for p in actionable}
    if not actionable:
        raise ValueError("All roadmap phases are complete. Nothing to run.")

    pulled: list[str] = []
    if start is None:
        wanted = set(act_slugs)
    else:
        if start not in by_slug:
            raise ValueError(f"Phase '{start}' not found in roadmap.")
        if start not in act_slugs:
            raise ValueError(f"Phase '{start}' is already complete.")
        s_pos = order[start]
        wanted = {p.slug for p in actionable if order[p.slug] >= s_pos}
        # Closure over unmet ancestors.
        frontier = list(wanted)
        while frontier:
            slug = frontier.pop()
            for dep in unmet_dependencies(by_slug[slug], by_slug, done):
                if dep in by_slug and dep in act_slugs and dep not in wanted:
                    wanted.add(dep)
                    pulled.append(dep)
                    frontier.append(dep)
        pulled.sort(key=lambda d: order[d])

    emitted: list[str] = []
    emitted_set: set[str] = set()
    remaining = sorted(wanted, key=lambda d: order[d])
    while remaining:
        pick = None
        for slug in remaining:
            deps = unmet_dependencies(by_slug[slug], by_slug, done)
            if all(d in emitted_set for d in deps):
                pick = slug
                break
        if pick is None:
            raise RoadmapGraphError(
                ["cycle among: " + ", ".join(remaining)])
        emitted.append(pick)
        emitted_set.add(pick)
        remaining.remove(pick)
    return emitted, pulled


def build_queue(
    roadmap_path: Path,
    start_phase: str | None = None,
    completed: Iterable[str] | None = None,
) -> list[str]:
    """Build an ordered queue of phase slugs to execute (Phase 40: a stable
    topological order; identical to the flat list for a well-formed roadmap
    without `Depends on` lines).

    Args:
        roadmap_path: Path to docs/roadmap.md.
        start_phase: Optional slug to start from (skips earlier phases that
            nothing after `start_phase` depends on).
        completed: Phases approved in the active full-roadmap run.

    Returns:
        List of phase slugs in execution order.

    Raises:
        FileNotFoundError: If roadmap file missing.
        RoadmapGraphError (a ValueError): identity/edge problems.
        ValueError: If no phases, all complete, or start_phase not found.
    """
    queue, _pulled = build_queue_with_notes(roadmap_path, start_phase, completed)
    return queue


def build_queue_with_notes(
    roadmap_path: Path,
    start_phase: str | None = None,
    completed: Iterable[str] | None = None,
) -> tuple[list[str], list[str]]:
    """`build_queue` plus the ancestors pulled in ahead of `start_phase`."""
    phases = check_graph(roadmap_path)
    try:
        return topological_queue(phases, start=start_phase, completed=completed)
    except ValueError as e:
        # Keep the historical wording for the not-found case.
        msg = str(e)
        if msg.startswith("Phase '") and msg.endswith("not found in roadmap."):
            raise ValueError(
                f"Phase '{start_phase}' not found in {roadmap_path}.") from None
        raise


# ── Phase 40: active-run completed list for the CLI ─────────────


def active_run_completed(project_dir: str | Path) -> list[str]:
    """`state.roadmap.completed` of the project's active full-roadmap run,
    or [] when there is no such run."""
    try:
        from tagteam.state import read_state
        state = read_state(str(project_dir))
    except Exception:
        return []
    if not state or state.get("run_mode") != "full-roadmap":
        return []
    roadmap = state.get("roadmap") or {}
    return [c for c in (roadmap.get("completed") or []) if isinstance(c, str)]


def graph_text(phases: list[RoadmapPhase], mermaid: bool = False,
               completed: Iterable[str] | None = None) -> str:
    """Text tree (or a mermaid `flowchart LR`) of the roadmap graph."""
    by_slug = {p.slug: p for p in phases}
    done = _normalize_completed(completed)
    lines: list[str] = []
    if mermaid:
        lines.append("flowchart LR")
        for p in phases:
            mark = "✓ " if (is_terminal_status(p.status) or p.slug in done) else ""
            label = p.name.replace('"', "'")
            lines.append(f'    {_mid(p.slug)}["{mark}{label}"]')
        for p in phases:
            for d in p.depends_on:
                if d in by_slug:
                    lines.append(f"    {_mid(d)} --> {_mid(p.slug)}")
        return "\n".join(lines) + "\n"
    for p in phases:
        if is_terminal_status(p.status):
            mark = "✓"
        elif p.slug in done:
            mark = "✓*"
        elif unmet_dependencies(p, by_slug, done):
            mark = "⏸"
        else:
            mark = "▶"
        line = f"{mark} {p.slug}"
        if p.depends_on:
            line += "  ← " + ", ".join(p.depends_on)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _mid(slug: str) -> str:
    return "p_" + re.sub(r"[^A-Za-z0-9_]", "_", slug)


_USAGE = """\
Usage: tagteam roadmap <subcommand> [options]

Subcommands:
  queue [start-phase]        Print comma-separated queue of incomplete phase slugs
                             (Phase 40: stable topological order; with a start
                             phase, unmet dependency ancestors are pulled in first)
  phases                     List all phases with their status (+ depends_on when present)
  check                      Validate the roadmap graph (exit 1 with every problem)
  graph [--mermaid]          Print the dependency graph
  ready [--json] [--roadmap-only]
                             Phases that can start now (dependencies satisfied by a
                             terminal roadmap disposition or the active run's completed list)
  resume                     Re-run the full-roadmap advance now (after unblocking)
  worktree <phase> [--from REF] [--target BRANCH]
                             Create ../<repo>-<phase>/ as its own tagteam project
  worktree <phase> --remove [--force]
                             Remove a phase worktree (refuses unmerged without --force)
  worktrees [--json]         List phase worktrees with their state
"""


def _roadmap_path(project_dir: str | None = None) -> Path:
    if project_dir:
        return Path(project_dir) / "docs" / "roadmap.md"
    return Path("docs/roadmap.md")


def _project_root() -> str:
    from tagteam.state import _resolve_project_root
    return _resolve_project_root()


def roadmap_command(args: list[str]) -> int:
    """Handle `python -m tagteam roadmap [subcommand]`."""
    if not args:
        print(_USAGE.rstrip())
        return 1

    subcmd = args[0]
    rest = args[1:]
    roadmap_path = _roadmap_path()

    if subcmd == "queue":
        start_phase = rest[0] if rest else None
        try:
            slugs, pulled = build_queue_with_notes(roadmap_path, start_phase=start_phase)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        if pulled:
            print(f"note: pulled in {len(pulled)} dependency ancestor(s) ahead of "
                  f"'{start_phase}': {', '.join(pulled)}", file=sys.stderr)
        print(",".join(slugs))
        return 0

    if subcmd == "phases":
        try:
            phases = parse_roadmap(roadmap_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        with_deps = has_edges(phases)
        for p in phases:
            line = f"{p.slug}\t{p.status}\t{p.name}"
            if with_deps:
                line += "\t" + ",".join(p.depends_on)
            print(line)
        return 0

    if subcmd == "check":
        try:
            phases, problems = graph_problems(roadmap_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        if problems:
            print(f"roadmap invalid ({len(problems)} problem(s)):")
            for pr in problems:
                print(f"  - {pr}")
            return 1
        edges = sum(len(p.depends_on) for p in phases)
        print(f"roadmap ok: {len(phases)} phase(s), {edges} dependency edge(s)")
        return 0

    if subcmd == "graph":
        mermaid = "--mermaid" in rest
        try:
            phases = check_graph(roadmap_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        completed = active_run_completed(_project_root())
        print(graph_text(phases, mermaid=mermaid, completed=completed), end="")
        return 0

    if subcmd == "ready":
        as_json = "--json" in rest
        roadmap_only = "--roadmap-only" in rest
        try:
            phases = check_graph(roadmap_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        completed = [] if roadmap_only else active_run_completed(_project_root())
        ready = ready_phases(phases, completed)
        blocked = blocked_phases(phases, completed)
        if as_json:
            print(json.dumps({
                "ready": [{"slug": p.slug, "status": p.status, "name": p.name,
                           "depends_on": p.depends_on} for p in ready],
                "blocked": [{"slug": p.slug, "status": p.status, "name": p.name,
                             "unmet": unmet} for p, unmet in blocked],
                "completed_in_run": list(completed),
            }, indent=2))
            return 0
        for p in ready:
            print(f"{p.slug}\t{p.status}\t{p.name}")
        if blocked:
            for p, unmet in blocked:
                print(f"blocked: {p.slug} (needs {', '.join(unmet)})",
                      file=sys.stderr)
        if completed:
            print(f"(+ {len(completed)} phase(s) completed in the active run: "
                  f"{', '.join(completed)})", file=sys.stderr)
        return 0

    if subcmd == "resume":
        from tagteam.watcher import roadmap_resume
        return roadmap_resume(_project_root())

    if subcmd == "worktree":
        from tagteam.worktree import worktree_command
        return worktree_command(rest)

    if subcmd == "worktrees":
        from tagteam.worktree import worktrees_command
        return worktrees_command(rest)

    print(f"Unknown roadmap subcommand: {subcmd}")
    print("Usage: tagteam roadmap <queue|phases|check|graph|ready|resume|worktree|worktrees>")
    return 1
