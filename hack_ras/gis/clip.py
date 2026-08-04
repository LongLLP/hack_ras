# hack_ras/gis/clip.py
"""
Measuring how much of a line lies inside a polygon — and whether that
measurement can be trusted.

The motivating case is a FEMA floodway data table: the reported width is the
length of a cross-section line that falls inside the mapped floodway polygon.
That is a one-line shapely call, but it has a trap that is silent and severe.

**The coincident-boundary trap.**  A mapped polygon is often digitized *to* a
cross section — the floodway ends at the last mapped section, a levee-backed
boundary is snapped to a section, and so on.  When the polygon's edge lands a
fraction of a foot off the line instead of exactly on it, the line stops crossing
the polygon and starts running *alongside* it.  The intersection then picks up
only the slivers where the two happen to overlap, and the answer swings by tens
of feet for a sub-foot shift in either geometry.  Observed live: a cross section
measured 53.34 ft where the true width was 116.76 ft, from a ~1 ft offset.

Nothing about the intersection itself reveals this — the result is a clean,
single, plausible-looking segment.  :meth:`PolygonProbe.measure` therefore also
reports how far the line runs *near* the boundary, which separates the two cases
decisively:

* a line that CROSSES stays within ``tol`` of the boundary for only about
  ``4 * tol`` — twice for entering and twice for leaving;
* a line that runs ALONGSIDE racks up far more.

Measured across a real 21-section table at ``tol=1.0`` ft, clean crossings came
in at 4.0–6.1 ft while the two bad sections hit 24.7 ft and 119.1 ft — so
:data:`COINCIDENT_FACTOR` (10) sits in a wide gap, not on a knife edge.

The buffered geometries are computed once per polygon, in the constructor, since
buffering a large dissolved polygon is the expensive part and a caller typically
measures many lines against one polygon.
"""
from __future__ import annotations

from dataclasses import dataclass

# A clean crossing runs within tol of the boundary for ~4 x tol (entering and
# leaving).  Flag anything past this multiple as running along the boundary.
COINCIDENT_FACTOR = 10.0
DEFAULT_TOL = 1.0


@dataclass
class LineInPolygon:
    """
    One line measured against one polygon.

    Attributes
    ----------
    length : float
        Length of the line inside the polygon — the width to report.
    along_boundary : float
        Length of the line running within ``tol`` of the polygon's boundary.
        About ``4 * tol`` for a clean crossing; much larger when the line runs
        alongside the boundary.
    widened_length : float
        ``length`` recomputed against the polygon buffered outward by ``tol``.
        For a clean crossing this exceeds ``length`` by roughly ``2 * tol``; on a
        coincident line the gap is large, which makes it the number to quote when
        reporting the problem ("as drawn X, one tolerance out Y").
    clean_crossing : float
        ``4 * tol`` — what ``along_boundary`` would be for a clean crossing.
        Carried for error messages so callers need not recompute it.
    tol : float
        The tolerance used, in the polygon's coordinate units.
    coincident : bool
        True when ``along_boundary`` exceeds ``COINCIDENT_FACTOR * tol``, i.e.
        the line runs along the boundary and ``length`` is unreliable.
    """
    length: float
    along_boundary: float
    widened_length: float
    clean_crossing: float
    tol: float
    coincident: bool

    @property
    def empty(self) -> bool:
        """True when the line does not intersect the polygon at all."""
        return self.length <= 0.0


class PolygonProbe:
    """
    A polygon prepared for repeated line measurements.

    Both the outward buffer and the boundary band are built once here, so
    measuring N lines costs N cheap intersections rather than N buffers.

    Parameters
    ----------
    polygon : shapely Polygon / MultiPolygon
        The area to measure inside, in a PROJECTED CRS — lengths come out in its
        coordinate units, so a geographic CRS yields degrees, not feet.
    tol : float
        Coincidence tolerance in those same units (default 1.0).  Raise it to
        catch looser near-parallel cases, lower it to quiet the check down.

    Raises
    ------
    ValueError
        If ``tol`` is not positive.
    """

    def __init__(self, polygon, tol: float = DEFAULT_TOL) -> None:
        if tol <= 0:
            raise ValueError(f"tol must be positive; got {tol!r}")
        self.polygon = polygon
        self.tol = float(tol)
        self.widened = polygon.buffer(self.tol)
        self.near_boundary = polygon.boundary.buffer(self.tol)

    def measure(self, line) -> LineInPolygon:
        """Measure *line* against this polygon.  See :class:`LineInPolygon`."""
        along = float(line.intersection(self.near_boundary).length)
        return LineInPolygon(
            length=float(line.intersection(self.polygon).length),
            along_boundary=along,
            widened_length=float(line.intersection(self.widened).length),
            clean_crossing=4.0 * self.tol,
            tol=self.tol,
            coincident=along > COINCIDENT_FACTOR * self.tol,
        )
