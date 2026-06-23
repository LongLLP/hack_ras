# hack_ras/geometry/model.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict


@dataclass
class ManningDef:
    """Manning's roughness definition for a cross-section.

    Two mutually exclusive formats exist in HEC-RAS:

    'lob_ch_rob' (Standard, method=0 in #Mann= header):
        Three single n-values — one each for left overbank, channel, and right
        overbank.  No station information.  n_lob, n_channel, n_rob are set.

    'horizontal' (Horizontal Variation, method=1 in #Mann= header):
        N station-keyed n-values; the value at station s applies from s to the
        next defined station (step function).  entries is a list of
        (station, n_value) pairs.
    """
    method: str  # 'lob_ch_rob' or 'horizontal'
    # Standard LOB/Channel/ROB values (method='lob_ch_rob')
    n_lob: float = 0.0
    n_channel: float = 0.0
    n_rob: float = 0.0
    # Horizontal variation entries (method='horizontal')
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