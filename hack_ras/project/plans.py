# hack_ras/project/plans.py
"""Plan file operations: renumber, insert a numbering gap, clone with edits.

All functions take a RasProject and edit files in place. Plan files and the
.prj are treated as raw lines (lossless: untouched lines are preserved
byte-for-byte, including CRLF endings). The .prj is authoritative — a plan
file on disk that is not listed in the .prj is an orphan and is rejected.
"""
from __future__ import annotations

import os

from hack_ras.project.ras_project import RasProject


class PlanFileNotFound(FileNotFoundError):
    """A referenced plan file (.p##) was not found on disk."""


class PlanIdInUse(FileExistsError):
    """The target plan ID is already taken (listed in the .prj or on disk)."""


class DuplicatePlanTitle(ValueError):
    """The new plan title collides with an existing plan's title.

    HEC-RAS requires every plan title within a project to be unique.
    """


# ---------------------------
# Raw-line I/O (lossless)
# ---------------------------

def _read_lines(path: str) -> list[str]:
    """Read a RAS text file as raw lines with endings attached ('...\\r\\n')."""
    with open(path, "r", encoding="latin-1", newline="") as f:
        text = f.read()
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _write_lines(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write("".join(lines))


def _eol(lines: list[str]) -> str:
    """Line ending used by the file ('\\r\\n' or '\\n')."""
    for line in lines:
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
    return "\r\n"


def _content(line: str) -> str:
    return line.rstrip("\r\n")


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
    for line in _read_lines(path):
        if line.startswith("Plan Title="):
            return _content(line)[len("Plan Title="):].strip()
    return ""


# ---------------------------
# Public operations
# ---------------------------

def renumber_plan(project: RasProject, old_id: str, new_id: str) -> None:
    """Rename plan old_id to new_id: the .p## file, its .p##.hdf sidecar if
    present, the .prj 'Plan File=' entry, and 'Current Plan=' if it matches.

    Raises PlanFileNotFound if old_id has no file, ValueError if old_id is not
    listed in the .prj (orphan), PlanIdInUse if new_id is already taken.
    """
    old = _normalize_plan_id(old_id)
    new = _normalize_plan_id(new_id)
    if old == new:
        raise ValueError(f"Old and new plan IDs are the same: {old}")

    old_path = plan_path(project, old)
    if not os.path.isfile(old_path):
        raise PlanFileNotFound(f"Plan file not found: {old_path}")
    if old not in project.model.plan_file_ids:
        raise ValueError(
            f"Plan '{old}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to renumber it."
        )
    if _id_in_use(project, new):
        raise PlanIdInUse(f"Plan ID '{new}' is already in use.")

    new_path = plan_path(project, new)
    os.rename(old_path, new_path)
    if os.path.isfile(old_path + ".hdf"):
        os.rename(old_path + ".hdf", new_path + ".hdf")

    lines = _read_lines(project.prj_path)
    eol = _eol(lines)
    for i, line in enumerate(lines):
        c = _content(line)
        if c == f"Plan File={old}":
            lines[i] = f"Plan File={new}{eol}"
        elif c == f"Current Plan={old}":
            lines[i] = f"Current Plan={new}{eol}"
    _write_lines(project.prj_path, lines)
    _invalidate_model(project)


def insert_plan_gap(project: RasProject, at_id: str, count: int) -> dict[str, str]:
    """Shift every listed plan numbered >= at_id up by count, freeing the IDs
    at_id .. at_id+count-1 for new plans. Renames run highest-first so no
    intermediate collision can occur.

    Returns {old_id: new_id} for the plans that moved. All collisions and
    range overflows are validated before any file is touched.
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

    shifted = set(to_shift)
    for pid, target in mapping.items():
        if target in shifted:
            continue  # occupied now, but freed before pid is renamed
        if _id_in_use(project, target):
            raise PlanIdInUse(
                f"Cannot shift '{pid}' to '{target}': ID already in use."
            )

    for pid in sorted(to_shift, key=_plan_num, reverse=True):
        renumber_plan(project, pid, mapping[pid])
    return mapping


def clone_plan(
    project: RasProject,
    source_id: str,
    new_title: str,
    *,
    short_id: str | None = None,
    line_edits: dict[str, str] | None = None,
    new_id: str | None = None,
) -> str:
    """Create a new plan file as a copy of source_id with a new title.

    new_title / short_id (defaults to new_title) replace the 'Plan Title=' and
    'Short Identifier=' lines; the short identifier keeps the source's field
    padding. line_edits maps a line prefix (e.g. 'Breach Start=') to the full
    replacement line content (no line ending); each prefix must match exactly
    one line. new_id defaults to the next free plan number; the 'Plan File='
    entry is inserted in the .prj keeping plan entries in ascending order.

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

    lines = _read_lines(src_path)
    eol = _eol(lines)
    short = short_id if short_id is not None else new_title
    for i, line in enumerate(lines):
        c = _content(line)
        if c.startswith("Plan Title="):
            lines[i] = f"Plan Title={new_title}{eol}"
        elif c.startswith("Short Identifier="):
            width = len(c) - len("Short Identifier=")
            lines[i] = f"Short Identifier={short.ljust(width)}{eol}"

    for prefix, replacement in (line_edits or {}).items():
        matches = [i for i, l in enumerate(lines) if _content(l).startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(
                f"line_edits prefix {prefix!r} matched {len(matches)} lines in "
                f"{os.path.basename(src_path)}; expected exactly 1."
            )
        lines[matches[0]] = f"{replacement}{eol}"

    _write_lines(plan_path(project, new), lines)

    prj_lines = _read_lines(project.prj_path)
    prj_eol = _eol(prj_lines)
    entry = f"Plan File={new}{prj_eol}"
    plan_idx = [
        (i, _content(l)[len("Plan File="):].strip())
        for i, l in enumerate(prj_lines)
        if _content(l).startswith("Plan File=")
    ]
    insert_at = None
    for i, pid in plan_idx:
        if _plan_num(pid) > _plan_num(new):
            insert_at = i
            break
    if insert_at is None:
        insert_at = plan_idx[-1][0] + 1 if plan_idx else len(prj_lines)
    prj_lines.insert(insert_at, entry)
    _write_lines(project.prj_path, prj_lines)
    _invalidate_model(project)
    return new
