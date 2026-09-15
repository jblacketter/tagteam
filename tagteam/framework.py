"""Phase 52: safe framework migration — the engine behind ``tagteam setup``
and ``tagteam upgrade``.

Before this phase both commands overwrote every managed file on every run.
Now each managed path is *classified* against the current package and a
per-project manifest (``tagteam-manifest.json``, committed) of what tagteam
last wrote:

* ``absent``      → create
* ``current``     → byte-identical to the package → no write (adopted into
                    the manifest so the next package version can refresh it)
* ``framework``   → provably tagteam's bytes (manifest hash, a known vendored
                    contract, or the package rendered for the configured /
                    swapped names) → refresh
* ``custom``      → anything else → kept and reported; overwritten (or, for a
                    legacy flat skill, deleted) only with ``--accept PATH``
* ``unsupported`` → a symlink, directory, or non-regular file at the path or
                    any component → refused under every flag

Recovery is git: an accepted overwrite or deletion needs the path tracked and
clean so ``git checkout -- PATH`` restores it; ``--force`` lifts only that
refusal. Every write and delete re-checks the (existence, type, sha256)
preimage captured at classification. A second run on a migrated tree writes
nothing. Nothing here follows a symlink or deletes directory contents.

Naming: ``tagteam/migrate.py`` already holds the legacy tagteam.yaml
migration, so the plan's ``migrate.py`` lives here as ``framework.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tagteam.config import read_config
from tagteam.plugin import (PluginStatus, plugin_status, vendored_skill_provenance,
                            known_contract_hashes)
from tagteam.templates import render_template, get_template_variables

MANIFEST_NAME = "tagteam-manifest.json"
MANIFEST_SCHEMA = 1
SKILL_DIR_REL = ".claude/skills/handoff"
SKILL_REL = SKILL_DIR_REL + "/SKILL.md"
SKILLS_DIR_REL = ".claude/skills"

UNSUPPORTED, ABSENT, CURRENT, FRAMEWORK, CUSTOM = (
    "unsupported", "absent", "current", "framework", "custom")

# Verbs: planned action → outcome after apply.
_DONE = {"create": "created", "refresh": "refreshed", "accept": "accepted",
         "remove": "removed", "keep": "keep", "current": "current",
         "refuse": "refused", "none": ""}


def package_version() -> str:
    from tagteam import __version__
    return __version__


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Filesystem shape (lstat, never follow)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Shape:
    kind: str            # "absent" | "file" | "dir" | "unsupported"
    detail: str = ""     # for unsupported: what was found
    sha256: str | None = None


def _walk(root: Path, rel: str, want: str) -> Shape:
    """lstat every component of ``rel`` under ``root``. Parents must be plain
    directories or absent; the leaf must be absent or a plain ``want``
    (``"file"`` or ``"dir"``). Anything else is unsupported."""
    parts = Path(rel).parts
    cur = root
    for i, part in enumerate(parts):
        cur = cur / part
        shown = cur.relative_to(root).as_posix()
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return Shape("absent")
        except OSError as e:
            return Shape("unsupported", f"{shown} unreadable ({e.__class__.__name__})")
        if stat.S_ISLNK(st.st_mode):
            return Shape("unsupported", f"{shown} is a symlink")
        last = i == len(parts) - 1
        if not last:
            if not stat.S_ISDIR(st.st_mode):
                return Shape("unsupported", f"{shown} is not a directory")
            continue
        if want == "dir":
            if stat.S_ISDIR(st.st_mode):
                return Shape("dir")
            return Shape("unsupported", f"{shown} is not a directory")
        if stat.S_ISDIR(st.st_mode):
            return Shape("unsupported", f"{shown} is a directory")
        if not stat.S_ISREG(st.st_mode):
            return Shape("unsupported", f"{shown} is not a regular file")
        try:
            return Shape("file", sha256=_sha256_file(cur))
        except OSError as e:
            return Shape("unsupported", f"{shown} unreadable ({e.__class__.__name__})")
    return Shape("absent")


def observe(root: Path, rel: str) -> Shape:
    return _walk(root, rel, "file")


def observe_dir(root: Path, rel: str) -> Shape:
    return _walk(root, rel, "dir")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def manifest_path(root: Path) -> Path:
    return Path(root) / MANIFEST_NAME


def read_manifest(root: Path) -> tuple[dict | None, str]:
    """(manifest, state) — state is ``ok`` / ``none`` / ``invalid (<why>)``.
    Anything but ``ok`` means pre-manifest: no entry is trusted."""
    shape = observe(Path(root), MANIFEST_NAME)
    if shape.kind == "absent":
        return None, "none"
    if shape.kind != "file":
        return None, f"invalid ({shape.detail})"
    try:
        data = json.loads(manifest_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return None, f"invalid ({e.__class__.__name__})"
    if (not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA
            or not isinstance(data.get("files"), dict)):
        return None, "invalid (unrecognised schema)"
    files = {k: v for k, v in data["files"].items()
             if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("sha256"), str)}
    return {"schema": MANIFEST_SCHEMA, "tagteam": str(data.get("tagteam", "?")),
            "written_at": str(data.get("written_at", "")), "files": files}, "ok"


def _manifest_bytes(m: dict) -> bytes:
    return (json.dumps(m, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _same_manifest(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return a is b
    strip = lambda m: {k: v for k, v in m.items() if k != "written_at"}   # noqa: E731
    return strip(a) == strip(b)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class Item:
    rel: str
    kind: str                       # file | skill | skill-handover | extra | legacy
    source: Path | None = None
    source_rel: str = ""
    package_sha: str | None = None
    shape: Shape = field(default_factory=lambda: Shape("absent"))
    cls: str = ""
    reason: str = ""
    action: str = "none"            # create | refresh | accept | remove | keep | current | refuse | none
    entry: dict | None = None       # the manifest entry for this path, if any
    outcome: str = ""               # after apply: created | refreshed | … | refused: <why>
    note: str = ""                  # extra text for the report line

    @property
    def done(self) -> bool:
        return self.outcome in ("created", "refreshed", "accepted", "removed")

    @property
    def refused(self) -> bool:
        return self.outcome.startswith("refused") or (not self.outcome and self.action == "refuse")


@dataclass
class Plan:
    root: Path
    package_version: str
    plugin: PluginStatus
    vendor_skill: bool
    manifest: dict | None
    manifest_state: str
    items: list[Item]
    accept: tuple[str, ...]
    force: bool
    unknown_accepts: list[str] = field(default_factory=list)
    header: str = ""                # version line as observed at classification
    manifest_outcome: str = ""      # after apply: written | unchanged | refused: <why>
    applied: bool = False

    @property
    def refused(self) -> list[str]:
        out = [f"{i.rel}: {i.outcome or i.reason}" for i in self.items if i.refused]
        out += [f"--accept {p}: not a custom managed path" for p in self.unknown_accepts]
        if self.manifest_outcome.startswith("refused"):
            out.append(f"{MANIFEST_NAME}: {self.manifest_outcome}")
        return out


def _sources(data_dir: Path) -> list[tuple[str, Path, str]]:
    """(project relpath, package file, data-relative source) for every managed
    file the package ships — templates, checklists, workflows.md."""
    out: list[tuple[str, Path, str]] = []
    for f in sorted((data_dir / "templates").glob("*.md")):
        out.append((f"templates/{f.name}", f, f"templates/{f.name}"))
    for f in sorted((data_dir / "checklists").glob("*.md")):
        out.append((f"docs/checklists/{f.name}", f, f"checklists/{f.name}"))
    wf = data_dir / "workflows.md"
    if wf.is_file():
        out.append(("docs/workflows.md", wf, "workflows.md"))
    return out


def _render_variants(package: bytes, root: Path) -> list[tuple[str, bytes]]:
    """The package file rendered for the configured names and the swapped
    names — pre-manifest evidence that an older tagteam wrote the file.
    Empty when the package file carries no placeholders."""
    if b"{{" not in package:
        return []
    names = get_template_variables(read_config(root / "tagteam.yaml"))
    lead, reviewer = names.get("lead"), names.get("reviewer")
    if not lead or not reviewer:
        return []
    text = package.decode("utf-8")
    return [("configured names", render_template(text, {"lead": lead, "reviewer": reviewer}).encode("utf-8")),
            ("swapped names", render_template(text, {"lead": reviewer, "reviewer": lead}).encode("utf-8"))]


def _classify(item: Item, package: bytes, root: Path, known: dict[str, str]) -> None:
    s = item.shape
    if s.kind == "unsupported":
        item.cls, item.reason = UNSUPPORTED, f"unsupported filesystem shape ({s.detail})"
        return
    if s.kind == "absent":
        item.cls, item.reason = ABSENT, "absent"
        return
    sha = s.sha256
    if sha == item.package_sha:
        item.cls, item.reason = CURRENT, "matches the package"
        return
    if item.entry is not None and item.entry.get("sha256") == sha:
        item.cls, item.reason = FRAMEWORK, f"written by tagteam {item.entry.get('tagteam', '?')}"
        return
    if item.kind == "skill" and sha in known:
        item.cls, item.reason = FRAMEWORK, f"{known[sha]} contract"
        return
    for label, variant in _render_variants(package, root):
        if sha == sha256_bytes(variant):
            item.cls, item.reason = FRAMEWORK, f"matches the package rendered for the {label}"
            return
    if item.entry is not None:
        item.cls = CUSTOM
        item.reason = f"modified since tagteam {item.entry.get('tagteam', '?')} wrote it"
        return
    item.cls, item.reason = CUSTOM, "differs from the package"


def recoverability(root: Path, rel: str) -> tuple[bool, str]:
    """Can ``git checkout -- rel`` restore this path? (tracked and clean)"""
    git = shutil.which("git")
    if not git:
        return False, "git not available"

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([git, "-C", str(root), *args], capture_output=True, text=True)

    if run("rev-parse", "--show-toplevel").returncode != 0:
        return False, "not a git repository"
    if run("ls-files", "--error-unmatch", "--", rel).returncode != 0:
        return False, "untracked"
    r = run("status", "--porcelain", "--", rel)
    if r.returncode != 0:
        return False, "git status failed"
    if r.stdout.strip():
        return False, "uncommitted changes"
    return True, "tracked and clean"


def _skill_items(root: Path, data_dir: Path, vendor: bool,
                 entries: dict, known: dict[str, str]) -> list[Item]:
    items: list[Item] = []
    skill_dir = root / SKILL_DIR_REL
    if not vendor:
        prov = vendored_skill_provenance(skill_dir)
        it = Item(SKILL_REL, "skill-handover", entry=entries.get(SKILL_REL))
        if prov.removable:
            it.shape = observe(root, SKILL_REL)
            it.action, it.reason = "remove", prov.reason
        elif prov.reason == "absent":
            it.action, it.reason = "none", "served by the plugin — nothing to vendor"
        else:
            it.shape = observe(root, SKILL_REL)
            it.action, it.reason = "keep", prov.reason
        return [it]
    # Vendoring: SKILL.md is a managed file; the directory's other entries are
    # never touched.
    src = data_dir / SKILL_REL
    package = src.read_bytes()
    it = Item(SKILL_REL, "skill", source=src, source_rel=SKILL_REL,
              package_sha=sha256_bytes(package), entry=entries.get(SKILL_REL))
    dshape = observe_dir(root, SKILL_DIR_REL)
    if dshape.kind == "unsupported":
        it.shape = Shape("unsupported", dshape.detail)
    else:
        it.shape = observe(root, SKILL_REL)
    _classify(it, package, root, known)
    items.append(it)
    if dshape.kind == "dir":
        try:
            names = sorted(e.name for e in os.scandir(skill_dir) if e.name != "SKILL.md")
        except OSError:
            names = []
        for name in names:
            items.append(Item(f"{SKILL_DIR_REL}/{name}", "extra", cls=CUSTOM,
                              action="keep", reason="not managed by tagteam; never touched"))
    return items


def _legacy_items(root: Path, accept: set[str]) -> list[Item]:
    """Pre-plugin flat skills ``.claude/skills/handoff-*.md`` / ``handoff.md``:
    name matches are candidates, not provenance — kept unless accepted."""
    if observe_dir(root, SKILLS_DIR_REL).kind != "dir":
        return []
    try:
        names = sorted(e.name for e in os.scandir(root / SKILLS_DIR_REL)
                       if e.name == "handoff.md" or (e.name.startswith("handoff-") and e.name.endswith(".md")))
    except OSError:
        return []
    out = []
    for name in names:
        rel = f"{SKILLS_DIR_REL}/{name}"
        it = Item(rel, "legacy", shape=observe(root, rel), cls=CUSTOM,
                  reason="legacy flat skill; no provenance")
        if it.shape.kind == "unsupported":
            it.cls, it.reason = UNSUPPORTED, f"unsupported filesystem shape ({it.shape.detail})"
            it.action = "refuse" if rel in accept else "keep"
        elif rel in accept:
            it.action = "remove"
        else:
            it.action = "keep"
        out.append(it)
    return out


def build_plan(target: str | Path, *, data_dir: Path, no_plugin: bool = False,
               accept: tuple[str, ...] | list[str] = (), force: bool = False,
               plugin: PluginStatus | None = None) -> Plan:
    """Classify every managed path. Reads only."""
    root = Path(target).resolve()
    accept_set = {Path(a).as_posix().strip("/") for a in accept}
    status = PluginStatus(False, "--no-plugin") if no_plugin else (plugin or plugin_status(root))
    vendor = not status.installed
    manifest, mstate = read_manifest(root)
    entries = manifest["files"] if manifest else {}
    known = known_contract_hashes()

    items: list[Item] = []
    for rel, src, src_rel in _sources(data_dir):
        package = src.read_bytes()
        it = Item(rel, "file", source=src, source_rel=src_rel, package_sha=sha256_bytes(package),
                  shape=observe(root, rel), entry=entries.get(rel))
        _classify(it, package, root, known)
        items.append(it)
    items += _skill_items(root, data_dir, vendor, entries, known)
    items += _legacy_items(root, accept_set)

    matched: set[str] = set()
    for it in items:
        if it.kind in ("skill-handover", "extra", "legacy"):
            if it.action == "remove" and it.kind == "legacy":
                matched.add(it.rel)
            continue
        if it.cls == UNSUPPORTED:
            it.action = "refuse"
        elif it.cls == ABSENT:
            it.action = "create"
        elif it.cls == CURRENT:
            it.action = "current"
        elif it.cls == FRAMEWORK:
            it.action = "refresh"
        elif it.rel in accept_set:
            it.action = "accept"
            matched.add(it.rel)
        else:
            it.action = "keep"
    # Recoverability for everything the user authorised (accept / legacy remove).
    for it in items:
        if it.action in ("accept", "remove") and it.kind != "skill-handover":
            ok, why = recoverability(root, it.rel)
            if ok:
                it.note = why
            elif force:
                it.note = f"{why}; --force"
            else:
                it.action, it.reason = "refuse", f"not recoverable: {why} (use --force)"
    unknown = sorted(a for a in accept_set if a not in matched)
    return Plan(root=root, package_version=package_version(), plugin=status, vendor_skill=vendor,
                manifest=manifest, manifest_state=mstate, items=items,
                accept=tuple(sorted(accept_set)), force=force, unknown_accepts=unknown,
                header=_version_text(manifest, mstate, status))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _recheck(root: Path, item: Item) -> str | None:
    """None if the preimage (existence, type, sha256) still holds, else why not."""
    now = observe(root, item.rel)
    before = item.shape
    if now.kind == "unsupported":
        return f"changed since classification: {now.detail}"
    if now.kind != before.kind:
        return f"changed since classification: now {now.kind}, was {before.kind}"
    if now.sha256 != before.sha256:
        return "changed since classification: content differs"
    return None


def _mkdirs(root: Path, rel: str) -> None:
    """Create absent parent components one by one — never through a link."""
    cur = root
    for part in Path(rel).parts[:-1]:
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            os.mkdir(cur)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"{cur.relative_to(root).as_posix()} is not a plain directory")


def _write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _replace(path: Path, data: bytes) -> None:
    """temp file in the same directory, then rename over the target."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _act(root: Path, item: Item) -> None:
    path = root / item.rel
    why = _recheck(root, item)
    if why:
        item.outcome = f"refused: {why}"
        return
    try:
        if item.action == "create":
            _mkdirs(root, item.rel)
            _write_exclusive(path, item.source.read_bytes())
            item.outcome = "created"
        elif item.action in ("refresh", "accept"):
            _replace(path, item.source.read_bytes())
            item.outcome = _DONE[item.action]
        elif item.action == "remove":
            os.unlink(path)
            item.outcome = "removed"
    except FileExistsError:
        item.outcome = "refused: appeared since classification"
    except OSError as e:
        item.outcome = f"refused: {e.__class__.__name__}: {e}"


def _handover(root: Path, item: Item) -> None:
    """Plugin serves the contract: unlink the revalidated sole SKILL.md and
    remove the then-empty directory. Never recursive."""
    skill_dir = root / SKILL_DIR_REL
    prov = vendored_skill_provenance(skill_dir)
    if not prov.removable:
        item.outcome = f"refused: changed since classification: {prov.reason}"
        return
    why = _recheck(root, item)
    if why:
        item.outcome = f"refused: {why}"
        return
    try:
        os.unlink(skill_dir / "SKILL.md")
    except OSError as e:
        item.outcome = f"refused: {e.__class__.__name__}: {e}"
        return
    item.outcome = "removed"
    try:
        os.rmdir(skill_dir)
    except OSError as e:
        item.note = f"directory left in place ({e.__class__.__name__})"


def projected_manifest(plan: Plan) -> dict | None:
    """The manifest the tree should have after this run: entries exactly for
    paths whose bytes are verified package output."""
    prior = plan.manifest["files"] if plan.manifest else {}
    files: dict[str, dict] = {}
    for it in plan.items:
        if it.kind not in ("file", "skill", "skill-handover"):
            continue
        done = it.done if plan.applied else it.action in ("create", "refresh", "accept")
        refused = it.refused if plan.applied else it.action == "refuse"
        if it.kind == "skill-handover":
            removed = it.outcome == "removed" if plan.applied else it.action == "remove"
            if removed:
                continue
            e = prior.get(it.rel)
            if e and it.shape.kind == "file" and it.shape.sha256 == e.get("sha256"):
                files[it.rel] = e
            continue
        if done:
            files[it.rel] = {"sha256": it.package_sha, "source": it.source_rel,
                             "tagteam": plan.package_version}
        elif it.cls == CURRENT:
            e = it.entry
            if e and e.get("sha256") == it.shape.sha256:
                files[it.rel] = e
            else:
                files[it.rel] = {"sha256": it.package_sha, "source": it.source_rel,
                                 "tagteam": plan.package_version}
        elif refused or it.cls == UNSUPPORTED:
            if it.rel in prior:
                files[it.rel] = prior[it.rel]
        # custom / absent-refused: no entry
    if not files and plan.manifest is None:
        return None
    return {"schema": MANIFEST_SCHEMA, "tagteam": plan.package_version,
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": files}


def manifest_pending(plan: Plan) -> bool:
    proj = projected_manifest(plan)
    return not _same_manifest(proj, plan.manifest)


def _write_manifest(plan: Plan) -> None:
    proj = projected_manifest(plan)
    if _same_manifest(proj, plan.manifest):
        plan.manifest_outcome = "unchanged"
        return
    shape = observe(plan.root, MANIFEST_NAME)
    if shape.kind == "unsupported":
        plan.manifest_outcome = f"refused: unsupported filesystem shape ({shape.detail})"
        return
    path = manifest_path(plan.root)
    try:
        if shape.kind == "absent":
            _write_exclusive(path, _manifest_bytes(proj))
        else:
            _replace(path, _manifest_bytes(proj))
    except OSError as e:
        plan.manifest_outcome = f"refused: {e.__class__.__name__}: {e}"
        return
    plan.manifest_outcome = "written"


def apply(plan: Plan) -> Plan:
    """Act on the plan in report order; each path is re-checked right before
    it is touched and refused alone if it moved. The manifest is written
    last, once, only if the projection differs from disk."""
    for it in plan.items:
        if it.kind == "skill-handover":
            if it.action == "remove":
                _handover(plan.root, it)
            else:
                it.outcome = _DONE.get(it.action, "")
        elif it.action in ("create", "refresh", "accept", "remove"):
            _act(plan.root, it)
        elif it.action == "refuse":
            it.outcome = f"refused: {it.reason}"
        else:
            it.outcome = _DONE.get(it.action, "")
    plan.applied = True
    _write_manifest(plan)
    return plan


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _version_text(m: dict | None, state: str, plugin: PluginStatus) -> str:
    if m:
        mtext = f"manifest {m['tagteam']}" + (f" (written {m['written_at']})" if m["written_at"] else "")
    else:
        mtext = f"manifest {state}"
    return f"package {package_version()} · {mtext} · plugin: {plugin}"


def version_line(root: Path, plugin: PluginStatus | None = None) -> str:
    """``package X · manifest Y (written D) · plugin: …`` — three things a
    package update does not synchronise."""
    m, state = read_manifest(Path(root))
    return _version_text(m, state, plugin or plugin_status(root))


def _accept_hint(plan: Plan, it: Item) -> str:
    return f"accept with: tagteam setup {plan.root} --accept {it.rel}"


def format_report(plan: Plan) -> str:
    lines = [f"Framework files: {plan.header}"]
    if plan.force:
        lines.append("  --force: recoverability refusals lifted for accepted paths")
    current = 0
    for it in plan.items:
        if it.kind == "skill-handover":
            if it.action == "remove":
                verb = it.outcome or "remove"
                if verb == "removed":
                    line = f"  removed vendored handoff skill ({it.reason}) — served by the plugin"
                elif verb == "remove":
                    line = f"  remove   {it.rel} — {it.reason} — served by the plugin"
                else:
                    line = f"  {verb:<8} {it.rel} — {it.outcome}"
            elif it.action == "none":
                line = f"  handoff skill {it.reason}"
            else:
                line = f"  kept {SKILL_DIR_REL}/: {it.reason}"
            if it.note:
                line += f" ({it.note})"
            lines.append(line)
            continue
        if it.action == "current" and not it.refused:
            current += 1
            continue
        verb = it.outcome if plan.applied else it.action
        if verb.startswith("refused"):
            text = f"  refused  {it.rel} — {verb[len('refused: '):] if verb.startswith('refused: ') else it.reason}"
        else:
            verb = verb or it.action
            text = f"  {verb:<8} {it.rel} — {it.reason}"
            if it.action in ("accept", "remove") and it.note:
                text += f" ({it.note})"
            if it.action == "keep" and it.kind in ("file", "skill"):
                text += f"; {_accept_hint(plan, it)}"
            elif it.action == "keep" and it.kind == "legacy":
                text += f"; delete with: tagteam setup {plan.root} --accept {it.rel}"
        lines.append(text)
    if current:
        lines.append(f"  current  {current} file(s) match the package — no change")
    for p in plan.unknown_accepts:
        lines.append(f"  refused  --accept {p} — not a custom managed path")
    if plan.applied:
        lines.append(f"Manifest: {MANIFEST_NAME} {plan.manifest_outcome}")
    else:
        lines.append(f"Manifest: {MANIFEST_NAME} {'would be written' if manifest_pending(plan) else 'unchanged'}"
                     " (preview — nothing written)")
    return "\n".join(lines)
