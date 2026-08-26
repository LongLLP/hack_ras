# hack_ras/project/geoms.py
"""Geometry file operations: renumber (single/bulk), insert a numbering gap,
compact to contiguous, reorder, clone with a new title, delete.

The geometry analogue of project/plans.py. A geometry is a SHARED dependency —
many plans point at one geometry via `Geom File=g##` — so renumbering a geometry
rewrites that reference in every plan file, not just the geometry's own family.

Files keyed to a geometry number:
- the .g## text file and its .g##.hdf preprocessor sidecar,
- the .x## preprocessor run file (keyed to GEOMETRY, not the plan — see
  project/plans.py).
References to a geometry number:
- `Geom File=g##` in the .prj and in EVERY plan (.p##) that uses it,
- `Base.g##` tokens in the .rasmap (the <Geometries> RASGeometry layer and the
  `GeometryHDF=` attribute on plan layers) — see project/rasmap.py.

Left alone by design (cosmetic, same policy as plan renumbering): the .g##.hdf
internal attributes, the `Geometry Filename` attr embedded in each .p##.hdf, and
the stale plan title on .x## line 3. The .prj has no "Current Geometry" key
(geometry is selected per-plan), so there is no global pointer to repoint.

A plan mid-run on this geometry (a `.p##.tmp.hdf` next to a plan whose
`Geom File=` names it) makes operations refuse with GeomRunActive rather than
pull files from under a running simulation.
"""
from __future__ import annotations

import logging
import os

from hack_ras.project.plans import (
    _invalidate_model,
    _read_plan_ref,
    plan_path,
)
from hack_ras.project.ras_project import RasProject
from hack_ras.project.rasmap import (
    remove_geoms_from_rasmap,
    renumber_geoms_in_rasmap,
    retitle_in_rasmap,
)
from hack_ras.resolve import expand_id_spec
from hack_ras.utils.lines import content_of, eol_of, read_lines, write_lines

logger = logging.getLogger(__name__)


class GeomFileNotFound(FileNotFoundError):
    """A referenced geometry file (.g##) was not found on disk."""


class GeomIdInUse(FileExistsError):
    """The target geometry ID is already taken (listed in the .prj or on disk)."""


class GeomInUse(RuntimeError):
    """The geometry is still referenced by a plan's `Geom File=`."""


class DuplicateGeomTitle(ValueError):
    """The new geometry title collides with an existing geometry's title.

    HEC-RAS requires every geometry title within a project to be unique.
    """


class GeomRunActive(RuntimeError):
    """A plan using this geometry has a `.p##.tmp.hdf` — HEC-RAS is running it."""


# ---------------------------
# ID helpers
# ---------------------------

def _normalize_geom_id(raw: str) -> str:
    """'2' / 'G2' / 'g2' -> 'g02'. Raises ValueError for anything else."""
    s = str(raw).strip().lower().lstrip("g")
    if not s.isdigit():
        raise ValueError(f"Invalid geometry ID: {raw!r}")
    n = int(s)
    if not 1 <= n <= 99:
        raise ValueError(f"Geometry number out of range 1-99: {raw!r}")
    return f"g{n:02d}"


def _geom_num(gid: str) -> int:
    return int(gid[1:])


def geom_path(project: RasProject, geom_id: str) -> str:
    """Absolute path of a geometry file next to the .prj (existence not checked)."""
    gid = _normalize_geom_id(geom_id)
    return os.path.join(project.folder, f"{project.base_name}.{gid}")


def _geom_id_in_use(project: RasProject, gid: str) -> bool:
    """True if gid is listed in the .prj or has a .g##/.g##.hdf file on disk."""
    if gid in project.model.geom_file_ids:
        return True
    path = geom_path(project, gid)
    return os.path.exists(path) or os.path.exists(path + ".hdf")


def _read_geom_title(path: str) -> str:
    for line in read_lines(path):
        if line.startswith("Geom Title="):
            return content_of(line)[len("Geom Title="):].strip()
    return ""


# ---------------------------
# Geometry-keyed files & cross-references
# ---------------------------

def _plans_using_geom(project: RasProject, gid: str) -> list[str]:
    """Plan IDs (listed in the .prj, file present) whose Geom File= is gid."""
    hits = []
    for pid in project.model.plan_file_ids:
        ppath = plan_path(project, pid)
        if os.path.isfile(ppath) and _read_plan_ref(ppath, "Geom File") == gid:
            hits.append(pid)
    return hits


def _assert_no_geom_run_active(project: RasProject, gid: str) -> None:
    for pid in _plans_using_geom(project, gid):
        tmp = f"{project.base_name}.{pid}.tmp.hdf"
        if os.path.exists(os.path.join(project.folder, tmp)):
            raise GeomRunActive(
                f"{tmp} exists — HEC-RAS appears to be running plan '{pid}', "
                f"which uses geometry '{gid}'. Finish or stop the run first."
            )


def _geom_family_names(project: RasProject, gid: str) -> list[str]:
    """Existing filenames (relative to the project folder) keyed to gid:
    the .g## file, its .hdf sidecar, and the .x## preprocessor run file."""
    base = project.base_name
    num = gid[1:]
    fixed = [f"{base}.{gid}", f"{base}.{gid}.hdf", f"{base}.x{num}"]
    return [n for n in fixed
            if os.path.isfile(os.path.join(project.folder, n))]


def _renamed_geom_family_name(name: str, base: str, old: str, new: str) -> str:
    """Counterpart of one family filename under the new geometry ID."""
    o, n = old[1:], new[1:]
    if name.startswith(f"{base}.{old}"):        # .g##, .g##.hdf
        return f"{base}.{new}" + name[len(f"{base}.{old}"):]
    if name == f"{base}.x{o}":
        return f"{base}.x{n}"
    raise ValueError(f"Not a geometry-family filename for {old}: {name!r}")


def _rewrite_geom_refs_in_plans(project: RasProject, idmap: dict) -> list[str]:
    """Rewrite `Geom File=g##` references in every listed plan, one pass with
    the complete mapping (chain-safe). Returns 'p##: old -> new' strings."""
    report = []
    for pid in project.model.plan_file_ids:
        ppath = plan_path(project, pid)
        if not os.path.isfile(ppath):
            continue
        lines = read_lines(ppath)
        for i, line in enumerate(lines):
            c = content_of(line)
            if c.startswith("Geom File="):
                gid = c[len("Geom File="):].strip()
                if gid in idmap:
                    lines[i] = f"Geom File={idmap[gid]}" + line[len(c):]
                    write_lines(ppath, lines)
                    report.append(f"{pid}: {gid} -> {idmap[gid]}")
                break  # exactly one Geom File= per plan
    for entry in report:
        logger.info("plan geometry reference updated: %s", entry)
    return report


# ---------------------------
# Public operations
# ---------------------------

def renumber_geoms(project: RasProject, mapping: dict) -> dict:
    """Renumber several geometries at once: {'g03': 'g02', 'g05': 'g03', ...}.

    Everything is validated before any file is touched: every source must exist
    and be listed in the .prj, no two sources may share a target, and a target
    may only be occupied if its occupant is itself being moved by this mapping.
    Chains and cycles are handled automatically (a '<name>.renumtmp' hop breaks
    cycles).

    Renames the geometry family (.g##, .g##.hdf, .x##), then applies the
    complete mapping in ONE pass to the .prj (`Geom File=`), to `Geom File=` in
    EVERY plan that uses a renumbered geometry, and to `Base.g##` tokens in the
    .rasmap. One pass matters: sequential per-entry application would corrupt
    chained mappings (g03->g02 while g05->g03).

    Returns {'files': [(old, new), ...], 'plan_refs': [...],
    'rasmap_tokens': int}.
    """
    idmap: dict = {}
    for old_raw, new_raw in mapping.items():
        old, new = _normalize_geom_id(old_raw), _normalize_geom_id(new_raw)
        if old == new:
            raise ValueError(f"Old and new geometry IDs are the same: {old}")
        if old in idmap:
            raise ValueError(f"Duplicate source geometry ID: {old}")
        idmap[old] = new
    if len(set(idmap.values())) != len(idmap):
        raise ValueError(f"Duplicate target geometry IDs in mapping: {idmap}")

    listed = project.model.geom_file_ids
    for old in idmap:
        if not os.path.isfile(geom_path(project, old)):
            raise GeomFileNotFound(f"Geometry file not found: {geom_path(project, old)}")
        if old not in listed:
            raise ValueError(
                f"Geometry '{old}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to renumber it."
            )
        _assert_no_geom_run_active(project, old)
    for new in idmap.values():
        if new in idmap:
            continue  # occupied now, but its occupant is being moved too
        if _geom_id_in_use(project, new):
            raise GeomIdInUse(
                f"Geometry ID '{new}' is already in use."
            )

    base = project.base_name
    folder = project.folder
    pairs = []
    for old, new in idmap.items():
        for name in _geom_family_names(project, old):
            pairs.append((name, _renamed_geom_family_name(name, base, old, new)))
    sources = {src for src, _ in pairs}
    for src, dst in pairs:
        if dst not in sources and os.path.exists(os.path.join(folder, dst)):
            raise GeomIdInUse(
                f"Cannot rename {src} -> {dst}: target file already exists "
                "and is not part of this renumbering."
            )

    # Execute: rename whatever has a free target; break cycles via a temp name.
    pending = list(pairs)
    deferred = []
    while pending:
        ready = [(s, d) for s, d in pending
                 if not os.path.exists(os.path.join(folder, d))]
        if ready:
            for src, dst in ready:
                os.rename(os.path.join(folder, src), os.path.join(folder, dst))
            pending = [p for p in pending if p not in ready]
        else:
            src, dst = pending.pop(0)
            tmp = src + ".renumtmp"
            os.rename(os.path.join(folder, src), os.path.join(folder, tmp))
            deferred.append((tmp, dst))
    for tmp, dst in deferred:
        os.rename(os.path.join(folder, tmp), os.path.join(folder, dst))

    # .prj — one pass (no Current Geometry key to worry about).
    prj_lines = read_lines(project.prj_path)
    eol = eol_of(prj_lines)
    for i, line in enumerate(prj_lines):
        c = content_of(line)
        if c.startswith("Geom File="):
            gid = c[len("Geom File="):].strip()
            if gid in idmap:
                prj_lines[i] = f"Geom File={idmap[gid]}{eol}"
    write_lines(project.prj_path, prj_lines)

    plan_refs = _rewrite_geom_refs_in_plans(project, idmap)

    rasmap_path = os.path.join(folder, f"{base}.rasmap")
    rasmap_tokens = 0
    if os.path.isfile(rasmap_path):
        rasmap_tokens = renumber_geoms_in_rasmap(rasmap_path, base, idmap)

    _invalidate_model(project)
    logger.info(
        "renumbered %d geometr(ies): %d file(s), %d plan ref(s), "
        "%d rasmap token(s)",
        len(idmap), len(pairs), len(plan_refs), rasmap_tokens,
    )
    return {"files": pairs, "plan_refs": plan_refs,
            "rasmap_tokens": rasmap_tokens}


def renumber_geom(project: RasProject, old_id: str, new_id: str) -> None:
    """Rename geometry old_id to new_id — the single-entry case of
    renumber_geoms(); see there for everything that gets renamed/updated."""
    renumber_geoms(project, {old_id: new_id})


def insert_geom_gap(project: RasProject, at_id: str, count: int) -> dict:
    """Shift every listed geometry numbered >= at_id up by count, freeing the
    IDs at_id .. at_id+count-1. Returns {old_id: new_id} for what moved."""
    at = _geom_num(_normalize_geom_id(at_id))
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    to_shift = [g for g in project.model.geom_file_ids if _geom_num(g) >= at]
    mapping = {}
    for gid in to_shift:
        n = _geom_num(gid) + count
        if n > 99:
            raise ValueError(
                f"Shifting '{gid}' by {count} exceeds g99 — cannot insert gap."
            )
        mapping[gid] = f"g{n:02d}"

    if mapping:
        renumber_geoms(project, mapping)
    return mapping


def compact_geoms(project: RasProject) -> dict:
    """Renumber the listed geometries to a contiguous g01..gN by ascending
    number, filling any gaps (e.g. g01,g03,g05 -> g01,g02,g03). Returns the
    {old_id: new_id} mapping of what moved (empty if already contiguous)."""
    sorted_ids = sorted(project.model.geom_file_ids, key=_geom_num)
    mapping = {}
    for i, gid in enumerate(sorted_ids, start=1):
        target = f"g{i:02d}"
        if gid != target:
            mapping[gid] = target
    if mapping:
        renumber_geoms(project, mapping)
    return mapping


def reorder_geoms(project: RasProject, order) -> dict:
    """Renumber the listed geometries into the given order as a contiguous
    g01..gN.

    `order` is the COMPLETE list of the project's current geometry IDs, written
    in the order you want them to end up: passing ['g01','g03','g02'] moves g03
    into the g02 slot and g02 down to g03. Positions are assigned from the list,
    so the result is always contiguous — reordering a project with gaps
    (g01,g02,g09) compacts it as well, exactly like compact_geoms.

    The complete list is required on purpose: naming only the geometries you
    want to move would make the outcome depend on geometries you never
    mentioned. Every geometry listed in the .prj must appear exactly once — a
    missing, duplicated, or unknown ID raises ValueError before any file is
    touched, so a partial order cannot silently reshuffle the rest.

    Note this is the most far-reaching of the three reorder operations: a
    geometry is shared, so every renumber rewrites `Geom File=` in EVERY plan
    that uses it (plus the .rasmap's `<Geometries>` layer and each plan layer's
    `GeometryHDF=`). The renumbering itself is done by renumber_geoms; see there.

    Returns the {old_id: new_id} mapping of what moved (empty if `order` is
    already the current numbering).
    """
    ids = [_normalize_geom_id(g) for g in order]
    dupes = sorted({g for g in ids if ids.count(g) > 1}, key=_geom_num)
    if dupes:
        raise ValueError(f"Duplicate geometry IDs in order: {dupes}")

    listed = set(project.model.geom_file_ids)
    unknown = [g for g in ids if g not in listed]
    if unknown:
        raise ValueError(
            f"Geometr(ies) not listed in {project.base_name}.prj: {unknown}"
        )
    missing = sorted(listed - set(ids), key=_geom_num)
    if missing:
        raise ValueError(
            "order must list every geometry in the project — missing: "
            f"{missing}. (Add them in the position you want them to keep.)"
        )

    mapping = {}
    for i, gid in enumerate(ids, start=1):
        target = f"g{i:02d}"
        if gid != target:
            mapping[gid] = target
    if mapping:
        renumber_geoms(project, mapping)
    return mapping


def retitle_geom(
    project: RasProject,
    geom_id: str,
    new_title: str,
    *,
    clean_rasmap: bool = True,
    update_hdf: bool = True,
) -> dict:
    """Rename a geometry in place, without changing its number.

    A geometry has one title and no short identifier (the plan-side asymmetry —
    only plans carry a `Short Identifier=`), so there is nothing to name apart
    from `Geom Title=`.

    Updates the `.g##` text file's `Geom Title=`, the `<Geometries>` RASGeometry
    layer's display name in the `.rasmap` (`clean_rasmap=True`), and
    `Geometry/Title` inside the `.g##.hdf` (`update_hdf=True`). That last one is
    required rather than cosmetic: the `<Geometries>` layer's `Filename` points
    at the `.g##.hdf`, so RAS Mapper refreshes the layer name from the HDF and
    would revert a rasmap-only edit. See `hdf_titles.py`.

    Raises GeomFileNotFound, ValueError for an orphan (on disk but not listed in
    the .prj), DuplicateGeomTitle, or GeomRunActive. Returns a report:
    `geom_id`, `old_title`, `new_title`, `rasmap_renamed`, `hdf_attrs`.
    """
    gid = _normalize_geom_id(geom_id)
    path = geom_path(project, gid)
    if not os.path.isfile(path):
        raise GeomFileNotFound(f"Geometry file not found: {path}")
    if gid not in project.model.geom_file_ids:
        raise ValueError(
            f"Geometry '{gid}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to retitle it."
        )
    _assert_no_geom_run_active(project, gid)

    for other in project.model.geom_file_ids:
        if other == gid:
            continue
        p = geom_path(project, other)
        if os.path.isfile(p) and _read_geom_title(p) == new_title:
            raise DuplicateGeomTitle(
                f"Geometry title '{new_title}' is already used by '{other}' — "
                "HEC-RAS requires unique geometry titles."
            )

    old_title = _read_geom_title(path)
    lines = read_lines(path)
    eol = eol_of(lines)
    for i, line in enumerate(lines):
        if content_of(line).startswith("Geom Title="):
            lines[i] = f"Geom Title={new_title}{eol}"
            break
    write_lines(path, lines)

    renamed: list = []
    if clean_rasmap and os.path.isfile(project.rasmap_path):
        renamed = retitle_in_rasmap(
            project.rasmap_path, project.base_name, "geom", {gid: new_title}
        )

    attrs: list = []
    if update_hdf:
        from hack_ras.project.hdf_titles import retitle_geom_hdf
        attrs = retitle_geom_hdf(path + ".hdf", new_title)

    _invalidate_model(project)
    logger.info("Retitled %s: %r -> %r", gid, old_title, new_title)
    return {
        "geom_id": gid,
        "old_title": old_title,
        "new_title": new_title,
        "rasmap_renamed": renamed,
        "hdf_attrs": attrs,
    }


def clone_geom(
    project: RasProject,
    source_id: str,
    new_title: str,
    *,
    new_id: str | None = None,
) -> str:
    """Create a new geometry file as a copy of source_id with a new title.

    Copies only the .g## text file (RAS regenerates the .g##.hdf preprocessor
    output on the next run); the 'Geom Title=' line is replaced with new_title,
    and a `Geom File=` entry is inserted in the .prj keeping geometry entries in
    ascending order. new_id defaults to the next free geometry number.

    Returns the new geometry ID. Raises DuplicateGeomTitle if new_title matches
    any listed geometry's title (HEC-RAS requires unique titles), GeomIdInUse if
    new_id is taken.
    """
    src = _normalize_geom_id(source_id)
    src_path = geom_path(project, src)
    if not os.path.isfile(src_path):
        raise GeomFileNotFound(f"Geometry file not found: {src_path}")
    listed = project.model.geom_file_ids
    if src not in listed:
        raise ValueError(
            f"Geometry '{src}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to clone it."
        )

    if new_id is None:
        used = {_geom_num(g) for g in listed}
        used.update(
            _geom_num(g) for g in project.available_ids().get("geom", [])
        )
        n = max(used) + 1
        if n > 99:
            raise ValueError("No free geometry number left (g99 is in use).")
        new = f"g{n:02d}"
    else:
        new = _normalize_geom_id(new_id)
        if _geom_id_in_use(project, new):
            raise GeomIdInUse(f"Geometry ID '{new}' is already in use.")

    for gid in listed:
        p = geom_path(project, gid)
        if os.path.isfile(p) and _read_geom_title(p) == new_title:
            raise DuplicateGeomTitle(
                f"Geometry title '{new_title}' is already used by '{gid}' — "
                "HEC-RAS requires unique geometry titles."
            )

    lines = read_lines(src_path)
    eol = eol_of(lines)
    for i, line in enumerate(lines):
        if content_of(line).startswith("Geom Title="):
            lines[i] = f"Geom Title={new_title}{eol}"
            break
    write_lines(geom_path(project, new), lines)

    prj_lines = read_lines(project.prj_path)
    prj_eol = eol_of(prj_lines)
    entry = f"Geom File={new}{prj_eol}"
    geom_idx = [
        (i, content_of(l)[len("Geom File="):].strip())
        for i, l in enumerate(prj_lines)
        if content_of(l).startswith("Geom File=")
    ]
    insert_at = None
    for i, gid in geom_idx:
        if _geom_num(gid) > _geom_num(new):
            insert_at = i
            break
    if insert_at is None:
        insert_at = geom_idx[-1][0] + 1 if geom_idx else len(prj_lines)
    prj_lines.insert(insert_at, entry)
    write_lines(project.prj_path, prj_lines)
    _invalidate_model(project)
    return new


def delete_geom(
    project: RasProject,
    geom_id: str,
    *,
    force: bool = False,
    clean_rasmap: bool = True,
) -> dict:
    """Delete a geometry and everything keyed to its number: the .g## file, the
    .g##.hdf preprocessor sidecar, the .x## run file, and its `Geom File=` entry
    in the .prj.

    Refuses (GeomInUse) if any listed plan still references the geometry via
    `Geom File=`, unless force=True — in which case it deletes anyway and warns
    that those plans now point at a missing geometry (they will not open/run
    until repointed).

    With clean_rasmap (default True), the geometry's `<Geometries>` RASGeometry
    layer is removed from the .rasmap (via remove_geoms_from_rasmap).

    Returns {'deleted': [filenames], 'prj_removed': [entries],
    'referencing_plans': [...], 'warnings': [...],
    'rasmap_removed': [geom ids]}.
    """
    gid = _normalize_geom_id(geom_id)
    gpath = geom_path(project, gid)
    if not os.path.isfile(gpath):
        raise GeomFileNotFound(f"Geometry file not found: {gpath}")
    if gid not in project.model.geom_file_ids:
        raise ValueError(
            f"Geometry '{gid}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to delete it."
        )
    _assert_no_geom_run_active(project, gid)

    base = project.base_name
    folder = project.folder
    referencing = _plans_using_geom(project, gid)
    warnings = []
    if referencing and not force:
        raise GeomInUse(
            f"Geometry '{gid}' is still referenced by plan(s) "
            f"{referencing} — refusing to delete. Pass force=True to delete "
            "anyway (those plans will then point at a missing geometry)."
        )
    if referencing and force:
        msg = (f"geometry '{gid}' deleted while still referenced by plan(s) "
               f"{referencing} — those plans now point at a missing geometry.")
        warnings.append(msg)
        logger.warning(msg)

    deleted = []
    for name in (f"{base}.{gid}", f"{base}.{gid}.hdf", f"{base}.x{gid[1:]}"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            os.remove(path)
            deleted.append(name)

    prj_lines = read_lines(project.prj_path)
    kept = []
    prj_removed = []
    for line in prj_lines:
        if content_of(line) == f"Geom File={gid}":
            prj_removed.append(content_of(line))
            continue
        kept.append(line)
    write_lines(project.prj_path, kept)

    rasmap_removed = []
    if clean_rasmap:
        rasmap_path = os.path.join(folder, f"{base}.rasmap")
        if os.path.isfile(rasmap_path):
            rasmap_removed = remove_geoms_from_rasmap(rasmap_path, base, [gid])

    _invalidate_model(project)
    logger.info(
        "deleted geometry %s: %d file(s), %d prj entrie(s), "
        "%d rasmap layer(s)",
        gid, len(deleted), len(prj_removed), len(rasmap_removed),
    )
    return {"deleted": deleted, "prj_removed": prj_removed,
            "referencing_plans": referencing, "warnings": warnings,
            "rasmap_removed": rasmap_removed}


def delete_geoms(
    project: RasProject,
    spec,
    *,
    force: bool = False,
    clean_rasmap: bool = True,
) -> dict:
    """Delete several geometries given a flexible id spec, e.g. 'g02,g04-g05'
    or ['02', 'g04', '5'] (see resolve.expand_id_spec). A comma-separated
    string is accepted directly.

    All ids are validated up front (each must exist, be listed in the .prj, and
    have no plan of theirs mid-run) and, unless force=True, the whole call is
    refused with GeomInUse if ANY target is still referenced by a plan — so a
    bad spec fails before any geometry is deleted. Then each is removed via
    delete_geom.

    Returns a consolidated report:
        {'deleted_geoms': [gid, ...], 'deleted': [filenames],
         'prj_removed': [entries], 'referencing_plans': {gid: [pid, ...]},
         'warnings': [...], 'rasmap_removed': [gid, ...]}
    """
    if isinstance(spec, str):
        spec = spec.split(",")
    gids = expand_id_spec(spec, kind="g")

    listed = project.model.geom_file_ids
    referenced = {}
    for gid in gids:
        if not os.path.isfile(geom_path(project, gid)):
            raise GeomFileNotFound(f"Geometry file not found: {geom_path(project, gid)}")
        if gid not in listed:
            raise ValueError(
                f"Geometry '{gid}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to delete it."
            )
        _assert_no_geom_run_active(project, gid)
        refs = _plans_using_geom(project, gid)
        if refs:
            referenced[gid] = refs
    if referenced and not force:
        raise GeomInUse(
            f"Geometr(ies) {sorted(referenced)} still referenced by plan(s) "
            f"{referenced} — refusing to delete. Pass force=True to delete "
            "anyway (those plans will then point at a missing geometry)."
        )

    report = {
        "deleted_geoms": [], "deleted": [], "prj_removed": [],
        "referencing_plans": {}, "warnings": [], "rasmap_removed": [],
    }
    for gid in gids:
        r = delete_geom(project, gid, force=force, clean_rasmap=clean_rasmap)
        report["deleted_geoms"].append(gid)
        report["deleted"].extend(r["deleted"])
        report["prj_removed"].extend(r["prj_removed"])
        if r["referencing_plans"]:
            report["referencing_plans"][gid] = r["referencing_plans"]
        report["warnings"].extend(r["warnings"])
        report["rasmap_removed"].extend(r["rasmap_removed"])
    return report
