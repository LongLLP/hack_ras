# hack_ras/geometry/shift.py

from __future__ import annotations
import copy
import logging
import math
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from .model import GeometryFile, XSGISCutLine

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _normalize_names(s: str) -> str:
    return " ".join(str(s).strip().split()).lower()


def _normalize_rs(value) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s.endswith("*"):
        s = s[:-1].strip()
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return s
    d = d.normalize()
    s2 = format(d, "f")
    if "." in s2:
        s2 = s2.rstrip("0").rstrip(".")
    return s2


def _seg_len(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _remove_consecutive_points(
    points: List[Tuple[float, float]], eps: float = 1e-2
) -> List[Tuple[float, float]]:
    if not points:
        return points
    cleaned = [points[0]]
    for x, y in points[1:]:
        x0, y0 = cleaned[-1]
        if _seg_len(x0, y0, x, y) > eps:
            cleaned.append((x, y))
    return cleaned


def _format_xs_gis_lines(
    points: List[Tuple[float, float]], line_width: int = 65
) -> List[str]:
    flat = "".join(f"{x:16.6f}{y:16.6f}" for x, y in points)
    out: List[str] = []
    s = flat
    while len(s) > line_width:
        out.append(s[:line_width] + "\n")
        s = s[line_width:]
    out.append(s + "\n")
    return out


# ---------------------------------------------------------------------------
# Public: core polyline algorithm
# ---------------------------------------------------------------------------

def shift_polyline(
    points: List[Tuple[float, float]],
    dist: float,
    tol: float = 1e-9,
) -> List[Tuple[float, float]]:
    """
    Slide a polyline along itself by *dist* while preserving total arc length.

    A positive *dist* advances the start point; a negative *dist* retreats it
    (implemented by reversing, shifting, reversing back).  Returns a new list
    of (x, y) tuples — the point count may differ from the input.
    """
    before_n = len(points)
    points = _remove_consecutive_points(points)
    removed_n = before_n - len(points)
    if removed_n > 0:
        _logger.debug("shift_polyline: removed %d near-duplicate point(s).", removed_n)

    if len(points) < 2 or abs(dist) < tol:
        return points[:]

    reversed_flag = False
    if dist < 0:
        reversed_flag = True
        dist = abs(dist)
        points = points[::-1]

    cum = [0.0]
    for i in range(1, len(points)):
        cum.append(
            cum[-1] + _seg_len(points[i - 1][0], points[i - 1][1],
                                points[i][0], points[i][1])
        )
    total = cum[-1]

    if dist >= total - tol:
        x_prev, y_prev = points[-2]
        x_last, y_last = points[-1]
        L = _seg_len(x_prev, y_prev, x_last, y_last)
        if L == 0:
            out = [points[-1], points[-1]]
        else:
            ux, uy = (x_last - x_prev) / L, (y_last - y_prev) / L
            start = (x_last + (dist - total) * ux, y_last + (dist - total) * uy)
            end = (start[0] + total * ux, start[1] + total * uy)
            out = [start, end]
        if reversed_flag:
            out = out[::-1]
        return out

    k = 0
    while cum[k + 1] < dist - tol:
        k += 1

    if abs(dist - cum[k]) <= tol:
        new_start = points[k]
        start_index = k
    elif abs(dist - cum[k + 1]) <= tol:
        new_start = points[k + 1]
        start_index = k + 1
    else:
        x1, y1 = points[k]
        x2, y2 = points[k + 1]
        segL = cum[k + 1] - cum[k]
        t = (dist - cum[k]) / segL
        new_start = (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
        start_index = k + 1

    out = [new_start] + points[start_index:]

    if len(out) < 2:
        out = [new_start, points[-1]]
    else:
        x_prev, y_prev = out[-2]
        x_last, y_last = out[-1]
        L = _seg_len(x_prev, y_prev, x_last, y_last)
        if L != 0:
            ux, uy = (x_last - x_prev) / L, (y_last - y_prev) / L
            out.append((x_last + dist * ux, y_last + dist * uy))
        else:
            out.append((x_last, y_last))

    if reversed_flag:
        out = out[::-1]
    return out


# ---------------------------------------------------------------------------
# Public: translation dict builder
# ---------------------------------------------------------------------------

def build_translation_dict(df) -> Dict[Tuple[str, str, str], float]:
    """
    Convert a DataFrame with columns ``River``, ``Reach``, ``River Station``,
    ``Translation`` into a ``{(norm_river, norm_reach, norm_rs): float}`` dict.

    Raises ``ValueError`` if required columns are missing.  Duplicate
    (River, Reach, RS) keys emit a ``logging.WARNING``; the last value wins.
    """
    required = {"River", "Reach", "River Station", "Translation"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame is missing required columns: {missing}")

    result: Dict[Tuple[str, str, str], float] = {}
    row_sources: Dict = defaultdict(list)

    for idx, row in df.iterrows():
        excel_row = idx + 2
        try:
            r = _normalize_names(row["River"])
            re = _normalize_names(row["Reach"])
            rs = _normalize_rs(row["River Station"])
            val = float(row["Translation"])
            key = (r, re, rs)
            row_sources[key].append(excel_row)
            result[key] = val
        except Exception as exc:
            _logger.warning("Row %d failed to parse: %s", excel_row, exc)

    for key, rows in row_sources.items():
        if len(rows) > 1:
            _logger.warning(
                "Duplicate (River, Reach, RS) key %s found in rows %s; using last value.",
                key,
                rows,
            )

    return result


# ---------------------------------------------------------------------------
# Public: main geometry transformation
# ---------------------------------------------------------------------------

def shift_xs_cutlines(
    geom: GeometryFile,
    translations: Dict[Tuple[str, str, str], float],
    new_title: Optional[str] = None,
) -> GeometryFile:
    """
    Apply GIS cut-line shifts to a parsed geometry and return a new
    ``GeometryFile``.

    Parameters
    ----------
    geom:
        Parsed geometry (from ``GeometryParser``).
    translations:
        Mapping of ``(norm_river, norm_reach, norm_rs) -> distance`` as
        returned by :func:`build_translation_dict`.
    new_title:
        If provided, replaces the ``Geom Title=`` line in the output.

    Returns
    -------
    A new ``GeometryFile`` whose ``raw_lines`` contain the shifted
    coordinates.  The in-memory ``CrossSection.cutline`` objects are also
    updated so the returned object is self-consistent.  The original *geom*
    is not modified.
    """
    modified_lines: List[str] = []
    current_river: Optional[str] = None
    current_reach: Optional[str] = None
    rs_norm: Optional[str] = None
    applied_keys: set = set()
    shifted_pts_by_key: Dict = {}

    lines = geom.raw_lines
    N = len(lines)
    i = 0

    while i < N:
        line = lines[i]

        # --- Geom Title ---
        if line.startswith("Geom Title="):
            if new_title is not None:
                modified_lines.append(f"Geom Title={new_title}\n")
            else:
                modified_lines.append(line)
            i += 1
            continue

        # --- River Reach= ---
        if line.startswith("River Reach="):
            try:
                _, rest = line.split("=", 1)
                parts = rest.split(",", 1)
                current_river = _normalize_names(parts[0])
                current_reach = _normalize_names(parts[1])
            except Exception:
                pass
            modified_lines.append(line)
            i += 1
            continue

        # --- Type RM Length (extracts RS) ---
        if line.startswith("Type RM Length"):
            try:
                rs_norm = _normalize_rs(line.split("=")[1].split(",")[1].strip())
            except Exception:
                rs_norm = None
            modified_lines.append(line)
            i += 1
            continue

        # --- XS GIS Cut Line block ---
        if line.startswith("XS GIS Cut Line="):
            num_pairs = int(line.split("=")[1].strip())
            needed = num_pairs * 2
            chunks: List[str] = []
            orig_coord_lines: List[str] = []
            consumed = 0
            j = i + 1

            while j < N and len(chunks) < needed:
                raw = lines[j]
                cline = raw.rstrip("\n")
                max_chars = min(len(cline), 16 * (needed - len(chunks)))
                chunks.extend([cline[k:k + 16] for k in range(0, max_chars, 16)])
                orig_coord_lines.append(raw)
                consumed += 1
                j += 1

            pts = [
                (float(chunks[c]), float(chunks[c + 1]))
                for c in range(0, len(chunks), 2)
            ]
            key = (current_river, current_reach, rs_norm)

            if key in translations:
                applied_keys.add(key)
                dist = translations[key]
                try:
                    new_pts = shift_polyline(pts, dist)
                    _logger.debug("Shifted XS %s by %s.", key, dist)
                except Exception:
                    _logger.warning("Failed shifting XS %s; leaving unchanged.", key)
                    new_pts = pts
                shifted_pts_by_key[key] = new_pts
                new_count = len(new_pts)
                header = (
                    f"XS GIS Cut Line={new_count}\n"
                    if new_count != num_pairs
                    else line
                )
                modified_lines.append(header)
                modified_lines.extend(_format_xs_gis_lines(new_pts))
            else:
                modified_lines.append(line)
                modified_lines.extend(orig_coord_lines)

            i += 1 + consumed
            continue

        modified_lines.append(line)
        i += 1

    # Warn about translation entries that never matched any XS
    unmatched = set(translations) - applied_keys
    for k in sorted(unmatched, key=lambda t: (t[0], t[1], str(t[2]))):
        _logger.warning("Translation key %s was not matched in the geometry.", k)

    # Build result with deepcopied model so original geom is untouched
    result_rivers = copy.deepcopy(geom.rivers)
    result_title = new_title if new_title is not None else geom.title

    # Sync in-memory cutlines for shifted cross-sections
    for key, new_pts in shifted_pts_by_key.items():
        norm_river, norm_reach, norm_rs = key
        for r_name, river_obj in result_rivers.items():
            if _normalize_names(r_name) == norm_river:
                for reach_name, reach_obj in river_obj.reaches.items():
                    if _normalize_names(reach_name) == norm_reach:
                        for xs in reach_obj.cross_sections:
                            if _normalize_rs(xs.station) == norm_rs:
                                xs.cutline = XSGISCutLine(
                                    len(new_pts), list(new_pts)
                                )
                                break

    return GeometryFile(
        title=result_title,
        rivers=result_rivers,
        raw_lines=modified_lines,
    )
