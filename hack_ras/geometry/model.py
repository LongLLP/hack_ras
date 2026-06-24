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

    # Raw line range within GeometryFile.raw_lines (set by parser; not semantic)
    _raw_line_start: int = field(default=-1, repr=False, compare=False)
    _raw_line_end: int = field(default=-1, repr=False, compare=False)

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

    raw_lines: List[str] = field(default_factory=list)  # for passthrough/editing

    def get_reach(self, river: str, reach: str) -> Reach:
        return self.rivers[river].reaches[reach]

    def add_cross_section(self, cs: CrossSection):
        r = self.rivers.setdefault(cs.river, River(cs.river))
        reach = r.reaches.setdefault(cs.reach, Reach(cs.reach))
        reach.cross_sections.append(cs)