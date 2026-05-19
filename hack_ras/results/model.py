# hack_ras/results/model.py
from __future__ import annotations
from dataclasses import dataclass, field

import numpy as np


@dataclass
class PlanMetadata:
    """Title and geometry reference parsed from a HEC-RAS plan text file (.p##)."""
    geom_id: str      # e.g. 'g01'
    plan_title: str


@dataclass
class AreaGeometry:
    """
    Cell geometry for one HEC-RAS 2D flow area, read from a plan HDF5 file.

    Attributes
    ----------
    cell_centers : np.ndarray, shape (N, 2)
        XY coordinates of each cell centre.
    min_elevations : np.ndarray, shape (N,)
        Minimum terrain elevation per cell. NaN for perimeter dummy cells.
    polygons : list[shapely.Polygon | None], length N
        Cell polygon for each cell; None if fewer than 3 face points.
    boundary : shapely.Polygon
        Outer perimeter of the 2D flow area.
    cell_gdf : geopandas.GeoDataFrame
        Rows for non-dummy cells only (NaN min_elev excluded).
        Columns: 'cell_idx' (int), 'geometry' (Polygon).
        Has a spatial index for fast intersection queries.
    """
    cell_centers: np.ndarray
    min_elevations: np.ndarray
    polygons: list
    boundary: object
    cell_gdf: object
