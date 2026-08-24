"""
Tests for read_face_geometry and the gis.mesh Manning's n face export.

Face n is the value RAS actually computes with, and it can move when the
cell-centre value does not — which is why the cell-centre export was removed and
this is the only Manning's n layer.  The fixture shows the divergence plainly: in
``Interior`` cell 0 the centre n is 0.14 while its own faces span 0.03 to 0.14.

The tiling property is the load-bearing claim about the dual ("diamond")
polygons — that they cover the mesh with no overlaps — so it is asserted
directly rather than trusted.
"""
import os
import tempfile
import unittest

import numpy as np

try:
    import h5py
    from hack_ras.results.reader import (
        list_areas,
        read_area_geometry,
        read_face_geometry,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

try:
    import geopandas as gpd
    from shapely.ops import unary_union
    from hack_ras.gis.mesh import (
        FACE_FIELDS,
        export_face_mannings_shp,
        face_mannings_gdf,
    )
    HAS_GIS = True
except ImportError:
    HAS_GIS = False

HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "data",
    "2D culvert bridge levee precip pipes",
    "Model.p02.hdf",
)
HAS_HDF = os.path.exists(HDF_FIXTURE)

VALUES_KEY = "Faces Area Elevation Values"
INFO_KEY = "Faces Area Elevation Info"


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestReadFaceGeometry(unittest.TestCase):

    def test_array_shapes_agree_with_the_hdf(self):
        for area in list_areas(HDF_FIXTURE):
            fg = read_face_geometry(HDF_FIXTURE, area)
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                n = len(hdf[f"Geometry/2D Flow Areas/{area}/Faces Cell Indexes"])
            self.assertEqual(fg.cell_indexes.shape, (n, 2), area)
            self.assertEqual(fg.facepoint_indexes.shape, (n, 2), area)
            self.assertEqual(fg.mannings_n.shape, (n,), area)
            self.assertEqual(fg.normals.shape, (n, 2), area)
            self.assertEqual(fg.lengths.shape, (n,), area)
            self.assertEqual(len(fg.polygons), n, area)

    def test_n_matches_column_3_of_the_face_property_table(self):
        for area in list_areas(HDF_FIXTURE):
            fg = read_face_geometry(HDF_FIXTURE, area)
            base = f"Geometry/2D Flow Areas/{area}"
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                info = hdf[f"{base}/{INFO_KEY}"][:]
                vals = hdf[f"{base}/{VALUES_KEY}"][:]
            expected = np.array([vals[s, 3] if c > 0 else np.nan for s, c in info])
            np.testing.assert_allclose(fg.mannings_n, expected, rtol=1e-6)

    def test_the_hdf_labels_column_3_as_mannings_n(self):
        # The whole face layer rests on column 3 being n; RAS says so itself.
        for area in list_areas(HDF_FIXTURE):
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                cols = hdf[f"Geometry/2D Flow Areas/{area}/{VALUES_KEY}"].attrs["Column"]
            self.assertEqual(cols[3].decode(), "Manning's n", area)

    def test_n_is_constant_within_each_face(self):
        # Documented assumption behind taking the lowest-elevation row. If a
        # future fixture breaks this, the reader warns -- and this test says so.
        for area in list_areas(HDF_FIXTURE):
            base = f"Geometry/2D Flow Areas/{area}"
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                info = hdf[f"{base}/{INFO_KEY}"][:]
                vals = hdf[f"{base}/{VALUES_KEY}"][:]
            for start, count in info:
                if count > 1:
                    col = vals[start:start + count, 3]
                    self.assertTrue(np.all(col == col[0]), area)

    def test_values_are_plausible_mannings_n(self):
        for area in list_areas(HDF_FIXTURE):
            n = read_face_geometry(HDF_FIXTURE, area).mannings_n
            good = n[~np.isnan(n)]
            self.assertTrue((good > 0).all(), area)
            self.assertTrue((good < 1).all(), area)

    def test_normals_are_unit_vectors(self):
        for area in list_areas(HDF_FIXTURE):
            fg = read_face_geometry(HDF_FIXTURE, area)
            np.testing.assert_allclose(
                np.hypot(fg.normals[:, 0], fg.normals[:, 1]), 1.0, atol=1e-5
            )

    def test_unknown_area_raises_keyerror(self):
        with self.assertRaises(KeyError):
            read_face_geometry(HDF_FIXTURE, "__no_such_area__")


@unittest.skipUnless(HAS_GIS, "hack_ras[gis,results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestDualPolygons(unittest.TestCase):

    def test_polygons_are_valid_and_positive_area(self):
        gdf = face_mannings_gdf(HDF_FIXTURE)
        self.assertTrue(gdf.geometry.is_valid.all())
        self.assertTrue((gdf.geometry.geom_type == "Polygon").all())
        self.assertTrue((gdf.geometry.area > 0).all())

    def test_the_face_is_the_diagonal_not_an_edge(self):
        # Ring order must alternate centre / face point. Getting it wrong makes
        # a self-intersecting bowtie, which is exactly what is_valid catches --
        # so assert the ring order itself, per face.
        for area in list_areas(HDF_FIXTURE):
            fg = read_face_geometry(HDF_FIXTURE, area)
            geom = read_area_geometry(HDF_FIXTURE, area)
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                fp_xy = hdf[f"Geometry/2D Flow Areas/{area}/FacePoints Coordinate"][:]
            for i, poly in enumerate(fg.polygons):
                if poly is None:
                    continue
                ring = list(poly.exterior.coords)[:-1]
                c_l, c_r = fg.cell_indexes[i]
                a, b = fg.facepoint_indexes[i]
                expected = {
                    tuple(geom.cell_centers[c_l]), tuple(fp_xy[a]),
                    tuple(geom.cell_centers[c_r]), tuple(fp_xy[b]),
                }
                self.assertTrue(set(map(tuple, ring)).issubset(expected), (area, i))

    def test_dual_polygons_tile_the_mesh(self):
        # The claim that makes diamonds the right choice: they cover the mesh
        # with no overlap. Overlap would show as union < sum.
        for area in list_areas(HDF_FIXTURE):
            sub = face_mannings_gdf(HDF_FIXTURE, areas=[area])
            total = float(sub.geometry.area.sum())
            union = unary_union(list(sub.geometry)).area
            self.assertAlmostEqual(union / total, 1.0, places=6, msg=area)

    def test_tiled_area_matches_the_mesh_area(self):
        # Diamonds should reproduce the mesh, not some fraction of it. The
        # tolerance covers perimeter faces whose bend the diagonal cuts across.
        for area in list_areas(HDF_FIXTURE):
            sub = face_mannings_gdf(HDF_FIXTURE, areas=[area])
            geom = read_area_geometry(HDF_FIXTURE, area)
            mesh = float(geom.plan_areas[geom.plan_areas > 1.0].sum())
            self.assertAlmostEqual(
                float(sub.geometry.area.sum()) / mesh, 1.0, places=2, msg=area
            )


@unittest.skipUnless(HAS_GIS, "hack_ras[gis,results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestFaceManningsGdf(unittest.TestCase):

    def test_columns_and_row_count(self):
        gdf = face_mannings_gdf(HDF_FIXTURE)
        self.assertEqual(list(gdf.columns), FACE_FIELDS)
        expected = sum(
            len(read_face_geometry(HDF_FIXTURE, a).polygons)
            for a in list_areas(HDF_FIXTURE)
        )
        self.assertEqual(len(gdf), expected)

    def test_field_names_fit_the_shapefile_limit(self):
        for name in FACE_FIELDS:
            if name != "geometry":
                self.assertLessEqual(len(name), 10, name)

    def test_n_value_is_joined_to_the_right_face(self):
        gdf = face_mannings_gdf(HDF_FIXTURE)
        for area in list_areas(HDF_FIXTURE):
            fg = read_face_geometry(HDF_FIXTURE, area)
            sub = gdf[gdf["area"] == area]
            np.testing.assert_allclose(
                sub["mannings_n"].to_numpy(),
                fg.mannings_n[sub["face_idx"].to_numpy()],
                rtol=1e-6,
            )

    def test_cell_ids_are_local_indices_into_the_area(self):
        # cell_l / cell_r must index that area's own cell arrays -- they are what
        # joins this layer to read_area_geometry and to WSE / volume output.
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            sub = face_mannings_gdf(HDF_FIXTURE, areas=[area])
            n_cells = len(geom.cell_centers)
            for col in ("cell_l", "cell_r"):
                vals = sub[col].to_numpy()
                self.assertTrue((vals >= 0).all(), (area, col))
                self.assertTrue((vals < n_cells).all(), (area, col))

    def test_every_face_touches_at_least_one_real_cell(self):
        # Perimeter faces name a ghost on one side; both sides ghost would mean
        # the dual polygon is anchored to nothing real.
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            real = set(geom.cell_gdf["cell_idx"].tolist())
            sub = face_mannings_gdf(HDF_FIXTURE, areas=[area])
            touching = [
                (l in real) or (r in real)
                for l, r in zip(sub["cell_l"], sub["cell_r"])
            ]
            self.assertTrue(all(touching), area)

    def test_single_area_subset(self):
        area = list_areas(HDF_FIXTURE)[0]
        gdf = face_mannings_gdf(HDF_FIXTURE, areas=[area])
        self.assertEqual(set(gdf["area"]), {area})

    def test_unknown_area_raises_valueerror(self):
        with self.assertRaises(ValueError):
            face_mannings_gdf(HDF_FIXTURE, areas=["__no_such_area__"])


@unittest.skipUnless(HAS_GIS, "hack_ras[gis,results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestExportFaceManningsShp(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out = os.path.join(self._tmpdir.name, "sub", "face_mannings.shp")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_writes_shapefile_that_reads_back_intact(self):
        returned = export_face_mannings_shp(HDF_FIXTURE, self.out)
        self.assertEqual(returned, self.out)
        self.assertTrue(os.path.exists(self.out))

        written = gpd.read_file(self.out)
        expected = face_mannings_gdf(HDF_FIXTURE)
        self.assertEqual(len(written), len(expected))
        # Field names survive the 10-char shapefile limit unmangled.
        self.assertEqual(list(written.columns), FACE_FIELDS)
        np.testing.assert_allclose(
            written["mannings_n"].to_numpy(),
            expected["mannings_n"].to_numpy(),
            rtol=1e-6,
        )

    def test_crs_comes_from_the_ras_project(self):
        export_face_mannings_shp(HDF_FIXTURE, self.out)
        self.assertIsNotNone(gpd.read_file(self.out).crs)

    def test_explicit_crs_is_used(self):
        export_face_mannings_shp(HDF_FIXTURE, self.out, crs="EPSG:2226")
        self.assertEqual(gpd.read_file(self.out).crs.to_epsg(), 2226)


if __name__ == "__main__":
    unittest.main()
