# hack_ras/geometry/active_flow.py
"""
Compute the active (effective) flow area of a HEC-RAS cross section at a given
water-surface elevation (WSE).

The active flow area is expressed as a list of *station* ranges ``[(a, b), ...]``
along the cross section.  There may be more than one range when flow is
disconnected (e.g. water ponded in two separate low spots), or when an
ineffective flow area splits the conveying width.

Definition used here
---------------------
The active flow area is the **wetted** portion of the section (ground below the
water surface) with the **non-conveying** portions removed:

    active = wetted_segments  -  blocking_ranges

* ``wetted_segments`` -- contiguous station ranges where the ground profile is
  at or below the WSE, with the water's edge interpolated onto the ground line.
  Disconnected low areas naturally produce multiple segments.

* ``blocking_ranges`` -- station ranges that do not convey flow at this WSE.
  Three feature types contribute, each reducing to a blocking range governed by
  the same overtopping rule (block while ``WSE <= elevation``): ineffective flow
  areas (IFAs), levee markers, and blocked obstructions.  See
  ``_ineff_blocking_ranges`` / ``_levee_blocking_ranges`` /
  ``_blocked_obstruction_ranges``.

Scope: this module computes the **active** (conveying) top width only.  Levees
and blocked obstructions also reduce the *inactive*/total top width (an IFA does
not), but that distinction does not affect the active width computed here.

Ineffective flow area (IFA) semantics
-------------------------------------
An IFA blocks its station range **unless it is overtopped**:

* A blank / ``None`` trigger elevation never overtops -> the range always blocks.
* A real trigger elevation blocks only while ``WSE <= elevation``.  Once
  ``WSE > elevation`` the area is overtopped and its width conveys.

For the purpose of the active **top width** (which is what this module maps),
*permanent* and *normal* IFAs behave identically: an overtopped IFA's surface
width becomes active in both cases.  The permanent-vs-normal distinction only
matters for flow area / conveyance below the trigger elevation (a permanent IFA
holds water but never conveys), which does not change the surface width drawn
here.  The ``permanent`` flag is therefore intentionally *not* branched on.

Non-zero starting station
--------------------------
All stations are handled in the cross section's own station space; the section's
first station may be non-zero.  The ``0.0`` sentinel used by HEC-RAS means "the
cross-section extremity" **only for "normal"-type** IFAs/obstructions, so it is
resolved to ``min_sta`` / ``max_sta`` there.  For "multiple_block" features the
stations are literal (``0.0`` is a real coordinate) and are used as-is -- see
``_resolve_area``.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from .model import BlockedObstructions, IneffFlowAreas, Levee

Station = float
Segment = Tuple[Station, Station]

# Minimum width (station units) for a segment to be kept; drops numerical slivers.
_MIN_WIDTH = 1e-6


# ---------------------------------------------------------------------------
# Wetted extent
# ---------------------------------------------------------------------------

def wetted_segments(
    sta_elev: Sequence[Tuple[float, float]],
    wse: float,
) -> List[Segment]:
    """
    Return the contiguous station ranges where the ground is at or below *wse*.

    The water's edge is linearly interpolated onto each ground segment that
    crosses the WSE.  Disconnected wetted areas yield multiple segments.

    Parameters
    ----------
    sta_elev : sequence of (station, elevation)
        The cross section's station-elevation ground profile, ordered by
        increasing station (as parsed from the geometry file).
    wse : float
        Water-surface elevation.

    Returns
    -------
    list of (start_station, end_station)
    """
    pts = list(sta_elev)
    if len(pts) < 2:
        return []

    segs: List[Segment] = []
    cur: Optional[float] = None

    for i in range(len(pts) - 1):
        s0, e0 = pts[i]
        s1, e1 = pts[i + 1]

        # Open a segment when the current vertex is at/below the water surface.
        if e0 <= wse and cur is None:
            cur = s0

        # A strict sign change means the WSE crosses this ground segment.
        if (e0 - wse) * (e1 - wse) < 0:
            t = (wse - e0) / (e1 - e0)
            x = s0 + t * (s1 - s0)
            if cur is None:
                cur = x          # entering the water
            else:
                segs.append((cur, x))   # leaving the water
                cur = None

    # Close a segment that runs to the last vertex.
    if cur is not None:
        last_sta, last_el = pts[-1]
        if last_el <= wse:
            segs.append((cur, last_sta))
        else:
            # Degenerate: open segment but last point above water and no
            # crossing captured; ignore rather than emit a bogus endpoint.
            pass

    return segs


# ---------------------------------------------------------------------------
# Blocking ranges
# ---------------------------------------------------------------------------

def _resolve_area(area, kind: str, min_sta: float, max_sta: float):
    """
    Resolve one station-range area (IFA or blocked obstruction) to a concrete
    ``(start, end)`` pair, applying the HEC-RAS ``0.0`` edge sentinel ONLY for
    "normal"-type features.

    Returns ``None`` when the area carries no spatial information (a blank
    "normal" area with both stations ``0.0``).  For "multiple_block" features the
    stations are literal and used as-is (``0.0`` is a real coordinate, not the
    XS edge -- resolving it would corrupt a block on an XS whose edge station is
    not zero, e.g. one starting at a negative station).
    """
    start, end = area.start_sta, area.end_sta
    if kind == "normal":
        if start == 0.0 and end == 0.0:
            return None
        start = min_sta if start == 0.0 else start
        end = max_sta if end == 0.0 else end
    return start, end


def _blocks_at(elevation: Optional[float], wse: float) -> bool:
    """
    True when a feature with the given trigger/top *elevation* blocks flow at
    *wse*.  A blank (``None``) elevation always blocks; a real elevation blocks
    only while ``wse <= elevation`` (i.e. it is NOT overtopped).  This single
    rule governs IFAs, levees, and blocked obstructions alike.
    """
    return elevation is None or wse <= elevation


def _ineff_blocking_ranges(
    ineff: Optional[IneffFlowAreas],
    min_sta: float,
    max_sta: float,
    wse: float,
) -> List[Segment]:
    """Station ranges removed from active flow by ineffective flow areas at *wse*."""
    if ineff is None:
        return []
    ranges: List[Segment] = []
    for area in ineff.areas:
        resolved = _resolve_area(area, ineff.ifa_type, min_sta, max_sta)
        if resolved is None or not _blocks_at(area.elevation, wse):
            continue
        start, end = resolved
        if end > start:
            ranges.append((start, end))
    return ranges


def _levee_blocking_ranges(
    levee: Optional[Levee],
    min_sta: float,
    max_sta: float,
    wse: float,
) -> List[Segment]:
    """
    Station ranges removed from ACTIVE flow by levee markers at *wse*.

    A left levee at station ``L`` (crest ``E``) removes everything outboard of
    it -> ``[min_sta, L]``; a right levee at ``R`` removes ``[R, max_sta]``.  A
    side blocks only while it is not overtopped (``wse <= E``); an overtopped
    levee has no effect.  (Levees also remove *inactive* width, but this module
    computes active width only.)
    """
    if levee is None:
        return []
    ranges: List[Segment] = []
    if levee.left_sta is not None and _blocks_at(levee.left_elev, wse):
        if levee.left_sta > min_sta:
            ranges.append((min_sta, levee.left_sta))
    if levee.right_sta is not None and _blocks_at(levee.right_elev, wse):
        if levee.right_sta < max_sta:
            ranges.append((levee.right_sta, max_sta))
    return ranges


def _blocked_obstruction_ranges(
    obstructions: Optional[BlockedObstructions],
    min_sta: float,
    max_sta: float,
    wse: float,
) -> List[Segment]:
    """
    Station ranges removed from ACTIVE flow by blocked obstructions at *wse*.

    Each obstruction spans ``[start, end]`` up to a top elevation.  It removes
    its range while it pierces the surface (``wse <= top``); once submerged
    (``wse > top``) the surface is continuous over it and it no longer affects
    top width.  Same overtopping rule as an IFA; "normal" vs "multiple_block"
    handled by :func:`_resolve_area`.  (Obstructions also remove *inactive*
    width, but this module computes active width only.)
    """
    if obstructions is None:
        return []
    ranges: List[Segment] = []
    for area in obstructions.areas:
        resolved = _resolve_area(area, obstructions.obstr_type, min_sta, max_sta)
        if resolved is None or not _blocks_at(area.elevation, wse):
            continue
        start, end = resolved
        if end > start:
            ranges.append((start, end))
    return ranges


# ---------------------------------------------------------------------------
# Interval subtraction
# ---------------------------------------------------------------------------

def subtract_intervals(
    segments: Sequence[Segment],
    blockers: Sequence[Segment],
    min_width: float = _MIN_WIDTH,
) -> List[Segment]:
    """
    Remove every *blocker* range from every *segment*, returning the remaining
    sub-segments (ordered, slivers narrower than *min_width* dropped).
    """
    result: List[Segment] = []
    for a, b in segments:
        pieces: List[Segment] = [(a, b)]
        for ba, bb in blockers:
            nxt: List[Segment] = []
            for pa, pb in pieces:
                if bb <= pa or ba >= pb:      # no overlap
                    nxt.append((pa, pb))
                    continue
                if ba > pa:                   # keep left remainder
                    nxt.append((pa, min(ba, pb)))
                if bb < pb:                   # keep right remainder
                    nxt.append((max(bb, pa), pb))
            pieces = nxt
        for pa, pb in pieces:
            if pb - pa > min_width:
                result.append((pa, pb))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def active_flow_segments(
    sta_elev: Sequence[Tuple[float, float]],
    wse: float,
    ineff: Optional[IneffFlowAreas] = None,
    levee: Optional[Levee] = None,
    blocked_obstructions: Optional[BlockedObstructions] = None,
) -> List[Segment]:
    """
    Return the active-flow station ranges for a cross section at a given WSE.

    Parameters
    ----------
    sta_elev : sequence of (station, elevation)
        Ground profile, ordered by increasing station.
    wse : float
        Water-surface elevation for the profile of interest.
    ineff : IneffFlowAreas, optional
        Parsed ineffective flow areas for the cross section (``xs.ineff``).
    levee : Levee, optional
        Parsed levee markers (``xs.levee``).  A non-overtopped levee clips the
        active area at its station.
    blocked_obstructions : BlockedObstructions, optional
        Parsed blocked obstructions (``xs.blocked_obstructions``).  A
        non-submerged obstruction removes its station range.

    Returns
    -------
    list of (start_station, end_station)
        One or more ranges (multiple => disconnected active flow).  Empty when
        the section is dry or fully blocked.
    """
    pts = list(sta_elev)
    if len(pts) < 2 or wse is None:
        return []

    min_sta = pts[0][0]
    max_sta = pts[-1][0]

    wet = wetted_segments(pts, wse)
    if not wet:
        return []

    blockers: List[Segment] = []
    blockers += _ineff_blocking_ranges(ineff, min_sta, max_sta, wse)
    blockers += _levee_blocking_ranges(levee, min_sta, max_sta, wse)
    blockers += _blocked_obstruction_ranges(blocked_obstructions, min_sta, max_sta, wse)

    return subtract_intervals(wet, blockers)
