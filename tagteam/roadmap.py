"""
Roadmap parser for extracting phase information from docs/roadmap.md.

Parses the markdown roadmap file to produce an ordered list of phases
with their completion status, used by full-roadmap execution mode.
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RoadmapPhase:
    """A single phase extracted from the roadmap."""

    slug: str
    name: str
    status: str


# Pattern: ### Phase N: <name>
_PHASE_HEADING_RE = re.compile(
    r"^###\s+Phase\s+\d+:\s+(.+)$", re.MULTILINE
)

# Pattern: - **Status:** <status>
_STATUS_RE = re.compile(
    r"^-\s+\*\*Status:\*\*\s+(.+)$", re.MULTILINE
)


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
    for i, match in enumerate(headings):
        name = match.group(1).strip()
        slug = _slugify(name)

        # Extract the section text between this heading and the next
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        section = content[start:end]

        # Look for status line in this section
        status_match = _STATUS_RE.search(section)
        status = status_match.group(1).strip() if status_match else "Unknown"

        phases.append(RoadmapPhase(slug=slug, name=name, status=status))

    return phases


def get_incomplete_phases(roadmap_path: Path) -> list[RoadmapPhase]:
    """Parse roadmap and return only incomplete phases.

    Raises:
        FileNotFoundError: If roadmap_path does not exist.
        ValueError: If no phases found, or all phases are complete.
    """
    all_phases = parse_roadmap(roadmap_path)
    incomplete = [p for p in all_phases if p.status != "Complete"]

    if not incomplete:
        raise ValueError(
            "All roadmap phases are complete. Nothing to run."
        )

    return incomplete


def build_queue(
    roadmap_path: Path,
    start_phase: str | None = None,
) -> list[str]:
    """Build an ordered queue of phase slugs to execute.

    Args:
        roadmap_path: Path to docs/roadmap.md.
        start_phase: Optional slug to start from (skips earlier phases).

    Returns:
        List of phase slugs in execution order.

    Raises:
        FileNotFoundError: If roadmap file missing.
        ValueError: If no phases, all complete, or start_phase not found.
    """
    incomplete = get_incomplete_phases(roadmap_path)
    slugs = [p.slug for p in incomplete]

    if start_phase is None:
        return slugs

    # Find the start phase in the incomplete list
    if start_phase not in slugs:
        # Check if it exists in the full roadmap (might be complete)
        all_phases = parse_roadmap(roadmap_path)
        all_slugs = [p.slug for p in all_phases]
        if start_phase in all_slugs:
            raise ValueError(
                f"Phase '{start_phase}' is already complete."
            )
        raise ValueError(
            f"Phase '{start_phase}' not found in {roadmap_path}."
        )

    start_idx = slugs.index(start_phase)
    return slugs[start_idx:]


def roadmap_command(args: list[str]) -> int:
    """Handle `python -m tagteam roadmap [subcommand]`.

    Subcommands:
        queue [start-phase]   Print comma-separated queue of incomplete phase slugs
        phases                List all phases with their status
    """
    if not args:
        print("Usage: python -m tagteam roadmap <queue|phases> [options]")
        print()
        print("Subcommands:")
        print("  queue [start-phase]   Print comma-separated queue of incomplete phase slugs")
        print("  phases                List all phases with their status")
        return 1

    subcmd = args[0]
    roadmap_path = Path("docs/roadmap.md")

    if subcmd == "queue":
        start_phase = args[1] if len(args) > 1 else None
        try:
            slugs = build_queue(roadmap_path, start_phase=start_phase)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        print(",".join(slugs))
        return 0

    if subcmd == "phases":
        try:
            phases = parse_roadmap(roadmap_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}")
            return 1
        for p in phases:
            print(f"{p.slug}\t{p.status}\t{p.name}")
        return 0

    print(f"Unknown roadmap subcommand: {subcmd}")
    print("Usage: python -m tagteam roadmap <queue|phases>")
    return 1
