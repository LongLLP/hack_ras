# hack_ras/gis/mesh.py
# Requires: pip install hack_ras[gis,results]
"""
Exporting 2D mesh cell attributes as GIS polygons.

HEC-RAS Mapper can display cell Manning's n as a raster, but there is no way to
get the *per-cell* value out of it — the raster is resampled from the land-cover
layer, not from what the solver actually used.  The plan HDF holds the real
number the run was computed with, one float per cell, in
``Cells Center Manning's n``.  Pairing it with the cell polygon rebuilt from the
face points gives a layer that can be symbolized, joined, or diffed between
plans in any GIS.

**Why one shapefile with an ``area`` field, not one per 2D flow area.**  Cell
indices are local to each area, so two areas both start at cell 0.  Keeping them
in one file with the area name alongside makes that collision visible and lets a
single symbology cover the whole model; splitting the file would hide it and
double the layer count for no gain.  Filter on ``area`` if a single-mesh layer is
wanted.

**Perimeter dummy cells are dropped.**  RAS pads each area's cell arrays with
ghost cells that carry a Manning's n value but have fewer than three face points
and a NaN minimum elevation — there is no polygon to draw.  They are excluded by
reusing :func:`~hack_ras.results.reader.read_area_geometry`'s ``cell_gdf``, which
already applies exactly that filter, rather than re-deriving it here.  Observed
live on the Hillside model (``NKC_Hillside_Levee.p12.hdf``): 229 of 2844 cells in
the ``Interior`` mesh and 191 of 1262 in ``RockCr`` are ghosts, so the count is
not a rounding-error concern.

**Field names are 10 characters or shorter** because the shapefile driver
silently truncates longer ones, and two fields truncating to the same stem
collide.  ``mannings_n`` is exactly at the limit; do not lengthen it.

**Cells on the mesh boundary follow the 2D flow area perimeter**, because that is
the outline RAS computes with — see ``reader._perimeter_polygons``.  Measured
across 8 meshes in 4 plans (Hillside p12/p51/p62, the test fixture p02, 7486
cells): every cell polygon matches RAS's own ``Cells Surface Area`` to at worst
5.4e-08 relative, which is float32 round-off on that dataset, and the cells tile
the ``Perimeter`` polygon with **zero** symmetric difference.  Face points alone
missed by up to 25% on a single boundary cell and left 1.18M sq ft of one mesh
untiled.
"""
from __future__ import annotations

import logging
import os

from hack_ras.resolve import CrsProjectionFileNotFound, read_crs_wkt
from hack_ras.results.reader import list_areas, read_area_geometry, read_cell_mannings


def cell_mannings_gdf(hdf_path: str, areas: list[str] | None = None):
    """
    Build a polygon GeoDataFrame of cell-center Manning's n for a plan HDF.

    One row per real (non-perimeter-dummy) mesh cell, with columns:
    ``area`` (2D flow area name), ``cell_idx`` (local cell index),
    ``mannings_n``, and ``geometry`` (the cell polygon).

    Parameters
    ----------
    hdf_path : str
        Absolute path to the .p##.hdf file.
    areas : list[str], optional
        2D flow area names to include.  Defaults to every area in the file.

    Returns
    -------
    geopandas.GeoDataFrame
        No CRS is attached — see :func:`export_cell_mannings_shp`.

    Raises
    ------
    ValueError
        If the plan HDF has no 2D flow areas, or a requested area is not in it.
    KeyError
        If an area has no "Cells Center Manning's n" dataset.
    """
    import geopandas as gpd
    import pandas as pd

    available = list_areas(hdf_path)
    if not available:
        raise ValueError(f"No 2D flow areas found in: {hdf_path}")

    if areas is None:
        areas = available
    else:
        missing = [a for a in areas if a not in available]
        if missing:
            raise ValueError(
                f"2D flow area(s) not in {hdf_path}: {', '.join(missing)}\n"
                f"Available: {', '.join(available)}"
            )

    frames = []
    for area in areas:
        geom = read_area_geometry(hdf_path, area)
        n_values = read_cell_mannings(hdf_path, area)

        gdf = geom.cell_gdf.copy()
        gdf.insert(0, "area", area)
        gdf["mannings_n"] = n_values[gdf["cell_idx"].to_numpy()]
        frames.append(gdf[["area", "cell_idx", "mannings_n", "geometry"]])

    return gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry"
    )


def export_cell_mannings_shp(
    hdf_path: str,
    out_path: str,
    areas: list[str] | None = None,
    crs: str | None = None,
) -> str:
    """
    Write a polygon shapefile of cell-center Manning's n for a plan HDF.

    Parameters
    ----------
    hdf_path : str
        Absolute path to the .p##.hdf file.
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
        If the plan HDF has no 2D flow areas, or a requested area is not in it.
    KeyError
        If an area has no "Cells Center Manning's n" dataset.
    """
    gdf = cell_mannings_gdf(hdf_path, areas)

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

    logging.info("Wrote %d cell polygons to %s", len(gdf), out_path)
    return out_path
