# hack_ras/geometry/merge.py
"""
Cross-section merge utilities for the XS Editor.

Public API
----------
Transform        : horizontal / vertical offset and scale for one source
MergeConfig      : all user settings for one cross-section merge
merge_sta_elev() : stitch station/elevation data from two sources
merge_manning()  : merge Manning's n according to the chosen option
build_merged_cutline() : extend/clip a GIS cut line to a new station range
write_merged_geometry(): write a complete merged geometry file
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import CrossSection, GeometryFile, ManningDef, XSGISCutLine
from .blocks.xs_sta_elev import parse_sta_elev
from .blocks.xs_gis import parse_cutline
from .blocks.xs_mann import parse_mann
from .blocks.xs_bank_sta import parse_bank_sta
from .xs_interp import clip_xs_polyline, _cumulative_lengths


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Transform:
    """
    Linear horizontal and vertical transformation for a cross-section.

    Applied in order:
        new_station   = old_station   * h_scale + h_offset
        new_elevation = old_elevation * v_scale + v_offset
    """
    h_offset: float = 0.0
    h_scale: float = 1.0
    v_offset: float = 0.0
    v_scale: float = 1.0

    def apply_station(self, sta: float) -> float:
        return sta * self.h_scale + self.h_offset

    def apply_elevation(self, elev: float) -> float:
        return elev * self.v_scale + self.v_offset

    def to_orig_station(self, new_sta: float) -> float:
        """Invert the station transform: new_sta → original station."""
        if abs(self.h_scale) < 1e-12:
            return 0.0
        return (new_sta - self.h_offset) / self.h_scale

    def is_identity(self) -> bool:
        return (
            self.h_offset == 0.0
            and self.h_scale == 1.0
            and self.v_offset == 0.0
            and self.v_scale == 1.0
        )


@dataclass
class MergeConfig:
    """User's merge configuration for a single cross-section."""
    transform_a: Transform
    transform_b: Transform
    # Sorted station values defining segment boundaries.
    # Always includes the overall start (index 0) and end (index -1).
    breakpoints: List[float]
    # Source for each segment between consecutive breakpoints.
    # 'A', 'B', or None (gap).  len == len(breakpoints) - 1.
    segment_sources: List[Optional[str]]
    mann_option: str = 'merge'    # 'A', 'B', or 'merge'
    cutline_source: str = 'A'     # 'A' or 'B'


# ---------------------------------------------------------------------------
# Station / elevation helpers
# ---------------------------------------------------------------------------

def _interp_elevation(
    sta_elev: List[Tuple[float, float]], station: float
) -> float:
    """Linear interpolation of elevation at *station*; clamps at ends."""
    if not sta_elev:
        raise ValueError("Empty sta_elev list.")
    stations = [s for s, _ in sta_elev]
    elevations = [e for _, e in sta_elev]
    if station <= stations[0]:
        return elevations[0]
    if station >= stations[-1]:
        return elevations[-1]
    for i in range(len(stations) - 1):
        if stations[i] <= station <= stations[i + 1]:
            t = (station - stations[i]) / (stations[i + 1] - stations[i])
            return elevations[i] + t * (elevations[i + 1] - elevations[i])
    return elevations[-1]


def _extract_segment(
    sta_elev: List[Tuple[float, float]],
    sta_start: float,
    sta_end: float,
) -> List[Tuple[float, float]]:
    """
    Return station/elevation pairs within [sta_start, sta_end].
    Endpoints are interpolated; interior points are preserved as-is.
    """
    if not sta_elev or sta_end <= sta_start:
        return []

    result: List[Tuple[float, float]] = []
    result.append((sta_start, _interp_elevation(sta_elev, sta_start)))

    for sta, elev in sta_elev:
        if sta_start < sta < sta_end:
            result.append((sta, elev))

    result.append((sta_end, _interp_elevation(sta_elev, sta_end)))
    return result


def transform_sta_elev(
    sta_elev: List[Tuple[float, float]], t: Transform
) -> List[Tuple[float, float]]:
    """Apply *t* to every (station, elevation) pair."""
    return [(t.apply_station(s), t.apply_elevation(e)) for s, e in sta_elev]


# ---------------------------------------------------------------------------
# Public: merge station/elevation
# ---------------------------------------------------------------------------

def merge_sta_elev(
    sta_elev_a: List[Tuple[float, float]],
    sta_elev_b: List[Tuple[float, float]],
    breakpoints: List[float],
    segment_sources: List[Optional[str]],
) -> List[Tuple[float, float]]:
    """
    Stitch station/elevation data from two (already-transformed) sources.

    breakpoints    : sorted station values; includes overall start and end.
    segment_sources: 'A', 'B', or None (gap) for each segment.
    """
    if len(segment_sources) != len(breakpoints) - 1:
        raise ValueError(
            "segment_sources length must equal len(breakpoints) - 1"
        )

    merged: List[Tuple[float, float]] = []

    for i, source in enumerate(segment_sources):
        bp_start = breakpoints[i]
        bp_end = breakpoints[i + 1]

        if source is None:
            # Gap: no interior points; adjacent segments provide the boundary
            # elevations and HEC-RAS will linearly interpolate between them.
            continue

        src = sta_elev_a if source == 'A' else sta_elev_b
        segment = _extract_segment(src, bp_start, bp_end)

        if merged and segment:
            # Avoid duplicating the junction station
            if abs(segment[0][0] - merged[-1][0]) < 1e-9:
                segment = segment[1:]

        merged.extend(segment)

    return merged


# ---------------------------------------------------------------------------
# Manning's n helpers
# ---------------------------------------------------------------------------

def _transform_manning_def(
    mann_def: Optional[ManningDef], t: Transform
) -> Optional[ManningDef]:
    """
    Return a copy of *mann_def* with each station value transformed by *t*.

    Manning's n values are roughness coefficients; they are NOT affected by
    vertical scale or offset.  Only station values use the horizontal transform.
    The original method integer is preserved for roundtrip accuracy.
    """
    if mann_def is None:
        return None
    return ManningDef(
        method=mann_def.method,
        entries=[(t.apply_station(s), n) for s, n in mann_def.entries],
    )


def _n_at_station(entries: List[Tuple[float, float]], station: float) -> Optional[float]:
    """
    Step-function lookup: return the n_value whose station is the largest
    value ≤ *station*.  Returns None if no entry exists at or before *station*.
    """
    result: Optional[float] = None
    for sta, n in entries:
        if sta <= station + 1e-9:
            result = n
        else:
            break
    return result


def _mann_def_to_entries_in_segment(
    mann_def: ManningDef,
    t: Transform,
    bp_start: float,
    bp_end: float,
) -> List[Tuple[float, float]]:
    """
    Return (station, n_value) pairs from *mann_def* covering segment
    [bp_start, bp_end]: one entry at bp_start (from step-function lookup)
    plus any defined entries strictly inside the segment.
    """
    all_entries = [(t.apply_station(s), n) for s, n in mann_def.entries]
    n_start = _n_at_station(all_entries, bp_start)

    result: List[Tuple[float, float]] = []
    if n_start is not None:
        result.append((bp_start, n_start))
    for sta, n in all_entries:
        if bp_start < sta < bp_end:
            result.append((sta, n))
    return result


def merge_manning(
    xs_a: CrossSection,
    xs_b: CrossSection,
    config: MergeConfig,
) -> Optional[ManningDef]:
    """
    Merge Manning's n values according to config.mann_option.

    'A' / 'B':  Return that source's ManningDef with station transform applied.
                The original method integer is preserved for roundtrip accuracy.

    'merge':    Per segment, pull n-values from the corresponding source.
                Always produces method=-1 output ("Horizontal Variation" ON),
                since merged segments may come from different sources and carry
                arbitrary breakpoints.  At each segment boundary, a value is
                explicitly inserted so the step function is defined at the join.
    """
    if config.mann_option == 'A':
        return _transform_manning_def(xs_a.manning_def, config.transform_a)
    if config.mann_option == 'B':
        return _transform_manning_def(xs_b.manning_def, config.transform_b)

    # --- 'merge' option ---
    result: List[Tuple[float, float]] = []

    for i, source in enumerate(config.segment_sources):
        bp_start = config.breakpoints[i]
        bp_end = config.breakpoints[i + 1]

        if source is None:
            continue

        xs_src = xs_a if source == 'A' else xs_b
        t = config.transform_a if source == 'A' else config.transform_b
        mann_def = xs_src.manning_def
        if mann_def is None:
            continue

        for sta, n_val in _mann_def_to_entries_in_segment(mann_def, t, bp_start, bp_end):
            if not result or abs(result[-1][0] - sta) > 1e-9:
                result.append((sta, n_val))

    if not result:
        return None
    return ManningDef(method=-1, entries=result)


# ---------------------------------------------------------------------------
# Public: GIS cut-line construction
# ---------------------------------------------------------------------------

def build_merged_cutline(
    source_xs: CrossSection,
    source_transform: Transform,
    merged_sta_start: float,
    merged_sta_end: float,
) -> Optional[XSGISCutLine]:
    """
    Build a GIS cut line for the merged cross-section.

    The source XS's cut line maps its original station range to GIS
    coordinates.  If the merged station range extends beyond the original,
    the cut line is projected linearly using the tangent at the end.

    Parameters
    ----------
    source_xs        : CrossSection whose cut line to use
    source_transform : Transform applied to that source's station data
    merged_sta_start : first station of the merged XS (in merged/display space)
    merged_sta_end   : last station of the merged XS (in merged/display space)
    """
    if source_xs.cutline is None or source_xs.sta_elev is None:
        return None

    pts = source_xs.cutline.points
    if len(pts) < 2:
        return None

    orig_sta_min = source_xs.sta_elev[0][0]
    orig_sta_max = source_xs.sta_elev[-1][0]
    orig_sta_range = orig_sta_max - orig_sta_min
    if orig_sta_range < 1e-9:
        return None

    # Convert merged display stations back to the source's original station space
    orig_start = source_transform.to_orig_station(merged_sta_start)
    orig_end = source_transform.to_orig_station(merged_sta_end)

    # Arc length and scale factor (GIS distance per original station unit)
    cum = _cumulative_lengths(pts)
    total_arc = cum[-1]
    scale = total_arc / orig_sta_range

    # Distances for backward / forward extensions (in GIS units)
    back_dist = max(0.0, (orig_sta_min - orig_start) * scale)
    fwd_dist = max(0.0, (orig_end - orig_sta_max) * scale)

    # Clip the interior portion (clamped to original station range)
    clip_start = max(orig_start, orig_sta_min)
    clip_end = min(orig_end, orig_sta_max)

    if clip_start >= clip_end:
        interior: List[Tuple[float, float]] = [pts[0], pts[-1]]
    else:
        interior = clip_xs_polyline(source_xs, clip_start, clip_end)

    new_pts: List[Tuple[float, float]] = list(interior)

    # Prepend backward extension
    if back_dist > 1e-9:
        dx = pts[1][0] - pts[0][0]
        dy = pts[1][1] - pts[0][1]
        seg_len = math.hypot(dx, dy)
        if seg_len > 1e-9:
            ux, uy = dx / seg_len, dy / seg_len
            back_pt = (pts[0][0] - back_dist * ux, pts[0][1] - back_dist * uy)
            new_pts = [back_pt] + new_pts

    # Append forward extension
    if fwd_dist > 1e-9:
        dx = pts[-1][0] - pts[-2][0]
        dy = pts[-1][1] - pts[-2][1]
        seg_len = math.hypot(dx, dy)
        if seg_len > 1e-9:
            ux, uy = dx / seg_len, dy / seg_len
            fwd_pt = (pts[-1][0] + fwd_dist * ux, pts[-1][1] + fwd_dist * uy)
            # Avoid duplicate with interior end
            if math.hypot(fwd_pt[0] - new_pts[-1][0], fwd_pt[1] - new_pts[-1][1]) > 1e-9:
                new_pts.append(fwd_pt)

    return XSGISCutLine(len(new_pts), new_pts)


# ---------------------------------------------------------------------------
# Fixed-format writers
# ---------------------------------------------------------------------------

def _fmt(v: float, width: int = 8) -> str:
    """Format *v* right-justified in *width* characters using 'g' notation."""
    for prec in range(width - 1, 0, -1):
        s = f"{v:.{prec}g}"
        if len(s) <= width:
            return s.rjust(width)
    return f"{v:.2g}".rjust(width)


def _write_sta_elev_block(sta_elev: List[Tuple[float, float]]) -> List[str]:
    lines = [f"#Sta/Elev= {len(sta_elev)} \n"]
    values = []
    for sta, elev in sta_elev:
        values.append(sta)
        values.append(elev)
    for i in range(0, len(values), 10):
        lines.append("".join(_fmt(v, 8) for v in values[i : i + 10]) + "\n")
    return lines


def _write_mann_block(mann_def: ManningDef) -> List[str]:
    """Write a #Mann= block from a ManningDef.

    All horizontal variation formats use (station, n_value, position_code)
    triplets in 8-char fixed-width fields.  The method integer from the
    ManningDef is written verbatim to preserve the original format.

        #Mann= N ,<method> , 0
        <station> <n_value> <0>  ...  (N triplets)
    """
    entries = mann_def.entries
    lines = [f"#Mann= {len(entries)} ,{mann_def.method} , 0 \n"]
    values: List[float] = []
    for sta, n_val in entries:
        values.extend([sta, n_val, 0.0])
    for i in range(0, len(values), 10):
        lines.append("".join(_fmt(v, 8) for v in values[i : i + 10]) + "\n")
    return lines


def _write_cutline_block(cutline: XSGISCutLine) -> List[str]:
    lines = [f"XS GIS Cut Line= {cutline.n_points} \n"]
    values = []
    for x, y in cutline.points:
        values.extend([x, y])
    # 16-char fields, 4 per line (2 XY pairs)
    for i in range(0, len(values), 4):
        lines.append("".join(f"{v:>16.9g}" for v in values[i : i + 4]) + "\n")
    return lines


def _write_bank_sta_line(bank_stations: Tuple[float, float]) -> str:
    left, right = bank_stations
    return f"Bank Sta={left:g},{right:g}\n"


# ---------------------------------------------------------------------------
# Raw-line helpers for pass-through content
# ---------------------------------------------------------------------------

_KEY_PREFIXES = (
    "XS GIS Cut Line=",
    "#Sta/Elev=",
    "#Mann=",
    "Bank Sta=",
)

_KEY_PARSERS = {
    "XS GIS Cut Line=": parse_cutline,
    "#Sta/Elev=": parse_sta_elev,
    "#Mann=": parse_mann,
    "Bank Sta=": lambda lines, i: (None, 1),
}


def _scan_xs_content(
    raw_lines: List[str], xs_start: int, xs_end: int
) -> Tuple[List[str], List[str]]:
    """
    Partition an XS's raw lines (excluding the Type RM Length header) into
    content that comes *before* the first key block and content that comes
    *after* the last key block.

    Key blocks (cutline, sta/elev, mann, bank_sta) are skipped; their
    positions are determined by calling the block parsers.
    """
    pre_key: List[str] = []
    post_key: List[str] = []
    in_key_zone = False

    i = xs_start + 1  # skip Type RM Length line
    while i < xs_end:
        line = raw_lines[i]
        stripped = line.strip()

        matched_parser = None
        for prefix, parser_fn in _KEY_PARSERS.items():
            if stripped.startswith(prefix):
                matched_parser = parser_fn
                break

        if matched_parser is not None:
            in_key_zone = True
            _, consumed = matched_parser(raw_lines, i)
            i += consumed
        elif not in_key_zone:
            pre_key.append(line)
            i += 1
        else:
            post_key.append(line)
            i += 1

    return pre_key, post_key


# ---------------------------------------------------------------------------
# Geometry file header / reach extraction helpers
# ---------------------------------------------------------------------------

def _extract_geom_header(geom: GeometryFile) -> List[str]:
    """Lines before the first 'River Reach=' line."""
    for i, line in enumerate(geom.raw_lines):
        if line.strip().startswith("River Reach="):
            return geom.raw_lines[:i]
    return list(geom.raw_lines)


def _extract_reach_header(
    geom: GeometryFile, river: str, reach: str
) -> List[str]:
    """
    'River Reach=...' line plus all reach-level lines up to (not including)
    the first 'Type RM Length=' line for that reach.
    """
    norm_river = river.strip().upper()
    norm_reach = reach.strip().upper()

    result: List[str] = []
    in_reach = False

    for line in geom.raw_lines:
        stripped = line.strip()

        if stripped.startswith("River Reach="):
            parts = stripped[len("River Reach="):].split(",", 1)
            r = parts[0].strip().upper()
            rch = parts[1].strip().upper() if len(parts) > 1 else ""
            if r == norm_river and rch == norm_reach:
                in_reach = True
                result.append(line)
                continue
            elif in_reach:
                break  # hit the next River Reach=

        if in_reach:
            if stripped.startswith("Type RM Length"):
                break
            result.append(line)

    return result


def _norm_key(river: str, reach: str, station: str) -> Tuple[str, str, str]:
    return (river.strip().upper(), reach.strip().upper(), station.strip().upper())


def _collect_xs_pairs(
    geom_a: Optional[GeometryFile],
    geom_b: Optional[GeometryFile],
) -> List[Tuple[str, str, str, Optional[CrossSection], Optional[CrossSection]]]:
    """
    Build an ordered list of (river, reach, station, xs_a, xs_b) tuples
    covering all XS from both geometries, ordered by source A's ordering
    then any B-only XS appended per reach.
    """
    index_a: Dict[Tuple, CrossSection] = {}
    index_b: Dict[Tuple, CrossSection] = {}
    order_a: List[Tuple[str, str, str]] = []

    if geom_a:
        for river in geom_a.rivers.values():
            for rch in river.reaches.values():
                for xs in rch.cross_sections:
                    k = _norm_key(xs.river, xs.reach, xs.station)
                    index_a[k] = xs
                    order_a.append((xs.river, xs.reach, xs.station))

    if geom_b:
        for river in geom_b.rivers.values():
            for rch in river.reaches.values():
                for xs in rch.cross_sections:
                    k = _norm_key(xs.river, xs.reach, xs.station)
                    index_b[k] = xs

    result = []
    seen: set = set()

    for river, reach, station in order_a:
        k = _norm_key(river, reach, station)
        seen.add(k)
        result.append((river, reach, station, index_a.get(k), index_b.get(k)))

    # B-only XS appended per reach
    if geom_b:
        for river in geom_b.rivers.values():
            for rch in river.reaches.values():
                for xs in rch.cross_sections:
                    k = _norm_key(xs.river, xs.reach, xs.station)
                    if k not in seen:
                        seen.add(k)
                        result.append((xs.river, xs.reach, xs.station, None, xs))

    return result


# ---------------------------------------------------------------------------
# Public: write merged geometry file
# ---------------------------------------------------------------------------

def write_merged_geometry(
    geom_a: Optional[GeometryFile],
    geom_b: Optional[GeometryFile],
    merge_configs: Dict[Tuple[str, str, str], MergeConfig],
    output_path: str,
    title: str,
) -> None:
    """
    Write a merged HEC-RAS geometry file.

    merge_configs : keyed by normalised (river, reach, station) tuples;
                   XS not present default to pass-through from A (or B).
    """
    lines: List[str] = []

    # 1. Geometry header from source A (replace the title line)
    src_header = geom_a or geom_b
    if src_header is None:
        raise ValueError("At least one geometry file must be provided.")

    for line in _extract_geom_header(src_header):
        if line.startswith("Geom Title="):
            lines.append(f"Geom Title={title}\n")
        else:
            lines.append(line)

    # 2. Rivers / reaches / cross-sections
    xs_pairs = _collect_xs_pairs(geom_a, geom_b)
    prev_reach_key: Optional[Tuple[str, str]] = None

    for river, reach, station, xs_a, xs_b in xs_pairs:
        reach_key = (river.strip().upper(), reach.strip().upper())

        if reach_key != prev_reach_key:
            prev_reach_key = reach_key
            reach_src = geom_a if geom_a else geom_b
            lines.extend(_extract_reach_header(reach_src, river, reach))

        config_key = _norm_key(river, reach, station)
        config = merge_configs.get(config_key)

        if xs_a is None:
            # Only in B: copy raw lines from geom_b
            if xs_b is not None and xs_b._raw_line_start >= 0:
                lines.extend(geom_b.raw_lines[xs_b._raw_line_start : xs_b._raw_line_end])
        elif xs_b is None or config is None:
            # Only in A, or no merge configured: copy raw lines from geom_a
            if xs_a._raw_line_start >= 0:
                lines.extend(geom_a.raw_lines[xs_a._raw_line_start : xs_a._raw_line_end])
        else:
            # Merge
            lines.extend(_build_merged_xs_lines(geom_a, xs_a, geom_b, xs_b, config))

    # Ensure file ends with a newline
    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _build_merged_xs_lines(
    geom_a: GeometryFile,
    xs_a: CrossSection,
    geom_b: GeometryFile,
    xs_b: CrossSection,
    config: MergeConfig,
) -> List[str]:
    """Generate all output lines for one merged cross-section."""
    out: List[str] = []

    # 1. Type RM Length= header (from source A)
    out.append(geom_a.raw_lines[xs_a._raw_line_start])

    # 2. Pre-key pass-through content from source A
    pre_key, post_key = _scan_xs_content(
        geom_a.raw_lines, xs_a._raw_line_start, xs_a._raw_line_end
    )
    out.extend(pre_key)

    # 3. Compute merged data
    sta_elev_a = transform_sta_elev(xs_a.sta_elev or [], config.transform_a)
    sta_elev_b = transform_sta_elev(xs_b.sta_elev or [], config.transform_b)

    merged_se = merge_sta_elev(
        sta_elev_a, sta_elev_b, config.breakpoints, config.segment_sources
    )
    merged_mann = merge_manning(xs_a, xs_b, config)

    if config.cutline_source == 'A':
        merged_cl = build_merged_cutline(
            xs_a, config.transform_a,
            config.breakpoints[0], config.breakpoints[-1],
        )
    else:
        merged_cl = build_merged_cutline(
            xs_b, config.transform_b,
            config.breakpoints[0], config.breakpoints[-1],
        )

    # Bank stations from the cutline source
    if config.cutline_source == 'A' and xs_a.bank_stations:
        left, right = xs_a.bank_stations
        merged_bank = (
            config.transform_a.apply_station(left),
            config.transform_a.apply_station(right),
        )
    elif config.cutline_source == 'B' and xs_b.bank_stations:
        left, right = xs_b.bank_stations
        merged_bank = (
            config.transform_b.apply_station(left),
            config.transform_b.apply_station(right),
        )
    else:
        merged_bank = None

    # 4. Write new key blocks
    if merged_cl is not None:
        out.extend(_write_cutline_block(merged_cl))
    if merged_se:
        out.extend(_write_sta_elev_block(merged_se))
    if merged_mann is not None:
        out.extend(_write_mann_block(merged_mann))
    if merged_bank is not None:
        out.append(_write_bank_sta_line(merged_bank))

    # 5. Post-key pass-through content from source A
    out.extend(post_key)

    return out
