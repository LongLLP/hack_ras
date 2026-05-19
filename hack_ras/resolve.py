# hack_ras/resolve.py
from __future__ import annotations
import os
import re
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
    Keys: 'geom', 'plan', 'unsteady', 'steady'.
    NOTE: .b## outputs are intentionally excluded.
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

class GeometryFileNotFound(FileNotFoundError):
    """The .prj references a geometry file that does not exist on disk."""

class PlanHdfNotFound(FileNotFoundError):
    """A specified plan HDF file (.p##.hdf) was not found on disk."""

class CrsProjectionFileNotFound(FileNotFoundError):
    """No ESRI .prj projection file could be found for CRS lookup."""


# ---------------------------
# Plan HDF and CRS discovery
# ---------------------------

_PLAN_HDF_PAT = re.compile(r"\.p(\d+)\.hdf$", re.IGNORECASE)

def find_plan_hdfs(folder: str, plan_ids: list[str] | None = None) -> list[str]:
    """
    Discover *.p##.hdf plan output files in folder.

    If plan_ids is given (e.g. ['p01', 'p02']), returns only those plans.
    Raises PlanHdfNotFound if the folder contains no HDF files, or if a
    specified plan ID has no corresponding file.

    Returns a sorted list of absolute paths.
    """
    found: dict[str, str] = {}
    for f in glob.glob(os.path.join(folder, "*.p*.hdf")):
        m = _PLAN_HDF_PAT.search(os.path.basename(f))
        if m:
            pid = "p" + m.group(1).zfill(2)
            found[pid] = os.path.abspath(f)

    if not found:
        raise PlanHdfNotFound(
            f"No plan HDF files (*.pXX.hdf) found in {folder}"
        )

    if plan_ids:
        result: dict[str, str] = {}
        for raw in plan_ids:
            pid = "p" + str(raw).strip().lower().lstrip("p").zfill(2)
            if pid not in found:
                raise PlanHdfNotFound(
                    f"Plan '{raw}' (-> '{pid}') not found in {folder}. "
                    f"Available: {sorted(found.keys())}"
                )
            result[pid] = found[pid]
        return [result[k] for k in sorted(result.keys())]

    return [found[k] for k in sorted(found.keys())]


def find_crs_prj(folder: str, specified: str | None = None) -> str:
    """
    Find an ESRI .prj file (CRS definition) in or below folder.

    If specified is given, validates it exists and returns its absolute path.
    Otherwise searches recursively for .prj files that are NOT HEC-RAS project
    files (uses is_hecras_prj as the inverse filter).

    If multiple ESRI .prj files are found the first is returned. For CRS
    conflict detection across multiple files, use pyproj directly.

    Raises CrsProjectionFileNotFound if no suitable file can be found.
    """
    if specified:
        p = os.path.abspath(specified)
        if not os.path.exists(p):
            raise CrsProjectionFileNotFound(
                f"Projection file not found: {p}"
            )
        return p

    hits = [
        os.path.abspath(p)
        for p in glob.glob(os.path.join(folder, "**", "*.prj"), recursive=True)
        if not is_hecras_prj(p)
    ]
    if not hits:
        raise CrsProjectionFileNotFound(
            f"No ESRI projection file found in {folder}. "
            "Set 'projection_file' in your config to specify one explicitly."
        )
    return hits[0]


def resolve_default_geom(prj_path: str, prj_geom_id: Optional[str]) -> str:
    """
    Resolve the geometry file for a project strictly:
      - If the .prj has no Geom File entry, raises ValueError.
      - If the .prj lists a geometry ID but the file is missing on disk, raises GeometryFileNotFound.
      - Otherwise returns the absolute path to the geometry file.
    """
    if not prj_geom_id:
        raise ValueError(
            f"Project file does not reference a geometry file: {prj_path}"
        )
    path = resolve_id(prj_path, prj_geom_id)
    if path is None:
        folder, base = project_base_parts(prj_path)
        expected = os.path.join(folder, f"{base}.{prj_geom_id}")
        raise GeometryFileNotFound(
            f"Geometry file listed in .prj not found on disk: {expected}"
        )
    return path
