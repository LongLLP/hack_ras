# hack_ras/geometry/model.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict


@dataclass
class ManningDef:
    """Manning's roughness definition for a cross-section.

    All formats store data as (station, n_value, position_code) triplets in
    8-char fixed-width fields; position_code is discarded on parse.

    The method integer is the raw value from the #Mann= header and is preserved
    for lossless roundtrip:

    method=0  — "Horizontal Variation in n-values" is OFF (the HEC-RAS GUI
                checkbox).  Always exactly 3 entries whose stations align with
                the XS left edge, left bank station, and right bank station —
                i.e., one n-value each for LOB, channel, and ROB.

    method=-1 — "Horizontal Variation in n-values" is ON (modern convention).
                N entries at arbitrary stations.

    method=1  — Same semantics as method=-1; an older convention still found in
                some legacy files.  Read both; always write -1 for new output.

    entries: (station, n_value) pairs in station-ascending order; the n_value at
             station s applies from s to the next defined station (step function).
    """
    method: int  # raw integer from #Mann= header (0, -1, or 1)
    entries: List[Tuple[float, float]] = field(default_factory=list)  # (station, n_value)


@dataclass
class XSGISCutLine:
    n_points: int
    points: List[Tuple[float, float]] = field(default_factory=list)

@dataclass
class IneffArea:
    start_sta: float           # left station; 0.0 means XS leftmost
    end_sta: float             # right station; 0.0 means XS rightmost
    elevation: Optional[float] # None = infinite height (blank in file)
    permanent: bool            # from Permanent Ineff= block

@dataclass
class IneffFlowAreas:
    ifa_type: str              # "normal" (flag=0) or "multiple_block" (flag=-1)
    areas: List[IneffArea] = field(default_factory=list)

@dataclass
class Levee:
    """Levee markers for a cross-section (from the `Levee=` line).

    A side is present only when its station is not None.  A levee at station S
    with crest elevation E makes the ground on its *outboard* side (left of S
    for the left levee, right of S for the right levee) unavailable until the
    water surface exceeds E (the levee is overtopped).
    """
    left_sta: Optional[float] = None
    left_elev: Optional[float] = None
    right_sta: Optional[float] = None
    right_elev: Optional[float] = None
    name: str = ""

@dataclass
class BlockObstructArea:
    start_sta: float           # left station; 0.0 = XS leftmost only for "normal" type
    end_sta: float             # right station; 0.0 = XS rightmost only for "normal" type
    elevation: Optional[float] # top-of-obstruction elevation; None = blank field

@dataclass
class BlockedObstructions:
    """Blocked obstructions for a cross-section (from the `#Block Obstruct=` block).

    obstr_type mirrors the IFA convention: "normal" (flag=0) means one area left
    of the channel and one right (with 0.0 station sentinels for the XS edges);
    "multiple_block" (flag=-1) means 1-N arbitrary blocks with literal stations.
    """
    obstr_type: str            # "normal" (flag=0) or "multiple_block" (flag=-1)
    areas: List[BlockObstructArea] = field(default_factory=list)

@dataclass
class CrossSection:
    river: str
    reach: str
    station: str

    rm: Optional[str] = None
    cutline: Optional[XSGISCutLine] = None

    sta_elev: Optional[List[Tuple[float, float]]] = None
    manning_def: Optional[ManningDef] = None
    ineff: Optional[IneffFlowAreas] = None
    bank_stations: Optional[Tuple[float, float]] = None
    levee: Optional[Levee] = None
    blocked_obstructions: Optional[BlockedObstructions] = None

    # Raw line range within GeometryFile.raw_lines (set by parser; not semantic)
    _raw_line_start: int = field(default=-1, repr=False, compare=False)
    _raw_line_end: int = field(default=-1, repr=False, compare=False)

@dataclass
class StorageArea2D:
    """A storage area's 2D-mesh cell-seed points (from ``Storage Area 2D Points=``).

    ``points`` are the cell centers HEC-RAS uses to (re)generate the 2D mesh
    when the geometry is opened.  A storage area that is 1D (or a 2D area whose
    mesh has not been generated) has an empty ``points`` list.

    The three ``_raw_*`` indices locate the block within
    ``GeometryFile.raw_lines`` so an editor can rewrite only the coordinate
    data lines and leave every other byte untouched.  They are positional
    bookkeeping set by the parser, not semantic fields.
    """
    name: str
    points: List[Tuple[float, float]] = field(default_factory=list)

    _header_line: int = field(default=-1, repr=False, compare=False)  # 'Storage Area 2D Points=' line
    _data_start: int = field(default=-1, repr=False, compare=False)   # first coordinate line
    _data_end: int = field(default=-1, repr=False, compare=False)     # one past the last coordinate line


# Conn Routing Type= -> the connection Mode shown in the RAS GUI and stored in
# the geometry HDF's Structures/Attributes 'Mode' field. Verified by matching
# every ASCII connection against its HDF row across the sample models: type 1
# is always 'Weir/Gate/Culverts' (178 connections) and type 32 always
# 'Bridge Opening' (32). Other RAS routing types exist but have not been seen,
# so an unrecognized value maps to None rather than a guess.
CONN_ROUTING_MODES = {
    1: "Weir/Gate/Culverts",
    32: "Bridge Opening",
}


@dataclass
class Connection:
    """An SA/2D connection — the ``Connection=`` block (levee, weir, or
    hydraulic structure joining two 2D areas/storage areas, or crossing a
    single one).

    ``centerline`` is the ``Connection Line=`` polyline in projected map units;
    ``weir_profile`` is the ``Conn Weir SE=`` (station, elevation) pairs — the
    spillway/levee crest RAS routes flow over; ``terrain_profile`` is the
    ``Connection Centerline Profile=`` pairs, the ground surface sampled under
    the centerline (usually empty — RAS writes 0 points unless the profile has
    been pulled from terrain in the GUI).  RAS's own breach plot draws these two
    as "Spillway" and "Centerline Terrain".

    **Stationing is arc length**, unlike a cross section: HEC-RAS requires a
    connection's station/elevation length to match its GIS centerline length
    and refuses to run otherwise, so a station is a direct distance along
    ``centerline``.  See :mod:`hack_ras.geometry.conn_interp`, which owns that
    mapping and the tolerance check — do not mix up ``xs_interp``'s fractional
    mapping, which is for cross sections only.

    ``up_sa`` / ``dn_sa`` are the headwater / tailwater area names
    (``Connection Up SA=`` / ``Dn SA=``); the two are equal for a connection
    interior to a single 2D area.

    The ``_raw_line_*`` indices locate the block within
    ``GeometryFile.raw_lines``; they are positional bookkeeping, not semantics.
    """
    name: str
    label_xy: Optional[Tuple[float, float]] = None
    description: str = ""
    centerline: List[Tuple[float, float]] = field(default_factory=list)
    weir_profile: List[Tuple[float, float]] = field(default_factory=list)
    terrain_profile: List[Tuple[float, float]] = field(default_factory=list)
    up_sa: str = ""
    dn_sa: str = ""
    weir_coef: Optional[float] = None
    weir_width: Optional[float] = None
    routing_type: Optional[int] = None
    last_edited: str = ""

    _raw_line_start: int = field(default=-1, repr=False, compare=False)
    _raw_line_end: int = field(default=-1, repr=False, compare=False)

    @property
    def mode(self) -> Optional[str]:
        """RAS connection Mode from ``Conn Routing Type=``, or None if unknown.

        Matches the geometry HDF's ``Structures/Attributes`` ``Mode`` field —
        see :data:`CONN_ROUTING_MODES`.  The mode decides whether the weir
        profile is a spillway spanning the whole centerline (``Weir/Gate/
        Culverts``) or a bridge opening that need not
        (:mod:`hack_ras.geometry.conn_interp`).
        """
        return CONN_ROUTING_MODES.get(self.routing_type)

    @property
    def is_weir_mode(self) -> bool:
        """True for a ``Weir/Gate/Culverts`` connection (levees, spillways).

        These are the connections a breach can be placed on, and the ones whose
        stationing HEC-RAS holds to its GIS length.
        """
        return self.routing_type == 1


@dataclass
class Reach:
    name: str
    cross_sections: List[CrossSection] = field(default_factory=list)

@dataclass
class River:
    name: str
    reaches: Dict[str, Reach] = field(default_factory=dict)

@dataclass
class GeometryFile:
    title: Optional[str] = None
    rivers: Dict[str, River] = field(default_factory=dict)
    storage_areas_2d: List[StorageArea2D] = field(default_factory=list)
    connections: Dict[str, Connection] = field(default_factory=dict)

    raw_lines: List[str] = field(default_factory=list)  # for passthrough/editing

    def get_reach(self, river: str, reach: str) -> Reach:
        return self.rivers[river].reaches[reach]

    def add_cross_section(self, cs: CrossSection):
        r = self.rivers.setdefault(cs.river, River(cs.river))
        reach = r.reaches.setdefault(cs.reach, Reach(cs.reach))
        reach.cross_sections.append(cs)