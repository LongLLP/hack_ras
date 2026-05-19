# hack_ras/gis/profile.py
# Requires: pip install hack_ras[gis,results]
from __future__ import annotations
import dataclasses

import numpy as np
from shapely.geometry import LineString, Point

from hack_ras.results.model import AreaGeometry
from .model import ProfilePoint


# ---------------------------
# Private geometry helpers
# ---------------------------

def _as_linestring(geom) -> LineString:
    """Coerce a shapely geometry to a LineString. Raises ValueError on unsupported types."""
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        return LineString([c for part in geom.geoms for c in part.coords])
    raise ValueError(f"Unsupported profile geometry type: {geom.geom_type}")


def _boundary_crossings(line: LineString, boundary_poly) -> list[float]:
    """Sorted station values where line crosses the outer ring of boundary_poly."""
    crossing = line.intersection(boundary_poly.exterior)
    if crossing.is_empty:
        return []
    geoms = [crossing] if not hasattr(crossing, "geoms") else list(crossing.geoms)
    stations = []
    for g in geoms:
        if g.geom_type == "Point":
            stations.append(line.project(g))
        elif g.geom_type == "LineString":
            stations.append(line.project(Point(g.coords[0])))
            stations.append(line.project(Point(g.coords[-1])))
    return sorted(set(round(s, 4) for s in stations))


def _find_cell_at_point(pt: Point, cell_gdf, tol: float = 0.01) -> int | None:
    """Return cell_idx of the cell containing (or within tol of) pt, else None."""
    for idx in cell_gdf.sindex.intersection(pt.buffer(tol).bounds):
        if cell_gdf.iloc[idx].geometry.distance(pt) <= tol:
            return int(cell_gdf.iloc[idx]["cell_idx"])
    return None


# ---------------------------
# Profile station computation
# ---------------------------

def compute_profile_stations(
    line: LineString,
    area_data: dict[str, AreaGeometry],
) -> list[ProfilePoint]:
    """
    Build the ordered list of output points for one profile line.

    Endpoint rule: if station=0 or station=L lies within a cell, that station
    is kept and the cell's centre projection is suppressed (avoids near-duplicate
    points a few feet apart).

    Perimeter rule: cells with NaN min_elevation are excluded from the spatial
    index (cell_gdf) and will never appear as profile points.

    Parameters
    ----------
    line : shapely.LineString
        Profile line in the same CRS as the HEC-RAS model.
    area_data : dict[str, AreaGeometry]
        Keyed by 2D flow area name.

    Returns
    -------
    list[ProfilePoint]
        Sorted by station. wse, min_elev, and status are None (call assign_wse
        to populate them).
    """
    pts: list[ProfilePoint] = []
    L = line.length

    # ── Identify endpoint cells (suppress their centre projections) ──────────
    suppressed: set[tuple[str, int]] = set()
    endpoint_pts: list[ProfilePoint] = []

    for st_val in (0.0, L):
        ep = line.interpolate(st_val)
        for area, ag in area_data.items():
            if not ag.boundary.buffer(0.01).contains(ep):
                continue
            cidx = _find_cell_at_point(ep, ag.cell_gdf)
            if cidx is not None:
                suppressed.add((area, cidx))
            endpoint_pts.append(ProfilePoint(
                station=st_val, area=area,
                cell_idx=cidx, point_type="endpoint",
            ))
            break

    # ── Cell centres and boundary crossings ─────────────────────────────────
    for area, ag in area_data.items():
        if not line.intersects(ag.boundary):
            continue

        for row_idx in ag.cell_gdf.sindex.intersection(line.bounds):
            row = ag.cell_gdf.iloc[row_idx]
            if not row.geometry.intersects(line):
                continue
            cidx = int(row["cell_idx"])
            if (area, cidx) in suppressed:
                continue
            cx, cy = ag.cell_centers[cidx]
            st = line.project(Point(cx, cy))
            pts.append(ProfilePoint(
                station=st, area=area,
                cell_idx=cidx, point_type="cell",
            ))

        for st in _boundary_crossings(line, ag.boundary):
            pts.append(ProfilePoint(
                station=st, area=area,
                cell_idx=None, point_type="boundary",
            ))

    pts.extend(endpoint_pts)

    # ── Sort and deduplicate ─────────────────────────────────────────────────
    pts.sort(key=lambda p: p.station)
    dedup: list[ProfilePoint] = []
    for p in pts:
        if dedup and abs(dedup[-1].station - p.station) < 0.01:
            # When coincident, prefer cell-centre over boundary/endpoint
            if p.point_type == "cell" and dedup[-1].point_type != "cell":
                dedup[-1] = p
        else:
            dedup.append(p)

    return dedup


# ---------------------------
# WSE assignment
# ---------------------------

def assign_wse(
    pts: list[ProfilePoint],
    area_wse: dict[str, np.ndarray],
    area_min_elev: dict[str, np.ndarray],
) -> list[ProfilePoint]:
    """
    Return a new list of ProfilePoints with wse, min_elev, and status populated.

    Wet cell  → WSE from HEC-RAS output; status='wet'
    Dry cell  → min_elevation (WSE == min_elev means cell never got wet); status='dry'
    Boundary/endpoint without cell_idx → linearly interpolated from adjacent
                                         cell points; status='interpolated'
    No usable neighbours → wse remains None; status='no_cell'

    Parameters
    ----------
    pts : list[ProfilePoint]
        Output of compute_profile_stations.
    area_wse : dict[str, np.ndarray]
        {area_name: wse_array} as returned by read_wse.
    area_min_elev : dict[str, np.ndarray]
        {area_name: min_elevations} from AreaGeometry.min_elevations.
    """
    n = len(pts)
    wse_vals  = [None] * n
    me_vals   = [None] * n
    statuses  = [None] * n

    # Pass 1: direct lookup for points with a known cell_idx
    for i, p in enumerate(pts):
        if p.cell_idx is None:
            continue
        wse_arr = area_wse.get(p.area)
        me_arr  = area_min_elev.get(p.area)
        if wse_arr is None:
            continue
        raw = float(wse_arr[p.cell_idx])
        me  = float(me_arr[p.cell_idx]) if me_arr is not None else None
        me_vals[i] = me
        if me is not None and raw <= me + 0.01:
            wse_vals[i] = me
            statuses[i] = "dry"
        else:
            wse_vals[i] = raw
            statuses[i] = "wet"

    # Pass 2: interpolate boundary/endpoint points with no cell_idx
    for i, p in enumerate(pts):
        if p.cell_idx is not None or p.point_type not in ("boundary", "endpoint"):
            continue
        cell_pairs = [
            (pts[j].station, wse_vals[j])
            for j in range(n)
            if pts[j].area == p.area
            and pts[j].point_type == "cell"
            and wse_vals[j] is not None
        ]
        if not cell_pairs:
            statuses[i] = "no_cell"
            continue
        cell_pairs.sort()
        sts  = np.array([c[0] for c in cell_pairs])
        wses = np.array([c[1] for c in cell_pairs], dtype=float)
        wse_vals[i] = float(np.interp(p.station, sts, wses))
        statuses[i] = "interpolated"

    return [
        dataclasses.replace(p, wse=wse_vals[i], min_elev=me_vals[i], status=statuses[i])
        for i, p in enumerate(pts)
    ]
