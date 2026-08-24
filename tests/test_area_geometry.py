"""
Tests for read_area_geometry's cell polygon reconstruction.

The polygons must follow the 2D flow area perimeter, not just the cell face
points. Two independent ground truths pin that, both taken from the plan HDF
itself so the test cannot drift with the reconstruction it checks:

  1. `Cells Surface Area` — RAS's own plan area for each cell. That dataset is
     float32, so agreement to ~1e-7 relative is an exact match.
  2. The cells must tile `Perimeter` with nothing left over: the union of every
     real cell polygon has zero symmetric difference against the 2D flow area
     boundary.

Face points alone fail both on any boundary cell whose perimeter face bends
between its two end points — the bend vertices live in `Faces Perimeter Values`,
outside `Cells FacePoint Indexes`. Measured on the Hillside model before the fix:
up to 25% off on a single cell, and 1.18M sq ft of untiled area in one mesh.

Verified only against RAS 7.0 plan HDFs — no pre-7.0 2D model was available to
exercise the face-point fallback path.
"""
import os
import unittest

import numpy as np

try:
    import h5py
    from shapely.ops import unary_union
    from hack_ras.results.reader import (
        _facepoint_polygons,
        list_areas,
        read_area_geometry,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "data",
    "2D culvert bridge levee precip pipes",
    "Model.p02.hdf",
)
HAS_HDF = os.path.exists(HDF_FIXTURE)

# Cells Surface Area is float32: ~7 significant digits.
FLOAT32_REL = 1e-6


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestCellPolygonsFollowPerimeter(unittest.TestCase):

    def _real_cells(self, geom):
        """Indices of cells that have both a polygon and a real min elevation."""
        return [
            i for i in range(len(geom.polygons))
            if geom.polygons[i] is not None and not np.isnan(geom.min_elevations[i])
        ]

    def test_polygon_area_matches_cells_surface_area(self):
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            real = self._real_cells(geom)
            self.assertGreater(len(real), 0, area)
            for i in real:
                self.assertAlmostEqual(
                    geom.polygons[i].area / geom.plan_areas[i], 1.0,
                    delta=FLOAT32_REL,
                    msg=f"{area} cell {i}: polygon area disagrees with "
                        f"Cells Surface Area",
                )

    def test_cells_tile_the_flow_area_boundary(self):
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            union = unary_union([geom.polygons[i] for i in self._real_cells(geom)])
            leftover = union.symmetric_difference(geom.boundary).area
            # Scale-free: any real gap is orders of magnitude above round-off.
            self.assertLess(leftover / geom.boundary.area, 1e-9, area)

    def test_all_polygons_are_valid(self):
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            for i in self._real_cells(geom):
                self.assertTrue(geom.polygons[i].is_valid, f"{area} cell {i}")

    def test_plan_areas_come_from_the_hdf_not_the_polygons(self):
        # plan_areas must be Cells Surface Area verbatim; the polygon check above
        # is only meaningful if this is an independent number.
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                raw = hdf[f"Geometry/2D Flow Areas/{area}/Cells Surface Area"][:]
            np.testing.assert_allclose(geom.plan_areas, raw)

    def test_boundary_cells_are_where_the_two_reconstructions_differ(self):
        # Pins the reason the perimeter walk exists: interior cells are identical
        # under both reconstructions, and at least one boundary cell is not.
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            base = f"Geometry/2D Flow Areas/{area}"
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                fp_xy = hdf[f"{base}/FacePoints Coordinate"][:]
                fp_idx = hdf[f"{base}/Cells FacePoint Indexes"][:]
                is_per = hdf[f"{base}/FacePoints Is Perimeter"][:]
            fp_only = _facepoint_polygons(fp_xy, fp_idx)

            differs = []
            for i in self._real_cells(geom):
                same = abs(fp_only[i].area / geom.polygons[i].area - 1) < 1e-9
                sel = fp_idx[i][fp_idx[i] >= 0]
                on_boundary = bool((is_per[sel] != 0).any())
                if not same:
                    differs.append(i)
                    self.assertTrue(
                        on_boundary,
                        f"{area} cell {i} changed but is not on the mesh boundary",
                    )
            self.assertGreater(
                len(differs), 0,
                f"{area}: no cell differs, so this fixture cannot detect a "
                f"regression to face-point-only outlines",
            )


if __name__ == "__main__":
    unittest.main()
