# hack_ras/geometry/xs_interp.py
"""
Utilities for placing station-based XS features onto GIS cut-line polylines.

Background
----------
HEC-RAS 1D cross-section stationing and GIS cut-line coordinates are
fundamentally independent: the model may station a cross-section from 0 to
800 ft while the GIS cut line (XS GIS Cut Line= block) is only 400 ft long
in projected map units.  The HEC-RAS GUI reconciles this by mapping features
(IFAs, blocked obstructions, Manning's n changes, etc.) proportionally:

    fraction = (station - min_sta) / (max_sta - min_sta)
    dist_along_cutline = fraction * cutline_arc_length

These functions implement that mapping so that Python scripts produce the
same spatial positions as the HEC-RAS GUI.
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .model import CrossSection


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cumulative_lengths(points: List[Tuple[float, float]]) -> List[float]:
    """Cumulative arc lengths along a polyline; length == len(points)."""
    lengths = [0.0]
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        lengths.append(lengths[-1] + math.hypot(dx, dy))
    return lengths


def _walk_to_dist(
    points: List[Tuple[float, float]],
    cum: List[float],
    dist: float,
) -> Tuple[float, float]:
    """Return the interpolated XY point at arc-length distance *dist* along the polyline."""
    total = cum[-1]
    dist = max(0.0, min(dist, total))
    if dist <= 0.0:
        return points[0]
    if dist >= total:
        return points[-1]
    for i in range(1, len(cum)):
        if cum[i] >= dist:
            seg_len = cum[i] - cum[i - 1]
            t = (dist - cum[i - 1]) / seg_len if seg_len > 0 else 0.0
            x = points[i - 1][0] + t * (points[i][0] - points[i - 1][0])
            y = points[i - 1][1] + t * (points[i][1] - points[i - 1][1])
            return (x, y)
    return points[-1]


def _station_fraction(station: float, min_sta: float, max_sta: float) -> float:
    """Fractional position of *station* within [min_sta, max_sta], clamped to [0, 1]."""
    sta_range = max_sta - min_sta
    if sta_range <= 0:
        return 0.0
    return max(0.0, min(1.0, (station - min_sta) / sta_range))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def station_to_xy(xs: CrossSection, station: float) -> Tuple[float, float]:
    """
    Return the (X, Y) position on a cross-section's GIS cut line corresponding
    to a given RAS station.

    Raises ValueError if the cross-section has no GIS cut line or no
    station/elevation data (both are required for the mapping).
    """
    if xs.cutline is None:
        raise ValueError(
            f"CrossSection {xs.station!r} has no XS GIS Cut Line; "
            "cannot map station to XY."
        )
    if xs.sta_elev is None:
        raise ValueError(
            f"CrossSection {xs.station!r} has no Sta/Elev data; "
            "cannot determine station range."
        )
    min_sta = xs.sta_elev[0][0]
    max_sta = xs.sta_elev[-1][0]
    points = xs.cutline.points
    cum = _cumulative_lengths(points)
    total = cum[-1]
    dist = _station_fraction(station, min_sta, max_sta) * total
    return _walk_to_dist(points, cum, dist)


def clip_xs_polyline(
    xs: CrossSection,
    sta_start: float,
    sta_end: float,
) -> List[Tuple[float, float]]:
    """
    Return the sub-polyline of a cross-section's GIS cut line that spans the
    station range [sta_start, sta_end].

    Entry and exit points are interpolated; interior cut-line vertices that
    fall within the range are preserved.  Returns at least two points.

    Raises ValueError if the cross-section has no GIS cut line or no
    station/elevation data.
    """
    if xs.cutline is None:
        raise ValueError(
            f"CrossSection {xs.station!r} has no XS GIS Cut Line."
        )
    if xs.sta_elev is None:
        raise ValueError(
            f"CrossSection {xs.station!r} has no Sta/Elev data."
        )
    min_sta = xs.sta_elev[0][0]
    max_sta = xs.sta_elev[-1][0]
    points = xs.cutline.points
    cum = _cumulative_lengths(points)
    total = cum[-1]

    dist_start = _station_fraction(sta_start, min_sta, max_sta) * total
    dist_end   = _station_fraction(sta_end,   min_sta, max_sta) * total

    if dist_end <= dist_start:
        pt = _walk_to_dist(points, cum, dist_start)
        return [pt, pt]

    result: List[Tuple[float, float]] = []

    for i in range(len(points)):
        d_prev = cum[i - 1] if i > 0 else 0.0
        d_curr = cum[i]

        # Interpolate entry point on the segment that crosses dist_start
        if i > 0 and d_prev <= dist_start < d_curr and not result:
            seg_len = d_curr - d_prev
            t = (dist_start - d_prev) / seg_len if seg_len > 0 else 0.0
            x = points[i - 1][0] + t * (points[i][0] - points[i - 1][0])
            y = points[i - 1][1] + t * (points[i][1] - points[i - 1][1])
            result.append((x, y))
        elif i == 0 and dist_start == 0.0:
            result.append(points[0])

        # Include interior vertices that fall strictly within the range
        if result and dist_start < d_curr < dist_end:
            result.append(points[i])

        # Interpolate exit point on the segment that crosses dist_end
        if i > 0 and d_prev < dist_end <= d_curr:
            seg_len = d_curr - d_prev
            t = (dist_end - d_prev) / seg_len if seg_len > 0 else 0.0
            x = points[i - 1][0] + t * (points[i][0] - points[i - 1][0])
            y = points[i - 1][1] + t * (points[i][1] - points[i - 1][1])
            result.append((x, y))
            break

    # Fallback for degenerate cases
    if len(result) < 2:
        result = [
            _walk_to_dist(points, cum, dist_start),
            _walk_to_dist(points, cum, dist_end),
        ]

    return result
