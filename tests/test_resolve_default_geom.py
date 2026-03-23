import unittest
import tempfile
import os
from hack_ras.project.resolve import resolve_default_geom, GeometryFileNotFound


def _make_prj(folder, name="TestProject"):
    """Create a minimal .prj file and return its path as a string."""
    prj_path = os.path.join(folder, f"{name}.prj")
    with open(prj_path, "w") as f:
        f.write(f"Proj Title={name}\n")
    return prj_path


class TestResolveDefaultGeom(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_raises_value_error_when_no_geom_id(self):
        prj_path = _make_prj(self.tmp)
        with self.assertRaises(ValueError):
            resolve_default_geom(prj_path, None)

    def test_raises_geometry_file_not_found_when_file_missing(self):
        prj_path = _make_prj(self.tmp)
        # .prj says g01, but TestProject.g01 does not exist on disk
        with self.assertRaises(GeometryFileNotFound):
            resolve_default_geom(prj_path, "g01")

    def test_geometry_file_not_found_is_subclass_of_file_not_found_error(self):
        prj_path = _make_prj(self.tmp)
        with self.assertRaises(FileNotFoundError):
            resolve_default_geom(prj_path, "g01")

    def test_returns_path_when_geom_file_exists(self):
        prj_path = _make_prj(self.tmp)
        geom_path = os.path.join(self.tmp, "TestProject.g01")
        with open(geom_path, "w") as f:
            f.write("Geom Title=Test\n")

        result = resolve_default_geom(prj_path, "g01")

        self.assertEqual(result, geom_path)


if __name__ == "__main__":
    unittest.main()
