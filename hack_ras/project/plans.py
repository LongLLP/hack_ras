# hack_ras/project/plans.py
"""Plan file operations: renumber (single or bulk), insert a numbering gap,
clone with edits, delete with outputs.

All functions take a RasProject and edit files in place. Plan files and the
.prj are treated as raw lines (lossless: untouched lines are preserved
byte-for-byte, including CRLF endings). The .prj is authoritative — a plan
file on disk that is not listed in the .prj is an orphan and is rejected.

Renumbering knows about everything keyed to a plan's number:
- the .p## file and its .p##.hdf results sidecar,
- run artifacts written by HEC-RAS: .b##, .bco##, .ic.o##,
- restart files the plan wrote: `Base.p##.<stamp>.rst` (arbitrary restart
  names like `banana.rst` carry no plan number and are never touched),
- `Restart Filename=` references inside the project's .u files,
- `Base.p##` filename tokens inside the .rasmap (see project/rasmap.py).
A `.p##.tmp.hdf` next to the plan means HEC-RAS is mid-run on it —
operations refuse with PlanRunActive rather than pull files out from under
a running simulation. The .x## run files are keyed to GEOMETRY files, not
plans, and are left alone (empirically confirmed, GMF_DFA 2026-07-17).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from hack_ras.project.ras_project import RasProject
from hack_ras.project.rasmap import renumber_plans_in_rasmap
from hack_ras.utils.lines import content_of, eol_of, read_lines, write_lines

logger = logging.getLogger(__name__)


class PlanFileNotFound(FileNotFoundError):
    """A referenced plan file (.p##) was not found on disk."""


class PlanIdInUse(FileExistsError):
    """The target plan ID is already taken (listed in the .prj or on disk)."""


class DuplicatePlanTitle(ValueError):
    """The new plan title collides with an existing plan's title.

    HEC-RAS requires every plan title within a project to be unique.
    """


class PlanRunActive(RuntimeError):
    """A .p##.tmp.hdf exists — HEC-RAS is currently running this plan."""


# ---------------------------
# ID helpers
# ---------------------------

def _normalize_plan_id(raw: str) -> str:
    """'25' / 'P25' / 'p25' -> 'p25'. Raises ValueError for anything else."""
    s = str(raw).strip().lower().lstrip("p")
    if not s.isdigit():
        raise ValueError(f"Invalid plan ID: {raw!r}")
    n = int(s)
    if not 1 <= n <= 99:
        raise ValueError(f"Plan number out of range 1-99: {raw!r}")
    return f"p{n:02d}"


def _plan_num(pid: str) -> int:
    return int(pid[1:])


def plan_path(project: RasProject, plan_id: str) -> str:
    """Absolute path of a plan file next to the .prj (existence not checked)."""
    pid = _normalize_plan_id(plan_id)
    return os.path.join(project.folder, f"{project.base_name}.{pid}")


def _invalidate_model(project: RasProject) -> None:
    """Drop the cached ProjectModel so the next access re-parses the .prj."""
    project.__dict__.pop("model", None)


def _id_in_use(project: RasProject, pid: str) -> bool:
    """True if pid is listed in the .prj or has a .p##/.p##.hdf file on disk."""
    if pid in project.model.plan_file_ids:
        return True
    path = plan_path(project, pid)
    return os.path.exists(path) or os.path.exists(path + ".hdf")


def _read_plan_title(path: str) -> str:
    for line in read_lines(path):
        if line.startswith("Plan Title="):
            return content_of(line)[len("Plan Title="):].strip()
    return ""


def _read_plan_ref(path: str, key: str) -> str | None:
    """First '<key>=<id>' value in a plan file (e.g. key='Geom File')."""
    for line in read_lines(path):
        if line.startswith(f"{key}="):
            return content_of(line)[len(key) + 1:].strip()
    return None


# ---------------------------
# Plan-keyed file families
# ---------------------------

def _assert_no_active_run(project: RasProject, pid: str) -> None:
    tmp = f"{project.base_name}.{pid}.tmp.hdf"
    if os.path.exists(os.path.join(project.folder, tmp)):
        raise PlanRunActive(
            f"{tmp} exists — HEC-RAS appears to be running plan '{pid}'. "
            "Finish or stop the run first."
        )


def _family_names(project: RasProject, pid: str) -> list[str]:
    """Existing filenames (relative to the project folder) keyed to pid:
    the plan file, .hdf sidecar, run artifacts (b/bco/ic.o), and any
    `Base.p##.<...>.rst` restart files the plan wrote."""
    base = project.base_name
    num = pid[1:]
    fixed = [
        f"{base}.{pid}",
        f"{base}.{pid}.hdf",
        f"{base}.b{num}",
        f"{base}.bco{num}",
        f"{base}.ic.o{num}",
    ]
    names = [n for n in fixed
             if os.path.isfile(os.path.join(project.folder, n))]
    rst_prefix = f"{base}.{pid}."
    for name in sorted(os.listdir(project.folder)):
        if name.startswith(rst_prefix) and name.endswith(".rst"):
            names.append(name)
    return names


def _renamed_family_name(name: str, base: str, old: str, new: str) -> str:
    """Counterpart of one family filename under the new plan ID."""
    o, n = old[1:], new[1:]
    if name.startswith(f"{base}.{old}"):        # .p##, .p##.hdf, .p##.*.rst
        return f"{base}.{new}" + name[len(f"{base}.{old}"):]
    for stem in ("b", "bco", "ic.o"):
        if name == f"{base}.{stem}{o}":
            return f"{base}.{stem}{n}"
    raise ValueError(f"Not a plan-family filename for {old}: {name!r}")


# ---------------------------
# Cross-file reference updates
# ---------------------------

def _rewrite_restart_refs(project: RasProject, idmap: dict) -> list[str]:
    """Rewrite `Restart Filename=Base.p##.<stamp>.rst` references in the
    project's .u files, one pass with the complete mapping (chain-safe).
    Restart references that do not embed a plan number are left alone.
    Returns human-readable 'u##: old -> new' strings for what changed."""
    base = project.base_name
    report = []
    for uid in project.model.unsteady_file_ids:
        upath = os.path.join(project.folder, f"{base}.{uid}")
        if not os.path.isfile(upath):
            continue
        lines = read_lines(upath)
        changed = False
        for i, line in enumerate(lines):
            c = content_of(line)
            if not c.startswith("Restart Filename="):
                continue
            value = c[len("Restart Filename="):]
            for old, new in idmap.items():
                prefix = f"{base}.{old}."
                if value.startswith(prefix):
                    new_value = f"{base}.{new}." + value[len(prefix):]
                    lines[i] = f"Restart Filename={new_value}" + line[len(c):]
                    report.append(f"{uid}: {value} -> {new_value}")
                    changed = True
                    break
        if changed:
            write_lines(upath, lines)
    for entry in report:
        logger.info("restart reference updated: %s", entry)
    return report


def _restart_refs_to_plan(project: RasProject, pid: str) -> list[str]:
    """u-file IDs whose `Restart Filename=` references plan pid's restarts."""
    base = project.base_name
    prefix = f"{base}.{pid}."
    hits = []
    for uid in project.model.unsteady_file_ids:
        upath = os.path.join(project.folder, f"{base}.{uid}")
        if not os.path.isfile(upath):
            continue
        for line in read_lines(upath):
            c = content_of(line)
            if c.startswith("Restart Filename=") and \
                    c[len("Restart Filename="):].startswith(prefix):
                hits.append(uid)
                break
    return hits


# ---------------------------
# Public operations
# ---------------------------

def renumber_plans(project: RasProject, mapping: dict) -> dict:
    """Renumber several plans at once: {'p02': 'p06', 'p20': 'p02', ...}.

    Everything is validated before any file is touched: every source must
    exist and be listed in the .prj, no two sources may share a target, and a
    target may only be occupied if its occupant is itself being moved by this
    mapping. Chains and cycles are handled automatically — files are renamed
    in a collision-free order, with a temporary '<name>.renumtmp' hop when a
    cycle leaves no free slot.

    Renames the whole plan-keyed family (.p##, .p##.hdf, .b##, .bco##,
    .ic.o##, Base.p##.*.rst), then applies the complete mapping in ONE pass
    to the .prj (Plan File= and Current Plan=), to `Restart Filename=`
    references in the .u files, and to Base.p## tokens in the .rasmap if one
    exists. One pass matters: applying entries sequentially would corrupt
    chained mappings (p02->p06 while p06->p12).

    Returns a report dict: {'files': [(old_name, new_name), ...],
    'restart_refs': [...], 'rasmap_tokens': int}.
    """
    idmap: dict = {}
    for old_raw, new_raw in mapping.items():
        old, new = _normalize_plan_id(old_raw), _normalize_plan_id(new_raw)
        if old == new:
            raise ValueError(f"Old and new plan IDs are the same: {old}")
        if old in idmap:
            raise ValueError(f"Duplicate source plan ID: {old}")
        idmap[old] = new
    if len(set(idmap.values())) != len(idmap):
        raise ValueError(f"Duplicate target plan IDs in mapping: {idmap}")

    listed = project.model.plan_file_ids
    for old in idmap:
        old_path = plan_path(project, old)
        if not os.path.isfile(old_path):
            raise PlanFileNotFound(f"Plan file not found: {old_path}")
        if old not in listed:
            raise ValueError(
                f"Plan '{old}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to renumber it."
            )
        _assert_no_active_run(project, old)
    for new in idmap.values():
        if new in idmap:
            continue  # occupied now, but its occupant is being moved too
        if _id_in_use(project, new):
            raise PlanIdInUse(
                f"Plan ID '{new}' is already in use. (If it is a stale .prj "
                "entry with no file on disk, run sync_prj first.)"
            )

    # Build the full file-level rename plan, then pre-scan for collisions
    # with files that are not themselves being moved.
    base = project.base_name
    folder = project.folder
    pairs = []
    for old, new in idmap.items():
        for name in _family_names(project, old):
            pairs.append((name, _renamed_family_name(name, base, old, new)))
    sources = {src for src, _ in pairs}
    for src, dst in pairs:
        if dst not in sources and os.path.exists(os.path.join(folder, dst)):
            raise PlanIdInUse(
                f"Cannot rename {src} -> {dst}: target file already exists "
                "and is not part of this renumbering."
            )

    # Execute: rename whatever has a free target; break cycles via a
    # temporary name (e.g. 'Base.p02' -> 'Base.p02.renumtmp' -> 'Base.p06').
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

    # .prj — one pass with the complete mapping.
    prj_lines = read_lines(project.prj_path)
    eol = eol_of(prj_lines)
    for i, line in enumerate(prj_lines):
        c = content_of(line)
        if c.startswith("Plan File="):
            pid = c[len("Plan File="):].strip()
            if pid in idmap:
                prj_lines[i] = f"Plan File={idmap[pid]}{eol}"
        elif c.startswith("Current Plan="):
            pid = c[len("Current Plan="):].strip()
            if pid in idmap:
                prj_lines[i] = f"Current Plan={idmap[pid]}{eol}"
    write_lines(project.prj_path, prj_lines)

    restart_refs = _rewrite_restart_refs(project, idmap)

    rasmap_path = os.path.join(folder, f"{base}.rasmap")
    rasmap_tokens = 0
    if os.path.isfile(rasmap_path):
        rasmap_tokens = renumber_plans_in_rasmap(rasmap_path, base, idmap)

    _invalidate_model(project)
    logger.info(
        "renumbered %d plan(s): %d file(s), %d restart ref(s), "
        "%d rasmap token(s)",
        len(idmap), len(pairs), len(restart_refs), rasmap_tokens,
    )
    return {"files": pairs, "restart_refs": restart_refs,
            "rasmap_tokens": rasmap_tokens}


def renumber_plan(project: RasProject, old_id: str, new_id: str) -> None:
    """Rename plan old_id to new_id — the single-entry case of
    renumber_plans(); see there for everything that gets renamed/updated.

    Raises PlanFileNotFound if old_id has no file, ValueError if old_id is not
    listed in the .prj (orphan), PlanIdInUse if new_id is already taken,
    PlanRunActive if the plan is mid-run.
    """
    renumber_plans(project, {old_id: new_id})


def insert_plan_gap(project: RasProject, at_id: str, count: int) -> dict:
    """Shift every listed plan numbered >= at_id up by count, freeing the IDs
    at_id .. at_id+count-1 for new plans.

    Returns {old_id: new_id} for the plans that moved. All collisions and
    range overflows are validated (by renumber_plans) before any file is
    touched.
    """
    at = _plan_num(_normalize_plan_id(at_id))
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    to_shift = [p for p in project.model.plan_file_ids if _plan_num(p) >= at]
    mapping = {}
    for pid in to_shift:
        n = _plan_num(pid) + count
        if n > 99:
            raise ValueError(
                f"Shifting '{pid}' by {count} exceeds p99 — cannot insert gap."
            )
        mapping[pid] = f"p{n:02d}"

    if mapping:
        renumber_plans(project, mapping)
    return mapping


def delete_plan(
    project: RasProject,
    plan_id: str,
    *,
    delete_unused_geom: bool = False,
    delete_unused_flow: bool = False,
) -> dict:
    """Delete a plan and everything keyed to its number: the .p## file, the
    .p##.hdf results, run artifacts (.b##, .bco##, .ic.o##), restart files it
    wrote (Base.p##.*.rst), and its 'Plan File=' entry in the .prj
    (repointing 'Current Plan=' to the first surviving plan if needed).

    With delete_unused_geom / delete_unused_flow, the plan's geometry / flow
    file (plus .hdf sidecar, and the geometry's .x## run file) is also
    deleted IF no other listed plan references it, along with its .prj entry.

    The .rasmap is deliberately left alone: RAS Mapper flags layers whose
    files are missing and purges them via Tools > "remove missing layers".

    Logs a warning (and reports it) when a surviving .u file's
    'Restart Filename=' references the deleted plan's restart output — those
    flow files lose their initial-conditions source.

    Returns {'deleted': [filenames], 'prj_removed': [entries],
    'warnings': [...], 'current_plan': (old, new) or None}.
    """
    pid = _normalize_plan_id(plan_id)
    ppath = plan_path(project, pid)
    if not os.path.isfile(ppath):
        raise PlanFileNotFound(f"Plan file not found: {ppath}")
    if pid not in project.model.plan_file_ids:
        raise ValueError(
            f"Plan '{pid}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to delete it."
        )
    _assert_no_active_run(project, pid)

    base = project.base_name
    folder = project.folder
    geom_id = _read_plan_ref(ppath, "Geom File")
    flow_id = _read_plan_ref(ppath, "Flow File")

    warnings = []
    for uid in _restart_refs_to_plan(project, pid):
        msg = (f"{base}.{uid} references a restart file written by deleted "
               f"plan '{pid}' — that flow file loses its initial conditions.")
        warnings.append(msg)
        logger.warning(msg)

    deleted = []
    for name in _family_names(project, pid):
        os.remove(os.path.join(folder, name))
        deleted.append(name)

    # .prj: drop the plan entry, fix Current Plan.
    prj_lines = read_lines(project.prj_path)
    eol = eol_of(prj_lines)
    prj_removed = []
    kept = []
    surviving = []
    for line in prj_lines:
        c = content_of(line)
        if c == f"Plan File={pid}":
            prj_removed.append(c)
            continue
        if c.startswith("Plan File="):
            surviving.append(c[len("Plan File="):].strip())
        kept.append(line)
    current_change = None
    for i, line in enumerate(kept):
        c = content_of(line)
        if c.startswith("Current Plan="):
            if c[len("Current Plan="):].strip() == pid and surviving:
                kept[i] = f"Current Plan={surviving[0]}{eol}"
                current_change = (pid, surviving[0])
            break

    # Optionally drop the plan's geometry / flow file when nothing else
    # listed still references it.
    def _still_referenced(key: str, fid: str) -> bool:
        for other in surviving:
            opath = plan_path(project, other)
            if os.path.isfile(opath) and _read_plan_ref(opath, key) == fid:
                return True
        return False

    def _drop_entry(prefix: str, fid: str) -> None:
        for i, line in enumerate(kept):
            if content_of(line) == f"{prefix}{fid}":
                prj_removed.append(content_of(kept.pop(i)))
                return

    if delete_unused_geom and geom_id and \
            not _still_referenced("Geom File", geom_id):
        for name in (f"{base}.{geom_id}", f"{base}.{geom_id}.hdf",
                     f"{base}.x{geom_id[1:]}"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(name)
        _drop_entry("Geom File=", geom_id)

    if delete_unused_flow and flow_id and \
            not _still_referenced("Flow File", flow_id):
        for name in (f"{base}.{flow_id}", f"{base}.{flow_id}.hdf"):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(name)
        _drop_entry("Unsteady File=", flow_id)

    write_lines(project.prj_path, kept)
    _invalidate_model(project)
    logger.info("deleted plan %s: %d file(s), %d prj entrie(s)",
                pid, len(deleted), len(prj_removed))
    return {"deleted": deleted, "prj_removed": prj_removed,
            "warnings": warnings, "current_plan": current_change}


def clone_plan(
    project: RasProject,
    source_id: str,
    new_title: str,
    *,
    short_id: str | None = None,
    line_edits: dict | None = None,
    new_id: str | None = None,
) -> str:
    """Create a new plan file as a copy of source_id with a new title.

    new_title / short_id (defaults to new_title) replace the 'Plan Title=' and
    'Short Identifier=' lines; the short identifier keeps the source's field
    padding. line_edits maps a line prefix (e.g. 'Breach Start=') to the full
    replacement line content (no line ending); each prefix must match exactly
    one line. new_id defaults to the next free plan number; the 'Plan File='
    entry is inserted in the .prj keeping plan entries in ascending order.

    After writing, the clone's breach triggers are sanity-checked: a
    Set Time trigger dated outside the plan's Simulation Date window logs a
    warning (never an error — placeholder triggers are legitimate).

    Returns the new plan ID. Raises DuplicatePlanTitle if new_title matches
    any listed plan's title (HEC-RAS requires unique titles), PlanIdInUse if
    new_id is taken, ValueError for a line_edits prefix that does not match
    exactly one line.
    """
    src = _normalize_plan_id(source_id)
    src_path = plan_path(project, src)
    if not os.path.isfile(src_path):
        raise PlanFileNotFound(f"Plan file not found: {src_path}")
    listed = project.model.plan_file_ids
    if src not in listed:
        raise ValueError(
            f"Plan '{src}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to clone it."
        )

    if new_id is None:
        used = {_plan_num(p) for p in listed}
        used.update(
            _plan_num(p) for p in project.available_ids().get("plan", [])
        )
        n = max(used) + 1
        if n > 99:
            raise ValueError("No free plan number left (p99 is in use).")
        new = f"p{n:02d}"
    else:
        new = _normalize_plan_id(new_id)
        if _id_in_use(project, new):
            raise PlanIdInUse(f"Plan ID '{new}' is already in use.")

    for pid in listed:
        p = plan_path(project, pid)
        if os.path.isfile(p) and _read_plan_title(p) == new_title:
            raise DuplicatePlanTitle(
                f"Plan title '{new_title}' is already used by '{pid}' — "
                "HEC-RAS requires unique plan titles."
            )

    lines = read_lines(src_path)
    eol = eol_of(lines)
    short = short_id if short_id is not None else new_title
    for i, line in enumerate(lines):
        c = content_of(line)
        if c.startswith("Plan Title="):
            lines[i] = f"Plan Title={new_title}{eol}"
        elif c.startswith("Short Identifier="):
            width = len(c) - len("Short Identifier=")
            lines[i] = f"Short Identifier={short.ljust(width)}{eol}"

    for prefix, replacement in (line_edits or {}).items():
        matches = [i for i, l in enumerate(lines)
                   if content_of(l).startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(
                f"line_edits prefix {prefix!r} matched {len(matches)} lines in "
                f"{os.path.basename(src_path)}; expected exactly 1."
            )
        lines[matches[0]] = f"{replacement}{eol}"

    write_lines(plan_path(project, new), lines)

    prj_lines = read_lines(project.prj_path)
    prj_eol = eol_of(prj_lines)
    entry = f"Plan File={new}{prj_eol}"
    plan_idx = [
        (i, content_of(l)[len("Plan File="):].strip())
        for i, l in enumerate(prj_lines)
        if content_of(l).startswith("Plan File=")
    ]
    insert_at = None
    for i, pid in plan_idx:
        if _plan_num(pid) > _plan_num(new):
            insert_at = i
            break
    if insert_at is None:
        insert_at = plan_idx[-1][0] + 1 if plan_idx else len(prj_lines)
    prj_lines.insert(insert_at, entry)
    write_lines(project.prj_path, prj_lines)
    _invalidate_model(project)

    _warn_breach_triggers(lines, f"{project.base_name}.{new}")
    return new


# ---------------------------
# Breach trigger sanity check (advisory only)
# ---------------------------

def _parse_ras_datetime(date_str: str, time_str: str):
    """'01JAN2026', '0300' -> datetime; RAS's 2400 wraps to next-day 0000.
    Returns None for blank fields."""
    date_str, time_str = date_str.strip(), time_str.strip()
    if not date_str or not time_str:
        return None
    extra_day = 0
    if time_str == "2400":
        time_str, extra_day = "0000", 1
    dt = datetime.strptime(f"{date_str} {time_str.zfill(4)}", "%d%b%Y %H%M")
    return dt + timedelta(days=extra_day)


def _warn_breach_triggers(lines: list, plan_label: str) -> list:
    """Advisory check on a plan's lines: for every 'Breach Start=' whose
    ACTIVE trigger mode is Set Time, warn (via logging) when the date/time
    falls outside the plan's 'Simulation Date=' window.

    Field layout (empirically decoded, GMF_DFA 2026-07-17):
    Breach Start=F1,F2,F3,F4,F5,F6,F7,F8 — F1 True = "WS Elev" mode,
    F5 True = "WS Elev + Duration" mode, both False = "Set Time" using F3/F4.
    Inactive fields are stored but unused. WS-based modes have nothing
    statically checkable, so only Set Time is examined.

    Never raises — a malformed line simply isn't checked. Returns the
    warning messages (for tests)."""
    warnings = []
    try:
        window = None
        for line in lines:
            c = content_of(line)
            if c.startswith("Simulation Date="):
                f = c[len("Simulation Date="):].split(",")
                start = _parse_ras_datetime(f[0], f[1])
                end = _parse_ras_datetime(f[2], f[3])
                if start and end:
                    window = (start, end)
                break
        if window is None:
            return warnings
        for line in lines:
            c = content_of(line)
            if not c.startswith("Breach Start="):
                continue
            try:
                f = c[len("Breach Start="):].split(",")
                if len(f) < 5:
                    continue
                ws_mode = f[0].strip().lower() == "true"
                duration_mode = f[4].strip().lower() == "true"
                if ws_mode or duration_mode:
                    continue
                trigger = _parse_ras_datetime(f[2], f[3])
                if trigger is None:
                    continue
                if not window[0] <= trigger <= window[1]:
                    msg = (
                        f"{plan_label}: Set Time breach trigger "
                        f"{f[2].strip()},{f[3].strip()} is outside the "
                        f"simulation window "
                        f"({window[0]:%d%b%Y %H%M} to {window[1]:%d%b%Y %H%M})"
                        " — the breach will never fire as configured."
                    )
                    warnings.append(msg)
                    logger.warning(msg)
            except (ValueError, IndexError):
                continue
    except (ValueError, IndexError):
        pass
    return warnings
