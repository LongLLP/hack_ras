# hack_ras/results/reader.py
# Requires: pip install hack_ras[results]
from __future__ import annotations
import logging
import re
from datetime import datetime
from pathlib import Path

import h5py
import numpy as np

from ..version import RasVersion
from .model import (
    AreaGeometry,
    CellVolumeTable,
    FaceGeometry,
    ConduitPath,
    ConduitProfile,
    ConduitTimeSeries,
    NodeMaxWse,
    NodeRims,
    PathProfile,
    Pump,
    PumpCurve,
    PumpGroup,
    PumpStation,
    VolumeAccounting,
    NodeTimeSeries,
    PipeConduit,
    PipeNetwork,
    PipeNode,
    PlanMetadata,
    Sa2dCell,
    Sa2dConnection,
)


def _decode(val) -> str:
    if isinstance(val, (bytes, np.bytes_)):
        return val.decode('utf-8', errors='replace').strip()
    return str(val).strip()


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

# Datasets needed to reconstruct a cell outline that follows the 2D flow area
# perimeter. Present in RAS 7.0; a plan HDF missing any of them falls back to the
# face-point-only outline.
_PERIM_POLY_KEYS = (
    "Cells Face and Orientation Info",
    "Cells Face and Orientation Values",
    "Faces FacePoint Indexes",
    "Faces Perimeter Info",
    "Faces Perimeter Values",
)


def _facepoint_polygons(fp_xy, fp_idx):
    """Cell outlines from face points alone — the pre-7.0 fallback.

    Correct for interior cells and wrong for any boundary cell whose perimeter
    face bends between its two face points; see _perimeter_polygons.
    """
    from shapely.geometry import Polygon

    polys = []
    for row in fp_idx:
        idx = row[row >= 0]
        polys.append(Polygon(fp_xy[idx]) if len(idx) >= 3 else None)
    return polys


def _perimeter_polygons(fp_xy, face_info, face_vals, face_fp, perim_info, perim_vals):
    """Cell outlines that follow the 2D flow area perimeter exactly.

    Walks each cell's faces in stored order and splices in the intermediate
    vertices RAS keeps in `Faces Perimeter Values` — the vertices where the mesh
    boundary bends *between* a face's two end face points, which are therefore
    absent from `Cells FacePoint Indexes`. Face orientation (the second column of
    `Cells Face and Orientation Values`) says which way to traverse the face, and
    the intermediate run is reversed with it.
    """
    from shapely.geometry import Polygon

    polys = []
    for start, count in face_info:
        if count < 3:
            polys.append(None)
            continue
        ring = []
        for face, orient in face_vals[start:start + count]:
            a, b = face_fp[face]
            if orient < 0:
                a = b
            p_start, p_count = perim_info[face]
            mid = perim_vals[p_start:p_start + p_count]
            if orient < 0:
                mid = mid[::-1]
            ring.append(fp_xy[a])
            ring.extend(mid)
        polys.append(Polygon(ring) if len(ring) >= 3 else None)
    return polys


def read_area_geometry(hdf_path: str, area: str) -> AreaGeometry:
    """
    Read cell geometry for one 2D flow area from a plan HDF5 file.

    Cell outlines follow the 2D flow area perimeter — see _perimeter_polygons for
    why the face points alone are not enough, and the module docstring of
    hack_ras/gis/mesh.py for the measurements that settled it. A plan HDF that
    predates those datasets falls back to face points only.

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

    import geopandas as gpd
    from shapely.geometry import Polygon

    with h5py.File(hdf_path, "r") as hdf:
        grp          = hdf[base]
        centers      = grp["Cells Center Coordinate"][:]
        fp_xy        = grp["FacePoints Coordinate"][:]
        fp_idx       = grp["Cells FacePoint Indexes"][:]
        perim        = grp["Perimeter"][:]
        min_elev_arr = grp["Cells Minimum Elevation"][:].astype(np.float64)

        exact = all(k in grp for k in _PERIM_POLY_KEYS)
        if exact:
            polys = _perimeter_polygons(
                fp_xy,
                grp["Cells Face and Orientation Info"][:],
                grp["Cells Face and Orientation Values"][:],
                grp["Faces FacePoint Indexes"][:],
                grp["Faces Perimeter Info"][:],
                grp["Faces Perimeter Values"][:],
            )
        else:
            logging.warning(
                "%s / %s has no perimeter face datasets (pre-RAS-7.0 plan HDF); "
                "boundary cell outlines will be approximate.", hdf_path, area
            )
            polys = _facepoint_polygons(fp_xy, fp_idx)

        if "Cells Surface Area" in grp:
            plan_areas = grp["Cells Surface Area"][:].astype(np.float64)
        else:
            plan_areas = np.array(
                [p.area if p is not None else np.nan for p in polys], dtype=np.float64
            )

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
        plan_areas=plan_areas,
        boundary=boundary,
        cell_gdf=cell_gdf,
    )


# ---------------------------
# Face geometry and roughness
# ---------------------------

# Datasets needed to describe faces. A plan HDF missing any of them cannot be
# read for face roughness at all — unlike the cell path, there is no fallback,
# because nothing else in the file carries the per-face n.
_FACE_KEYS = (
    "Faces Cell Indexes",
    "Faces FacePoint Indexes",
    "Faces NormalUnitVector and Length",
    "Faces Area Elevation Info",
    "Faces Area Elevation Values",
)


def read_face_geometry(hdf_path: str, area: str) -> FaceGeometry:
    """
    Read face geometry and per-face Manning's n for one 2D flow area.

    Face n is what RAS uses for conveyance *between* two cells; it is sampled
    along the face from the land cover, not taken from either cell centre.  See
    FaceGeometry for why the two can disagree and for the dual-polygon
    construction returned in `polygons`.

    Parameters
    ----------
    hdf_path : str
        Absolute path to a .p##.hdf or .g##.hdf file.
    area : str
        Name of the 2D flow area (from list_areas).

    Returns
    -------
    FaceGeometry

    Raises
    ------
    KeyError
        If the area is absent, or lacks any of the face datasets (a pre-RAS-7.0
        plan HDF).
    """
    base = f"Geometry/2D Flow Areas/{area}"

    from shapely.geometry import Polygon

    with h5py.File(hdf_path, "r") as hdf:
        if base not in hdf:
            raise KeyError(f"2D flow area not found: {base}\nIn HDF file: {hdf_path}")
        grp = hdf[base]

        missing = [k for k in _FACE_KEYS if k not in grp]
        if missing:
            raise KeyError(
                f"{base} has no face property datasets: {', '.join(missing)}\n"
                f"In HDF file: {hdf_path}\n"
                f"Face Manning's n is only stored in RAS 7.0 and later."
            )

        cell_idx = grp["Faces Cell Indexes"][:]
        fp_idx   = grp["Faces FacePoint Indexes"][:]
        nvl      = grp["Faces NormalUnitVector and Length"][:].astype(np.float64)
        info     = grp["Faces Area Elevation Info"][:]
        values   = grp["Faces Area Elevation Values"][:].astype(np.float64)
        centers  = grp["Cells Center Coordinate"][:]
        fp_xy    = grp["FacePoints Coordinate"][:]

    # Manning's n: column 3 of the face property table. Stored per elevation
    # step, but constant within a face on every mesh measured — take the
    # lowest-elevation row and say so loudly if that assumption ever breaks,
    # rather than silently averaging a curve down to one number.
    n_faces = len(info)
    mannings = np.full(n_faces, np.nan, dtype=np.float64)
    varying = 0
    for i, (start, count) in enumerate(info):
        if count <= 0:
            continue
        col = values[start:start + count, 3]
        mannings[i] = col[0]
        if count > 1 and not np.all(col == col[0]):
            varying += 1
    if varying:
        logging.warning(
            "%s / %s: Manning's n varies with elevation on %d of %d faces; "
            "using the lowest-elevation value.", hdf_path, area, varying, n_faces
        )

    polygons = []
    for i in range(n_faces):
        c_l, c_r = cell_idx[i]
        a, b = fp_idx[i]
        # The face is the DIAGONAL of the diamond, not an edge: alternate
        # cell centre / face point around the ring, or it comes out a bowtie.
        poly = Polygon([centers[c_l], fp_xy[a], centers[c_r], fp_xy[b]])
        if not poly.is_valid:
            poly = poly.buffer(0)
        polygons.append(poly if (not poly.is_empty and poly.area > 0) else None)

    return FaceGeometry(
        cell_indexes=cell_idx,
        facepoint_indexes=fp_idx,
        mannings_n=mannings,
        normals=nvl[:, :2],
        lengths=nvl[:, 2],
        polygons=polygons,
    )


# ---------------------------
# Cell volume-elevation tables
# ---------------------------

def read_cell_volume_table(hdf_path: str, area: str) -> CellVolumeTable:
    """
    Read the volume-elevation lookup table for all cells in one 2D flow area.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    area : str
        Name of the 2D flow area (from list_areas).

    Returns
    -------
    CellVolumeTable
        info   shape (N_cells, 2) int32  — [start_idx, count] per cell
        values shape (total_pairs, 2) float32 — [elevation, volume] pairs

    Raises
    ------
    KeyError
        If the volume-elevation datasets are absent from the HDF file.
    """
    base = f"Geometry/2D Flow Areas/{area}"
    with h5py.File(hdf_path, "r") as hdf:
        info   = hdf[f"{base}/Cells Volume Elevation Info"][:]
        values = hdf[f"{base}/Cells Volume Elevation Values"][:]
    return CellVolumeTable(info=info, values=values)


def interpolate_cell_volume(
    table: CellVolumeTable,
    cell_idx: int,
    wse: float,
    cell_plan_area: float,
) -> float:
    """
    Compute the water volume stored in one cell at a given water surface elevation.

    Interpolates within the cell's volume-elevation table. When WSE exceeds the
    highest terrain elevation in the table, HEC-RAS computes additional volume
    using the full plan area of the cell (a linear extension), so this function
    does the same rather than clamping.

    Parameters
    ----------
    table : CellVolumeTable
        As returned by read_cell_volume_table().
    cell_idx : int
        Zero-based cell index.
    wse : float
        Water surface elevation to evaluate at.
    cell_plan_area : float
        Horizontal footprint area of the cell (ft² or m², matching the model's
        coordinate units). Used for linear extrapolation above the table maximum.
        Obtain from AreaGeometry.plan_areas[cell_idx] — RAS's own per-cell area.
        Not `polygons[cell_idx].area`: that has to be reconstructed, and on a
        pre-7.0 plan HDF the reconstruction is approximate for boundary cells.

    Returns
    -------
    float
        Volume for the cell at the given WSE.
        0.0 if WSE is at or below the cell's minimum terrain elevation.
        Linearly extrapolated above the table maximum using cell_plan_area.
    """
    start = int(table.info[cell_idx, 0])
    count = int(table.info[cell_idx, 1])
    if count == 0:
        return 0.0
    elev = table.values[start : start + count, 0]
    vol  = table.values[start : start + count, 1]
    if wse <= float(elev[0]):
        return 0.0
    if wse > float(elev[-1]):
        # WSE above the highest terrain point in the table: the cell is fully
        # inundated and HEC-RAS extends volume linearly at the full plan area.
        return float(vol[-1]) + (wse - float(elev[-1])) * cell_plan_area
    return float(np.interp(wse, elev, vol))


# ---------------------------
# Water surface elevations
# ---------------------------

_PIPE_TS_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Unsteady Time Series/Pipe Networks/{network}"
)
_PUMP_TS_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Unsteady Time Series/Pumping Stations"
)
_PUMP_GEOM = "Geometry/Pump Stations"
_PIPE_NODES = "Geometry/Pipe Nodes/Attributes"
# RAS 7.0 moved the run summary under Results/Unsteady; 6.x and
# earlier wrote it at Results/Summary. Check both, newest first.
_VOL_ROOTS = (
    "Results/Unsteady/Summary/Volume Accounting",
    "Results/Summary/Volume Accounting",
)

_PIPE_SUM_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Summary Output/Pipe Networks/{network}"
)
_SA2D_TS_BASE = (
    "Results/Unsteady/Output/Output Blocks/Base Output"
    "/Unsteady Time Series/SA 2D Area Conn/{connection}"
)

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


# ---------------------------
# Timestamps
# ---------------------------

def read_timestamps(hdf_path: str) -> np.ndarray:
    """
    Read the unsteady time-series timestamp array from a plan HDF5 file.

    Returns
    -------
    np.ndarray, shape (T,), dtype str
        HEC-RAS time-date stamp strings, e.g. '01Jan2025 00:30:00'.

    Raises
    ------
    KeyError
        If the Time Date Stamp dataset is absent.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        return np.array([_decode(t) for t in hdf[_TS_DATES][()]])


# ---------------------------
# SA 2D Area Conn discovery
# ---------------------------

def list_sa2d_connections(hdf_path: str) -> list[str]:
    """
    Return the names of all SA 2D Area Conn features in a plan HDF5 file.
    Returns an empty list if the file has no SA 2D Area Conn results.
    """
    sa2d_path = (
        "Results/Unsteady/Output/Output Blocks/Base Output"
        "/Unsteady Time Series/SA 2D Area Conn"
    )
    with h5py.File(hdf_path, "r") as hdf:
        try:
            grp = hdf[sa2d_path]
            return [k for k in grp.keys() if isinstance(grp[k], h5py.Group)]
        except KeyError:
            return []


# ---------------------------
# SA 2D Area Conn time series
# ---------------------------

def _seg_station_map(
    unique_indices: np.ndarray,
    seg_labels: np.ndarray,
    seg_stations: np.ndarray,
) -> dict:
    """
    Map each unique cell index to (station_start, station_center, station_end).

    For each segment j (0 … N_segs-1):
      midpoint_j = (seg_stations[j] + seg_stations[j+1]) / 2

    station_center = mean of midpoint_j across all segments where the cell appears.
    station_start  = minimum face-point station bounding those segments.
    station_end    = maximum face-point station bounding those segments.

    Returns (nan, nan, nan) for cells whose index does not appear in any segment.
    """
    n_segs = len(seg_labels)
    midpoints = [
        (float(seg_stations[j]) + float(seg_stations[j + 1])) / 2.0
        for j in range(n_segs)
    ]

    cell_to_mids: dict = {}
    cell_to_bounds: dict = {}
    for j, raw in enumerate(seg_labels):
        label = _decode(raw)
        try:
            cell_id = int(label)
        except ValueError:
            continue
        cell_to_mids.setdefault(cell_id, []).append(midpoints[j])
        sta_j  = float(seg_stations[j])
        sta_j1 = float(seg_stations[j + 1])
        if cell_id not in cell_to_bounds:
            cell_to_bounds[cell_id] = [min(sta_j, sta_j1), max(sta_j, sta_j1)]
        else:
            cell_to_bounds[cell_id][0] = min(cell_to_bounds[cell_id][0], sta_j, sta_j1)
            cell_to_bounds[cell_id][1] = max(cell_to_bounds[cell_id][1], sta_j, sta_j1)

    _nan = float("nan")
    return {
        int(idx): (
            cell_to_bounds[int(idx)][0] if int(idx) in cell_to_bounds else _nan,
            float(np.mean(cell_to_mids[int(idx)])) if int(idx) in cell_to_mids else _nan,
            cell_to_bounds[int(idx)][1] if int(idx) in cell_to_bounds else _nan,
        )
        for idx in unique_indices
    }


def read_sa2d_connection(hdf_path: str, connection: str) -> Sa2dConnection:
    """
    Read HW and TW cell time series for one SA 2D Area Conn feature.

    SA 2D Area Conn features have no Summary Output in the HDF.  The time of
    maximum WSE must be derived from the returned time series via nanargmax.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    connection : str
        Connection name, as returned by list_sa2d_connections().

    Returns
    -------
    Sa2dConnection
        hw_cells and tw_cells are each sorted by station ascending.

    Raises
    ------
    KeyError
        If the connection group or required datasets are absent.
    """
    base = _SA2D_TS_BASE.format(connection=connection)

    with h5py.File(hdf_path, "r") as hdf:
        timestamps   = np.array([_decode(t) for t in hdf[_TS_DATES][()]])
        hw_indices   = hdf[f"{base}/Headwater Cells"][()]
        tw_indices   = hdf[f"{base}/Tailwater Cells"][()]
        hw_wse       = hdf[f"{base}/HW TW Cells/Water Surface HW Cells"][()].astype(np.float64)
        tw_wse       = hdf[f"{base}/HW TW Cells/Water Surface TW Cells"][()].astype(np.float64)
        seg_stations = hdf[f"{base}/HW TW Segments/HW TW Station"][()]
        seg_hw       = hdf[f"{base}/HW TW Segments/Headwater Cells"][()]
        seg_tw       = hdf[f"{base}/HW TW Segments/Tailwater Cells"][()]

    hw_map = _seg_station_map(hw_indices, seg_hw, seg_stations)
    tw_map = _seg_station_map(tw_indices, seg_tw, seg_stations)

    hw_cells = sorted(
        [Sa2dCell(
            cell_idx=int(hw_indices[i]),
            station=hw_map[int(hw_indices[i])][1],
            wse=hw_wse[:, i],
            station_start=hw_map[int(hw_indices[i])][0],
            station_end=hw_map[int(hw_indices[i])][2],
         ) for i in range(len(hw_indices))],
        key=lambda c: c.station,
    )
    tw_cells = sorted(
        [Sa2dCell(
            cell_idx=int(tw_indices[i]),
            station=tw_map[int(tw_indices[i])][1],
            wse=tw_wse[:, i],
            station_start=tw_map[int(tw_indices[i])][0],
            station_end=tw_map[int(tw_indices[i])][2],
         ) for i in range(len(tw_indices))],
        key=lambda c: c.station,
    )

    return Sa2dConnection(
        name=connection,
        timestamps=timestamps,
        hw_cells=hw_cells,
        tw_cells=tw_cells,
    )


def read_sa2d_areas(hdf_path: str, connection: str) -> tuple[str, str]:
    """
    Return (hw_area, tw_area) for an SA 2D Area Conn by looking up
    US SA/2D and DS SA/2D in Geometry/Structures/Attributes.

    Uses the Node Pointer attribute on the connection's results group
    to match against the SNN ID field in Structures/Attributes.

    Raises
    ------
    KeyError
        If the connection group or Structures/Attributes is absent.
    ValueError
        If no structure row matches the connection's Node Pointer.
    """
    conn_grp = _SA2D_TS_BASE.format(connection=connection)
    with h5py.File(hdf_path, "r") as hdf:
        node_ptr = int(hdf[conn_grp].attrs["Node Pointer"])
        attrs    = hdf["Geometry/Structures/Attributes"][()]
    matches = [r for r in attrs if int(r["SNN ID"]) == node_ptr]
    if not matches:
        raise ValueError(
            f"No structure found with SNN ID={node_ptr} "
            f"for connection '{connection}' in {hdf_path}"
        )
    row = matches[0]
    return _decode(row["US SA/2D"]), _decode(row["DS SA/2D"])


# Hardcoded column names for SA 2D Area Conn variable datasets.
# Column order matches HEC-RAS HDF output (verified against plans with breach).
_SA2D_STRUCTURE_COLS = ("Total Flow", "Weir Flow", "Stage HW", "Stage TW")
_SA2D_WEIR_COLS = (
    "Weir Flow", "Sta US", "Sta DS", "Top Width",
    "Max Depth", "Avg Depth", "Flow Area", "Coef", "Submergence",
)
_SA2D_BREACHING_COLS = (
    "Stage HW", "Stage TW", "Bottom-Width", "Bottom-Elevation",
    "Left Side Slope", "Right Side Slope",
    "Breach Flow", "Breach Velocity", "Breach Flow Area",
)


def read_breach_timeseries(hdf_path: str, connection: str) -> dict:
    """
    Read Structure Variables, Breaching Variables, and Weir Variables time
    series for one SA 2D Area Conn feature.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    connection : str
        Connection name, as returned by list_sa2d_connections().

    Returns
    -------
    dict with keys:

    ``'timestamps'``
        np.ndarray, shape (T,), str — HEC-RAS time-date stamp strings,
        e.g. ``'01JAN2025 00:30:00'``.

    ``'structure'``
        dict[str, np.ndarray] — always present.
        Keys: ``'Total Flow'``, ``'Weir Flow'``, ``'Stage HW'``, ``'Stage TW'``.

    ``'breaching'``
        dict[str, np.ndarray] or None — present only when a breach has occurred.
        Keys: ``'Stage HW'``, ``'Stage TW'``, ``'Bottom-Width'``,
        ``'Bottom-Elevation'``, ``'Left Side Slope'``, ``'Right Side Slope'``,
        ``'Breach Flow'``, ``'Breach Velocity'``, ``'Breach Flow Area'``.

    ``'weir'``
        dict[str, np.ndarray] or None — present when weir overflow occurs.
        Keys: ``'Weir Flow'``, ``'Sta US'``, ``'Sta DS'``, ``'Top Width'``,
        ``'Max Depth'``, ``'Avg Depth'``, ``'Flow Area'``, ``'Coef'``,
        ``'Submergence'``.

    Raises
    ------
    KeyError
        If the connection group or the Structure Variables dataset are absent.
    """
    base = _SA2D_TS_BASE.format(connection=connection)

    with h5py.File(hdf_path, "r") as hdf:
        timestamps = np.array([_decode(t) for t in hdf[_TS_DATES][()]])

        # Structure Variables — always present for any SA 2D Area Conn
        sv = hdf[f"{base}/Structure Variables"][()].astype(np.float64)
        structure = {name: sv[:, i] for i, name in enumerate(_SA2D_STRUCTURE_COLS)}

        # Breaching Variables — only exists when a breach has occurred in this plan
        breaching = None
        bv_path = f"{base}/Breaching Variables"
        if bv_path in hdf:
            bv = hdf[bv_path][()].astype(np.float64)
            breaching = {name: bv[:, i] for i, name in enumerate(_SA2D_BREACHING_COLS)}

        # Weir Variables — present when there is weir overflow
        weir = None
        wv_path = f"{base}/Weir Variables"
        if wv_path in hdf:
            wv = hdf[wv_path][()].astype(np.float64)
            weir = {name: wv[:, i] for i, name in enumerate(_SA2D_WEIR_COLS)}

    return {
        "timestamps": timestamps,
        "structure":  structure,
        "breaching":  breaching,
        "weir":       weir,
    }


def read_simulation_start_time(hdf_path: str) -> datetime:
    """
    Read simulation start time from Plan Data/Plan Information.

    Returns
    -------
    datetime
        Parsed from the 'Simulation Start Time' attribute, e.g. '01Jan2025 00:00:00'.

    Raises
    ------
    KeyError
        If Plan Data/Plan Information is absent.
    """
    with h5py.File(hdf_path, "r") as hdf:
        raw = hdf["Plan Data/Plan Information"].attrs["Simulation Start Time"]
    return datetime.strptime(_decode(raw), "%d%b%Y %H:%M:%S")


def read_summary_max(
    hdf_path: str,
    area: str,
    cell_indices,
) -> dict[int, tuple[float, float]]:
    """
    Read max WSE and time of max from Summary Output for specific cells.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    area : str
        Name of the 2D flow area.
    cell_indices : iterable of int
        Local cell indices (0-based within the area) to retrieve.

    Returns
    -------
    dict[int, tuple[float, float]]
        {cell_idx: (max_wse, time_days)} where time_days is decimal days
        from midnight of the simulation start date (sub-step resolution).

    Raises
    ------
    KeyError
        If the Summary Output group for the area is absent.
    """
    SUM = _SUM_BASE.format(area=area)
    with h5py.File(hdf_path, "r") as hdf:
        data = hdf[f"{SUM}/Maximum Water Surface"][()]   # shape (2, N_cells)
    return {idx: (float(data[0, idx]), float(data[1, idx])) for idx in cell_indices}


# ---------------------------
# Pipe network discovery
# ---------------------------

def list_pipe_networks(hdf_path: str) -> list[str]:
    """
    Return names of all pipe networks in a plan HDF5 file.
    Returns an empty list if the file has no pipe network geometry.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        try:
            grp = hdf['Geometry/Pipe Networks']
            return [k for k in grp.keys() if isinstance(grp[k], h5py.Group)]
        except KeyError:
            return []


# ---------------------------
# Pipe network geometry
# ---------------------------

def read_pipe_network(hdf_path: str, network: str) -> PipeNetwork:
    """
    Read geometry and index maps for one pipe network.

    Reads the global Pipe Nodes and Pipe Conduits attribute tables, then uses
    the per-network Node Indices / Conduit Indices to build result-position maps
    and adjacency dicts.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    network : str
        Network name, as returned by list_pipe_networks().

    Returns
    -------
    PipeNetwork

    Raises
    ------
    KeyError
        If the network group or required geometry datasets are absent.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        raw_nodes = hdf['Geometry/Pipe Nodes/Attributes'][()]
        global_nodes = [
            PipeNode(name=_decode(r['Name']), system_name=_decode(r['System Name']))
            for r in raw_nodes
        ]

        raw_conduits = hdf['Geometry/Pipe Conduits/Attributes'][()]
        global_conduits = [
            PipeConduit(
                name=_decode(r['Name']),
                us_node=_decode(r['US Node']),
                ds_node=_decode(r['DS Node']),
            )
            for r in raw_conduits
        ]

        grp = hdf[f'Geometry/Pipe Networks/{network}']
        node_indices    = grp['Node Indices'][()]
        conduit_indices = grp['Conduit Indices'][()]

    nodes: dict = {}
    for results_pos, global_idx in enumerate(node_indices):
        name = global_nodes[int(global_idx)].name
        nodes[name] = results_pos

    conduits: dict = {}
    conduit_index: dict = {}
    upstream_of: dict = {}
    downstream_of: dict = {}
    for results_pos, global_idx in enumerate(conduit_indices):
        c = global_conduits[int(global_idx)]
        conduits[c.name] = c
        conduit_index[c.name] = results_pos
        upstream_of.setdefault(c.ds_node, []).append(c.name)
        downstream_of.setdefault(c.us_node, []).append(c.name)

    return PipeNetwork(
        name=network,
        nodes=nodes,
        conduits=conduits,
        conduit_index=conduit_index,
        upstream_of=upstream_of,
        downstream_of=downstream_of,
    )


# ---------------------------
# Pipe network time series
# ---------------------------

def read_node_timeseries(
    hdf_path: str,
    network: PipeNetwork,
    node_name: str,
) -> NodeTimeSeries:
    """
    Read and compute full time-series for one pipe node.

    flow_in is the sum of Pipe Flow DS for conduits in network.upstream_of[node_name].
    flow_out is the sum of Pipe Flow US for conduits in network.downstream_of[node_name].
    Nodes with no incoming or outgoing conduits produce zeros, not errors.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    network : PipeNetwork
        As returned by read_pipe_network(). Must be the network containing node_name.
    node_name : str
        Must be a key in network.nodes.

    Returns
    -------
    NodeTimeSeries

    Raises
    ------
    KeyError
        If node_name is not in network.nodes, or HDF datasets are absent.
    """
    if node_name not in network.nodes:
        raise KeyError(
            f"Node '{node_name}' not found in pipe network '{network.name}'"
        )

    base = _PIPE_TS_BASE.format(network=network.name)
    nidx = network.nodes[node_name]

    with h5py.File(hdf_path, 'r') as hdf:
        timestamps   = np.array([_decode(t) for t in hdf[_TS_DATES][()]])
        depth        = hdf[f'{base}/Nodes/Depth'][:, nidx].astype(np.float64)
        wse          = hdf[f'{base}/Nodes/Water Surface'][:, nidx].astype(np.float64)
        inlet_flow   = hdf[f'{base}/Nodes/Top + Side Inlet Flow'][:, nidx].astype(np.float64)

        pipe_flow_ds = hdf[f'{base}/Pipes/Pipe Flow DS'][()].astype(np.float64)
        pipe_flow_us = hdf[f'{base}/Pipes/Pipe Flow US'][()].astype(np.float64)

    n_times  = len(timestamps)
    flow_in  = np.zeros(n_times, dtype=np.float64)
    flow_out = np.zeros(n_times, dtype=np.float64)

    for cname in network.upstream_of.get(node_name, []):
        flow_in += pipe_flow_ds[:, network.conduit_index[cname]]

    for cname in network.downstream_of.get(node_name, []):
        flow_out += pipe_flow_us[:, network.conduit_index[cname]]

    return NodeTimeSeries(
        timestamps=timestamps,
        depth=depth,
        wse=wse,
        inlet_flow=inlet_flow,
        flow_in=flow_in,
        flow_out=flow_out,
    )


def read_conduit_timeseries(
    hdf_path: str,
    network: PipeNetwork,
    conduit_name: str,
) -> ConduitTimeSeries:
    """
    Read time-series for one pipe conduit.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    network : PipeNetwork
        As returned by read_pipe_network().
    conduit_name : str
        Must be a key in network.conduit_index.

    Returns
    -------
    ConduitTimeSeries

    Raises
    ------
    KeyError
        If conduit_name is not in network.conduit_index, or HDF datasets are absent.
    """
    if conduit_name not in network.conduit_index:
        raise KeyError(
            f"Conduit '{conduit_name}' not found in pipe network '{network.name}'"
        )

    base = _PIPE_TS_BASE.format(network=network.name)
    cidx = network.conduit_index[conduit_name]

    with h5py.File(hdf_path, 'r') as hdf:
        timestamps = np.array([_decode(t) for t in hdf[_TS_DATES][()]])
        flow_us    = hdf[f'{base}/Pipes/Pipe Flow US'][:, cidx].astype(np.float64)
        flow_ds    = hdf[f'{base}/Pipes/Pipe Flow DS'][:, cidx].astype(np.float64)
        vel_us     = hdf[f'{base}/Pipes/Vel US'][:, cidx].astype(np.float64)
        vel_ds     = hdf[f'{base}/Pipes/Vel DS'][:, cidx].astype(np.float64)

    return ConduitTimeSeries(
        timestamps=timestamps,
        flow_us=flow_us,
        flow_ds=flow_ds,
        vel_us=vel_us,
        vel_ds=vel_ds,
    )


# ---------------------------
# Pipe conduit profiles
# ---------------------------

def _pipe_units_are_si(hdf) -> bool:
    """
    Determine the model unit system from an open plan HDF.

    Prefers the Geometry group's 'SI Units' attribute, falls back to the root
    'Units System' attribute. Defaults to False (US Customary) if neither exists.
    """
    try:
        return _decode(hdf['Geometry'].attrs['SI Units']).lower() == 'true'
    except (KeyError, AttributeError):
        pass
    try:
        return _decode(hdf.attrs['Units System']).lower().startswith('si')
    except (KeyError, AttributeError):
        return False


def read_conduit_profile(
    hdf_path: str,
    network,
    conduit_name: str,
    when: str = 'Maximum',
) -> ConduitProfile:
    """
    Read the along-conduit profile (station, invert, WSE, velocity, flow) for one pipe.

    This is what RAS Mapper's pipe-conduit profile plot draws. It reads every
    computation FACE on the conduit, unlike read_conduit_timeseries which reads
    only the two lumped per-conduit US/DS values.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file. A geometry-only .g##.hdf has no results and
        raises KeyError.
    network : PipeNetwork or str
        The network containing the conduit, as a PipeNetwork from
        read_pipe_network() or a bare network name from list_pipe_networks().
    conduit_name : str
        Conduit name as it appears in Geometry/Pipe Conduits/Attributes.
    when : str, default 'Maximum'
        'Maximum' or 'Minimum' for the per-face envelope from Summary Output, or
        a time-stamp string (e.g. '01JAN2025 09:00:00') for an instant.

    Returns
    -------
    ConduitProfile

    Raises
    ------
    KeyError
        If the network, conduit, or required HDF datasets are absent.
    ValueError
        If `when` is not a recognised time stamp for this plan.

    Notes
    -----
    'Maximum' and 'Minimum' are PER-FACE ENVELOPES, not a snapshot: the peak WSE,
    peak velocity, and peak flow at one face need not occur at the same time, and
    neighbouring faces need not peak together. For a physically consistent
    profile, pass an explicit time stamp.

    'Minimum' is NOT a strict lower bound on the output time series. On dry or
    near-dry faces RAS's Minimum Face Water Surface sits ABOVE the smallest value
    in Unsteady Time Series -- measured at up to 0.278 ft (5 of 93 faces) on the
    test fixture and 3.185 ft (58 of 2850 faces) on Hillside p10, because the two
    are referenced to different dry-bed elevations. 'Maximum' has no such problem
    (0 violations on both models). Treat 'Minimum' as indicative only.

    The IDs stored in Geometry/Pipe Networks/{net}/Faces Conduit ID and Stations
    are GLOBAL indices into Geometry/Pipe Conduits/Attributes -- NOT the network-
    local results positions held in PipeNetwork.conduit_index. The two spaces
    coincide only when the network's Conduit Indices happens to be the identity
    permutation. This function resolves the global index explicitly.
    """
    net_name = network.name if hasattr(network, 'name') else str(network)

    with h5py.File(hdf_path, 'r') as hdf:
        si_units = _pipe_units_are_si(hdf)

        raw_conduits = hdf['Geometry/Pipe Conduits/Attributes'][()]
        names = [_decode(r['Name']) for r in raw_conduits]
        try:
            global_idx = names.index(conduit_name)
        except ValueError:
            raise KeyError(
                f"Conduit '{conduit_name}' not found in "
                f"Geometry/Pipe Conduits/Attributes of {hdf_path}"
            ) from None
        row = raw_conduits[global_idx]

        grp_path = f'Geometry/Pipe Networks/{net_name}'
        if grp_path not in hdf:
            raise KeyError(
                f"Pipe network '{net_name}' not found in {hdf_path}"
            )
        grp = hdf[grp_path]

        # Confirm the conduit really belongs to this network. Conduit Indices maps
        # results position -> global index, so membership is a lookup in its values.
        if global_idx not in set(int(v) for v in grp['Conduit Indices'][()]):
            raise KeyError(
                f"Conduit '{conduit_name}' is not part of pipe network "
                f"'{net_name}'"
            )

        faces = grp['Faces Conduit ID and Stations'][()]
        sel = np.where(faces['ConduitID'] == global_idx)[0]
        if sel.size == 0:
            raise KeyError(
                f"Conduit '{conduit_name}' has no faces in pipe network "
                f"'{net_name}' -- the network geometry may be stale"
            )
        sel = sel[np.argsort(faces['ConduitStation'][sel], kind='stable')]

        station = faces['ConduitStation'][sel].astype(np.float64)
        invert = faces['Elevation'][sel].astype(np.float64)

        ts_base = _PIPE_TS_BASE.format(network=net_name)
        sum_base = _PIPE_SUM_BASE.format(network=net_name)

        if when in ('Maximum', 'Minimum'):
            # Summary datasets are (2, N_faces): row 0 = value, row 1 = time in days.
            wse = hdf[f'{sum_base}/{when} Face Water Surface'][0, :][sel]
            velocity = hdf[f'{sum_base}/{when} Face Velocity'][0, :][sel]
            flow = hdf[f'{sum_base}/{when} Face Flow'][0, :][sel]
        else:
            stamps = [_decode(t) for t in hdf[_TS_DATES][()]]
            try:
                t_idx = stamps.index(when)
            except ValueError:
                raise ValueError(
                    f"Time stamp '{when}' not found in {hdf_path}. "
                    f"Expected 'Maximum', 'Minimum', or one of {len(stamps)} "
                    f"stamps from '{stamps[0]}' to '{stamps[-1]}'."
                ) from None
            wse = hdf[f'{ts_base}/Face Water Surface'][t_idx, :][sel]
            velocity = hdf[f'{ts_base}/Face Velocity'][t_idx, :][sel]
            flow = hdf[f'{ts_base}/Face Flow'][t_idx, :][sel]

    return ConduitProfile(
        network=net_name,
        conduit=conduit_name,
        when=when,
        station=station,
        invert=invert,
        wse=np.asarray(wse, dtype=np.float64),
        velocity=np.asarray(velocity, dtype=np.float64),
        flow=np.asarray(flow, dtype=np.float64),
        face_indices=sel.astype(np.int64),
        us_node=_decode(row['US Node']),
        ds_node=_decode(row['DS Node']),
        us_invert=float(row['US Elevation']),
        ds_invert=float(row['DS Elevation']),
        length=float(row['Conduit Length']),
        rise=float(row['Rise']),
        span=float(row['Span']),
        shape=_decode(row['Shape']),
        si_units=si_units,
    )


# ---------------------------
# Pump stations
# ---------------------------

def _split_network_node(raw: str):
    """
    Split a pump station's pipe-node reference into (network, node).

    RAS writes these as ``'Base [J314]'``. Returns (None, None) for a blank
    field, which is what a station tied to a 2D area or a 1D reach carries.
    """
    text = _decode(raw)
    if not text:
        return None, None
    m = re.match(r'^(.*?)\s*\[(.*)\]$', text)
    if not m:
        return None, text
    return m.group(1).strip(), m.group(2).strip()


def list_pump_stations(hdf_path: str) -> list[str]:
    """
    Return the names of all pump stations in a plan HDF5 file.

    Returns an empty list when the plan has none -- a gravity-flow geometry
    simply omits the whole ``Geometry/Pump Stations`` group, so an empty result
    is normal and not an error.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        try:
            raw = hdf[f'{_PUMP_GEOM}/Attributes'][()]
        except KeyError:
            return []
        return [_decode(r['Name']) for r in raw]


def read_pump_station(hdf_path: str, station: str) -> PumpStation:
    """
    Read results and connectivity for one pump station.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    station : str
        Station name, as returned by list_pump_stations().

    Returns
    -------
    PumpStation

    Raises
    ------
    KeyError
        If the station or its results are absent.

    Notes
    -----
    The result columns are NOT in a fixed order or count: each station's
    ``Structure Variables`` dataset carries a ``Variable_Unit`` attribute naming
    every column, and different stations have different widths (on the Hillside
    model, one station writes 11 columns and the other 5). This reader maps
    columns by that attribute; never index them positionally.

    Per-pump results are reported PER GROUP, not per individual pump. A group of
    three pumps has one flow column and a `pumps_on` count running 0..3. The
    individual pumps appear in ``PumpGroup.pumps`` for their on/off trigger
    elevations.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        try:
            raw = hdf[f'{_PUMP_GEOM}/Attributes'][()]
        except KeyError:
            raise KeyError(
                f"No pump stations in {hdf_path} -- this plan's geometry has "
                f"no '{_PUMP_GEOM}' group (a gravity-flow geometry omits it)."
            ) from None
        names = [_decode(r['Name']) for r in raw]
        try:
            st_idx = names.index(station)
        except ValueError:
            raise KeyError(
                f"Pump station '{station}' not found in {hdf_path}. "
                f"Available: {names}"
            ) from None
        row = raw[st_idx]

        ts_path = f'{_PUMP_TS_BASE}/{station}/Structure Variables'
        if ts_path not in hdf:
            raise KeyError(
                f"No results for pump station '{station}' at {ts_path}"
            )
        ds = hdf[ts_path]
        values = ds[()].astype(np.float64)
        var_unit = ds.attrs.get('Variable_Unit')
        if var_unit is None:
            raise KeyError(
                f"Pump station '{station}' results carry no 'Variable_Unit' "
                f"attribute; column meanings cannot be resolved safely."
            )
        cols = [(_decode(v[0]), _decode(v[1])) for v in var_unit]

        timestamps = np.array([_decode(t) for t in hdf[_TS_DATES][()]])

        # Group geometry: which groups belong to this station, and their pumps.
        groups_meta, pumps_meta = [], []
        gpath = f'{_PUMP_GEOM}/Pump Groups/Attributes'
        if gpath in hdf:
            groups_meta = hdf[gpath][()]
        ppath = f'{_PUMP_GEOM}/Pump Groups/Pumps/Attributes'
        if ppath in hdf:
            pumps_meta = hdf[ppath][()]

    def column(label: str, unit: str):
        for i, (nm, un) in enumerate(cols):
            if nm == label and un == unit:
                return values[:, i]
        return None

    total = column('Flow', 'cfs')
    if total is None:
        total = np.zeros(len(timestamps), dtype=np.float64)
    hw = column('Stage HW', 'ft')
    tw = column('Stage TW', 'ft')
    zeros = np.zeros(len(timestamps), dtype=np.float64)

    # Pumps for a group are matched by the group's row index in the global table.
    pumps_by_group: dict = {}
    for pr in pumps_meta:
        pumps_by_group.setdefault(int(pr['Pump Group ID']), []).append(
            Pump(name=_decode(pr['Name']),
                 ws_on=float(pr['WS On']), ws_off=float(pr['WS Off'])))

    groups = []
    for g_idx, g in enumerate(groups_meta):
        if int(g['Pump Station ID']) != st_idx:
            continue
        gname = _decode(g['Name'])
        gflow = column(gname, 'cfs')
        gon = column(gname, 'Pumps on')
        groups.append(PumpGroup(
            name=gname,
            flow=gflow if gflow is not None else zeros.copy(),
            pumps_on=gon if gon is not None else zeros.copy(),
            pumps=pumps_by_group.get(g_idx, []),
        ))

    in_net, in_node = _split_network_node(row['Inlet Pipe Node'])
    out_net, out_node = _split_network_node(row['Outlet Pipe Node'])
    in_area = _decode(row['Inlet SA/2D']) or None
    out_area = _decode(row['Outlet SA/2D']) or None

    return PumpStation(
        name=station,
        timestamps=timestamps,
        flow=total,
        stage_hw=hw if hw is not None else zeros.copy(),
        stage_tw=tw if tw is not None else zeros.copy(),
        groups=groups,
        inlet_network=in_net,
        inlet_node=in_node,
        outlet_network=out_net,
        outlet_node=out_node,
        inlet_area=in_area,
        outlet_area=out_area,
        highest_pump_line_elev=float(row['Highest Pump Line Elevation']),
    )


def pump_stations_for_node(hdf_path: str, node: str,
                           network: str = None) -> list[str]:
    """
    Return the names of pump stations whose inlet or outlet is `node`.

    Convenience for the common question "which pump serves this manhole?".
    `network` narrows the match when several pipe networks reuse a node name.
    """
    out = []
    for name in list_pump_stations(hdf_path):
        ps = read_pump_station(hdf_path, name)
        for net, nd in ((ps.inlet_network, ps.inlet_node),
                        (ps.outlet_network, ps.outlet_node)):
            if nd == node and (network is None or net == network):
                out.append(name)
                break
    return out


# ---------------------------
# Pipe network routing
# ---------------------------

class AmbiguousRoute(ValueError):
    """Raised when more than one downstream branch reaches the destination."""


def read_node_points(hdf_path: str) -> dict:
    """
    Return {node name: (x, y)} for every pipe node in the file.

    Used to measure the real distance across a physical break when joining path
    segments; also handy for placing nodes in GIS.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        attrs = hdf['Geometry/Pipe Nodes/Attributes'][()]
        pts = hdf['Geometry/Pipe Nodes/Points'][()]
    return {_decode(r['Name']): (float(pts[i][0]), float(pts[i][1]))
            for i, r in enumerate(attrs)}


def peak_flow_resolver(hdf_path: str, network: PipeNetwork):
    """
    Build a fork resolver that prefers the branch with the greater peak |flow|.

    Returns a callable suitable for `trace_path(..., resolver=...)`.

    WARNING -- this is PLAN-DEPENDENT. Flow distribution changes between plans,
    so the same fork can resolve differently in different runs: on the Hillside
    model, node BedJ293 prefers BedC195 in six of eight plans but BedC193 in the
    two 10-year pumped runs. Trace the path ONCE with this resolver and reuse the
    resulting ConduitPath for every plan you compare -- re-tracing per plan can
    give two plans different station axes without any error being raised.
    """
    def resolve(node: str, candidates: list) -> str:
        flows = {c: float(np.abs(
            read_conduit_profile(hdf_path, network, c, 'Maximum').flow).max())
            for c in candidates}
        best = max(flows, key=flows.get)
        detail = ', '.join(f'{c} ({flows[c]:.2f})' for c in candidates
                           if c != best)
        return best, (f'{node}: took {best} (peak |Q| {flows[best]:.2f} cfs) '
                      f'over {detail}')
    return resolve


def _reaches(network: PipeNetwork, start_node: str, target: str) -> bool:
    """Is `target` reachable downstream from `start_node`?"""
    seen, stack = {start_node}, [start_node]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        for c in network.downstream_of.get(cur, []):
            nxt = network.conduits[c].ds_node
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def trace_path(network: PipeNetwork, start: str, end: str,
               via: list = None, resolver=None) -> ConduitPath:
    """
    Trace the downstream route from `start` to `end` through a pipe network.

    Branches that cannot reach the destination are discarded automatically, so a
    node with several outgoing conduits is only treated as ambiguous when more
    than one of them genuinely leads to `end`.

    Parameters
    ----------
    network : PipeNetwork
        From read_pipe_network().
    start, end : str
        Node names. Both must be in the network.
    via : list[str], optional
        Intermediate nodes the route must pass through, in order. The usual way
        to pin a braided reach without needing results.
    resolver : callable, optional
        ``resolver(node, candidates) -> (chosen, note)`` used when more than one
        branch reaches the destination. See `peak_flow_resolver`, and read its
        warning about plan dependence. Without a resolver a genuine fork raises
        AmbiguousRoute rather than guessing.

    Returns
    -------
    ConduitPath
        Route only -- no results. Reuse one path across every plan you compare.

    Raises
    ------
    ValueError
        If a node is unknown, or the destination is unreachable.
    AmbiguousRoute
        If a fork has several destination-reaching branches and no resolver.
    """
    for node in [start, end] + list(via or []):
        if node not in network.nodes:
            raise ValueError(
                f"Node '{node}' is not in pipe network '{network.name}'")

    waypoints = [start] + list(via or []) + [end]
    conduits, nodes, forks = [], [start], []

    for leg_start, leg_end in zip(waypoints, waypoints[1:]):
        if leg_start == leg_end:
            continue
        if not _reaches(network, leg_start, leg_end):
            raise ValueError(
                f"No downstream route from '{leg_start}' to '{leg_end}' in "
                f"network '{network.name}'. Check the order of `via` nodes, or "
                f"split the path into segments if a physical break separates "
                f"them.")
        cur, guard = leg_start, 0
        while cur != leg_end:
            guard += 1
            if guard > len(network.conduits) + 1:
                raise ValueError(
                    f"Route {leg_start} -> {leg_end} did not terminate; the "
                    f"network may contain a loop.")
            outs = network.downstream_of.get(cur, [])
            live = [c for c in outs
                    if _reaches(network, network.conduits[c].ds_node, leg_end)
                    or network.conduits[c].ds_node == leg_end]
            if not live:
                raise ValueError(
                    f"Route {leg_start} -> {leg_end} dead-ends at '{cur}'.")
            if len(live) == 1:
                chosen = live[0]
            elif resolver is None:
                raise AmbiguousRoute(
                    f"Node '{cur}' has {len(live)} downstream branches that all "
                    f"reach '{leg_end}': {live}. Pass `via=[...]` to pin the "
                    f"route, or a `resolver` such as peak_flow_resolver(). "
                    f"Note that resolving by flow is plan-dependent.")
            else:
                chosen, note = resolver(cur, live)
                forks.append(note)
            conduits.append(chosen)
            cur = network.conduits[chosen].ds_node
            nodes.append(cur)

    return ConduitPath(network=network.name, conduits=conduits, nodes=nodes,
                       segments=[(start, end)], bridges=[], forks=forks)


def join_paths(paths: list, node_points: dict) -> ConduitPath:
    """
    Join several traced paths into one route across physical breaks.

    Use this where the run is hydraulically continuous but has no conduit -- an
    open channel or a surface detention pond between two pipe systems. The
    station axis later advances by the straight-line distance between the
    joining nodes, so it stays a real distance.

    Parameters
    ----------
    paths : list[ConduitPath]
        In downstream order.
    node_points : dict
        From read_node_points().

    Raises
    ------
    ValueError
        If the list is empty, mixes networks, or a joining node has no coordinate.
    """
    if not paths:
        raise ValueError('join_paths requires at least one ConduitPath')
    nets = {p.network for p in paths}
    if len(nets) > 1:
        raise ValueError(f'Cannot join paths from different networks: {nets}')

    out = ConduitPath(network=paths[0].network,
                      conduits=list(paths[0].conduits),
                      nodes=list(paths[0].nodes),
                      segments=list(paths[0].segments),
                      bridges=list(paths[0].bridges),
                      forks=list(paths[0].forks))
    for nxt in paths[1:]:
        a, b = out.nodes[-1], nxt.nodes[0]
        for node in (a, b):
            if node not in node_points:
                raise ValueError(
                    f"Node '{node}' has no coordinate; cannot measure the "
                    f"break between '{a}' and '{b}'.")
        gap = float(np.hypot(node_points[a][0] - node_points[b][0],
                             node_points[a][1] - node_points[b][1]))
        out.bridges.append((a, b, gap))
        out.conduits.extend(nxt.conduits)
        out.nodes.extend(nxt.nodes)
        out.segments.extend(nxt.segments)
        out.forks.extend(nxt.forks)
    return out


def read_path_profile(hdf_path: str, network: PipeNetwork, path: ConduitPath,
                      when: str = 'Maximum') -> PathProfile:
    """
    Read one continuous profile along a ConduitPath.

    Chains read_conduit_profile over the path's conduits onto a single station
    axis, advancing by each conduit's length and by the measured distance across
    any bridged break.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    network : PipeNetwork
        Must be the network the path was traced in, read from THIS plan.
    path : ConduitPath
    when : str, default 'Maximum'
        'Maximum', 'Minimum', or a time-stamp string. See read_conduit_profile
        for the envelope caveats, which apply here unchanged.

    Returns
    -------
    PathProfile

    Raises
    ------
    ValueError
        If the path was traced in a different network, or a conduit on the path
        is missing from this plan -- which is what happens when a gravity-flow
        and a pumped geometry are mixed up, since the pumped geometry drops the
        outfall conduits.
    """
    if path.network != network.name:
        raise ValueError(
            f"Path was traced in network '{path.network}' but was given "
            f"network '{network.name}'")
    missing = [c for c in path.conduits if c not in network.conduit_index]
    if missing:
        raise ValueError(
            f"{len(missing)} conduit(s) on the path are absent from this "
            f"plan's geometry: {missing[:5]}. The two geometries are not the "
            f"same network -- a gravity-flow geometry carries outfall conduits "
            f"that a pumped geometry does not.")

    bridge_at = {a: (b, gap) for a, b, gap in path.bridges}
    station, invert, crown, wse, vel, flow, owner = [], [], [], [], [], [], []
    node_at = {}
    offset = 0.0
    prev_ds = None

    for cname in path.conduits:
        pr = read_conduit_profile(hdf_path, network, cname, when)
        if prev_ds is not None and prev_ds in bridge_at:
            offset += bridge_at[prev_ds][1]
        node_at[len(station)] = pr.us_node
        station.extend(offset + pr.station)
        invert.extend(pr.invert)
        crown.extend(pr.invert + pr.rise)
        wse.extend(pr.wse)
        vel.extend(pr.velocity)
        flow.extend(pr.flow)
        owner.extend([cname] * len(pr.station))
        offset += pr.length
        prev_ds = pr.ds_node
        si = pr.si_units

    if station:
        node_at[len(station) - 1] = path.nodes[-1]

    return PathProfile(
        path=path,
        when=when,
        station=np.asarray(station, dtype=np.float64),
        invert=np.asarray(invert, dtype=np.float64),
        crown=np.asarray(crown, dtype=np.float64),
        wse=np.asarray(wse, dtype=np.float64),
        velocity=np.asarray(vel, dtype=np.float64),
        flow=np.asarray(flow, dtype=np.float64),
        conduit_of=np.asarray(owner, dtype=object),
        node_at=node_at,
        total_length=offset,
        si_units=bool(si) if station else False,
    )


# ---------------------------
# Pump curves
# ---------------------------

def read_pump_curves(hdf_path: str, station: str) -> dict:
    """
    Read the head/flow curve for every pump group of one station.

    Parameters
    ----------
    hdf_path : str
        Path to a plan or geometry HDF containing pump stations.
    station : str
        Station name, as returned by list_pump_stations().

    Returns
    -------
    dict[str, PumpCurve]
        Keyed by pump group name, matching PumpGroup.name and the results
        column labels.

    Raises
    ------
    KeyError
        If the station or the curve datasets are absent.

    Notes
    -----
    RAS stores these under `Pump Groups/Efficiency Curves Info` (per group
    `[start, count]` into the values array) and `.../Efficiency Curves Values`
    (`(N, 2)`: column 0 = static head ascending, column 1 = delivered flow
    descending). The "Efficiency" name is RAS's; the content is the pump curve.

    **The tabulated flow is PER PUMP.** A group of three pumps sharing one curve
    delivers three times that flow with all three running -- verified against
    results, where `group flow / curve(head)` equals the `pumps_on` count exactly.
    `PumpCurve.n_pumps` carries the count and `group_capacity()` applies it.

    `Efficiency Curves Info` is indexed by the GLOBAL pump-group row, so the
    rows belonging to a station must be selected via `Pump Station ID` -- the
    same global-vs-local distinction that applies to pipe network indices.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        try:
            stations = hdf[f'{_PUMP_GEOM}/Attributes'][()]
        except KeyError:
            raise KeyError(
                f"No pump stations in {hdf_path} -- this geometry has no "
                f"'{_PUMP_GEOM}' group."
            ) from None
        names = [_decode(r['Name']) for r in stations]
        try:
            st_idx = names.index(station)
        except ValueError:
            raise KeyError(
                f"Pump station '{station}' not found in {hdf_path}. "
                f"Available: {names}"
            ) from None

        gpath = f'{_PUMP_GEOM}/Pump Groups/Attributes'
        ipath = f'{_PUMP_GEOM}/Pump Groups/Efficiency Curves Info'
        vpath = f'{_PUMP_GEOM}/Pump Groups/Efficiency Curves Values'
        for path in (gpath, ipath, vpath):
            if path not in hdf:
                raise KeyError(f"Pump curve dataset missing: {path}")
        groups = hdf[gpath][()]
        info = hdf[ipath][()]
        values = hdf[vpath][()]

    curves = {}
    for g_idx, g in enumerate(groups):
        if int(g['Pump Station ID']) != st_idx:
            continue
        start, count = int(info[g_idx][0]), int(info[g_idx][1])
        block = values[start:start + count]
        curves[_decode(g['Name'])] = PumpCurve(
            group=_decode(g['Name']),
            head=block[:, 0].astype(np.float64),
            flow=block[:, 1].astype(np.float64),
            n_pumps=int(g['Pumps']),
        )
    return curves


def pump_station_capacity(curves: dict, head) -> tuple:
    """
    Total station capacity with every pump running, at the given head(s).

    This is the denominator for "was outflow limited by the pumps?" -- the
    head-dependent capacity actually available during the run, NOT a nameplate
    or best-head rating. A best-head rating is never experienced by the system
    and so cannot answer that question.

    **Compare this against INFLOW, not against the station's own outflow.** RAS
    delivers exactly `pumps_on x curve(head)`, so station outflow equals this
    total whenever every pump is running and can never exceed it -- testing
    outflow against capacity is very nearly a tautology. The meaningful question
    is whether the water ARRIVING exceeded what the pumps could move, so use the
    inflow at the pump's inlet node (conduit inflow plus surface inlet flow, i.e.
    NodeTimeSeries.flow_in + NodeTimeSeries.inlet_flow).

    Parameters
    ----------
    curves : dict[str, PumpCurve]
        From read_pump_curves().
    head : float or array-like
        Static head. For a station discharging over a fixed pump line this is
        `stage_tw - stage_hw` from read_pump_station().

    Returns
    -------
    (capacity, n_clamped) : (np.ndarray, int)
        `capacity` is the summed flow across all groups, same shape as `head`.
        `n_clamped` counts head values that fell outside at least one group's
        tabulated curve and were clamped to its end rather than extrapolated --
        report it, because it does happen on real models and a silently clamped
        capacity is a quiet under- or over-statement.

    Raises
    ------
    ValueError
        If `curves` is empty.
    """
    if not curves:
        raise ValueError('pump_station_capacity requires at least one PumpCurve')
    h = np.atleast_1d(np.asarray(head, dtype=np.float64))
    total = np.zeros_like(h)
    covered = np.ones_like(h, dtype=bool)
    for curve in curves.values():
        total += curve.group_capacity(h)
        covered &= curve.in_range(h)
    return total, int(np.sum(~covered))


# ---------------------------
# Pipe node rims and maxima
# ---------------------------

def read_node_rims(hdf_path: str, source: str = 'override') -> NodeRims:
    """
    Read rim elevations and node types for every pipe node.

    Parameters
    ----------
    hdf_path : str
        Path to a plan or geometry HDF.
    source : {'override', 'terrain'}, default 'override'
        Which rim to place in `NodeRims.rim`.

        * ``'override'`` -- the modeller's `Terrain Elevation Override` where one
          is set, falling back to `Terrain Elevation` elsewhere. This is what RAS
          itself uses: `Invert Elevation + Depth` reproduces the override, not
          the terrain elevation. Default because it is the effective rim.
        * ``'terrain'`` -- the raw DEM-sampled `Terrain Elevation` everywhere,
          ignoring any override.

    Returns
    -------
    NodeRims

    Raises
    ------
    KeyError
        If the node attribute table is absent.
    ValueError
        If `source` is not one of the two accepted values.

    Notes
    -----
    The two sources agree at every node with no override, so on many models they
    are identical and the choice looks academic. Where an override IS set they
    can differ by many feet, and because the override is usually LOWER than the
    DEM value, using `'terrain'` under-counts rim exceedances. Both are offered
    because the choice belongs to the analyst, not the reader.

    Node types are returned unfiltered -- 'Junction', 'Start', 'External',
    'Closed' and 'Culvert Opening' all occur -- so callers can filter afterwards
    rather than having the reader decide what counts.
    """
    if source not in ('override', 'terrain'):
        raise ValueError(
            f"source must be 'override' or 'terrain', got {source!r}")

    with h5py.File(hdf_path, 'r') as hdf:
        if _PIPE_NODES not in hdf:
            raise KeyError(f'No pipe nodes in {hdf_path} ({_PIPE_NODES})')
        raw = hdf[_PIPE_NODES][()]

    names = [_decode(r['Name']) for r in raw]
    types = [_decode(r['Node Type']) for r in raw]
    invert = raw['Invert Elevation'].astype(np.float64)
    depth = raw['Depth'].astype(np.float64)
    terrain = raw['Terrain Elevation'].astype(np.float64)
    override = raw['Terrain Elevation Override'].astype(np.float64)

    if source == 'terrain':
        rim = terrain.copy()
    else:
        rim = np.where(np.isnan(override), terrain, override)

    return NodeRims(names=names, node_types=types, invert=invert, depth=depth,
                    terrain=terrain, override=override, rim=rim, source=source)


def read_node_max_wse(hdf_path: str, network) -> NodeMaxWse:
    """
    Maximum water surface over the run at every node of one pipe network.

    Reads the whole `Nodes/Water Surface` block once and reduces it, rather than
    looping read_node_timeseries per node.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    network : PipeNetwork or str
        From read_pipe_network(), or a bare network name.

    Returns
    -------
    NodeMaxWse

    Raises
    ------
    KeyError
        If the network or its node results are absent.

    Notes
    -----
    Summary Output's `Maximum Water Surface` for a pipe network is per CELL, not
    per node, so it cannot be used here.

    Node result columns are network-LOCAL positions; `PipeNetwork.nodes` maps a
    node name to its column. Do not index the global
    `Geometry/Pipe Nodes/Attributes` table with a results column.
    """
    if hasattr(network, 'nodes'):
        net = network
    else:
        net = read_pipe_network(hdf_path, str(network))

    base = _PIPE_TS_BASE.format(network=net.name)
    ws_path = f'{base}/Nodes/Water Surface'
    with h5py.File(hdf_path, 'r') as hdf:
        if ws_path not in hdf:
            raise KeyError(f'Node water surface results missing: {ws_path}')
        values = hdf[ws_path][()].astype(np.float64)
        timestamps = np.array([_decode(t) for t in hdf[_TS_DATES][()]])

    order = sorted(net.nodes.items(), key=lambda kv: kv[1])
    names = [name for name, _ in order]
    cols = [col for _, col in order]
    block = values[:, cols]

    return NodeMaxWse(
        network=net.name,
        names=names,
        wse=block.max(axis=0),
        time_index=block.argmax(axis=0).astype(np.int64),
        timestamps=timestamps,
    )


# ---------------------------
# Volume accounting
# ---------------------------

def _vol_root(hdf):
    for root in _VOL_ROOTS:
        if root in hdf:
            return root
    return None


def list_volume_accounting(hdf_path: str) -> dict:
    """
    Return {kind: [names]} for every volume-accounting entry in a plan.

    `kind` is RAS's sub-group name with the 'Volume Accounting ' prefix removed,
    e.g. '2D', 'Pipe Networks', '1D'. Returns an empty dict if the plan carries
    no volume accounting.
    """
    out: dict = {}
    with h5py.File(hdf_path, 'r') as hdf:
        root = _vol_root(hdf)
        if root is None:
            return out
        for sub in hdf[root]:
            node = hdf[f'{root}/{sub}']
            if not isinstance(node, h5py.Group):
                continue
            kind = sub.replace('Volume Accounting', '').strip() or sub
            out[kind] = [k for k in node
                         if isinstance(hdf[f'{root}/{sub}/{k}'], h5py.Group)]
    return out


def read_volume_accounting(hdf_path: str, name: str,
                           kind: str = '2D') -> VolumeAccounting:
    """
    Read RAS's volume-accounting summary for one area, pipe network, or reach.

    Parameters
    ----------
    hdf_path : str
        Path to the .p##.hdf file.
    name : str
        Area / network / reach name, as listed by list_volume_accounting().
    kind : str, default '2D'
        '2D', 'Pipe Networks', or '1D'.

    Returns
    -------
    VolumeAccounting

    Raises
    ------
    KeyError
        If the plan has no volume accounting, or the entry is absent.

    Notes
    -----
    These are RAS's own mass-balance totals, stored as group ATTRIBUTES rather
    than datasets. `vol_ending` is the volume standing in the area at the END of
    the run; it is NOT the peak. For a pumped-vs-gravity comparison the two
    answer different questions -- the peak says how bad it got, the ending volume
    says whether the pumps cleared it. A peak must be built separately from the
    cell volume tables; see read_cell_volume_table / interpolate_cell_volume.

    Check `error_percent` before quoting any of these: it is RAS's own closure
    error for the area.
    """
    with h5py.File(hdf_path, 'r') as hdf:
        root = _vol_root(hdf)
        if root is None:
            raise KeyError(
                f'No volume accounting in {hdf_path}; looked at {_VOL_ROOTS}')
        sub = f'Volume Accounting {kind}'
        path = f'{root}/{sub}/{name}'
        if path not in hdf:
            available = list_volume_accounting(hdf_path)
            raise KeyError(
                f"Volume accounting '{kind}' / '{name}' not found in "
                f'{hdf_path}. Available: {available}')
        attrs = dict(hdf[path].attrs)

    def num(key):
        val = attrs.get(key)
        return float('nan') if val is None else float(val)

    return VolumeAccounting(
        kind=kind,
        name=name,
        vol_starting=num('Vol Starting'),
        vol_ending=num('Vol Ending'),
        cum_inflow=num('Cum Inflow'),
        cum_outflow=num('Cum Outflow'),
        error=num('Error'),
        error_percent=num('Error Percent'),
        precip_excess=num('Precip Excess (acre feet)'),
        precip_excess_depth=num('Precip Excess (inches)'),
        units=_decode(attrs.get('Vol Accounting in', b'')) or 'unknown',
    )


# ---------------------------
# 1D steady-flow profile results
# ---------------------------

_STEADY_XS_BASE = (
    "/Results/Steady/Output/Output Blocks/Base Output/"
    "Steady Profiles/Cross Sections"
)
_STEADY_PROFILE_NAMES = (
    "/Results/Steady/Output/Output Blocks/Base Output/"
    "Steady Profiles/Profile Names"
)


_GEOM_XS = "/Geometry/Cross Sections"


def _xs_index_from_attributes(attrs) -> list:
    """(river, reach, station) tuples from a 6.0+ compound Cross Sections/Attributes."""
    return [(_decode(r["River"]), _decode(r["Reach"]), _decode(r["RS"])) for r in attrs]


def read_xs_name_index(hdf, hdf_path: str = "") -> list:
    """
    Return the ``[(river, reach, station), ...]`` index for the geometry cross
    sections in an open HEC-RAS HDF handle, aligned to the results arrays.

    This is version-aware by *structural probing* — it uses whichever known
    layout is actually present, so a new HEC-RAS version that keeps an existing
    schema needs no code change:

    * 5.x flat layout  -> ``Cross Sections/River Names`` / ``Reach Names`` /
      ``River Stations``.
    * 6.0+ compound layout -> ``Cross Sections/Attributes`` (fields ``River``,
      ``Reach``, ``RS``).

    If neither known layout is present, the most recent known layout (compound)
    is assumed and a warning is logged with the detected ``File Version``; if the
    assumed datasets are also absent the underlying ``KeyError`` propagates, so a
    genuinely unknown schema fails loudly rather than returning wrong data.
    """
    # 5.x flat layout
    if f"{_GEOM_XS}/River Names" in hdf:
        rivers = [_decode(v) for v in hdf[f"{_GEOM_XS}/River Names"][()]]
        reaches = [_decode(v) for v in hdf[f"{_GEOM_XS}/Reach Names"][()]]
        stations = [_decode(v) for v in hdf[f"{_GEOM_XS}/River Stations"][()]]
        return list(zip(rivers, reaches, stations))

    # 6.0+ compound layout
    if f"{_GEOM_XS}/Attributes" in hdf:
        return _xs_index_from_attributes(hdf[f"{_GEOM_XS}/Attributes"][()])

    # No known layout matched: assume latest known layout (compound) and warn.
    ver = RasVersion.from_hdf(hdf_path) if hdf_path else None
    logging.warning(
        "Cross Sections name index: no recognised geometry layout in %r "
        "(File Version=%s); assuming latest known (compound 'Attributes') layout.",
        hdf_path, ver,
    )
    return _xs_index_from_attributes(hdf[f"{_GEOM_XS}/Attributes"][()])


def read_steady_profile_wse(hdf_path: str):
    """
    Read per-cross-section water-surface elevations for a 1D steady-flow plan.

    WSE comes from the standalone ``Water Surface`` dataset (shape
    ``(n_profiles, n_xs)``), aligned by index to the geometry cross-section name
    index (see :func:`read_xs_name_index`, which is version-aware across the
    5.x flat and 6.0+ compound geometry layouts).  The misaligned
    ``Cross Section Variables`` WSEL column (5.x) is deliberately not used.

    Parameters
    ----------
    hdf_path : str
        Path to the ``.p##.hdf`` plan results file.

    Returns
    -------
    SteadyProfileResults

    Raises
    ------
    KeyError
        If the steady-profile results datasets are not present (e.g. the plan
        was not run, or is not a steady-flow plan).
    """
    from .model import SteadyProfileResults

    with h5py.File(hdf_path, "r") as hdf:
        profile_names = [_decode(v) for v in hdf[_STEADY_PROFILE_NAMES][()]]
        water_surface = hdf[f"{_STEADY_XS_BASE}/Water Surface"][()].astype(np.float64)
        keys = read_xs_name_index(hdf, hdf_path)

    wse = {}
    for i, key in enumerate(keys):
        wse[key] = water_surface[:, i]

    return SteadyProfileResults(profile_names=profile_names, wse=wse)


# HEC-RAS writes ~3.4e38 (single-precision max) where a value is undefined.
_STEADY_UNDEFINED = 3.0e38


def read_steady_xs_results(hdf_path: str):
    """
    Read every per-cross-section results dataset for a 1D steady-flow plan.

    Loads each ``(n_profiles, n_xs)`` dataset found directly under
    ``.../Steady Profiles/Cross Sections`` and under its ``Additional
    Variables`` subgroup, keyed by its HDF dataset name.  The available names
    are version-dependent (5.0.3 writes four Additional Variables; 7.0 writes
    ~50) so nothing is required to be present beyond the profile names and the
    geometry name index — callers check ``SteadyXsResults.has(name)``.

    The 5.x ``Cross Section Variables`` dataset is excluded: its declared shape
    ``(n_profiles, n_vars, n_xs)`` does not describe its actual record layout,
    so every column read out of it is index-misaligned.  ``Water Surface``,
    ``Flow`` and the Additional Variables are standalone, correctly aligned
    datasets and are what this function reads instead.  The shape filter drops
    it automatically.

    Parameters
    ----------
    hdf_path : str
        Path to the ``.p##.hdf`` plan results file.

    Returns
    -------
    SteadyXsResults

    Raises
    ------
    KeyError
        If the steady-profile results group is not present (e.g. the plan was
        not run, or is not a steady-flow plan).
    """
    from .model import SteadyXsResults

    with h5py.File(hdf_path, "r") as hdf:
        profile_names = [_decode(v) for v in hdf[_STEADY_PROFILE_NAMES][()]]
        keys = read_xs_name_index(hdf, hdf_path)
        shape = (len(profile_names), len(keys))

        base = hdf[_STEADY_XS_BASE]
        groups = [base]
        if "Additional Variables" in base:
            groups.append(base["Additional Variables"])

        values = {}
        for group in groups:
            for name, obj in group.items():
                if not isinstance(obj, h5py.Dataset) or obj.shape != shape:
                    continue
                arr = obj[()].astype(np.float64)
                arr[np.abs(arr) >= _STEADY_UNDEFINED] = np.nan
                values[name] = arr

    return SteadyXsResults(profile_names=profile_names, keys=keys, values=values)
