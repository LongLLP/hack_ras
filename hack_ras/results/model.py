# hack_ras/results/model.py
from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PlanMetadata:
    """Title and geometry reference parsed from a HEC-RAS plan text file (.p##)."""
    geom_id: str      # e.g. 'g01'
    plan_title: str


@dataclass
class AreaGeometry:
    """
    Cell geometry for one HEC-RAS 2D flow area, read from a plan HDF5 file.

    Attributes
    ----------
    cell_centers : np.ndarray, shape (N, 2)
        XY coordinates of each cell centre.
    min_elevations : np.ndarray, shape (N,)
        Minimum terrain elevation per cell. NaN for perimeter dummy cells.
    polygons : list[shapely.Polygon | None], length N
        Cell polygon for each cell; None if fewer than 3 face points.
    boundary : shapely.Polygon
        Outer perimeter of the 2D flow area.
    cell_gdf : geopandas.GeoDataFrame
        Rows for non-dummy cells only (NaN min_elev excluded).
        Columns: 'cell_idx' (int), 'geometry' (Polygon).
        Has a spatial index for fast intersection queries.
    """
    cell_centers: np.ndarray
    min_elevations: np.ndarray
    polygons: list
    boundary: object
    cell_gdf: object


@dataclass
class CellVolumeTable:
    """
    Volume-elevation lookup table for all cells in one 2D flow area.

    Attributes
    ----------
    info : np.ndarray, shape (N_cells, 2), dtype int32
        Per-cell [start_index, count] into the values array.
    values : np.ndarray, shape (total_pairs, 2), dtype float32
        Packed elevation-volume pairs: column 0 = elevation, column 1 = volume.
    """
    info:   np.ndarray
    values: np.ndarray


@dataclass
class PipeNode:
    """A pipe network junction node from Geometry/Pipe Nodes/Attributes."""
    name: str
    system_name: str


@dataclass
class PipeConduit:
    """A pipe from Geometry/Pipe Conduits/Attributes."""
    name: str
    us_node: str
    ds_node: str


@dataclass
class PipeNetwork:
    """
    Geometry and index maps for one HEC-RAS pipe network.

    Attributes
    ----------
    name : str
        Network name (group key under Geometry/Pipe Networks/).
    nodes : dict[str, int]
        node_name -> results-column index.
    conduits : dict[str, PipeConduit]
        conduit_name -> PipeConduit.
    conduit_index : dict[str, int]
        conduit_name -> results-column index.
    upstream_of : dict[str, list[str]]
        node_name -> conduit names whose ds_node == this node.
        Used to sum Pipe Flow DS into node flow_in.
    downstream_of : dict[str, list[str]]
        node_name -> conduit names whose us_node == this node.
        Used to sum Pipe Flow US into node flow_out.
    """
    name: str
    nodes: dict
    conduits: dict
    conduit_index: dict
    upstream_of: dict
    downstream_of: dict


@dataclass
class NodeTimeSeries:
    """
    Time-series results for one pipe node.

    Attributes
    ----------
    timestamps : np.ndarray, shape (T,), dtype str
        HEC-RAS time-date stamp strings, e.g. '01Jan2025 00:30:00'.
    depth : np.ndarray, shape (T,), dtype float64
    wse : np.ndarray, shape (T,), dtype float64
    inlet_flow : np.ndarray, shape (T,), dtype float64
        Top + Side Inlet Flow directly from HDF.
    flow_in : np.ndarray, shape (T,), dtype float64
        Sum of Pipe Flow DS for conduits draining into this node.
    flow_out : np.ndarray, shape (T,), dtype float64
        Sum of Pipe Flow US for conduits leaving this node.
    """
    timestamps: np.ndarray
    depth: np.ndarray
    wse: np.ndarray
    inlet_flow: np.ndarray
    flow_in: np.ndarray
    flow_out: np.ndarray


@dataclass
class ConduitTimeSeries:
    """
    Time-series results for one pipe conduit.

    Attributes
    ----------
    timestamps : np.ndarray, shape (T,), dtype str
    flow_us : np.ndarray, shape (T,), dtype float64
    flow_ds : np.ndarray, shape (T,), dtype float64
    vel_us : np.ndarray, shape (T,), dtype float64
    vel_ds : np.ndarray, shape (T,), dtype float64
    """
    timestamps: np.ndarray
    flow_us: np.ndarray
    flow_ds: np.ndarray
    vel_us: np.ndarray
    vel_ds: np.ndarray
