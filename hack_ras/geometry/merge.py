# hack_ras/geometry/merge.py
"""
Cross-section merge utilities for the XS Editor.

Public API
----------
Transform        : horizontal / vertical offset and scale for one source
MergeConfig      : all user settings for one cross-section merge
merge_sta_elev() : stitch station/elevation data from two sources
merge_manning()  : merge Manning's n, snapped onto the merged station/elevation data
merge_ineff()    : carry ineffective flow areas into the merged cross-section
build_merged_cutline() : extend/clip a GIS cut line to a new station range
write_merged_geometry(): write a complete merged geometry file
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import CrossSection, GeometryFile, IneffArea, IneffFlowAreas, ManningDef, XSGISCutLine
from .blocks.xs_sta_elev import parse_sta_elev, write_sta_elev
from .blocks.xs_gis import parse_cutline, write_cutline
from .blocks.xs_mann import parse_mann, write_mann
from .blocks.xs_ineff import parse_ineff, write_ineff
from .blocks.xs_bank_sta import write_bank_sta
from .xs_interp import clip_xs_polyline, _cumulative_lengths
from .xs_cutline_blend import try_blend_extension


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Transform:
    """
    Horizontal and vertical translation for a cross-section.

    Applied as:
        new_station   = old_station   + h_offset
        new_elevation = old_elevation * v_scale + v_offset
    """
    h_offset: float = 0.0
    v_offset: float = 0.0
    v_scale: float = 1.0

    def apply_station(self, sta: float) -> float:
        return sta + self.h_offset

    def apply_elevation(self, elev: float) -> float:
        return elev * self.v_scale + self.v_offset

    def to_orig_station(self, new_sta: float) -> float:
        """Invert the station transform: new_sta → original station."""
        return new_sta - self.h_offset

    def is_identity(self) -> bool:
        return self.h_offset == 0.0 and self.v_offset == 0.0 and self.v_scale == 1.0

    def inverse(self) -> "Transform":
        """Return the Transform that exactly undoes this one.

        Used when swapping A/B: the alignment offset expressed from B's frame
        becomes the negated offset expressed from A's frame.
        """
        return Transform(
            h_offset=-self.h_offset,
            v_offset=-self.v_offset,
            v_scale=self.v_scale,
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
    cutline_source: str = 'A'     # 'A' or 'B'
    ineff_source: str = 'A'       # 'A' or 'B' -- ineffective flow areas (#XS Ineff=)
    preserve_cutline: bool = False
    blend_cutline: bool = False
    blend_cutline_threshold_pct: float = 10.0
    blend_cutline_search_radius: float = 20.0
    bank_stations_override: Optional[Tuple[float, float]] = None
    mann_def_override: Optional[ManningDef] = None


# ---------------------------------------------------------------------------
# Station / elevation helpers
# ---------------------------------------------------------------------------

#: Decimal places HEC-RAS station/elevation text fields are rounded to for any
#: cross-section this tool actually rebuilds.  This is the single definition of
#: "does station X exist in the output" used by segment stitching, bank station
#: snapping, and Manning's n snapping alike.
_OUTPUT_DECIMALS = 2


def _round_sta(v: float) -> float:
    return round(v, _OUTPUT_DECIMALS)


def _stations_equal(a: float, b: float) -> bool:
    """True when *a* and *b* round to the same output station."""
    return _round_sta(a) == _round_sta(b)


def _dedupe_exact_duplicates(
    sta_elev: List[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    """
    Drop adjacent points that are exact duplicates -- same station AND same
    elevation -- after rounding.  Rounding two very close but distinct source
    points (e.g. a natural survey point sitting right next to a point HEC-RAS
    itself interpolated at a bank station) can otherwise produce a genuine
    carbon-copy row, which HEC-RAS rejects.  Points that share a station but
    differ in elevation are a real vertical wall (a common, valid HEC-RAS
    construct) and must be kept as separate rows.
    """
    result: List[Tuple[float, float]] = []
    for point in sta_elev:
        if result and result[-1] == point:
            continue
        result.append(point)
    return result


def _snap_to_nearest_station(
    candidate: float, sta_elev: List[Tuple[float, float]]
) -> float:
    """Return whichever station already in *sta_elev* is closest to *candidate*.

    Ties are broken in favor of the higher elevation -- the common case is a
    vertical wall, where two points share the same station (e.g. a bank
    station sitting exactly on one) with different elevations.
    """
    if not sta_elev:
        return candidate
    s, _ = min(sta_elev, key=lambda p: (abs(p[0] - candidate), -p[1]))
    return s


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


def _vertex_at(
    sta_elev: List[Tuple[float, float]], station: float
) -> Optional[Tuple[float, float]]:
    """
    Return the (station, elevation) vertex at *station* within *sta_elev*.

    If a source point already lands on *station* (after rounding to output
    precision), that point's elevation is reused so the value is not
    fabricated.  Otherwise the elevation is linearly interpolated so a real
    vertex always exists there — required because HEC-RAS demands that
    segment breakpoints, bank stations, and Manning's n changes land exactly
    on a cross-section station.  Returns None only when *sta_elev* is empty.
    """
    if not sta_elev:
        return None
    for s, e in sta_elev:
        if _stations_equal(s, station):
            return (station, e)
    return (station, _interp_elevation(sta_elev, station))


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

    Every non-gap segment is guaranteed a vertex at its own start station,
    taken from its assigned source — an exact source point if one lands there
    (see _vertex_at), otherwise interpolated.  This is what lets a segment
    breakpoint (and, downstream, a bank station or Manning's n change) land
    exactly on a cross-section station even when the two source surveys don't
    share a common station there.  The final segment also gets a vertex at
    the overall end station.  A breakpoint's vertex always comes from the
    segment that *starts* there, not the one that ends there, so each station
    appears exactly once in the output.  Gap segments (source=None) contribute
    nothing, leaving a straight chord between the surrounding real vertices.
    """
    if len(segment_sources) != len(breakpoints) - 1:
        raise ValueError(
            "segment_sources length must equal len(breakpoints) - 1"
        )

    merged: List[Tuple[float, float]] = []
    last_index = len(segment_sources) - 1

    for i, source in enumerate(segment_sources):
        bp_start = breakpoints[i]
        bp_end = breakpoints[i + 1]

        if source is None:
            continue

        src = sta_elev_a if source == 'A' else sta_elev_b
        if not src:
            continue

        start_vertex = _vertex_at(src, bp_start)
        if start_vertex is not None:
            merged.append(start_vertex)

        merged.extend((s, e) for s, e in src if bp_start < s < bp_end)

        if i == last_index:
            end_vertex = _vertex_at(src, bp_end)
            if end_vertex is not None and (
                not merged or not _stations_equal(merged[-1][0], end_vertex[0])
            ):
                merged.append(end_vertex)

    return merged


# ---------------------------------------------------------------------------
# Manning's n helpers
# ---------------------------------------------------------------------------

def _n_at_station(entries: List[Tuple[float, float]], station: float) -> Optional[float]:
    """
    Step-function lookup: return the n_value whose station is the largest
    value ≤ *station*.  Returns None if no entry exists at or before *station*.
    """
    result: Optional[float] = None
    for sta, n in entries:
        if _round_sta(sta) <= _round_sta(station):
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
    xs_b: Optional[CrossSection],
    config: MergeConfig,
    merged_se: List[Tuple[float, float]],
) -> Optional[ManningDef]:
    """
    Merge Manning's n values for a modified cross-section.

    Per segment, pull n-values from that segment's assigned source (the same
    source used for the station/elevation data there), then snap each entry's
    station onto the nearest station actually present in *merged_se* — the
    already-finalized output station/elevation block.  This guarantees every
    n-value change lands exactly on a cross-section station, as HEC-RAS
    requires.  Always produces method=-1 output ("Horizontal Variation" ON),
    since merged segments may come from different sources and carry arbitrary
    breakpoints.
    """
    result: List[Tuple[float, float]] = []

    for i, source in enumerate(config.segment_sources):
        bp_start = config.breakpoints[i]
        bp_end = config.breakpoints[i + 1]

        if source is None:
            continue

        xs_src = xs_a if source == 'A' else xs_b
        t = config.transform_a if source == 'A' else config.transform_b
        mann_def = xs_src.manning_def if xs_src is not None else None
        if mann_def is None:
            continue

        for sta, n_val in _mann_def_to_entries_in_segment(mann_def, t, bp_start, bp_end):
            snapped = _snap_to_nearest_station(sta, merged_se)
            if result and _stations_equal(result[-1][0], snapped):
                # Same output station as the previous entry: the later value
                # wins, so a new segment's opening n-value at a breakpoint
                # replaces the previous segment's value there.
                result[-1] = (result[-1][0], n_val)
            else:
                result.append((snapped, n_val))

    if not result:
        return None

    # HEC-RAS refuses to run a method -1 block with no n-value on the XS's
    # first station.  An extension segment can start left of where its
    # source's n-data begins (the step-function lookup finds nothing there),
    # so extend the earliest known n-value to the edge — mirroring how the
    # geometry extension flat-lines the edge elevation.
    if merged_se and not _stations_equal(result[0][0], merged_se[0][0]):
        result.insert(0, (merged_se[0][0], result[0][1]))

    return ManningDef(method=-1, entries=result)


def _method0_manning(
    xs_a: CrossSection,
    config: MergeConfig,
    merged_se: List[Tuple[float, float]],
    merged_bank: Optional[Tuple[float, float]],
) -> Optional[ManningDef]:
    """
    Preserve Manning method 0 (LOB/Channel/ROB, "Horizontal Variation" OFF)
    when an all-A edit is applied to a method-0 cross-section.

    Truncating, extending, or gapping A takes no Manning data from B and
    doesn't change the LOB/Channel/ROB structure, so the user doesn't expect
    the n-value method to flip to horizontal variation.  The output keeps
    method 0: A's three n-values positionally, keyed to the merged left edge
    and the output's own (already snapped) bank stations.  A bank cut off by
    a truncation has been snapped to the surviving edge station — method 0 is
    still kept; that side's region simply becomes zero-width.

    Returns None when the shape doesn't apply and the caller should fall back
    to the ordinary method -1 merge.
    """
    mann = xs_a.manning_def
    if mann is None or mann.method != 0 or len(mann.entries) != 3:
        return None
    if merged_bank is None or not merged_se:
        return None
    if any(s not in ('A', None) for s in config.segment_sources):
        return None
    n_lob, n_ch, n_rob = (n for _, n in mann.entries)
    return ManningDef(method=0, entries=[
        (merged_se[0][0], n_lob),
        (merged_bank[0], n_ch),
        (merged_bank[1], n_rob),
    ])


# ---------------------------------------------------------------------------
# Ineffective flow area (IFA) helpers
# ---------------------------------------------------------------------------

def merge_ineff(
    xs_a: CrossSection,
    xs_b: Optional[CrossSection],
    config: MergeConfig,
) -> Optional[IneffFlowAreas]:
    """
    Carry the chosen source's ineffective flow areas into the merged output.

    The source's ifa_type is preserved verbatim ('normal' stays 'normal',
    'multiple_block' stays 'multiple_block') rather than always converted to
    'multiple_block'.  A "normal" area's open-ended fields -- the left area's
    start_sta and the right area's end_sta, written as blank/0.0 in the
    source when left unbounded -- mean "extend to whatever this
    cross-section's edge turns out to be," a meaning HEC-RAS resolves itself
    whenever it reads the file, against that file's actual geometry.  Baking
    in a literal output station here would only freeze today's edges into
    the file and lose that self-updating behavior, so those two fields are
    carried through as literal 0.0 -- *without* applying the source's
    Transform, since transforming a sentinel would turn it into an arbitrary
    non-zero value and destroy its meaning.  Every other field -- the
    non-sentinel station in a "normal" area, and every field in a
    'multiple_block' area (which has no sentinel semantics at all) -- is
    shifted by the chosen source's Transform like any other station or
    elevation value.  A blank ("infinite height") elevation is likewise
    carried through as None: 'multiple_block' areas always have a real
    elevation already (never blank in a valid source file), and a "normal"
    area's blank elevation is valid on its own terms, so there's nothing to
    resolve either way.  Areas are not required to land on an existing
    output station (unlike bank stations and Manning's n breakpoints).
    """
    xs_src = xs_a if config.ineff_source == 'A' else xs_b
    t = config.transform_a if config.ineff_source == 'A' else config.transform_b
    src_ineff = xs_src.ineff if xs_src is not None else None
    if src_ineff is None:
        return None

    is_normal = src_ineff.ifa_type == 'normal'
    last_index = len(src_ineff.areas) - 1

    out_areas: List[IneffArea] = []
    for i, area in enumerate(src_ineff.areas):
        is_left_sentinel = is_normal and i == 0 and area.start_sta == 0.0
        is_right_sentinel = is_normal and i == last_index and area.end_sta == 0.0

        start_sta = area.start_sta if is_left_sentinel else t.apply_station(area.start_sta)
        end_sta = area.end_sta if is_right_sentinel else t.apply_station(area.end_sta)
        elevation = None if area.elevation is None else t.apply_elevation(area.elevation)

        out_areas.append(IneffArea(
            start_sta=_round_sta(start_sta),
            end_sta=_round_sta(end_sta),
            elevation=None if elevation is None else round(elevation, _OUTPUT_DECIMALS),
            permanent=area.permanent,
        ))

    return IneffFlowAreas(ifa_type=src_ineff.ifa_type, areas=out_areas)


# ---------------------------------------------------------------------------
# Public: GIS cut-line construction
# ---------------------------------------------------------------------------

def build_merged_cutline(
    source_xs: CrossSection,
    source_transform: Transform,
    merged_sta_start: float,
    merged_sta_end: float,
    other_xs: Optional[CrossSection] = None,
    blend: bool = False,
    blend_threshold_pct: float = 10.0,
    blend_search_radius: float = 20.0,
) -> Optional[XSGISCutLine]:
    """
    Build a GIS cut line for the merged cross-section.

    The source XS's cut line maps its original station range to GIS
    coordinates.  If the merged station range extends beyond the original,
    the cut line is extended.  By default the extension is a straight-line
    projection using the tangent at the end.  When ``blend=True`` and
    ``other_xs`` is provided, the function first attempts to use the other
    geometry's cut line for the extension via :func:`try_blend_extension`;
    it falls back to straight-line projection if the blend fails validation.

    Parameters
    ----------
    source_xs             : CrossSection whose cut line to use
    source_transform      : Transform applied to that source's station data
    merged_sta_start      : first station of the merged XS (merged frame)
    merged_sta_end        : last station of the merged XS (merged frame)
    other_xs              : other geometry's CrossSection (for blend extension)
    blend                 : attempt to use other_xs's cut line to extend
    blend_threshold_pct   : max mean deviation as % of source arc length
    blend_search_radius   : GIS-unit search radius for the handoff point
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

    use_blend = (
        blend
        and other_xs is not None
        and other_xs.cutline is not None
        and len(other_xs.cutline.points) >= 2
    )
    other_pts = other_xs.cutline.points if use_blend else []

    # Backward extension
    if back_dist > 1e-9:
        ext = None
        if use_blend:
            ext = try_blend_extension(
                pts, other_pts, "back", blend_threshold_pct, blend_search_radius
            )
        if ext:
            new_pts = ext + new_pts
        else:
            dx = pts[1][0] - pts[0][0]
            dy = pts[1][1] - pts[0][1]
            seg_len = math.hypot(dx, dy)
            if seg_len > 1e-9:
                ux, uy = dx / seg_len, dy / seg_len
                new_pts = [(pts[0][0] - back_dist * ux, pts[0][1] - back_dist * uy)] + new_pts

    # Forward extension
    if fwd_dist > 1e-9:
        ext = None
        if use_blend:
            ext = try_blend_extension(
                pts, other_pts, "fwd", blend_threshold_pct, blend_search_radius
            )
        if ext:
            new_pts = new_pts + ext
        else:
            dx = pts[-1][0] - pts[-2][0]
            dy = pts[-1][1] - pts[-2][1]
            seg_len = math.hypot(dx, dy)
            if seg_len > 1e-9:
                ux, uy = dx / seg_len, dy / seg_len
                fwd_pt = (pts[-1][0] + fwd_dist * ux, pts[-1][1] + fwd_dist * uy)
                if math.hypot(fwd_pt[0] - new_pts[-1][0], fwd_pt[1] - new_pts[-1][1]) > 1e-9:
                    new_pts.append(fwd_pt)

    return XSGISCutLine(len(new_pts), new_pts)


# ---------------------------------------------------------------------------
# Raw-line helpers for pass-through content
# ---------------------------------------------------------------------------

_KEY_PREFIXES = (
    "XS GIS Cut Line=",
    "#Sta/Elev=",
    "#Mann=",
    "#XS Ineff=",
    "Bank Sta=",
)

_KEY_PARSERS = {
    "XS GIS Cut Line=": parse_cutline,
    "#Sta/Elev=": parse_sta_elev,
    "#Mann=": parse_mann,
    "#XS Ineff=": parse_ineff,
    "Bank Sta=": lambda lines, i: (None, 1),
}


def _scan_xs_content(
    raw_lines: List[str], xs_start: int, xs_end: int
) -> Tuple[List[str], List[Tuple[str, List[str]]]]:
    """
    Partition an XS's raw lines (excluding the Type RM Length header) around
    the key blocks that _build_merged_xs_lines replaces.

    Returns
    -------
    initial_lines
        Non-key lines appearing before the first key block.
    key_segments
        Ordered list of (key_prefix, interstitial_lines) pairs, one per key
        block encountered.  *key_prefix* identifies which block was found;
        *interstitial_lines* are the non-key lines that immediately follow
        that block (before the next key block or end-of-XS).

    Preserving *interstitial_lines* per key block lets the writer re-emit them
    in their original positions, keeping lines like ``Node Last Edited Time=``
    and ``XS Rating Curve=`` exactly where the source file had them.
    """
    initial_lines: List[str] = []
    key_segments: List[Tuple[str, List[str]]] = []
    current_trail: Optional[List[str]] = None

    i = xs_start + 1  # skip Type RM Length line
    while i < xs_end:
        line = raw_lines[i]
        stripped = line.strip()

        matched_prefix: Optional[str] = None
        matched_parser = None
        for prefix, parser_fn in _KEY_PARSERS.items():
            if stripped.startswith(prefix):
                matched_prefix = prefix
                matched_parser = parser_fn
                break

        if stripped.startswith("River Reach="):
            # Stop here — remainder is the next reach's header, not this XS.
            break
        elif matched_parser is not None:
            current_trail = []
            key_segments.append((matched_prefix, current_trail))
            _, consumed = matched_parser(raw_lines, i)
            i += consumed
        elif current_trail is None:
            initial_lines.append(line)
            i += 1
        else:
            current_trail.append(line)
            i += 1

    return initial_lines, key_segments


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


def _raw_sta_elev_lines(geom: GeometryFile, xs: CrossSection) -> List[str]:
    """Return the raw #Sta/Elev= block lines verbatim from the geometry file."""
    raw = geom.raw_lines
    for i in range(xs._raw_line_start, xs._raw_line_end):
        if raw[i].strip().startswith("#Sta/Elev="):
            _, consumed = parse_sta_elev(raw, i)
            return raw[i : i + consumed]
    return []


def _xs_raw_lines(geom: GeometryFile, xs: CrossSection) -> List[str]:
    """
    Raw lines for a single XS, trimmed to exclude trailing reach-header content.

    Because _raw_line_end is set to the start of the *next* XS (which may be in
    a different reach), the raw slice can include the River Reach= line and
    reach-level lines of the next reach.  Strip them so the caller can emit the
    reach header once and only once.
    """
    raw = geom.raw_lines[xs._raw_line_start : xs._raw_line_end]
    for j, line in enumerate(raw):
        if line.strip().startswith("River Reach="):
            return raw[:j]
    return raw


def _xs_in_file_order(geom: GeometryFile) -> List[CrossSection]:
    """
    Return all cross-sections in the order they appear in raw_lines,
    preserving the original reach interleaving rather than grouping by river.
    """
    xs_all = []
    for river in geom.rivers.values():
        for rch in river.reaches.values():
            xs_all.extend(rch.cross_sections)
    xs_all.sort(key=lambda xs: xs._raw_line_start)
    return xs_all


def _collect_xs_pairs(
    geom_a: Optional[GeometryFile],
    geom_b: Optional[GeometryFile],
) -> List[Tuple[str, str, str, Optional[CrossSection], Optional[CrossSection]]]:
    """
    Build an ordered list of (river, reach, station, xs_a, xs_b) tuples
    covering only XS that exist in source A, in A's file order.  B-only XS are
    excluded because the output always follows A's structure.
    """
    index_a: Dict[Tuple, CrossSection] = {}
    index_b: Dict[Tuple, CrossSection] = {}
    order_a: List[Tuple[str, str, str]] = []

    if geom_a:
        for xs in _xs_in_file_order(geom_a):
            k = _norm_key(xs.river, xs.reach, xs.station)
            index_a[k] = xs
            order_a.append((xs.river, xs.reach, xs.station))

    if geom_b:
        for xs in _xs_in_file_order(geom_b):
            k = _norm_key(xs.river, xs.reach, xs.station)
            index_b[k] = xs

    result = []
    seen: set = set()

    for river, reach, station in order_a:
        k = _norm_key(river, reach, station)
        seen.add(k)
        result.append((river, reach, station, index_a.get(k), index_b.get(k)))

    # B-only XS are excluded: the output always follows A's structure.

    return result


# ---------------------------------------------------------------------------
# Public: write merged geometry file
# ---------------------------------------------------------------------------

def _is_trivial_config(config: MergeConfig, xs_master: CrossSection) -> bool:
    """
    Return True when the config results in output byte-for-byte identical to
    the master XS (A) — every configurable option must point to A, and the
    outer breakpoints must span A's full station extent.

    Checks:
      - Geometry (sta/elev): single segment from A, identity transform, and
        breakpoints matching A's actual first/last station (a truncated or
        extended all-A config is a real edit, not a pass-through).
        Manning's n is derived from the same segment sources, so a trivial
        geometry config implies trivial Manning's n too — no separate check.
      - GIS cut line source: A
      - Ineffective flow area source: A

    Blocked obstructions are not currently configurable; they always pass
    through verbatim from A via _scan_xs_content, so no check is needed
    for them.
    """
    if len(config.breakpoints) != 2:
        return False
    if len(config.segment_sources) != 1:
        return False
    if config.segment_sources[0] != 'A':
        return False
    if not config.transform_a.is_identity():
        return False
    if config.cutline_source != 'A':
        return False
    if config.ineff_source != 'A':
        return False
    se = xs_master.sta_elev
    if se:
        if not _stations_equal(config.breakpoints[0], se[0][0]):
            return False
        if not _stations_equal(config.breakpoints[-1], se[-1][0]):
            return False
    return True


def write_merged_geometry(
    geom_a: Optional[GeometryFile],
    geom_b: Optional[GeometryFile],
    merge_configs: Dict[Tuple[str, str, str], MergeConfig],
    output_path: str,
    title: str,
) -> None:
    """
    Write a merged HEC-RAS geometry file.

    Geometry A is always the master: it provides the geometry header, reach
    headers, the output's XS structure (B-only XS are excluded), and any XS
    without a merge config (or with a trivial config that simply mirrors A).
    An A-only XS with a non-trivial config referencing only A data (e.g. a
    trim) is honored like any other merge; a config requesting Geometry B
    data for an A-only XS is unsatisfiable and falls back to a raw Geometry A
    pass-through.
    The GUI's "Swap A / B" physically exchanges the two files instead of
    flipping a master flag, so no master-selection parameter exists here.

    merge_configs : keyed by normalised (river, reach, station) tuples.
    """
    geom_master = geom_a if geom_a is not None else geom_b
    if geom_master is None:
        raise ValueError("At least one geometry file must be provided.")

    lines: List[str] = []

    # 1. Geometry header from master (replace the title line)
    for line in _extract_geom_header(geom_master):
        if line.startswith("Geom Title="):
            lines.append(f"Geom Title={title}\n")
        else:
            lines.append(line)

    # 2. Rivers / reaches / cross-sections (A's structure; empty if A not loaded)
    xs_pairs = _collect_xs_pairs(geom_a, geom_b)
    prev_reach_key: Optional[Tuple[str, str]] = None

    for river, reach, station, xs_a, xs_b in xs_pairs:
        reach_key = (river.strip().upper(), reach.strip().upper())

        if reach_key != prev_reach_key:
            prev_reach_key = reach_key
            lines.extend(_extract_reach_header(geom_master, river, reach))

        config = merge_configs.get(_norm_key(river, reach, station))

        # A config that requests Geometry B data (segment source, cut line
        # source, or IFA source) is only satisfiable when the XS exists in B.
        # For A-only XS such a config falls back to a raw pass-through — the
        # GUI warns about these at export time.  An all-A config (e.g. a
        # trim) is honored even without a B counterpart.
        b_unsatisfiable = xs_b is None and config is not None and (
            'B' in config.segment_sources
            or config.cutline_source == 'B'
            or config.ineff_source == 'B'
        )

        if config is None or _is_trivial_config(config, xs_a) or b_unsatisfiable:
            # No merge configured, trivial (pass-through), or requests B data
            # that doesn't exist for this XS
            if xs_a._raw_line_start >= 0:
                lines.extend(_xs_raw_lines(geom_master, xs_a))
        else:
            # Real merge; xs_b may be None when the config references only A
            lines.extend(
                _build_merged_xs_lines(geom_a, xs_a, geom_b, xs_b, config)
            )

    # Ensure file ends with a newline
    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _insert_bank_station(
    sta_elev: List[Tuple[float, float]], station: float
) -> List[Tuple[float, float]]:
    """Insert an interpolated point at station into sta_elev if not already present."""
    if not sta_elev:
        return sta_elev
    if any(_stations_equal(s, station) for s, _ in sta_elev):
        return sta_elev
    elev = _interp_elevation(sta_elev, station)
    result = list(sta_elev)
    for i, (s, _) in enumerate(result):
        if s > station:
            result.insert(i, (station, elev))
            return result
    result.append((station, elev))
    return result


def _build_merged_xs_lines(
    geom_a: GeometryFile,
    xs_a: CrossSection,
    geom_b: Optional[GeometryFile],
    xs_b: Optional[CrossSection],
    config: MergeConfig,
) -> List[str]:
    """Generate all output lines for one merged cross-section.

    xs_b may be None only when *config* references no Geometry B data
    (write_merged_geometry routes B-referencing configs on A-only XS to the
    raw pass-through path instead).
    """
    out: List[str] = []

    # 1. Type RM Length= header (from source A)
    out.append(geom_a.raw_lines[xs_a._raw_line_start])

    # 2. Scan source A for interstitial content around each key block
    initial_lines, key_segments = _scan_xs_content(
        geom_a.raw_lines, xs_a._raw_line_start, xs_a._raw_line_end
    )
    # Build a lookup: key_prefix -> interstitial lines that followed it in source A
    trail_for: Dict[str, List[str]] = {}
    for kp, trail in key_segments:
        trail_for.setdefault(kp, trail)

    out.extend(initial_lines)

    # 3. Compute merged data
    sta_elev_a = transform_sta_elev(xs_a.sta_elev or [], config.transform_a)
    sta_elev_b = transform_sta_elev(
        (xs_b.sta_elev if xs_b is not None else None) or [], config.transform_b
    )

    merged_se = merge_sta_elev(
        sta_elev_a, sta_elev_b, config.breakpoints, config.segment_sources,
    )

    # Insert bank station override points before rounding, so the interpolation
    # that locates them uses full source precision.
    if config.bank_stations_override is not None and merged_se:
        merged_se = _insert_bank_station(merged_se, config.bank_stations_override[0])
        merged_se = _insert_bank_station(merged_se, config.bank_stations_override[1])

    # When all sta/elev comes from A with an identity transform AND the
    # breakpoints span A's full extent (a truncated/extended all-A config is a
    # real edit), the source's own raw lines are written verbatim (see step 4)
    # to preserve original numeric formatting.  In that case leave merged_se
    # unrounded too, since it already exactly matches what gets written.
    # Otherwise round every station and elevation to output precision — this
    # becomes the single source of truth that bank stations and Manning's n
    # breakpoints below are snapped onto, guaranteeing they land exactly on a
    # station HEC-RAS will see.
    se_unchanged = (
        len(config.breakpoints) == 2
        and len(config.segment_sources) == 1
        and config.segment_sources[0] == 'A'
        and config.transform_a.is_identity()
        and config.bank_stations_override is None
        and bool(xs_a.sta_elev)
        and _stations_equal(config.breakpoints[0], xs_a.sta_elev[0][0])
        and _stations_equal(config.breakpoints[-1], xs_a.sta_elev[-1][0])
    )
    if not se_unchanged:
        merged_se = [(_round_sta(s), round(e, _OUTPUT_DECIMALS)) for s, e in merged_se]
        merged_se = _dedupe_exact_duplicates(merged_se)

    if merged_se:
        actual_sta_start = merged_se[0][0]
        actual_sta_end = merged_se[-1][0]
    else:
        actual_sta_start = config.breakpoints[0]
        actual_sta_end = config.breakpoints[-1]

    # Bank stations are station-space values referencing the #Sta/Elev= array.
    # They must follow the geometry source (A = master), not the GIS cut line source.
    # Only fall back to B's bank stations when A has none and the entire geometry is from B.
    # Computed before Manning's n because the method-0 pass-through below is
    # keyed to the output's own snapped bank stations.
    all_from_b = bool(config.segment_sources) and all(
        s == 'B' for s in config.segment_sources
    )
    if config.bank_stations_override is not None:
        merged_bank = config.bank_stations_override
    elif xs_a.bank_stations and not all_from_b:
        left, right = xs_a.bank_stations
        merged_bank = (
            config.transform_a.apply_station(left),
            config.transform_a.apply_station(right),
        )
    elif all_from_b and xs_b.bank_stations:
        left, right = xs_b.bank_stations
        merged_bank = (
            config.transform_b.apply_station(left),
            config.transform_b.apply_station(right),
        )
    else:
        merged_bank = None

    # Bank stations must land exactly on a station in the block that's about to
    # be written, so snap them onto the nearest station actually present there.
    if merged_bank is not None and merged_se:
        merged_bank = (
            _snap_to_nearest_station(merged_bank[0], merged_se),
            _snap_to_nearest_station(merged_bank[1], merged_se),
        )

    if config.mann_def_override is not None:
        merged_mann = config.mann_def_override
    else:
        merged_mann = _method0_manning(xs_a, config, merged_se, merged_bank)
        if merged_mann is None:
            merged_mann = merge_manning(xs_a, xs_b, config, merged_se)

    merged_ineff = merge_ineff(xs_a, xs_b, config)

    if config.preserve_cutline:
        source_xs = xs_a if config.cutline_source == 'A' else xs_b
        merged_cl = source_xs.cutline
    else:
        if config.cutline_source == 'A':
            merged_cl = build_merged_cutline(
                xs_a, config.transform_a,
                actual_sta_start, actual_sta_end,
                other_xs=xs_b,
                blend=config.blend_cutline,
                blend_threshold_pct=config.blend_cutline_threshold_pct,
                blend_search_radius=config.blend_cutline_search_radius,
            )
        else:
            merged_cl = build_merged_cutline(
                xs_b, config.transform_b,
                actual_sta_start, actual_sta_end,
                other_xs=xs_a,
                blend=config.blend_cutline,
                blend_threshold_pct=config.blend_cutline_threshold_pct,
                blend_search_radius=config.blend_cutline_search_radius,
            )

    # 4. Write each key block followed by its original interstitial lines.
    #    This preserves the exact position of every non-key line from the source
    #    (e.g. "Node Last Edited Time=" stays between cutline and #Sta/Elev=,
    #    "XS Rating Curve=" stays after Bank Sta=, etc.).
    if merged_cl is not None:
        out.extend(write_cutline(merged_cl))
    out.extend(trail_for.get("XS GIS Cut Line=", []))

    if merged_se:
        if se_unchanged:
            # Write the raw source lines verbatim to preserve original numeric
            # formatting (idiosyncratic spacing, ".07" vs "0.07", etc.).
            raw_se = _raw_sta_elev_lines(geom_a, xs_a)
            out.extend(raw_se if raw_se else write_sta_elev(merged_se))
        else:
            out.extend(write_sta_elev(merged_se))
    out.extend(trail_for.get("#Sta/Elev=", []))

    if merged_mann is not None:
        out.extend(write_mann(merged_mann))
    out.extend(trail_for.get("#Mann=", []))

    if merged_ineff is not None:
        out.extend(write_ineff(merged_ineff))
    out.extend(trail_for.get("#XS Ineff=", []))

    if merged_bank is not None:
        out.append(write_bank_sta(merged_bank))
    out.extend(trail_for.get("Bank Sta=", []))

    return out
