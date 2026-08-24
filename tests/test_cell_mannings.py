"""
Tests for read_cell_mannings and the gis.mesh Manning's n cell export.

The reader and the export both need a real plan HDF, so everything here is
skipped without the fixture.  The fixture's two 2D flow areas both pad their
cell arrays with perimeter dummy cells (27 of 59 in Interior, 30 of 59 in
Watershed), which is what makes it a useful check that the export drops them.
"""
import os
import tempfile
import unittest

import numpy as np

try:
    import h5py
    from hack_ras.results.reader import list_areas, read_area_geometry, read_cell_mannings
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

try:
    import geopandas as gpd
    from hack_ras.gis.mesh import cell_mannings_gdf, export_cell_mannings_shp
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

MANN_KEY = "Cells Center Manning's n"


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestReadCellMannings(unittest.TestCase):

    def test_one_value_per_cell_including_dummies(self):
        for area in list_areas(HDF_FIXTURE):
            n = read_cell_mannings(HDF_FIXTURE, area)
            geom = read_area_geometry(HDF_FIXTURE, area)
            self.assertEqual(n.shape, (len(geom.cell_centers),), area)
            self.assertEqual(n.dtype, np.float64, area)

    def test_values_match_the_hdf_dataset(self):
        for area in list_areas(HDF_FIXTURE):
            with h5py.File(HDF_FIXTURE, "r") as hdf:
                raw = hdf[f"Geometry/2D Flow Areas/{area}/{MANN_KEY}"][:]
            np.testing.assert_allclose(read_cell_mannings(HDF_FIXTURE, area), raw)

    def test_values_are_plausible_mannings_n(self):
        for area in list_areas(HDF_FIXTURE):
            n = read_cell_mannings(HDF_FIXTURE, area)
            self.assertTrue((n > 0).all(), area)
            self.assertTrue((n < 1).all(), area)

    def test_unknown_area_raises_keyerror(self):
        with self.assertRaises(KeyError):
            read_cell_mannings(HDF_FIXTURE, "__no_such_area__")


@unittest.skipUnless(HAS_GIS, "hack_ras[gis,results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestCellManningsGdf(unittest.TestCase):

    def test_columns_and_row_count(self):
        gdf = cell_mannings_gdf(HDF_FIXTURE)
        self.assertEqual(
            list(gdf.columns), ["area", "cell_idx", "mannings_n", "geometry"]
        )
        expected = sum(
            len(read_area_geometry(HDF_FIXTURE, a).cell_gdf)
            for a in list_areas(HDF_FIXTURE)
        )
        self.assertEqual(len(gdf), expected)

    def test_perimeter_dummy_cells_are_excluded(self):
        # Every exported cell must have a real (non-NaN) minimum elevation, and
        # the export must be strictly smaller than the raw cell count -- the
        # fixture has dummies in both areas, so a no-op filter would show up.
        gdf = cell_mannings_gdf(HDF_FIXTURE)
        for area in list_areas(HDF_FIXTURE):
            geom = read_area_geometry(HDF_FIXTURE, area)
            sub = gdf[gdf["area"] == area]
            self.assertLess(len(sub), len(geom.cell_centers), area)
            kept = geom.min_elevations[sub["cell_idx"].to_numpy()]
            self.assertFalse(np.isnan(kept).any(), area)

    def test_n_value_is_joined_to_the_right_cell(self):
        gdf = cell_mannings_gdf(HDF_FIXTURE)
        for area in list_areas(HDF_FIXTURE):
            n = read_cell_mannings(HDF_FIXTURE, area)
            sub = gdf[gdf["area"] == area]
            np.testing.assert_allclose(
                sub["mannings_n"].to_numpy(), n[sub["cell_idx"].to_numpy()]
            )

    def test_polygons_are_valid(self):
        gdf = cell_mannings_gdf(HDF_FIXTURE)
        self.assertTrue(gdf.geometry.is_valid.all())
        self.assertTrue((gdf.geometry.geom_type == "Polygon").all())

    def test_single_area_subset(self):
        area = list_areas(HDF_FIXTURE)[0]
        gdf = cell_mannings_gdf(HDF_FIXTURE, areas=[area])
        self.assertEqual(set(gdf["area"]), {area})

    def test_unknown_area_raises_valueerror(self):
        with self.assertRaises(ValueError):
            cell_mannings_gdf(HDF_FIXTURE, areas=["__no_such_area__"])


@unittest.skipUnless(HAS_GIS, "hack_ras[gis,results] extras not installed")
@unittest.skipUnless(HAS_HDF, "no .p##.hdf fixture at tests/data/")
class TestExportCellManningsShp(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out = os.path.join(self._tmpdir.name, "sub", "cell_mannings.shp")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_writes_shapefile_that_reads_back_intact(self):
        returned = export_cell_mannings_shp(HDF_FIXTURE, self.out)
        self.assertEqual(returned, self.out)
        self.assertTrue(os.path.exists(self.out))

        written = gpd.read_file(self.out)
        expected = cell_mannings_gdf(HDF_FIXTURE)
        self.assertEqual(len(written), len(expected))
        # Field names survive the 10-char shapefile limit unmangled.
        self.assertEqual(
            list(written.columns), ["area", "cell_idx", "mannings_n", "geometry"]
        )
        np.testing.assert_allclose(
            written["mannings_n"].to_numpy(),
            expected["mannings_n"].to_numpy(),
            rtol=1e-6,
        )

    def test_crs_comes_from_the_ras_project(self):
        # The fixture folder has a .rasmap pointing at its ESRI .prj, so the
        # default CRS lookup must find it rather than writing a bare .shp.
        export_cell_mannings_shp(HDF_FIXTURE, self.out)
        self.assertIsNotNone(gpd.read_file(self.out).crs)

    def test_explicit_crs_is_used(self):
        export_cell_mannings_shp(HDF_FIXTURE, self.out, crs="EPSG:2226")
        self.assertEqual(gpd.read_file(self.out).crs.to_epsg(), 2226)


if __name__ == "__main__":
    unittest.main()
