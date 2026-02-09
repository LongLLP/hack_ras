# hack_ras/project/catalog.py
from __future__ import annotations
import os, glob
from .resolve import is_hecras_prj, discover_family

def catalog_folder(folder: str) -> dict[str, dict[str, list[str]]]:
    """
    { <project_prj_path> : { 'geom': [...], 'plan': [...], 'unsteady': [...], 'steady': [...] } }
    - includes only RAS .prj
    - plans are .p## only
    - ignores .b## outputs and ESRI projection.prj
    """
    out: dict[str, dict[str, list[str]]] = {}
    for prj in glob.glob(os.path.join(folder, "*.prj")):
        if not is_hecras_prj(prj):
            continue
        out[prj] = discover_family(prj)
    return out