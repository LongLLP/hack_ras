# hack_ras/project/parser.py
from __future__ import annotations
from typing import Iterable
from .model import ProjectModel

_KEYMAP = {
    "proj title":      "title",
    "geom file":       "geom_file_ids",
    "plan file":       "plan_file_ids",
    "unsteady file":   "unsteady_file_ids",
    "y axis title":    "y_axis_title",
    "x axis title(pf)": "x_axis_title_pf",
    "x axis title(xs)": "x_axis_title_xs",
    "dss file":        "dss_file",
}

_LIST_ATTRS = {"geom_file_ids", "plan_file_ids", "unsteady_file_ids"}


def _norm(s: str) -> str:
    return " ".join(s.strip().split()).lower()


def parse_project_lines(lines: Iterable[str]) -> ProjectModel:
    """
    Parse lines of a HEC-RAS .prj file into ProjectModel.
    Repeated keys (Geom File, Plan File, Unsteady File) are accumulated into lists.
    Unknown keys and empty/comment lines are ignored.
    """
    proj = ProjectModel()
    in_description = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if line.startswith("BEGIN DESCRIPTION:"):
            in_description = True
            continue
        if line.startswith("END DESCRIPTION:"):
            in_description = False
            continue
        if in_description:
            continue

        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = _norm(key)
        v = value.strip()

        attr = _KEYMAP.get(k)
        if attr is None:
            continue

        if attr in _LIST_ATTRS:
            getattr(proj, attr).append(v)
        else:
            setattr(proj, attr, v)

    return proj


def parse_project_file(path: str) -> ProjectModel:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return parse_project_lines(f.readlines())
