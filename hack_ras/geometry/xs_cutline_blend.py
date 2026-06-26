# hack_ras/geometry/xs_cutline_blend.py
"""
GIS cut-line blend extension.

When build_merged_cutline needs to extend the selected source's cut line
(because the output station range exceeds the source's original range),
try_blend_extension attempts to use the other geometry's cut line for the
extension rather than a straight-line projection.

The blend succeeds only when all three conditions hold:
  1. The two cut lines run in the same general direction.  If not, the other
     cut line's point order is reversed before any further checks.
  2. The mean perpendicular deviation between the two cut lines, sampled over
     the full extent of the selected (source) cut line, is below
     threshold_pct percent of its arc length.
  3. A handoff point on the other cut line is found within search_radius GIS
     units of the source endpoint, and its local tangent matches the source
     tangent within _MAX_HANDOFF_ANGLE_DEG.

If any check fails the caller falls back to straight-line projection.

The tangent at each point is estimated over a window of several surrounding
points rather than just the adjacent segment.  This avoids being misled by
collinear-cluster noise, which is common in HEC-RAS cut lines.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from .xs_interp import _cumulative_lengths


# Maximum angular difference allowed between the source tangent at the
# junction and the other cut line's tangent at the chosen handoff point.
_MAX_HANDOFF_ANGLE_DEG: float = 45.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tangent_at(
    pts: List[Tuple[float, float]],
    idx: int,
    window: int = 4,
) -> Tuple[float, float]:
    """
    Stable unit tangent at pts[idx], estimated over a window of surrounding
    vertices.  Using several points instead of just the adjacent segment
    avoids instability at collinear-cluster noise.
    """
    n = len(pts)
    lo = max(0, idx - window)
    hi = min(n - 1, idx + window)
    if lo == hi:
        if idx + 1 < n:
            dx = pts[idx + 1][0] - pts[idx][0]
            dy = pts[idx + 1][1] - pts[idx][1]
        elif idx > 0:
            dx = pts[idx][0] - pts[idx - 1][0]
            dy = pts[idx][1] - pts[idx - 1][1]
        else:
            return (1.0, 0.0)
    else:
        dx = pts[hi][0] - pts[lo][0]
        dy = pts[hi][1] - pts[lo][1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _mean_deviation_pct(
    base_pts: List[Tuple[float, float]],
    other_pts: List[Tuple[float, float]],
    n_samples: int = 20,
) -> float:
    """
    Mean perpendicular deviation from evenly-sampled points on base_pts to
    the nearest point on the other_pts polyline, expressed as a percentage of
    base_pts's total arc length.
    """
    if len(base_pts) < 2 or len(other_pts) < 2:
        return float("inf")

    cum_base = _cumulative_lengths(base_pts)
    total = cum_base[-1]
    if total < 1e-9:
        return float("inf")

    deviations: List[float] = []
    for k in range(n_samples):
        d_target = total * k / max(n_samples - 1, 1)
        d_target = max(0.0, min(d_target, total))

        # Interpolate sample point on base
        px, py = base_pts[-1]
        for i in range(1, len(cum_base)):
            if cum_base[i] >= d_target:
                seg_len = cum_base[i] - cum_base[i - 1]
                t = (d_target - cum_base[i - 1]) / seg_len if seg_len > 1e-9 else 0.0
                px = base_pts[i - 1][0] + t * (base_pts[i][0] - base_pts[i - 1][0])
                py = base_pts[i - 1][1] + t * (base_pts[i][1] - base_pts[i - 1][1])
                break

        # Nearest distance from sample point to other polyline
        min_d = float("inf")
        for j in range(len(other_pts) - 1):
            ax, ay = other_pts[j]
            bx, by = other_pts[j + 1]
            dx, dy = bx - ax, by - ay
            seg_sq = dx * dx + dy * dy
            if seg_sq < 1e-18:
                d = math.hypot(px - ax, py - ay)
            else:
                t2 = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_sq))
                cx = ax + t2 * dx
                cy = ay + t2 * dy
                d = math.hypot(px - cx, py - cy)
            if d < min_d:
                min_d = d
        deviations.append(min_d)

    return 100.0 * (sum(deviations) / len(deviations)) / total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_blend_extension(
    source_pts: List[Tuple[float, float]],
    other_pts: List[Tuple[float, float]],
    direction: str,
    threshold_pct: float,
    search_radius: float,
) -> Optional[List[Tuple[float, float]]]:
    """
    Attempt to find a cut-line extension from other_pts.

    Parameters
    ----------
    source_pts    : full GIS point list of the selected cut line
    other_pts     : full GIS point list of the other cut line
    direction     : ``'back'`` — extend before source_pts[0];
                    ``'fwd'``  — extend after source_pts[-1]
    threshold_pct : maximum mean perpendicular deviation as a percentage of
                    source arc length; blend is rejected if exceeded
    search_radius : GIS-unit radius around the source endpoint to search for
                    a handoff point on other_pts

    Returns
    -------
    List of GIS points to prepend (``'back'``) or append (``'fwd'``).
    The junction point itself is NOT included — caller keeps source_pts intact.
    Returns ``None`` if blending is not possible or fails validation.
    """
    if len(source_pts) < 2 or len(other_pts) < 2:
        return None

    # --- 1. Direction normalisation ---
    src_dx = source_pts[-1][0] - source_pts[0][0]
    src_dy = source_pts[-1][1] - source_pts[0][1]
    oth_dx = other_pts[-1][0] - other_pts[0][0]
    oth_dy = other_pts[-1][1] - other_pts[0][1]
    if src_dx * oth_dx + src_dy * oth_dy < 0:
        other_pts = list(reversed(other_pts))

    # --- 2. Alignment check ---
    dev_pct = _mean_deviation_pct(source_pts, other_pts)
    if dev_pct > threshold_pct:
        return None

    # --- 3. Find handoff point ---
    anchor = source_pts[0] if direction == "back" else source_pts[-1]
    src_tan = (
        _tangent_at(source_pts, 0)
        if direction == "back"
        else _tangent_at(source_pts, len(source_pts) - 1)
    )
    max_angle_rad = math.radians(_MAX_HANDOFF_ANGLE_DEG)

    best_idx: Optional[int] = None
    best_score = float("inf")

    for i, pt in enumerate(other_pts):
        dist = math.hypot(pt[0] - anchor[0], pt[1] - anchor[1])
        if dist > search_radius:
            continue
        oth_tan = _tangent_at(other_pts, i)
        cos_a = max(-1.0, min(1.0, src_tan[0] * oth_tan[0] + src_tan[1] * oth_tan[1]))
        ang = math.acos(cos_a)
        if ang > max_angle_rad:
            continue
        # Prioritise angular match; use distance as a lightweight tiebreaker
        score = ang + (dist / max(search_radius, 1e-9)) * 0.1
        if score < best_score:
            best_score = score
            best_idx = i

    if best_idx is None:
        return None

    # --- 4. Extract extension ---
    if direction == "back":
        # other_pts[0 … best_idx] runs toward the source start — prepend as-is.
        ext = list(other_pts[: best_idx + 1])
        # Drop any trailing point that duplicates source_pts[0]
        while ext and math.hypot(ext[-1][0] - anchor[0], ext[-1][1] - anchor[1]) < 1e-6:
            ext.pop()
    else:
        # other_pts[best_idx … end] departs from source end — append as-is.
        ext = list(other_pts[best_idx:])
        # Drop any leading point that duplicates source_pts[-1]
        while ext and math.hypot(ext[0][0] - anchor[0], ext[0][1] - anchor[1]) < 1e-6:
            ext.pop(0)

    return ext if ext else None
