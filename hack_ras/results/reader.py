# hack_ras/results/reader.py
# Requires: pip install hack_ras[results]
from __future__ import annotations
import re
from pathlib import Path

import h5py
import numpy as np

from .model import (
    AreaGeometry,
    CellVolumeTable,
    ConduitTimeSeries,
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

    import geopandas as gpd
    from shapely.geometry import Polygon

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
        Horizontal footprint area of the cell polygon (ft² or m², matching the
        model's coordinate units). Used for linear extrapolation above the table
        maximum. Obtain from AreaGeometry.polygons[cell_idx].area.

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
    Map each unique cell index to its representative station along a structure.

    For each segment j (0 … N_segs-1):
      midpoint_j = (seg_stations[j] + seg_stations[j+1]) / 2

    The station for a cell = mean of midpoint_j across all segments where
    the cell's index appears in seg_labels.  Returns nan for cells whose
    index does not appear in any segment label.
    """
    n_segs = len(seg_labels)
    midpoints = [
        (float(seg_stations[j]) + float(seg_stations[j + 1])) / 2.0
        for j in range(n_segs)
    ]

    cell_to_mids: dict = {}
    for j, raw in enumerate(seg_labels):
        label = _decode(raw)
        try:
            cell_id = int(label)
        except ValueError:
            continue
        cell_to_mids.setdefault(cell_id, []).append(midpoints[j])

    return {
        int(idx): float(np.mean(cell_to_mids[int(idx)]))
        if int(idx) in cell_to_mids
        else float("nan")
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
        [Sa2dCell(cell_idx=int(hw_indices[i]), station=hw_map[int(hw_indices[i])], wse=hw_wse[:, i])
         for i in range(len(hw_indices))],
        key=lambda c: c.station,
    )
    tw_cells = sorted(
        [Sa2dCell(cell_idx=int(tw_indices[i]), station=tw_map[int(tw_indices[i])], wse=tw_wse[:, i])
         for i in range(len(tw_indices))],
        key=lambda c: c.station,
    )

    return Sa2dConnection(
        name=connection,
        timestamps=timestamps,
        hw_cells=hw_cells,
        tw_cells=tw_cells,
    )


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
