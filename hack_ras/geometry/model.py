# hack_ras/geometry/model.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

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
    manning: Optional[List[Tuple[float, float]]] = None
    ineff: Optional[IneffFlowAreas] = None
    bank_stations: Optional[Tuple[float, float]] = None

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