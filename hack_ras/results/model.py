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
        Minimum EFFECTIVE ground elevation per cell — the terrain the model
        computes with, including the terrain layer's modifications (a burned
        channel or a culvert inlet/outlet invert lowers it, and that lowered
        value is the real one). NaN for perimeter dummy cells. See
        `CellVolumeTable.top_elevations` for the high-side counterpart and for
        why these cannot be reproduced by sampling the parent DEM.
    polygons : list[shapely.Polygon | None], length N
        Cell polygon for each cell; None if the cell has fewer than 3 faces.
        Follows the 2D flow area perimeter exactly where the plan HDF carries the
        perimeter face datasets (RAS 7.0); see reader._perimeter_polygons.
    plan_areas : np.ndarray, shape (N,)
        Horizontal plan area of each cell, from `Cells Surface Area` — RAS's own
        number, and the one to use as `interpolate_cell_volume`'s
        `cell_plan_area`. Prefer it over `polygons[i].area`: the two agree to
        float32 round-off, but this one needs no reconstruction and stays right
        on the fallback path. Numerically zero (~1e-12, sometimes negative) for
        perimeter dummy cells.
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
    plan_areas: np.ndarray
    boundary: object
    cell_gdf: object


@dataclass
class FaceGeometry:
    """
    Face geometry and roughness for one HEC-RAS 2D flow area.

    A face is the shared edge between two cells, and it is where RAS evaluates
    conveyance: the Manning's n here is what the momentum equation uses between
    the two cells, independent of either cell-centre value.  A land-cover edit
    that misses every cell centre can still move face n — measured live on
    `NKC_Hillside_Levee` g07 vs g09, where `Cells Center Manning's n` was
    bit-identical on all 4107 cells while 27 faces changed, some by more than 2x.

    Attributes
    ----------
    cell_indexes : np.ndarray, shape (F, 2), dtype int32
        The two cells sharing each face, as local cell indices.  A face on the
        mesh perimeter names a perimeter dummy cell on one side rather than a
        negative index.
    facepoint_indexes : np.ndarray, shape (F, 2), dtype int32
        The face's two end points, as indices into `FacePoints Coordinate`.
    mannings_n : np.ndarray, shape (F,), float64
        Manning's n per face, from column 3 of `Faces Area Elevation Values`.
        NaN where a face carries no property table.  RAS stores this per
        elevation step, but it has been constant within a face on every mesh
        measured (7792 faces across Hillside g07/g09, plus the test fixture);
        `read_face_geometry` takes the lowest-elevation row and warns if a face
        ever varies.
    normals : np.ndarray, shape (F, 2), float64
        Face unit normal, i.e. the direction flow through the face travels.
    lengths : np.ndarray, shape (F,), float64
        Face length, from `Faces NormalUnitVector and Length` column 2.
    polygons : list[shapely.Polygon | None], length F
        Dual ("diamond") polygon per face: the ring
        ``[cell_L centre, face point A, cell_R centre, face point B]``, with the
        face itself as the diagonal rather than an edge.  This is the face's
        control volume, and the polygons tile the mesh: measured on Hillside g07,
        zero overlap and within 0.007% of the summed `Cells Surface Area` (the
        residual is perimeter faces that bend, whose diagonal cuts the bend off).
        None where the ring is degenerate.
    """
    cell_indexes:      np.ndarray
    facepoint_indexes: np.ndarray
    mannings_n:        np.ndarray
    normals:           np.ndarray
    lengths:           np.ndarray
    polygons:          list


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

    @property
    def top_elevations(self) -> np.ndarray:
        """Highest EFFECTIVE ground elevation in each cell, shape (N_cells,).

        The counterpart to ``AreaGeometry.min_elevations`` (the lowest).  A cell
        with an empty table reads `nan`.

        "Effective" means the terrain the model actually computes with: the
        RAS Mapper terrain layer the geometry names, **with its modifications
        applied** — burned-in piers, channels, culvert inlet/outlet inverts.
        That is the intended reading, not a defect.  Terrain modifications are
        how HEC-RAS lets a modeller override the underlying raster where a
        hydraulically significant feature is known not to be in it, so the
        modified surface is the design surface; the source DEM is a means to an
        end.  The same applies to ``min_elevations`` — a burned channel or
        culvert invert moves it, and that lowered value is the real one.

        Confirmed against the RAS Mapper GUI: LAX_River_2D p19 cell 12432
        reports a maximum elevation of 649.658 in RAS Mapper's own
        volume-elevation table, which this property reproduces exactly
        (649.6576).  That cell sits under `RR6_CPKC_Overflo`, where `Pier 31` /
        `Pier 32` are burned in at 649.70.

        The practical consequence is that **you cannot reproduce these values by
        sampling the parent .tif**.  Over 376 cells of that model clear of every
        modification the two agree (mean +0.01 ft from the DEM maximum inside
        the cell polygon, p99 of \\|error\\| 0.88 ft); over 24 cells carrying a
        modification or a structure the table runs higher, and only ever higher
        — mean +4.40 ft, worst +26.73 ft at a Gillette St pier burned near
        669 ft where the bare DEM tops out around 642 ft.  Sample the terrain
        clone, or use these values, but do not cross-check one against the
        other's source.

        This is terrain, not structure geometry: the table follows the burned
        ground, not a bridge's chords.  `Brdg2_GRST`'s footprint cell 12660 tops
        out at 655.50 while its ``2DBR Cells`` `High Chord` is 682.0.
        """
        start = self.info[:, 0].astype(np.int64)
        count = self.info[:, 1].astype(np.int64)
        out = np.full(len(self.info), np.nan, dtype=np.float64)
        ok = count > 0
        out[ok] = self.values[start[ok] + count[ok] - 1, 0]
        return out


@dataclass
class BridgeCells:
    """The mesh footprint of one ``Bridge Opening`` connection.

    Read from the flat ``Geometry/Structures/2DBR *`` tables, filtered to a
    single structure.  RAS's own structured dtypes are kept rather than renamed,
    so ``footprint.dtype.names`` etc. stay self-describing.

    Attributes
    ----------
    connection : str
        Connection name, as it appears in ``Structures/Attributes``.
    footprint : np.ndarray
        Cells inside the bridge footprint — the deck band. Fields
        ``Cell Index``, ``High Chord``, ``Low Chord``.
    us, ds : np.ndarray
        Headwater / tailwater cells. Fields ``Cell Index``, ``Station Start``,
        ``Station End`` — the stationing a bridge-mode results group lacks.
    """
    connection: str
    footprint: np.ndarray
    us:         np.ndarray
    ds:         np.ndarray

    @property
    def cells(self) -> np.ndarray:
        """Footprint cell indices, shape (N,) int."""
        return self.footprint["Cell Index"]

    @property
    def high_chord(self) -> np.ndarray:
        """Per-cell deck high chord, shape (N,)."""
        return self.footprint["High Chord"]

    @property
    def low_chord(self) -> np.ndarray:
        """Per-cell deck low chord, shape (N,)."""
        return self.footprint["Low Chord"]

    def stations(self, side: str = "us") -> dict:
        """Map cell index -> (station_start, station_center, station_end).

        ``side`` is ``'us'`` or ``'ds'``.  Center is the midpoint of the cell's
        station range, matching how ``Sa2dCell.station`` is defined for a
        weir-mode connection (mean of its segment midpoints).  A cell listed
        more than once is merged to its outer bounds.
        """
        if side not in ("us", "ds"):
            raise ValueError(f"side must be 'us' or 'ds', got {side!r}")
        rows = self.us if side == "us" else self.ds
        bounds: dict = {}
        for r in rows:
            cid = int(r["Cell Index"])
            lo, hi = float(r["Station Start"]), float(r["Station End"])
            if cid in bounds:
                lo = min(lo, bounds[cid][0])
                hi = max(hi, bounds[cid][1])
            bounds[cid] = (lo, hi)
        return {c: (lo, (lo + hi) / 2.0, hi) for c, (lo, hi) in bounds.items()}


@dataclass
class CulvertGroupResults:
    """Time series for ONE culvert group of a connection.

    RAS reports a culvert connection's flow twice: summed as
    ``Total Culvert Flow`` in ``Structure Variables``, and split per group under
    ``Culvert Groups/<name>``.  This is the split.  ``name`` is RAS's group key
    (``'Culvert #1'``, …), the same string the geometry side uses in
    ``Geometry/Structures/Culvert Groups/Attributes['Name']``, so these join
    straight onto ``geometry.culverts.read_culverts()`` output.

    Attributes
    ----------
    name : str
        Culvert group name.
    timestamps : np.ndarray, shape (T,)
        Output-interval time stamps.
    columns : tuple[str, ...]
        Column names as RAS labelled them, from the dataset's ``Variable_Unit``.
    values : dict[str, np.ndarray]
        Each column as a (T,) float64 array, keyed by its RAS name.
    """
    name:       str
    timestamps: np.ndarray
    columns:    tuple
    values:     dict

    @property
    def flow(self) -> np.ndarray:
        """Culvert flow, (T,) — the group's own share, not the connection total."""
        return self.values["Culvert Flow"]

    @property
    def stage_hw(self) -> np.ndarray:
        return self.values["Stage HW"]

    @property
    def stage_tw(self) -> np.ndarray:
        return self.values["Stage TW"]


@dataclass
class PipeNode:
    """A pipe network junction node from Geometry/Pipe Nodes/Attributes."""
    name: str
    system_name: str


@dataclass
class PipeConduit:
    """
    A pipe from Geometry/Pipe Conduits/Attributes.

    `length` is that table's `Conduit Length`. It is carried here so a network's
    total length -- part of `PipeNetwork.fingerprint()` -- costs no extra HDF
    read; `ConduitProfile` remains the route to a conduit's full geometry.
    """
    name: str
    us_node: str
    ds_node: str
    length: float = float('nan')


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

    def fingerprint(self) -> dict:
        """
        A small, comparable summary of this network's shape.

        `{'network', 'nodes', 'conduits', 'total_length_ft'}`. Recorded alongside
        a serialized ConduitPath so a stored route can say which network it was
        accepted against -- a paired gravity / pumped geometry differs in all
        three numbers, and so does an edited mesh.

        Pure; no HDF read. `total_length_ft` is nan if any conduit length is.
        """
        return {
            'network': self.name,
            'nodes': len(self.nodes),
            'conduits': len(self.conduits),
            'total_length_ft': float(
                sum(c.length for c in self.conduits.values())),
        }


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
class Pump:
    """One physical pump inside a pump group (Geometry/.../Pumps/Attributes)."""
    name: str
    ws_on: float
    ws_off: float


@dataclass
class PumpGroup:
    """
    One pump group of a pump station, with its results time series.

    RAS reports flow and an on-count PER GROUP, not per individual pump: a group
    of three pumps has one flow column and a `pumps_on` count that runs 0..3.
    The individual pumps are carried in `pumps` for their trigger elevations.

    Attributes
    ----------
    name : str
        Group name as it appears in both the geometry table and the results
        Variable_Unit attribute.
    flow : np.ndarray, shape (T,), dtype float64
    pumps_on : np.ndarray, shape (T,), dtype float64
        Number of pumps running in this group at each output step.
    pumps : list[Pump]
    """
    name: str
    flow: np.ndarray
    pumps_on: np.ndarray
    pumps: list = field(default_factory=list)

    @property
    def n_pumps(self) -> int:
        return len(self.pumps)


@dataclass
class PumpStation:
    """
    Results and connectivity for one HEC-RAS pump station.

    Attributes
    ----------
    name : str
    timestamps : np.ndarray, shape (T,), dtype str
    flow : np.ndarray, shape (T,), dtype float64
        Total station flow.
    stage_hw, stage_tw : np.ndarray, shape (T,), dtype float64
        Headwater (wet well) and tailwater stage.
    groups : list[PumpGroup]
    inlet_network, inlet_node : str or None
        Pipe network and node the station draws FROM, parsed from the geometry
        table's 'Base [J314]' form. None when the station is not tied to a pipe
        node (it may be tied to a 2D area or a 1D reach instead).
    outlet_network, outlet_node : str or None
        Where it discharges to, same parsing.
    inlet_area, outlet_area : str or None
        2D flow area / storage area equivalents, when used instead of a node.
    highest_pump_line_elev : float
    """
    name: str
    timestamps: np.ndarray
    flow: np.ndarray
    stage_hw: np.ndarray
    stage_tw: np.ndarray
    groups: list = field(default_factory=list)
    inlet_network: object = None
    inlet_node: object = None
    outlet_network: object = None
    outlet_node: object = None
    inlet_area: object = None
    outlet_area: object = None
    highest_pump_line_elev: float = float('nan')

    @property
    def n_pumps(self) -> int:
        """Total individual pumps across all groups."""
        return sum(g.n_pumps for g in self.groups)

    @property
    def pumps_on(self) -> np.ndarray:
        """Total pumps running across all groups at each output step."""
        if not self.groups:
            return np.zeros(len(self.timestamps), dtype=np.float64)
        return np.sum([g.pumps_on for g in self.groups], axis=0)


@dataclass
class PumpCurve:
    """
    One pump group's head/flow curve, from Efficiency Curves Info/Values.

    **The curve is PER PUMP, not per group.** A group of three pumps sharing one
    curve delivers three times the tabulated flow when all three run. Verified
    against results: `group flow / curve(head)` equals the `pumps_on` count
    exactly (1.00, 2.00, 3.00 with no scatter) on every group of a real model.
    Use `group_capacity` for the whole group; `capacity` is one pump.

    Attributes
    ----------
    group : str
        Pump group name; matches PumpGroup.name and the results column label.
    n_pumps : int
        Number of pumps in the group that share this curve.
    head : np.ndarray, shape (P,), dtype float64
        Static head, ascending.
    flow : np.ndarray, shape (P,), dtype float64
        Delivered flow PER PUMP at that head, descending.
    """
    group: str
    head: np.ndarray
    flow: np.ndarray
    n_pumps: int = 1

    def capacity(self, head):
        """
        Interpolate ONE pump's delivered flow at the given head(s).

        Values outside the tabulated head range are CLAMPED to the end of the
        curve, never extrapolated -- a pump curve extrapolated past its ends is
        meaningless. Use `in_range` to find out whether that happened; it does on
        real models (a station was observed running to 19.33 ft of head against a
        curve tabulated only to 18.17 ft).
        """
        return np.interp(np.asarray(head, dtype=np.float64), self.head, self.flow)

    def group_capacity(self, head):
        """Full-group capacity: `n_pumps` x one pump's flow at that head."""
        return self.n_pumps * self.capacity(head)

    def in_range(self, head) -> np.ndarray:
        """Boolean: is each head within the tabulated curve, i.e. not clamped?"""
        h = np.asarray(head, dtype=np.float64)
        return (h >= self.head[0]) & (h <= self.head[-1])


@dataclass
class NodeRims:
    """
    Rim elevations and types for every pipe node in a geometry.

    Two rim sources exist and they are NOT interchangeable:

    * ``'terrain'`` -- ``Terrain Elevation``, sampled from the DEM.
    * ``'override'`` -- ``Terrain Elevation Override`` where the modeller set
      one, falling back to the terrain elevation where they did not.

    They agree at every node with no override. Where one IS set they can differ
    substantially, and the override is what RAS itself uses: ``Invert Elevation +
    Depth`` reproduces the override, not the terrain elevation. Measured on a
    real model, one outfall node carried terrain 728.67 against an override of
    719.00 -- a 9.67 ft difference, and using the terrain value would have
    UNDER-counted rim exceedances there.

    Attributes
    ----------
    names : list[str]
    node_types : list[str]
        e.g. 'Junction', 'Start', 'External', 'Closed', 'Culvert Opening'.
        Reported so callers can filter afterwards rather than the reader
        pre-filtering.
    invert, depth, terrain : np.ndarray, shape (N,), dtype float64
    override : np.ndarray, shape (N,), dtype float64
        NaN where the modeller set no override.
    rim : np.ndarray, shape (N,), dtype float64
        The rim per the selected `source`.
    source : str
        'override' or 'terrain'.
    """
    names: list
    node_types: list
    invert: np.ndarray
    depth: np.ndarray
    terrain: np.ndarray
    override: np.ndarray
    rim: np.ndarray
    source: str

    @property
    def has_override(self) -> np.ndarray:
        """Boolean per node: the modeller set an explicit override."""
        return ~np.isnan(self.override)

    def as_dict(self) -> dict:
        """{node name: rim elevation} for the selected source."""
        return {n: float(v) for n, v in zip(self.names, self.rim)}


@dataclass
class NodeMaxWse:
    """
    Maximum water surface over the run at every node of one pipe network.

    Summary Output's `Maximum Water Surface` is per CELL, not per node, so this
    is computed from the `Nodes/Water Surface` time series.

    Attributes
    ----------
    network : str
    names : list[str]
        Node names, in results-column order.
    wse : np.ndarray, shape (N,), dtype float64
    time_index : np.ndarray, shape (N,), dtype int64
        Index into `timestamps` at which each node peaked.
    timestamps : np.ndarray, shape (T,), dtype str
    """
    network: str
    names: list
    wse: np.ndarray
    time_index: np.ndarray
    timestamps: np.ndarray

    def as_dict(self) -> dict:
        """{node name: maximum WSE}."""
        return {n: float(v) for n, v in zip(self.names, self.wse)}

    def time_of_max(self, node: str) -> str:
        """Time stamp at which `node` reached its maximum."""
        try:
            i = self.names.index(node)
        except ValueError:
            raise KeyError(
                f"Node '{node}' not in pipe network '{self.network}'") from None
        return str(self.timestamps[self.time_index[i]])


@dataclass
class VolumeAccounting:
    """
    RAS's volume-accounting summary for one 2D area, pipe network, or 1D reach.

    All volumes are in the unit named by `units` (RAS writes acre-feet on US
    Customary models). `vol_ending` is the volume left in the area at the END of
    the run -- it is NOT the peak, and for a pumped-vs-gravity comparison the two
    answer different questions.

    Attributes
    ----------
    kind : str
        '2D', 'Pipe Networks', or '1D'.
    name : str
    vol_starting, vol_ending, cum_inflow, cum_outflow, error : float
    error_percent : float
    precip_excess, precip_excess_depth : float
        NaN where RAS did not write them (pipe networks carry no precipitation).
    units : str
    """
    kind: str
    name: str
    vol_starting: float
    vol_ending: float
    cum_inflow: float
    cum_outflow: float
    error: float
    error_percent: float
    precip_excess: float
    precip_excess_depth: float
    units: str


def _seq(key: str, value):
    """Require a list/tuple of list/tuples, for ConduitPath.from_dict."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(
            f"Serialized ConduitPath key '{key}' must be a list, got "
            f'{type(value).__name__}')
    for item in value:
        if not isinstance(item, (list, tuple)):
            raise ValueError(
                f"Serialized ConduitPath key '{key}' must hold lists, got "
                f'{item!r}')
    return value


@dataclass
class ConduitPath:
    """
    An ordered downstream route through a pipe network.

    Produced by `trace_path` (one contiguous run of conduits) or by `join_paths`
    (several runs joined across a physical break, e.g. an open pond).

    A ConduitPath is a decision about ROUTE ONLY -- it holds no results. Trace it
    once and reuse it across every plan you compare, so each plan lands on the
    same station axis. That matters: resolving a fork by flow is PLAN-DEPENDENT
    (on the Hillside model, node BedJ293 picks a different branch in the 10-year
    pumped runs than in the other six), so re-tracing per plan can silently give
    two plans different axes.

    Attributes
    ----------
    network : str
    conduits : list[str]
        Conduit names in downstream order.
    nodes : list[str]
        Node names, len(conduits) + 1 for a single segment.
    segments : list[tuple[str, str]]
        (start_node, end_node) of each contiguous run.
    via : list[list[str]]
        The waypoints each segment was traced with, PARALLEL to `segments` --
        `via[i]` belongs to `segments[i]`, and is `[]` for a segment that needed
        none. Kept so a path records HOW it was found, not only what was found:
        `segments` + `via` is the tracing recipe and `conduits` is the answer, so
        a serialized path can be re-traced and checked against itself. See
        `reader.verify_path`.
    bridges : list[tuple[str, str, float]]
        (from_node, to_node, distance_ft) for each joined break.
    forks : list[str]
        Human-readable record of how each ambiguous fork was resolved.
    """
    network: str
    conduits: list = field(default_factory=list)
    nodes: list = field(default_factory=list)
    segments: list = field(default_factory=list)
    via: list = field(default_factory=list)
    bridges: list = field(default_factory=list)
    forks: list = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.conduits)

    @property
    def start(self) -> str:
        return self.nodes[0]

    @property
    def end(self) -> str:
        return self.nodes[-1]

    def to_dict(self) -> dict:
        """
        This route as plain JSON/YAML-native types, for a durable record.

        Round-trips through `from_dict`. Deliberately CANONICAL -- it holds no
        derivable value (`start` / `end` come from `nodes`) and no provenance, so
        nothing in it can disagree with anything else in it. A caller writing a
        lock file adds its own schema version, timestamp and
        `PipeNetwork.fingerprint()` alongside this, not inside it.
        """
        return {
            'network': self.network,
            'conduits': list(self.conduits),
            'nodes': list(self.nodes),
            'segments': [[a, b] for a, b in self.segments],
            'via': [list(v) for v in self.via],
            'bridges': [[a, b, float(gap)] for a, b, gap in self.bridges],
            'forks': list(self.forks),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ConduitPath':
        """
        Rebuild a ConduitPath from `to_dict` output (or hand-written YAML).

        Rebuilds ONLY -- it does not consult a network, so it cannot tell whether
        the route is still valid. Call `reader.verify_path` for that; keeping the
        two apart is what puts the check at the call site instead of hiding it in
        a constructor.

        `via` may be omitted, and is then taken as no waypoints on any segment.

        Raises
        ------
        ValueError
            If a required key is missing, a field is not a list, `via` does not
            match `segments` in length, a bridge is not a 3-tuple, or the node
            count contradicts the conduit and segment counts.
        """
        if not isinstance(data, dict):
            raise ValueError(
                f'ConduitPath.from_dict needs a dict, got {type(data).__name__}')
        for key in ('network', 'conduits', 'nodes', 'segments'):
            if key not in data:
                raise ValueError(
                    f"Serialized ConduitPath is missing required key '{key}'. "
                    f'Present: {sorted(data)}')

        def _str_list(key, value):
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"Serialized ConduitPath key '{key}' must be a list, got "
                    f'{type(value).__name__}')
            return [str(v) for v in value]

        conduits = _str_list('conduits', data['conduits'])
        nodes = _str_list('nodes', data['nodes'])
        forks = _str_list('forks', data.get('forks') or [])

        segments = []
        for seg in _seq('segments', data['segments']):
            if len(seg) != 2:
                raise ValueError(
                    f'Serialized ConduitPath segment {seg!r} must be '
                    f'(start_node, end_node)')
            segments.append((str(seg[0]), str(seg[1])))

        raw_via = data.get('via')
        if raw_via is None:
            via = [[] for _ in segments]
        else:
            via = [_str_list('via', v) for v in _seq('via', raw_via)]
            if len(via) != len(segments):
                raise ValueError(
                    f'Serialized ConduitPath has {len(via)} `via` entries for '
                    f'{len(segments)} segments; `via` is parallel to `segments`, '
                    f'so give an empty list for a segment with no waypoints.')

        bridges = []
        for br in _seq('bridges', data.get('bridges') or []):
            if len(br) != 3:
                raise ValueError(
                    f'Serialized ConduitPath bridge {br!r} must be '
                    f'(from_node, to_node, distance_ft)')
            bridges.append((str(br[0]), str(br[1]), float(br[2])))

        if len(nodes) != len(conduits) + len(segments):
            raise ValueError(
                f'Serialized ConduitPath is inconsistent: {len(nodes)} nodes '
                f'for {len(conduits)} conduits across {len(segments)} '
                f'segment(s); each segment contributes one node more than its '
                f'conduit count, so {len(conduits) + len(segments)} were '
                f'expected.')

        return cls(network=str(data['network']), conduits=conduits, nodes=nodes,
                   segments=segments, via=via, bridges=bridges, forks=forks)


@dataclass
class PathProfile:
    """
    A continuous profile along a ConduitPath, chained across its conduits.

    Same per-face quantities as ConduitProfile, but with `station` accumulated
    over the whole route so it can be plotted as one line. Where the path crosses
    a bridged break, the station advances by the true node-to-node distance.

    Attributes
    ----------
    path : ConduitPath
    when : str
        'Maximum', 'Minimum', or a time-stamp string.
    station : np.ndarray, shape (F,), dtype float64
        Distance from the path's start node, ascending.
    invert, crown, wse, velocity, flow : np.ndarray, shape (F,), dtype float64
    conduit_of : np.ndarray, shape (F,), dtype object
        Conduit name each face belongs to.
    node_at : dict[int, str]
        Row index -> node name, for labelling junctions on a chart.
    total_length : float
        Sum of conduit lengths plus bridged gaps.
    si_units : bool
    """
    path: ConduitPath
    when: str
    station: np.ndarray
    invert: np.ndarray
    crown: np.ndarray
    wse: np.ndarray
    velocity: np.ndarray
    flow: np.ndarray
    conduit_of: np.ndarray
    node_at: dict = field(default_factory=dict)
    total_length: float = 0.0
    si_units: bool = False

    @property
    def depth(self) -> np.ndarray:
        """Water depth above the invert at each face."""
        return self.wse - self.invert

    @property
    def is_surcharged(self) -> np.ndarray:
        """Boolean per face: water surface at or above the crown."""
        return self.wse >= self.crown

    @property
    def surcharge_margin(self) -> np.ndarray:
        """
        Signed head relative to the conduit crown, `wse - crown`, per face.

        Positive = surcharged by that much; negative = that much freeboard. This
        is the preferred way to REPORT surcharge: a continuous series along the
        profile rather than a single anchored number. A scalar "surcharge
        extended N feet" cannot be defined without arbitrary choices -- where to
        anchor it, whether a short gap breaks a run -- and real trunks break the
        obvious definitions in both directions (a pumped trunk whose surcharge
        stops short of the pump, and one surcharged over its whole length so any
        number is only a lower bound). The series has none of those problems, and
        any summary statistic can be derived from it downstream.

        Note that if the FIRST or LAST element is positive, the surcharged reach
        continues beyond the traced path; report that rather than implying the
        path bounds it.
        """
        return self.wse - self.crown

    @property
    def energy_grade(self) -> np.ndarray:
        """Energy grade line, wse + V^2 / 2g. See ConduitProfile.energy_grade."""
        g = 9.80665 if self.si_units else 32.174
        return self.wse + self.velocity ** 2 / (2.0 * g)


@dataclass
class ConduitProfile:
    """
    Along-conduit profile results for one pipe conduit at one instant (or envelope).

    This is the data behind the RAS Mapper pipe-conduit profile plot. It is a
    different resolution from ConduitTimeSeries: that reads the two lumped
    per-conduit values (Pipes/Pipe Flow US and DS), while this reads every
    computation FACE along the conduit.

    Station convention
    ------------------
    ``station`` is HEC-RAS's ``ConduitStation``, which increases from the US node
    to the DS node (verified on 195 conduits, including 10 adverse-slope pipes).
    RAS Mapper's profile plot draws the reverse — downstream on the left — so use
    ``station_from_ds`` to reproduce that x-axis.

    Face coverage
    -------------
    Faces sit at internal cell boundaries, so the first face is typically half a
    cell in from the US node and the last is short of the DS node. RAS assigns the
    two end faces of a conduit to only one side of a junction, so a given conduit
    may or may not carry a face at station 0.0 or at ``length``. The arrays are
    returned exactly as stored (sorted by station) and are NOT padded out to the
    node ends.

    Attributes
    ----------
    network : str
        Pipe network name this conduit belongs to.
    conduit : str
        Conduit name.
    when : str
        Resolved selector — a time-stamp string, 'Maximum', or 'Minimum'.
    station : np.ndarray, shape (F,), dtype float64
        Distance along the conduit from the US node, ascending.
    invert : np.ndarray, shape (F,), dtype float64
        Conduit invert elevation at each face.
    wse : np.ndarray, shape (F,), dtype float64
        Water surface at each face. When the conduit is surcharged this is the
        piezometric head (the HGL), not a free surface — compare with ``crown``.
    velocity, flow : np.ndarray, shape (F,), dtype float64
    face_indices : np.ndarray, shape (F,), dtype int64
        Column index of each face into the network's Face * result datasets.
    us_node, ds_node : str
    us_invert, ds_invert : float
        Invert elevation at the two node ends, from the conduit geometry table.
        The face arrays generally stop short of both ends, so these are the only
        way to close the profile onto the nodes. For an ADVERSE-slope conduit
        ``ds_invert > us_invert`` -- do not assume the invert falls with station.
    length : float
        Conduit Length from the geometry table (not the face station span).
    rise, span : float
        Conduit vertical and horizontal dimensions.
    shape : str
        e.g. 'Circular', 'Box'.
    si_units : bool
        Unit system of the model, used by ``energy_grade``.
    """
    network: str
    conduit: str
    when: str
    station: np.ndarray
    invert: np.ndarray
    wse: np.ndarray
    velocity: np.ndarray
    flow: np.ndarray
    face_indices: np.ndarray
    us_node: str
    ds_node: str
    us_invert: float
    ds_invert: float
    length: float
    rise: float
    span: float
    shape: str
    si_units: bool = False

    @property
    def depth(self) -> np.ndarray:
        """Water depth above the invert at each face."""
        return self.wse - self.invert

    @property
    def crown(self) -> np.ndarray:
        """Conduit soffit elevation (invert + rise) at each face."""
        return self.invert + self.rise

    @property
    def is_surcharged(self) -> np.ndarray:
        """Boolean per face: water surface at or above the crown."""
        return self.wse >= self.crown

    @property
    def energy_grade(self) -> np.ndarray:
        """
        Energy grade line, wse + V^2 / 2g.

        HEC-RAS does not store EG for pipe conduits; RAS Mapper derives it the
        same way. Uses g = 32.174 ft/s^2 or 9.80665 m/s^2 per ``si_units``.
        """
        g = 9.80665 if self.si_units else 32.174
        return self.wse + self.velocity ** 2 / (2.0 * g)

    @property
    def station_from_ds(self) -> np.ndarray:
        """
        Station measured from the DS node, i.e. the RAS Mapper plot x-axis.

        Still ascending order is NOT guaranteed — this is ``length - station``,
        so it descends. Reverse the whole profile if you want plot order.
        """
        return self.length - self.station


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
class BreachState:
    """The breach a run actually opened, for one connection.

    Read from ``Breaching Variables``, which exists only once a breach has
    formed.  ``fired`` distinguishes "this plan defines a breach that never
    triggered" (a legitimate outcome) from "no breach data at all"; a
    never-fired breach still yields an object, with ``fired`` False and the
    geometry fields None.

    The realised geometry is the **widest state reached**, not the plan's
    terminal geometry — a run that ends before the breach finishes forming
    stops short, and the side slopes grow with it (observed: a plan whose
    final slopes are 2/3 was still at 0.61/0.92 when the simulation ended).
    ``top_width`` therefore peaks at whichever time step maximises
    ``bottom_width + (left+right slope) * (crest - bottom_elev)``, which need
    not be the step with the widest bottom.

    Attributes
    ----------
    connection : str
        Connection name (HDF group key).
    fired : bool
        Whether a breach actually formed during the run.
    hdf_path_kind : str
        Which parent held the data: ``'SA 2D Area Conn'`` or ``'2D Hyd Conn'``.
    center_station : float or None
        ``Centerline Breach`` group attribute — the station RAS breached at.
    breach_at : str
        ``Breach at`` group attribute, e.g. ``'01JAN2025 12:37:00'``.
    breach_at_days : float or None
        ``Breach at Time (Days)`` group attribute, decimal days from sim start.
    bottom_width, bottom_elev : float or None
        Widest bottom width and lowest invert reached.
    left_slope, right_slope : float or None
        Side slopes at the widest-opening time step.
    top_width : float or None
        Widest opening reached at the crest; None when no crest was supplied.
    max_flow, max_velocity, max_flow_area : float or None
        Peaks over the breached time steps.
    time_of_max_top_width : str
        Time stamp of the widest opening.
    columns : tuple[str, ...]
        Column names as the HDF itself declared them (see
        :func:`hack_ras.results.reader.read_structure_timeseries`).
    """
    connection: str
    fired: bool
    hdf_path_kind: str = ""
    center_station: float = None
    breach_at: str = ""
    breach_at_days: float = None
    bottom_width: float = None
    bottom_elev: float = None
    left_slope: float = None
    right_slope: float = None
    top_width: float = None
    max_flow: float = None
    max_velocity: float = None
    max_flow_area: float = None
    time_of_max_top_width: str = ""
    columns: tuple = ()


@dataclass
class ConnectionCenterline:
    """An SA/2D connection's geometry as stored in a RAS HDF.

    The HDF twin of the geometry file's ``Connection Line=`` and
    ``Conn Weir SE=`` blocks, for cross-checking an ASCII parse against what
    RAS computed with.  ``parts`` carries the ``Centerline Parts`` row: every
    connection seen is single-part, but the field exists so a multi-part line
    is not silently joined end-to-end.

    Attributes
    ----------
    name : str
        Connection name.
    points : np.ndarray, shape (N, 2)
        Centerline vertices in projected map units.
    profile : np.ndarray, shape (M, 2)
        (station, elevation) pairs of the centerline profile.
    us_area, ds_area : str
        Headwater / tailwater 2D area or storage area names.
    mode : str
        ``'Weir/Gate/Culverts'``, ``'Bridge Opening'``, ...
    snn_id : int
        Node id linking to a results group's ``Node Pointer`` attribute.
    parts : tuple
        The structure's ``Centerline Parts`` row.
    """
    name: str
    points: np.ndarray
    profile: np.ndarray
    us_area: str = ""
    ds_area: str = ""
    mode: str = ""
    snn_id: int = -1
    parts: tuple = ()


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
