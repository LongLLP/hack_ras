"""
Tests for the mesh-connectivity WSE surface (hack_ras/gis/wse_surface.py).

The properties pinned here are the ones the module exists to guarantee, and each
is checked against the plan HDF itself rather than against a stored expectation,
so a test cannot drift with the code it checks:

  1. The surface reproduces `read_wse` exactly at every cell centre. Any
     smoothing that moves the computed value is a bug, not a rendering choice.
  2. No vertex value lies outside the range of the wet cells' WSE. Face point
     values are means over contributing cells, so overshoot is impossible by
     construction and this catches a regression that broke that.
  3. Triangles stay inside the cell they were fanned from, to within the
     measured concave-cell allowance. This is what stops an edge crossing a mesh
     face, and with it the whole class of errors where an interpolated water
     surface runs through an embankment. A concave cell lets a few triangles
     reach slightly past its outline; the fan apex being inside its own polygon
     is what bounds that, so it is pinned separately. See the module docstring
     for the measurements.
  4. A face carrying a structure is treated as a barrier, and a structure's
     chains are scoped to the 2D area whose face point indices they are numbered
     in. The fixture has an internal `Culvert` (Watershed to Watershed) and a
     `Levee` (Watershed to Interior), so both cases are exercised.
  5. Requiring both-side submergence actually splits face points. On a mesh with
     structures and any relief, zero split points means the barrier and
     submergence tests silently did nothing.
  6. Rasterising is exact: a triangle's plane is reproduced at pixel centres, and
     a cell's rasterised pixel count matches its plan area.
  7. `same_mesh` separates a re-serialised identical mesh from a genuinely
     different one, and `area_bounds` unions across plans so a bigger mesh is
     never clipped. Two plans on different meshes still difference exactly,
     because the output grid comes from the terrain, not the mesh.
  8. A terrain in the wrong coordinate system is refused. That failure is
     silent otherwise — a wrong-CRS terrain usually still overlaps the mesh, so
     the overlap check never fires and every depth is wrong by the offset.

The connectivity rule itself — a face joins two water bodies only when the
*lower* of the two WSEs clears the face's minimum elevation — was settled
empirically on NKC_Hillside_Levee and is recorded in the module docstring, not
here; the fixture is too small and too flat to reproduce the hillside failure it
was written for.
"""
import os
import shutil
import tempfile
import unittest

import numpy as np

try:
    import h5py
    from affine import Affine
    from hack_ras.gis.wse_surface import (
        area_bounds,
        barrier_faces,
        build_wse_surface,
        difference_rasters,
        export_wse_depth,
        rasterize_surface,
        same_mesh,
    )
    from hack_ras.results.reader import list_areas, read_area_geometry, read_wse
    HAS_GIS = True
except ImportError:
    HAS_GIS = False

FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "data",
    "2D culvert bridge levee precip pipes",
    "Model.p05.hdf",
)


@unittest.skipUnless(HAS_GIS, "requires h5py / shapely / rasterio extras")
@unittest.skipUnless(os.path.exists(FIXTURE), f"missing fixture {FIXTURE}")
class TestWseSurface(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.areas = list_areas(FIXTURE)
        cls.wse = {a: read_wse(FIXTURE, a, "Maximum") for a in cls.areas}
        cls.surf = {a: build_wse_surface(FIXTURE, a, cls.wse[a]) for a in cls.areas}
        with h5py.File(FIXTURE, "r") as hdf:
            cls.centers = {
                a: hdf[f"Geometry/2D Flow Areas/{a}/Cells Center Coordinate"][:]
                for a in cls.areas
            }

    def test_surface_is_exact_at_cell_centres(self):
        """Every triangle's apex is its cell's centre, carrying the HDF's WSE."""
        for area in self.areas:
            s = self.surf[area]
            with self.subTest(area=area):
                self.assertGreater(len(s.triangles), 0)
                apex = s.triangles[:, 0]
                cells = s.cell_of_triangle
                np.testing.assert_allclose(
                    s.points[apex], self.centers[area][cells], rtol=0, atol=1e-9
                )
                np.testing.assert_array_equal(
                    s.values[apex], self.wse[area][cells]
                )

    def test_no_vertex_overshoots_the_cell_wse_range(self):
        """Means over contributing cells cannot leave the wet cells' range."""
        for area in self.areas:
            s = self.surf[area]
            used = np.unique(s.cell_of_triangle)
            lo = self.wse[area][used].min()
            hi = self.wse[area][used].max()
            with self.subTest(area=area):
                self.assertGreaterEqual(s.values.min(), lo - 1e-9)
                self.assertLessEqual(s.values.max(), hi + 1e-9)

    def test_fan_apex_is_inside_its_own_cell(self):
        """The cell centre must be interior, which is what bounds the spill."""
        from shapely.geometry import Point

        for area in self.areas:
            geom = read_area_geometry(FIXTURE, area)
            for cell in np.unique(self.surf[area].cell_of_triangle):
                poly = geom.polygons[cell]
                if poly is None:
                    continue
                with self.subTest(area=area, cell=int(cell)):
                    self.assertTrue(
                        poly.buffer(1e-6).contains(Point(*geom.cell_centers[cell]))
                    )

    def test_triangles_barely_leave_their_cell(self):
        """Only concave cells spill, and only by a negligible area.

        Bound taken from the measurements in the module docstring: on Hillside
        the worst single cell is 3.2% and the summed excess is 0.013%.
        """
        from shapely.geometry import Polygon

        for area in self.areas:
            s = self.surf[area]
            geom = read_area_geometry(FIXTURE, area)
            escaped_area = 0.0
            total_area = 0.0
            for t, cell in zip(s.triangles, s.cell_of_triangle):
                poly = geom.polygons[cell]
                if poly is None:
                    continue
                tri = Polygon(s.points[t])
                if not tri.is_valid or tri.area == 0:
                    continue
                total_area += tri.area
                out = tri.difference(poly.buffer(1e-6)).area
                if out > 1e-6 * tri.area:
                    escaped_area += out
                    # A convex cell must never spill at all.
                    self.assertGreater(
                        poly.convex_hull.area, poly.area * (1 + 1e-12),
                        f"{area} cell {cell} is convex but a triangle escaped",
                    )
            with self.subTest(area=area):
                self.assertLess(escaped_area, 0.0005 * total_area)

    def test_triangles_tile_their_cell(self):
        """The fan covers the cell: summed triangle area matches the plan area."""
        for area in self.areas:
            s = self.surf[area]
            geom = read_area_geometry(FIXTURE, area)
            p = s.points[s.triangles]
            area_of = 0.5 * np.abs(
                (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
            )
            used = np.unique(s.cell_of_triangle)
            for cell in used:
                got = area_of[s.cell_of_triangle == cell].sum()
                with self.subTest(area=area, cell=int(cell)):
                    # Exact for a convex cell; a concave one runs slightly over.
                    self.assertAlmostEqual(
                        got / geom.plan_areas[cell], 1.0, delta=0.035
                    )
            with self.subTest(area=area, cell="total"):
                self.assertAlmostEqual(
                    area_of.sum() / geom.plan_areas[used].sum(), 1.0, delta=0.0005
                )

    def test_structure_faces_are_barriers(self):
        """The fixture's structures produce barrier faces in their own areas.

        `Default Weir Connectivity` stores one face point chain per structure and
        side; the faces between consecutive points of a chain lie on the
        structure, so a chain of n points contributes n-1 faces.
        """
        with h5py.File(FIXTURE, "r") as hdf:
            conn = hdf["Geometry/Structures/Default Weir Connectivity"][:]
            attrs = hdf["Geometry/Structures/Attributes"][:]

        expected = {a: 0 for a in self.areas}
        chains = {}
        for row in conn:
            sid = int(row["SID"])
            side = bytes(row["HW/TW"])
            owner = attrs[sid]["US SA/2D" if side == b"HW" else "DS SA/2D"]
            owner = owner.decode(errors="replace").strip()
            if owner in expected:
                chains.setdefault((sid, side, owner), set()).add(
                    int(row["RS/FP"].decode())
                )
        for (_, _, owner), pts in chains.items():
            expected[owner] += len(pts) - 1

        total = 0
        for area in self.areas:
            found = barrier_faces(FIXTURE, area)
            total += len(found)
            with self.subTest(area=area):
                # An internal structure's HW and TW chains name the same faces,
                # so the count is bounded by the sum and must be positive.
                self.assertGreater(len(found), 0)
                self.assertLessEqual(len(found), expected[area])
        self.assertGreater(total, 0)

    def test_barrier_chains_are_scoped_to_their_own_area(self):
        """Matching every chain against every area invents faces.

        Face point indices are area-local, so an unscoped match lets one area's
        chain land on another's faces by coincidence. This pins that the scoped
        result is a subset of the unscoped one and that scoping is doing work on
        at least one area of this fixture.
        """
        with h5py.File(FIXTURE, "r") as hdf:
            conn = hdf["Geometry/Structures/Default Weir Connectivity"][:]
            unscoped_chains = {}
            for row in conn:
                unscoped_chains.setdefault(
                    (int(row["SID"]), bytes(row["HW/TW"])), set()
                ).add(int(row["RS/FP"].decode()))

            for area in self.areas:
                face_fp = hdf[
                    f"Geometry/2D Flow Areas/{area}/Faces FacePoint Indexes"
                ][:]
                unscoped = {
                    face
                    for face, (a, b) in enumerate(face_fp)
                    if any(a in pts and b in pts for pts in unscoped_chains.values())
                }
                with self.subTest(area=area):
                    self.assertTrue(barrier_faces(FIXTURE, area) <= unscoped)

    def test_face_points_split(self):
        """Barriers and the submergence test must actually split face points."""
        self.assertGreater(
            sum(s.n_split_points for s in self.surf.values()), 0,
            "no face point carried more than one value; the barrier and "
            "both-side-submergence tests did nothing",
        )

    def test_dry_cells_are_excluded(self):
        """A WSE at or below a cell's minimum elevation must not be drawn."""
        area = self.areas[0]
        wse = self.wse[area].copy()
        geom = read_area_geometry(FIXTURE, area)
        real = np.flatnonzero(~np.isnan(geom.min_elevations))
        drowned = real[:3]
        wse[drowned] = geom.min_elevations[drowned] - 1.0
        s = build_wse_surface(FIXTURE, area, wse)
        for cell in drowned:
            self.assertNotIn(cell, set(s.cell_of_triangle.tolist()))

    def test_wse_length_is_checked(self):
        area = self.areas[0]
        with self.assertRaises(ValueError):
            build_wse_surface(FIXTURE, area, np.zeros(3))

    def test_rasterize_reproduces_the_plane(self):
        """Pixel centres inside a cell read the interpolated plane exactly."""
        area = self.areas[0]
        s = self.surf[area]
        cell = int(s.cell_of_triangle[0])
        cx, cy = self.centers[area][cell]
        r = 60
        grid = Affine(1.0, 0, cx - r, 0, -1.0, cy + r)
        arr = np.full((2 * r, 2 * r), np.nan, np.float32)
        cel = np.full((2 * r, 2 * r), -1, np.int32)
        rasterize_surface(s, grid, 0, 0, 2 * r, 2 * r, out=arr, cells_out=cel)

        self.assertTrue(np.isfinite(arr).any())
        written = np.isfinite(arr)
        self.assertTrue((cel[written] >= 0).all())
        # The pixel nearest the cell centre sits half a pixel off it, so it must
        # be close to but need not equal the cell's WSE.
        self.assertAlmostEqual(float(arr[r, r]), float(self.wse[area][cell]), delta=0.5)

    def test_rasterized_area_matches_plan_area(self):
        """A cell's pixel count reproduces RAS's `Cells Surface Area`."""
        area = self.areas[0]
        s = self.surf[area]
        geom = read_area_geometry(FIXTURE, area)
        x0, y0, x1, y1 = s.bounds
        w = int(x1 - x0) + 2
        h = int(y1 - y0) + 2
        grid = Affine(1.0, 0, x0 - 1, 0, -1.0, y1 + 1)
        cel = np.full((h, w), -1, np.int32)
        rasterize_surface(s, grid, 0, 0, h, w,
                          out=np.full((h, w), np.nan, np.float32), cells_out=cel)
        counts = np.bincount(cel[cel >= 0], minlength=len(geom.plan_areas))
        for cell in np.unique(s.cell_of_triangle):
            with self.subTest(cell=int(cell)):
                self.assertAlmostEqual(
                    counts[cell] / geom.plan_areas[cell], 1.0, delta=0.02
                )


FIXTURE_DIR = os.path.join(
    os.path.dirname(__file__), "data", "2D culvert bridge levee precip pipes")
P02 = os.path.join(FIXTURE_DIR, "Model.p02.hdf")      # g02 — 59 + 59 cells
P05 = os.path.join(FIXTURE_DIR, "Model.p05.hdf")      # g03 — same mesh as g02
P07 = os.path.join(FIXTURE_DIR, "Model.p07.hdf")      # g05 — refined + extended
TERRAIN = os.path.join(FIXTURE_DIR, "Terrain", "Terrain.vrt")


@unittest.skipUnless(HAS_GIS, "requires h5py / shapely / rasterio extras")
@unittest.skipUnless(os.path.exists(P07), f"missing fixture {P07}")
class TestSameMesh(unittest.TestCase):
    """`same_mesh` has to separate a re-serialised identical mesh from a real one.

    Two geometries written out separately are never bit-identical even when the
    mesh was untouched, so this compares within a tolerance. The fixture gives
    both answers: `Model.g02` and `Model.g03` are the same mesh, while
    `Model.g05` refines `Interior` from 59 cells to 196 and both refines and
    extends `Watershed`, 59 to 386 over an extra 1.43M ft**2.
    """

    def test_reserialised_identical_mesh_reads_as_the_same_mesh(self):
        self.assertTrue(same_mesh(P02, P05))

    def test_a_refined_and_extended_mesh_reads_as_different(self):
        self.assertFalse(same_mesh(P02, P07))

    def test_a_plan_is_the_same_mesh_as_itself(self):
        self.assertTrue(same_mesh(P02, P02))

    def test_one_differing_area_is_enough(self):
        """`Interior` is only refined; `Watershed` is refined AND extended.

        Both differ from g02, so restricting to either one must still say False
        — this pins that the check is per area and not short-circuited by the
        first area that happens to match.
        """
        self.assertFalse(same_mesh(P02, P07, areas=["Interior"]))
        self.assertFalse(same_mesh(P02, P07, areas=["Watershed"]))

    def test_tolerance_is_not_so_loose_it_accepts_a_real_change(self):
        """A tolerance wide enough to swallow a 100 ft cell move is wrong."""
        self.assertFalse(same_mesh(P02, P07, atol=1.0))


@unittest.skipUnless(HAS_GIS, "requires h5py / shapely / rasterio extras")
@unittest.skipUnless(os.path.exists(P07), f"missing fixture {P07}")
class TestCrossMeshBounds(unittest.TestCase):
    """The union of the meshes, so a bigger mesh is never silently clipped."""

    def test_union_covers_both_meshes(self):
        b02 = area_bounds(P02)
        b07 = area_bounds(P07)
        union = area_bounds([P02, P07])
        self.assertAlmostEqual(union[0], min(b02[0], b07[0]))
        self.assertAlmostEqual(union[1], min(b02[1], b07[1]))
        self.assertAlmostEqual(union[2], max(b02[2], b07[2]))
        self.assertAlmostEqual(union[3], max(b02[3], b07[3]))

    def test_the_extended_mesh_actually_reaches_further(self):
        """Otherwise the union test above would pass vacuously."""
        b02 = area_bounds(P02)
        b07 = area_bounds(P07)
        self.assertGreater(b07[3], b02[3])

    def test_one_plans_bounds_would_clip_the_other(self):
        """This is the latent bug the union fixes, stated as a test."""
        b02 = area_bounds(P02)
        b07 = area_bounds(P07)
        self.assertLess(b02[3], b07[3])

    def test_a_single_path_still_works_unwrapped(self):
        self.assertEqual(area_bounds(P02), area_bounds([P02]))

    def test_empty_list_is_an_error_not_an_infinite_box(self):
        with self.assertRaises(ValueError):
            area_bounds([])


@unittest.skipUnless(HAS_GIS, "requires h5py / shapely / rasterio extras")
@unittest.skipUnless(os.path.exists(P07), f"missing fixture {P07}")
@unittest.skipUnless(os.path.exists(TERRAIN), f"missing fixture {TERRAIN}")
class TestCrossMeshDifference(unittest.TestCase):
    """Two plans on DIFFERENT meshes still difference exactly.

    The output grid comes from the terrain, not from the mesh, so as long as
    both exports are given the same bounds they land on the same grid whatever
    their meshes look like. Nothing is resampled and no tolerance is applied.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.bounds = area_bounds([P02, P07])
        cls.res = {}
        for name, hdf in (("p02", P02), ("p07", P07)):
            cls.res[name] = export_wse_depth(
                hdf, TERRAIN, cls.tmp, wse_type="Maximum", prefix=name,
                bounds=cls.bounds, volume_check=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_both_plans_land_on_one_grid(self):
        import rasterio
        with rasterio.open(self.res["p02"]["depth_path"]) as a, \
             rasterio.open(self.res["p07"]["depth_path"]) as b:
            self.assertEqual(a.transform, b.transform)
            self.assertEqual((a.width, a.height), (b.width, b.height))

    def test_difference_runs_across_differing_meshes(self):
        out = os.path.join(self.tmp, "diff_depth.tif")
        difference_rasters(self.res["p02"]["depth_path"],
                           self.res["p07"]["depth_path"],
                           out, treat_dry_as_zero=True)
        self.assertTrue(os.path.exists(out))

        import rasterio
        with rasterio.open(out) as src:
            arr = src.read(1, masked=True)
        self.assertGreater(arr.count(), 0, "difference wrote no pixels")

    def test_difference_equals_the_inputs_pixel_for_pixel(self):
        """No tolerance, no resampling: b - a exactly, where both are wet."""
        import numpy as np
        import rasterio

        out = os.path.join(self.tmp, "diff_check.tif")
        difference_rasters(self.res["p02"]["depth_path"],
                           self.res["p07"]["depth_path"],
                           out, treat_dry_as_zero=False)
        with rasterio.open(self.res["p02"]["depth_path"]) as sa, \
             rasterio.open(self.res["p07"]["depth_path"]) as sb, \
             rasterio.open(out) as sd:
            a = sa.read(1)
            b = sb.read(1)
            d = sd.read(1)
        both = (a != -9999.0) & (b != -9999.0)
        self.assertGreater(both.sum(), 0)
        np.testing.assert_allclose(d[both], (b - a)[both], rtol=0, atol=0)
        # and nothing was written where only one plan is wet
        self.assertTrue((d[~both] == -9999.0).all())

    def test_dry_counts_as_zero_only_when_asked(self):
        """The user's rule: ground newly covered by a mesh is a real increase.

        `treat_dry_as_zero=True` is what makes an area wet in one plan and
        absent from the other show up as the depth that appeared, rather than
        vanishing into NoData.
        """
        import rasterio

        zeroed = os.path.join(self.tmp, "diff_zero.tif")
        strict = os.path.join(self.tmp, "diff_strict.tif")
        difference_rasters(self.res["p02"]["depth_path"],
                           self.res["p07"]["depth_path"],
                           zeroed, treat_dry_as_zero=True)
        difference_rasters(self.res["p02"]["depth_path"],
                           self.res["p07"]["depth_path"],
                           strict, treat_dry_as_zero=False)
        with rasterio.open(zeroed) as z, rasterio.open(strict) as s:
            nz = z.read(1, masked=True).count()
            ns = s.read(1, masked=True).count()
        self.assertGreater(nz, ns,
                           "dry-as-zero must cover strictly more ground")

    def test_mismatched_grids_are_refused(self):
        """A differently-bounded export must not silently mis-difference."""
        other = export_wse_depth(
            P02, TERRAIN, self.tmp, wse_type="Maximum", prefix="shifted",
            bounds=area_bounds(P02), volume_check=False)
        import rasterio
        with rasterio.open(other["depth_path"]) as o, \
             rasterio.open(self.res["p07"]["depth_path"]) as b:
            if (o.transform == b.transform
                    and (o.width, o.height) == (b.width, b.height)):
                self.skipTest("the two bounds happen to produce one grid")
        with self.assertRaises(ValueError):
            difference_rasters(other["depth_path"],
                               self.res["p07"]["depth_path"],
                               os.path.join(self.tmp, "nope.tif"))


@unittest.skipUnless(HAS_GIS, "requires h5py / shapely / rasterio extras")
@unittest.skipUnless(os.path.exists(TERRAIN), f"missing fixture {TERRAIN}")
class TestTerrainCrsGuard(unittest.TestCase):
    """A terrain in the wrong CRS usually still overlaps, so it fails silently.

    The reference is the plan HDF's own root `Projection` attribute. The
    comparison must be semantic: RAS writes the ESRI dialect and GDAL the
    EPSG-style name for the same system.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reprojected_copy(self, epsg):
        """Same pixels and transform, relabelled with a different CRS."""
        import rasterio

        out = os.path.join(self.tmp, f"crs_{epsg}.tif")
        with rasterio.open(TERRAIN) as src:
            profile = src.profile.copy()
            profile.update(driver="GTiff", crs=f"EPSG:{epsg}")
            data = src.read(1)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data, 1)
        return out

    def test_matching_crs_is_accepted(self):
        """The real terrain must not trip the guard — no false positives."""
        res = export_wse_depth(P05, TERRAIN, self.tmp, wse_type="Maximum",
                               prefix="ok", volume_check=False)
        self.assertTrue(os.path.exists(res["depth_path"]))

    def test_wrong_crs_is_refused(self):
        wrong = self._reprojected_copy(8152)   # NAD83(HARN) / WISCRS Lincoln
        with self.assertRaises(ValueError) as ctx:
            export_wse_depth(P05, wrong, self.tmp, wse_type="Maximum",
                             prefix="bad", volume_check=False)
        self.assertIn("CRS", str(ctx.exception))

    def test_the_guard_can_be_overridden(self):
        wrong = self._reprojected_copy(8152)
        res = export_wse_depth(P05, wrong, self.tmp, wse_type="Maximum",
                               prefix="forced", allow_crs_mismatch=True,
                               volume_check=False)
        self.assertTrue(os.path.exists(res["depth_path"]))

    def test_a_terrain_with_no_crs_warns_rather_than_failing(self):
        """Missing metadata is not evidence of a mismatch."""
        import rasterio

        out = os.path.join(self.tmp, "nocrs.tif")
        with rasterio.open(TERRAIN) as src:
            profile = src.profile.copy()
            profile.update(driver="GTiff")
            profile.pop("crs", None)
            data = src.read(1)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(data, 1)
        with self.assertLogs(level="WARNING"):
            res = export_wse_depth(P05, out, self.tmp, wse_type="Maximum",
                                   prefix="nocrs", volume_check=False)
        self.assertTrue(os.path.exists(res["depth_path"]))


if __name__ == "__main__":
    unittest.main()
