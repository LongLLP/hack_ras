# hack_ras/gis/mesh.py
# Requires: pip install hack_ras[gis,results]
"""
Exporting 2D mesh face Manning's n as GIS polygons.

**Manning's n lives on faces, not cell centres.**  HEC-RAS builds a property
table per mesh *face*, and the Manning's n in it is the value the computations
use — see HEC's "Creating Hydraulic Property Tables for 2D Flow Areas".  The
plan HDF carries it in column 3 of ``Faces Area Elevation Values``, which the
file labels itself: ``attrs["Column"]`` = ``['Z', 'Area', 'Wetted Perimeter',
"Manning's n"]``.  Other hydraulic properties — volume, surface area, minimum
elevation — *are* cell-centred and apply to the cell; those live on
:func:`~hack_ras.results.reader.read_area_geometry` and
:func:`~hack_ras.results.reader.read_cell_volume_table` and are unaffected.

**There is deliberately no cell-centre Manning's n export.**  ``Cells Center
Manning's n`` exists in the HDF and RAS Mapper will draw it, but RAS does not
convey with it, so a layer built from it invites wrong conclusions.  This was not
a theoretical concern: on ``NKC_Hillside_Levee`` g07 vs g09 (2026-08-24) an edit
to one n-override polygon left ``Cells Center Manning's n`` bit-identical on all
4107 cells and every cell volume table unchanged, while changing 27 faces — three
of them 0.035 to 0.080 — and moving the computed WSE profile by up to 1.3 ft.  A
cell-centre export showed nothing at all.  The cell-centre reader and exporter
were removed on 2026-08-24 for that reason.  If the raw values are ever needed to
demonstrate that discrepancy, they are two lines away and need no library
support::

    with h5py.File(hdf_path, "r") as hdf:
        n = hdf[f"Geometry/2D Flow Areas/{area}/Cells Center Manning's n"][:]

**One value per face.**  RAS indexes face n by elevation, so it *can* vary with
stage when Vertical Variation in Manning's n is switched on.  It does not vary in
practice here: measured 2026-08-24 over every 2D model on disk — Hillside (all
geometries plus backups), PCA, Pattison, and the test fixture — **0 of 308,301
faces** vary.  :func:`~hack_ras.results.reader.read_face_geometry` therefore takes
the lowest-elevation row and logs a warning if a face ever varies, rather than
silently reducing a curve to one number.  Stage-varying face n is out of scope.

**Faces are exported as dual polygons, not polylines.**  A polyline layer at mesh
scale is unreadable — thousands of hairlines that cannot be filled or symbolised
by value.  Each face is instead drawn as its dual "diamond":
``[cell_L centre, face point A, cell_R centre, face point B]``, with the face as
the diagonal.  Alternating centre / face point around the ring is load-bearing:
ordering it ``[cL, A, B, cR]`` makes a self-intersecting bowtie (measured on
Hillside g07: 5331 of 5431 polygons invalid, total area collapsing to 26% of the
mesh).  The diamond is the face's own control volume, it needs no arbitrary
buffer width or clipping, and the polygons tile the mesh — measured on Hillside
g07, zero overlap between them and within 0.007% of the summed
``Cells Surface Area``, the residual being perimeter faces whose bend the
diagonal cuts across.  A Voronoi/Thiessen tessellation of face midpoints was
considered and rejected: it produces polygons that straddle mesh cells, needs
clipping to the perimeter, and encodes proximity rather than the connectivity
RAS actually solves on.

**Why one shapefile with an ``area`` field, not one per 2D flow area.**  Face
indices are local to each area, so two areas both start at face 0.  Keeping them
in one file with the area name alongside makes that collision visible and lets a
single symbology cover the whole model; splitting the file would hide it and
double the layer count for no gain.  Filter on ``area`` if a single-mesh layer is
wanted.

**Perimeter faces name a ghost cell** rather than a negative index.  RAS pads each
area's cell arrays with dummy cells and puts their centre at or near the face
midpoint, so the diamond collapses onto the one real half-cell — which is correct,
and is why the tiling still closes.

**Field names are 10 characters or shorter** because the shapefile driver
silently truncates longer ones, and two fields truncating to the same stem
collide.  ``mannings_n`` is exactly at the limit; do not lengthen it.
"""
from __future__ import annotations

import logging
import os

from hack_ras.resolve import CrsProjectionFileNotFound, read_crs_wkt
from hack_ras.results.reader import list_areas, read_face_geometry

FACE_FIELDS = ["area", "face_idx", "mannings_n", "cell_l", "cell_r",
               "face_len", "norm_x", "norm_y", "geometry"]


def _resolve_areas(hdf_path: str, areas: list[str] | None) -> list[str]:
    """Validate an area selection against what the HDF actually holds."""
    available = list_areas(hdf_path)
    if not available:
        raise ValueError(f"No 2D flow areas found in: {hdf_path}")
    if areas is None:
        return available
    missing = [a for a in areas if a not in available]
    if missing:
        raise ValueError(
            f"2D flow area(s) not in {hdf_path}: {', '.join(missing)}\n"
            f"Available: {', '.join(available)}"
        )
    return areas


def face_mannings_gdf(hdf_path: str, areas: list[str] | None = None):
    """
    Build a polygon GeoDataFrame of per-face Manning's n for a plan HDF.

    One row per mesh face, carrying the n RAS uses for conveyance across that
    face.  The geometry is the face's dual ("diamond") polygon — see
    :class:`~hack_ras.results.model.FaceGeometry` — so the layer draws as filled
    polygons that tile the mesh rather than as polylines, which are hard to read
    at mesh scale.

    Columns: ``area``, ``face_idx`` (local face index), ``mannings_n``,
    ``cell_l`` / ``cell_r`` (the two cells sharing the face, as local cell
    indices — the same indexing ``read_area_geometry`` and the WSE / volume
    output use, so the layer joins to those), ``face_len``, ``norm_x`` /
    ``norm_y`` (the face unit normal, i.e. the direction flow through it travels
    — enough to sort faces into streamwise and lateral in GIS without this
    function needing to know about any centreline), and ``geometry``.

    Faces whose dual polygon is degenerate are dropped.

    Parameters
    ----------
    hdf_path : str
        Absolute path to a .p##.hdf or .g##.hdf file.
    areas : list[str], optional
        2D flow area names to include.  Defaults to every area in the file.

    Returns
    -------
    geopandas.GeoDataFrame
        No CRS is attached — see :func:`export_face_mannings_shp`.

    Raises
    ------
    ValueError
        If the HDF has no 2D flow areas, or a requested area is not in it.
    KeyError
        If an area has no face property datasets (a pre-RAS-7.0 plan HDF).
    """
    import geopandas as gpd
    import pandas as pd

    frames = []
    for area in _resolve_areas(hdf_path, areas):
        faces = read_face_geometry(hdf_path, area)
        keep = [i for i, p in enumerate(faces.polygons) if p is not None]

        frames.append(gpd.GeoDataFrame({
            "area":       area,
            "face_idx":   keep,
            "mannings_n": faces.mannings_n[keep],
            "cell_l":     faces.cell_indexes[keep, 0],
            "cell_r":     faces.cell_indexes[keep, 1],
            "face_len":   faces.lengths[keep],
            "norm_x":     faces.normals[keep, 0],
            "norm_y":     faces.normals[keep, 1],
            "geometry":   [faces.polygons[i] for i in keep],
        }, geometry="geometry")[FACE_FIELDS])

    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry"
    )


def export_face_mannings_shp(
    hdf_path: str,
    out_path: str,
    areas: list[str] | None = None,
    crs: str | None = None,
) -> str:
    """
    Write a polygon shapefile of per-face Manning's n for a plan HDF.

    Geometry and fields are as described on :func:`face_mannings_gdf`.

    Parameters
    ----------
    hdf_path : str
        Absolute path to a .p##.hdf or .g##.hdf file.
    out_path : str
        Output .shp path.  Parent directories are created if needed.
    areas : list[str], optional
        2D flow area names to include.  Defaults to every area in the file.
    crs : str, optional
        CRS as WKT (or anything geopandas accepts).  Defaults to the CRS of the
        RAS project that owns the HDF, resolved with
        :func:`hack_ras.resolve.read_crs_wkt`.  If no projection file can be
        found, the shapefile is written without a .prj and a warning is logged —
        the coordinates are model coordinates either way.

    Returns
    -------
    str
        The path written (``out_path``).

    Raises
    ------
    ValueError
        If the HDF has no 2D flow areas, or a requested area is not in it.
    KeyError
        If an area has no face property datasets (a pre-RAS-7.0 plan HDF).
    """
    gdf = face_mannings_gdf(hdf_path, areas)
    return _write_shp(gdf, hdf_path, out_path, crs, "face")


def _write_shp(gdf, hdf_path: str, out_path: str, crs, what: str) -> str:
    """Attach a CRS (resolving it from the project if not given) and write."""
    if crs is None:
        try:
            crs = read_crs_wkt(os.path.dirname(os.path.abspath(hdf_path)))
        except CrsProjectionFileNotFound as exc:
            logging.warning("No CRS written to %s: %s", out_path, exc)

    if crs:
        gdf = gdf.set_crs(crs)

    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    gdf.to_file(out_path)

    logging.info("Wrote %d %s polygons to %s", len(gdf), what, out_path)
    return out_path
