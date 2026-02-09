# hack_ras/project/parser.py
from __future__ import annotations
from typing import Iterable
from .model import ProjectModel

# Keys we care about → attribute names on ProjectModel
_KEYMAP = {
    "proj title": "title",
    "geom file": "geom_file_id",
    "plan file": "plan_file_id",
    "unsteady file": "unsteady_file_id",
    "y axis title": "y_axis_title",
    "x axis title(pf)": "x_axis_title_pf",
    "x axis title(xs)": "x_axis_title_xs",
    "dss file": "dss_file",
}

def _norm(s: str) -> str:
    return " ".join(s.strip().split()).lower()

def parse_project_lines(lines: Iterable[str]) -> ProjectModel:
    """
    Parse lines of a HEC-RAS .prj file into ProjectModel.
    Ignores unknown keys and empty/comment lines.
    """
    proj = ProjectModel()
    in_description = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # Handle the optional BEGIN/END DESCRIPTION block
        if line.startswith("BEGIN DESCRIPTION:"):
            in_description = True
            continue
        if line.startswith("END DESCRIPTION:"):
            in_description = False
            continue
        if in_description:
            continue

        # Expect "Key=Value" lines
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = _norm(key)
        v = value.strip()

        attr = _KEYMAP.get(k)
        if attr is None:
            # Unknown key, ignore for now
            continue

        setattr(proj, attr, v)

    return proj

def parse_project_file(path: str) -> ProjectModel:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return parse_project_lines(f.readlines())
