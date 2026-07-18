import os
import unittest

from hack_ras import RasProject

_DATA = os.path.join(os.path.dirname(__file__), "data")
_BEAVER_PRJ = os.path.join(_DATA, "Beaver", "beaver.prj")
_ESRI_PRJ   = os.path.join(_DATA, "2D culvert bridge levee precip pipes",
                            "Terrain", "_ESRI projection StatePlane.prj")


class TestRasProjectConstruction(unittest.TestCase):

    def test_valid_path_stores_absolute_prj(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertTrue(os.path.isabs(p.prj_path))
        self.assertTrue(p.prj_path.endswith("beaver.prj"))

    def test_folder_is_directory_of_prj(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertEqual(p.folder, os.path.dirname(os.path.abspath(_BEAVER_PRJ)))

    def test_base_name_is_stem(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertEqual(p.base_name, "beaver")

    def test_title_from_prj(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertEqual(p.title, "Beaver Cr. - unsteady flow")

    def test_repr(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertIn("beaver.prj", repr(p))


class TestRasProjectErrors(unittest.TestCase):

    def test_missing_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            RasProject(os.path.join(_DATA, "nonexistent.prj"))

    def test_esri_prj_raises_value_error(self):
        # ESRI projection files share the .prj extension but are not RAS projects
        self.assertTrue(os.path.isfile(_ESRI_PRJ), "ESRI fixture missing")
        with self.assertRaises(ValueError):
            RasProject(_ESRI_PRJ)


class TestRasProjectModel(unittest.TestCase):

    def test_geom_file_ids_is_list(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertIsInstance(p.model.geom_file_ids, list)
        self.assertIn("g01", p.model.geom_file_ids)

    def test_plan_file_ids_is_list(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertIsInstance(p.model.plan_file_ids, list)
        self.assertIn("p03", p.model.plan_file_ids)

    def test_unsteady_file_ids_is_list(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertIsInstance(p.model.unsteady_file_ids, list)
        self.assertIn("u02", p.model.unsteady_file_ids)

    def test_no_duplicate_ids(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertEqual(len(p.model.plan_file_ids), len(set(p.model.plan_file_ids)))


class TestRasProjectFamily(unittest.TestCase):

    def test_available_ids_has_expected_keys(self):
        p = RasProject(_BEAVER_PRJ)
        ids = p.available_ids()
        self.assertIn("geom", ids)
        self.assertIn("plan", ids)
        self.assertIn("unsteady", ids)
        self.assertIn("steady", ids)

    def test_geom_id_present(self):
        p = RasProject(_BEAVER_PRJ)
        self.assertIn("g01", p.available_ids()["geom"])


if __name__ == "__main__":
    unittest.main()
