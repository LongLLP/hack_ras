# hack_ras/results/reader.py
# Requires: pip install hack_ras[results]
from __future__ import annotations
import re
from pathlib import Path

import h5py
import numpy as np
import geopandas as gpd
from shapely.geometry import Polygon

from .model import AreaGeometry, PlanMetadata


# ---------------------------
# Plan text file (.p##)
# ---------------------------

def read_plan_metadata(hdf_path: str) -> PlanMetadata:
    """
    Parse the plan text file (.p##) that sits alongside a .p##.hdf file.

    Returns a PlanMetadata with geom_id (e.g. 'g01') and plan_title.
    Raises FileNotFoundError if the sidecar text file is missing.
    Raises ValueError if 'Geom File=' is not found in the plan text.
    """
    txt_path = Path(str(hdf_path)[:-4])   # strip .hdf -> .p##
    if not txt_path.exists():
        raise FileNotFoundError(
            f"Plan text file not found: {txt_path}\n"
            f"Expected alongside HDF file: {hdf_path}"
        )

    txt = txt_path.read_text(encoding="utf-8-sig", errors="replace")

    gm = re.search(r"^Geom File\s*=\s*(g\d+)", txt, re.MULTILINE | re.IGNORECASE)
    if not gm:
        raise ValueError(
            f"'Geom File=' not found in plan file: {txt_path}"
        )

    pm = re.search(r"^Plan Title\s*=\s*(.+)", txt, re.MULTILINE)
    plan_title = pm.group(1).strip() if pm else txt_path.stem.split(".")[-1]

    return PlanMetadata(geom_id=gm.group(1).lower(), plan_title=plan_title)


# ---------------------------
# 2D flow area discovery
# ---------------------------

def list_areas(hdf_path: str) -> list[str]:
    """
    Return the names of all 2D flow areas in a plan HDF5 file.
    Returns an empty list if the file has no 2D flow area geometry.
    """
    with h5py.File(hdf_path, "r") as hdf:
        try:
            grp = hdf["Geometry/2D Flow Areas"]
            return [k for k in grp.keys() if isinstance(grp[k], h5py.Group)]
        except KeyError:
            return []


# ---------------------------
# Cell geometry
# ---------------------------

def read_area_geometry(hdf_path: str, area: str) -> AreaGeometry:
    """
    Read cell geometry for one 2D flow area from a plan HDF5 file.

    Perimeter dummy cells (NaN min_elev) are excluded from cell_gdf so they
    never appear as profile output points.

    Parameters
    ----------
    hdf_path : str
        Absolute path to the .p##.hdf file.
    area : str
        Name of the 2D flow area (from list_areas).

    Returns
    -------
    AreaGeometry
    """
    base = f"Geometry/2D Flow Areas/{area}"

    with h5py.File(hdf_path, "r") as hdf:
        centers      = hdf[f"{base}/Cells Center Coordinate"][:]
        fp_xy        = hdf[f"{base}/FacePoints Coordinate"][:]
        fp_idx       = hdf[f"{base}/Cells FacePoint Indexes"][:]
        perim        = hdf[f"{base}/Perimeter"][:]
        min_elev_arr = hdf[f"{base}/Cells Minimum Elevation"][:].astype(np.float64)

    polys = []
    for row in fp_idx:
        idx = row[row >= 0]
        polys.append(Polygon(fp_xy[idx]) if len(idx) >= 3 else None)

    boundary = Polygon(perim)

    cell_gdf = gpd.GeoDataFrame(
        [
            {"cell_idx": i, "geometry": polys[i]}
            for i in range(len(polys))
            if polys[i] is not None and not np.isnan(min_elev_arr[i])
        ],
        geometry="geometry",
    )

    return AreaGeometry(
        cell_centers=centers,
        min_elevations=min_elev_arr,
        polygons=polys,
        boundary=boundary,
        cell_gdf=cell_gdf,
    )


# ---------------------------
# Water surface elevations
# ---------------------------

_SUM_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Summary Output/2D Flow Areas/{area}"
)
_TS_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Unsteady Time Series/2D Flow Areas/{area}"
)
_TS_DATES = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Unsteady Time Series/Time Date Stamp"
)


def read_wse(
    hdf_path: str,
    area: str,
    wse_type: str,
    timestamp: str | None = None,
) -> np.ndarray:
    """
    Read a WSE array (float64, shape N_cells) from a plan HDF5 file.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    area : str
        Name of the 2D flow area.
    wse_type : str
        One of:
          'Maximum'                 — maximum WSE from summary output
          'Maximum from Time Series'— per-cell max across all time steps
          '<timestamp>'             — WSE at a specific time stamp,
                                     e.g. '01Jan2025 00:30:00'
    timestamp : str or None
        Only used when wse_type is a timestamp string. If omitted, wse_type
        itself is used as the timestamp.

    Returns
    -------
    np.ndarray, shape (N,), dtype float64
        Dry non-perimeter cells have WSE == cell minimum elevation.
        Perimeter dummy cells may have WSE == 0 or NaN; they are excluded
        from profile output by the cell_gdf filter in AreaGeometry.

    Raises
    ------
    KeyError
        If the requested HDF5 dataset path does not exist.
    ValueError
        If the requested timestamp is not found in the time series.
    """
    SUM = _SUM_BASE.format(area=area)
    TS  = _TS_BASE.format(area=area)

    with h5py.File(hdf_path, "r") as hdf:
        if wse_type == "Maximum":
            # Shape (2, N): row 0 = WSE, row 1 = time of maximum
            return hdf[f"{SUM}/Maximum Water Surface"][0, :].astype(np.float64)

        elif wse_type == "Maximum from Time Series":
            ts = hdf[f"{TS}/Water Surface"][:].astype(np.float64)
            ts[ts <= 0] = np.nan
            return np.nanmax(ts, axis=0)

        else:
            # Treat wse_type (or explicit timestamp) as a time stamp string
            target = (timestamp or wse_type).strip().upper()
            stamps = [
                s.decode("utf-8").strip() if isinstance(s, bytes) else str(s).strip()
                for s in hdf[_TS_DATES][:]
            ]
            matches = [i for i, s in enumerate(stamps) if s.upper() == target]
            if not matches:
                raise ValueError(
                    f"Timestamp '{target}' not found in {hdf_path}.\n"
                    f"  Available (first 5): {stamps[:5]}\n"
                    f"  Expected format example: '01Jan2025 00:30:00'"
                )
            return hdf[f"{TS}/Water Surface"][matches[0], :].astype(np.float64)
