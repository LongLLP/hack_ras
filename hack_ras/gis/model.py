# hack_ras/gis/model.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProfilePoint:
    """
    A single output point along a profile line.

    Attributes
    ----------
    station : float
        Distance from the start of the profile line (same units as CRS).
    area : str
        Name of the 2D flow area this point belongs to.
    cell_idx : int or None
        HEC-RAS cell index. None for boundary-crossing and endpoint points
        that do not map directly to a cell centre.
    point_type : str
        One of 'cell' (cell centre), 'boundary' (2DFA perimeter crossing),
        or 'endpoint' (profile line start/end).
    wse : float or None
        Water surface elevation. Populated by assign_wse(); None before that.
    min_elev : float or None
        Cell minimum terrain elevation. None for non-cell points.
    status : str or None
        One of 'wet', 'dry', 'interpolated', or 'no_cell'.
        Populated by assign_wse(); None before that.
    """
    station: float
    area: str
    cell_idx: Optional[int]
    point_type: str
    wse: Optional[float] = None
    min_elev: Optional[float] = None
    status: Optional[str] = None
