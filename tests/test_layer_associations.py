# tests/test_layer_associations.py
"""Tests for read_layer_associations / RasProject.layer_associations.

Read-only, so the real fixtures are used directly: '2D culvert bridge levee
precip pipes' has all three associations (Terrain, LandCover, Infiltration),
Baxter has terrain only, and Beaver is a 5.0.3 geometry with none. One
synthetic HDF covers the missing-/Geometry error.
"""
import os
import tempfile
import unittest

import h5py

from hack_ras import RasProject
from hack_ras.project.associations import read_layer_associations

DATA = os.path.join(os.path.dirname(__file__), "data")
FULL = os.path.join(DATA, "2D culvert bridge levee precip pipes")


class TestReadLayerAssociations(unittest.TestCase):

    def test_all_three_layers(self):
        a = read_layer_associations(os.path.join(FULL, "Model.g02.hdf"))
        self.assertEqual(a.terrain.name, "Terrain")
        self.assertEqual(a.terrain.filename, ".\\Terrain\\Terrain.hdf")
        self.assertEqual(a.terrain.path,
                         os.path.normpath(os.path.join(FULL, "Terrain", "Terrain.hdf")))
        self.assertTrue(os.path.isfile(a.terrain.path))
        self.assertEqual(a.mannings_n.name, "LandCover")
        self.assertTrue(os.path.isfile(a.mannings_n.path))
        self.assertEqual(a.infiltration.name, "Infiltration")
        self.assertTrue(os.path.isfile(a.infiltration.path))

    def test_plan_hdf_reads_the_same_way(self):
        a = read_layer_associations(os.path.join(FULL, "Model.p02.hdf"))
        self.assertEqual((a.terrain.name, a.mannings_n.name, a.infiltration.name),
                         ("Terrain", "LandCover", "Infiltration"))

    def test_terrain_only(self):
        a = read_layer_associations(os.path.join(DATA, "Baxter", "Baxter.g02.hdf"))
        self.assertEqual(a.terrain.name, "Terrain")
        self.assertIsNone(a.mannings_n)
        self.assertIsNone(a.infiltration)

    def test_no_associations_503(self):
        a = read_layer_associations(os.path.join(DATA, "Beaver", "beaver.g01.hdf"))
        self.assertIsNone(a.terrain)
        self.assertIsNone(a.mannings_n)
        self.assertIsNone(a.infiltration)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_layer_associations(os.path.join(DATA, "nope.g01.hdf"))

    def test_no_geometry_group_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.p01.hdf")
            with h5py.File(path, "w") as h:
                h.create_group("Results")
            with self.assertRaises(ValueError):
                read_layer_associations(path)


class TestProjectLayerAssociations(unittest.TestCase):

    def test_rows_are_geoms_then_run_plans(self):
        project = RasProject(os.path.join(FULL, "Model.prj"))
        rows = project.layer_associations()
        # p03 is listed in the .prj but has no .p03.hdf, so it has no row.
        self.assertEqual(list(rows),
                         ["g02", "g03", "g04", "g05", "p02", "p04", "p05", "p06", "p07"])
        self.assertTrue(all(r.terrain.name == "Terrain" for r in rows.values()))


if __name__ == "__main__":
    unittest.main()
