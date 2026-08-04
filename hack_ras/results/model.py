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


@dataclass
class Sa2dCell:
    """
    One cell on the HW or TW side of an SA 2D Area Conn structure.

    Attributes
    ----------
    cell_idx : int
        Index of the cell in the 2D flow area mesh.
    station : float
        Representative center station along the structure (model coordinate units).
        Computed as the mean of segment midpoint stations for all segments
        where this cell appears in HW TW Segments.
    station_start : float
        Minimum face-point station bounding the segments this cell occupies.
    station_end : float
        Maximum face-point station bounding the segments this cell occupies.
    wse : np.ndarray, shape (T,), dtype float64
        WSE time series for this cell.
    """
    cell_idx: int
    station: float
    wse: np.ndarray
    station_start: float = float("nan")
    station_end: float = float("nan")


@dataclass
class Sa2dConnection:
    """
    HW and TW cell time series for one SA 2D Area Conn (levee / lateral structure).

    SA 2D Area Conn features have no Summary Output in the HDF.  Use
    read_sa2d_areas() + read_summary_max() to look up time-of-max at sub-step
    accuracy from the connected 2D flow area's Summary Output.

    Attributes
    ----------
    name : str
        Connection name (HDF group key).
    timestamps : np.ndarray, shape (T,), dtype str
        HEC-RAS time-date stamp strings, e.g. '01JAN2025 00:30:00'.
    hw_cells : list[Sa2dCell]
        Cells on the headwater/upstream side, sorted by station ascending.
    tw_cells : list[Sa2dCell]
        Cells on the tailwater/downstream side, sorted by station ascending.
    """
    name: str
    timestamps: np.ndarray
    hw_cells: list
    tw_cells: list


@dataclass
class SteadyProfileResults:
    """
    Per-cross-section water-surface elevations for a 1D steady-flow plan,
    read from the ``/Results/Steady`` block of a plan HDF5 file.

    Alignment note
    --------------
    WSE is read from the standalone ``.../Steady Profiles/Cross Sections/
    Water Surface`` dataset, which is indexed in the same order as the
    ``/Geometry/Cross Sections`` River/Reach/Station name arrays.  The
    ``Cross Section Variables`` dataset's WSEL column is *not* used: its values
    are index-misaligned with geometry and do not match the RAS GUI output.

    Attributes
    ----------
    profile_names : list[str]
        Steady profile names in HDF order, e.g. ['100-year', 'Floodway', ...].
    wse : dict[tuple[str, str, str], np.ndarray]
        Maps (river, reach, station) -> array of WSE, one value per profile
        (same order as ``profile_names``).  River/reach/station keys are
        stripped of surrounding whitespace.
    """
    profile_names: list
    wse: dict

    def profile_index(self, profile: str) -> int:
        """Return the index of *profile* in ``profile_names`` (raises if absent)."""
        return self.profile_names.index(profile)

    def get_wse(self, river: str, reach: str, station: str, profile: str):
        """
        WSE for one cross section on one profile, or ``None`` if that
        cross section has no result entry.
        """
        arr = self.wse.get((river.strip(), reach.strip(), str(station).strip()))
        if arr is None:
            return None
        return float(arr[self.profile_index(profile)])


from ..utils.names import normalize_name as _normalize_name


def _station_value(station) -> float | None:
    """Numeric value of a HEC-RAS river station, or None if not numeric.

    Interpolated cross sections carry a trailing ``*`` (e.g. ``'9262.07*'``).
    """
    try:
        return float(str(station).strip().rstrip('*'))
    except ValueError:
        return None


@dataclass
class SteadyXsResults:
    """
    Per-cross-section, per-profile results for a 1D steady-flow plan, read from
    the ``/Results/Steady`` block of a plan HDF5 file.

    Every ``(n_profiles, n_xs)`` dataset found directly under
    ``.../Steady Profiles/Cross Sections`` and its ``Additional Variables``
    subgroup is loaded and keyed by its HDF dataset name, e.g. ``'Water
    Surface'``, ``'Flow'``, ``'Area Flow Total'``, ``'Top Width Total'``.  Which
    names are present depends on the HEC-RAS version that wrote the file (5.0.3
    writes four Additional Variables; 7.0 writes ~50, including
    ``'Velocity Total'``) — always check :meth:`has` before reading a name.

    Alignment note
    --------------
    These datasets are indexed in the same order as the ``/Geometry/Cross
    Sections`` name arrays (see ``read_xs_name_index``).  The 5.x
    ``Cross Section Variables`` dataset is deliberately NOT read: its declared
    shape does not match its actual record layout, so its columns (WSEL, Q, Vel
    Total, ...) are index-misaligned and do not match the RAS GUI output.  Use
    :meth:`mean_velocity` rather than that dataset's ``Vel Total`` column.

    Attributes
    ----------
    profile_names : list[str]
        Steady profile names in HDF order, e.g. ['100-year', 'Floodway', ...].
    keys : list[tuple[str, str, str]]
        ``(river, reach, station)`` for each results column, in HDF order.
        All three parts are stripped of surrounding whitespace.
    values : dict[str, np.ndarray]
        Dataset name -> ``(n_profiles, n_xs)`` float64 array.  HEC-RAS's
        undefined-value sentinel (~3.4e38) is converted to ``nan``.
    """
    profile_names: list
    keys: list
    values: dict

    def __post_init__(self):
        self._index = {k: i for i, k in enumerate(self.keys)}

    def profile_index(self, profile: str) -> int:
        """Return the index of *profile* in ``profile_names`` (raises if absent)."""
        return self.profile_names.index(profile)

    def variable_names(self) -> list:
        """Dataset names available in this file, sorted."""
        return sorted(self.values)

    def has(self, variable: str) -> bool:
        """True if *variable* was present in the results file."""
        return variable in self.values

    def find_keys(self, river: str, station, reach: str = None) -> list:
        """
        Every ``(river, reach, station)`` key matching *river* and *station*,
        comparing stations numerically so ``27962`` matches ``'27962'``.

        With *reach* omitted this infers the reach for a river/station pair that
        carries no reach name (e.g. a floodway data table row).  HEC-RAS permits
        the same station on two reaches of one river, so more than one hit is
        possible and means the pair is genuinely ambiguous — pass *reach* to
        resolve it.

        River and reach names are matched case-insensitively and with internal
        whitespace collapsed, so ``'Upper Reach B'`` finds RAS's
        ``'Upper Reach  B'``.
        """
        want_river = _normalize_name(river)
        # A blank or whitespace-only reach means "not supplied", not "no match".
        want_reach = _normalize_name(reach) if reach is not None else ""
        want_reach = want_reach or None
        want_sta = _station_value(station)
        want_txt = str(station).strip()
        hits = []
        for key in self.keys:
            if _normalize_name(key[0]) != want_river:
                continue
            if want_reach is not None and _normalize_name(key[1]) != want_reach:
                continue
            if want_sta is None:
                if key[2] == want_txt:
                    hits.append(key)
                continue
            have = _station_value(key[2])
            if have is not None and abs(have - want_sta) <= 1e-4:
                hits.append(key)
        return hits

    def reaches_of(self, river: str) -> list:
        """Reach names on *river*, in HDF order — for error messages."""
        want = _normalize_name(river)
        out = []
        for key in self.keys:
            if _normalize_name(key[0]) == want and key[1] not in out:
                out.append(key[1])
        return out

    def get(self, variable: str, river: str, reach: str, station: str,
            profile: str):
        """
        One value for one cross section on one profile, or ``None`` if the cross
        section has no results column.

        Raises ``KeyError`` if *variable* is not present in the file.
        """
        idx = self._index.get(
            (str(river).strip(), str(reach).strip(), str(station).strip()))
        if idx is None:
            return None
        return float(self.values[variable][self.profile_index(profile), idx])

    def mean_velocity(self, river: str, reach: str, station: str,
                      profile: str):
        """
        Cross-section average velocity (ft/s), or ``None`` if the cross section
        has no results column or its flow area is zero.

        Uses ``'Velocity Total'`` when the file has it (HEC-RAS 6.0+), otherwise
        derives it as ``Flow / Area Flow Total`` — the same quantity, and the
        only route available in 5.x files, where the ``Cross Section Variables``
        ``Vel Total`` column is unusable (see the class docstring).
        """
        if self.has("Velocity Total"):
            return self.get("Velocity Total", river, reach, station, profile)
        flow = self.get("Flow", river, reach, station, profile)
        area = self.get("Area Flow Total", river, reach, station, profile)
        if flow is None or area is None or not area:
            return None
        return flow / area
