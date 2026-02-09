# hack_ras/project/resolve.py
from __future__ import annotations
import os
import glob
from typing import Optional, Iterable

# ---------------------------
# HEC-RAS vs. ESRI projection file
# ---------------------------
def is_hecras_prj(path: str) -> bool:
    """True if file looks like a HEC‑RAS project (has RAS-style key=value lines).
    Keeps ESRI *.prj files out of discovery."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                # minimal signatures seen in real RAS .prj files
                if s.startswith("Proj Title="): return True
                if s.startswith("Geom File="):  return True
                if s.startswith("Plan File="):  return True
                if s.startswith("Flow File="):  return True
        return False
    except OSError:
        return False

def list_hecras_projects(folder: str) -> list[str]:
    """Only return .prj files that pass the RAS signature check."""
    paths = glob.glob(os.path.join(folder, "*.prj"))
    return [p for p in paths if is_hecras_prj(p)]


# ---------------------------
# Base-path utilities
# ---------------------------

def project_base_parts(prj_path: str) -> tuple[str, str]:
    """
    Returns (folder, base_name) for a .prj path.
    Example: '/path/Stream.prj' -> ('/path', 'Stream')
    """
    folder = os.path.dirname(os.path.abspath(prj_path))
    base = os.path.splitext(os.path.basename(prj_path))[0]
    return folder, base

def _discover_by_suffix(prj_path: str, suffix: str) -> list[str]:
    folder, base = project_base_parts(prj_path)
    return sorted(glob.glob(os.path.join(folder, f"{base}.{suffix}[0-9][0-9]")))

def discover_family(prj_path: str) -> dict[str, list[str]]:
    """
    List sibling INPUT files for THIS project base only.
    Plans are .p##; unsteady are .u##; geometry are .g##; steady are .f##.
    NOTE: .b## are outputs and intentionally ignored.
    """
    return {
        "geom":     _discover_by_suffix(prj_path, "g"),
        "plan":     _discover_by_suffix(prj_path, "p"),
        "unsteady": _discover_by_suffix(prj_path, "u"),
        "steady":   _discover_by_suffix(prj_path, "f"),
    }

def _candidate_path(folder: str, base: str, file_id: str) -> str:
    """
    Compose a candidate filename for given id: (folder,'Stream'), 'g01' -> /folder/Stream.g01
    """
    return os.path.join(folder, f"{base}.{file_id}")

# ---------------------------
# ID -> filepath resolution
# ---------------------------

def resolve_id(prj_path: str, file_id: str | None) -> str | None:
    """
    Resolve a single id (e.g., 'g01') to an absolute path next to the .prj.
    Returns None if id is None or file doesn't exist.
    """
    if not file_id:
        return None
    folder, base = project_base_parts(prj_path)
    cand = os.path.join(folder, f"{base}.{file_id}")
    return cand if os.path.exists(cand) else None


def resolve_project_files(
    prj_path: str,
    geom_id: str | None,
    plan_id: str | None,
    unsteady_id: str | None,
) -> dict[str, str | None]:
    """
    Map IDs from the .prj to real paths (if present).
    Only ever resolves to .g## / .p## / .u## (steady .f## optional).
    """
    return {
        "geom":     resolve_id(prj_path, geom_id),
        "plan":     resolve_id(prj_path, plan_id),
        "unsteady": resolve_id(prj_path, unsteady_id),
    }


# ---------------------------
# Discovery (BASE-scoped, avoids cross-project leakage)
# ---------------------------

def _discover(prj_path: str, suffix: str) -> list[str]:
    """
    Find all files for this project's base and a two-digit suffix, e.g. '.g01'..'.g99'.
    suffix should be one of 'g', 'p', 'u', 'f'.
    """
    folder, base = project_base_parts(prj_path)
    # Only match this base name, not others in the same folder:
    pattern = os.path.join(folder, f"{base}.{suffix}[0-9][0-9]")
    files = sorted(glob.glob(pattern))
    return files

def discover_family(prj_path: str) -> dict[str, list[str]]:
    """
    Lists all sibling files that belong to THIS project base only.
    Keys: 'geom', 'plan', 'unsteady', 'steady' (steady optional for future).
    """
    return {
        "geom":     _discover(prj_path, "g"),
        "plan":     _discover(prj_path, "p"),
        "unsteady": _discover(prj_path, "u"),
        "steady":   _discover(prj_path, "f"),
    }

# ---------------------------
# ID helpers and selection
# ---------------------------

def _ids_from_paths(paths: Iterable[str]) -> list[str]:
    """
    Convert '/path/Stream.g07' -> 'g07'. Assumes 'basename.ext' format and two-digit id.
    """
    out = []
    for p in paths:
        name = os.path.basename(p)      # 'Stream.g07'
        _, ext = os.path.splitext(name) # '.g07'
        if len(ext) == 4 and ext[1] in "gpuf" and ext[2:].isdigit():
            out.append(ext[1:].lower()) # 'g07'
    return sorted(out)

def list_available_ids(prj_path: str) -> dict[str, list[str]]:
    """
    For this project base, return available ids per type ('g??','p??','u??','f??').
    """
    fam = discover_family(prj_path)
    return {
        "geom":     _ids_from_paths(fam["geom"]),
        "plan":     _ids_from_paths(fam["plan"]),
        "unsteady": _ids_from_paths(fam["unsteady"]),
        "steady":   _ids_from_paths(fam["steady"]),
    }

def highest_id(ids: list[str]) -> Optional[str]:
    """
    Given ['g01','g02','g10'] -> 'g10'. Returns None if empty.
    """
    if not ids:
        return None
    # sort by numeric part
    return sorted(ids, key=lambda s: int(s[1:]))[-1]

def choose_default_ids(
    prj_path: str,
    prj_geom_id: Optional[str],
    prj_plan_id: Optional[str],
    prj_unsteady_id: Optional[str],
) -> dict[str, Optional[str]]:
    """
    Strategy:
      1) Prefer the ids recorded in the .prj (if those files exist).
      2) Else fall back to the highest available id for that type (e.g., g99...g01).
    """
    avail = list_available_ids(prj_path)

    # Geometry
    geom_id = prj_geom_id if resolve_id(prj_path, prj_geom_id) else highest_id(avail["geom"])

    # Plan
    plan_id = prj_plan_id if resolve_id(prj_path, prj_plan_id) else highest_id(avail["plan"])

    # Unsteady
    unsteady_id = prj_unsteady_id if resolve_id(prj_path, prj_unsteady_id) else highest_id(avail["unsteady"])

    return {"geom": geom_id, "plan": plan_id, "unsteady": unsteady_id}