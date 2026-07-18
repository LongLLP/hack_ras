# hack_ras/project/sync.py
"""Reconcile a .prj against the files actually on disk — removal only.

`sync_prj` removes file entries (Plan File=, Geom File=, Unsteady File=,
Steady File=) whose referenced file does not exist next to the .prj, and
repoints `Current Plan=` if its target was removed or is missing.

Deliberate non-goal: adopting orphans. Files on disk that the .prj does not
list are NOT added — the .prj is authoritative for what belongs to a project,
and the functions that create files (`clone_plan`, the XS shifter, ...) already
register their own entries.
"""
from __future__ import annotations

import os

from hack_ras.project.ras_project import RasProject
from hack_ras.utils.lines import read_lines, write_lines, eol_of, content_of

# prj key -> file-ID prefix letter ("Plan File=p01" -> "Mini.p01")
_FILE_KEYS = {
    "Plan File=": "plan",
    "Geom File=": "geom",
    "Unsteady File=": "unsteady",
    "Steady File=": "steady",
}


def sync_prj(project: RasProject) -> dict:
    """Remove .prj entries for files missing on disk; fix Current Plan.

    Returns a report dict:
        {'plan': [...removed ids...], 'geom': [...], 'unsteady': [...],
         'steady': [...], 'current_plan': (old, new) or None}

    `Current Plan=` is repointed to the first surviving listed plan when its
    target was removed or has no file; it is left untouched when no plan
    survives. Untouched lines are preserved byte-for-byte.
    """
    lines = read_lines(project.prj_path)
    eol = eol_of(lines)

    removed: dict = {kind: [] for kind in _FILE_KEYS.values()}
    kept_lines = []
    surviving_plans: list[str] = []
    for line in lines:
        c = content_of(line)
        for key, kind in _FILE_KEYS.items():
            if c.startswith(key):
                file_id = c[len(key):].strip()
                path = os.path.join(
                    project.folder, f"{project.base_name}.{file_id}"
                )
                if not os.path.isfile(path):
                    removed[kind].append(file_id)
                    break
                if kind == "plan":
                    surviving_plans.append(file_id)
                kept_lines.append(line)
                break
        else:
            kept_lines.append(line)

    current_change = None
    for i, line in enumerate(kept_lines):
        c = content_of(line)
        if c.startswith("Current Plan="):
            current = c[len("Current Plan="):].strip()
            if current not in surviving_plans and surviving_plans:
                new_current = surviving_plans[0]
                kept_lines[i] = f"Current Plan={new_current}{eol}"
                current_change = (current, new_current)
            break

    if any(removed.values()) or current_change:
        write_lines(project.prj_path, kept_lines)
        project.__dict__.pop("model", None)

    removed["current_plan"] = current_change
    return removed


def sort_prj_entries(
    project: RasProject,
    kinds: tuple = ("plan", "geom", "unsteady", "steady"),
) -> dict:
    """Re-sort the .prj's file entries into ascending numeric order, per kind.

    HEC-RAS tolerates a non-sequential entry order, but its file-open dialogs
    present entries in .prj order — after renumbering into freed slots (or a
    late clone) the plan list can read p01, p06, ..., p05, p14, and the same
    applies to geometry and unsteady lists. Optional tidy-up: each kind's
    entry lines are redistributed ascending across the positions they already
    occupy; every other line is byte-identical.

    kinds selects which groups to sort ('plan', 'geom', 'unsteady', 'steady').
    Returns {kind: [file IDs in final order]} for the kinds requested.
    """
    keys = {"plan": "Plan File=", "geom": "Geom File=",
            "unsteady": "Unsteady File=", "steady": "Steady File="}
    for kind in kinds:
        if kind not in keys:
            raise ValueError(
                f"Unknown kind {kind!r}; expected one of {sorted(keys)}"
            )

    lines = read_lines(project.prj_path)
    result = {}
    changed = False
    for kind in kinds:
        key = keys[kind]
        idx = [i for i, line in enumerate(lines)
               if content_of(line).startswith(key)]
        entries = sorted(
            (lines[i] for i in idx),
            key=lambda l: int(content_of(l)[len(key):].strip()[1:]),
        )
        for i, entry in zip(idx, entries):
            if lines[i] != entry:
                changed = True
                lines[i] = entry
        result[kind] = [content_of(e)[len(key):].strip() for e in entries]

    if changed:
        write_lines(project.prj_path, lines)
        project.__dict__.pop("model", None)
    return result
