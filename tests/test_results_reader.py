"""
Tests for hack_ras.results.reader

Functions that read HDF5 files (list_areas, read_area_geometry, read_wse)
require a real .p##.hdf fixture and are skipped when none is available.

read_plan_metadata reads a plain text sidecar file and is tested fully here.
"""
import os
import tempfile
import unittest

try:
    from hack_ras.results.reader import read_plan_metadata
    from hack_ras.results.model import PlanMetadata
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False


def _make_plan_file(folder, stem="TestPlan.p01", geom_id="g01", title="Test Plan"):
    """Write a minimal .p## text file and return its path."""
    path = os.path.join(folder, stem)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Plan Title={title}\n")
        f.write(f"Geom File={geom_id}\n")
    return path


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestReadPlanMetadata(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def _hdf_path(self, plan_stem):
        """Return the .hdf path corresponding to a plan stem like 'TestPlan.p01'."""
        return os.path.join(self.tmp, plan_stem + ".hdf")

    def test_returns_plan_metadata(self):
        plan_path = _make_plan_file(self.tmp, stem="Model.p01", geom_id="g02", title="My Plan")
        result = read_plan_metadata(self._hdf_path("Model.p01"))
        self.assertIsInstance(result, PlanMetadata)
        self.assertEqual(result.geom_id, "g02")
        self.assertEqual(result.plan_title, "My Plan")

    def test_geom_id_is_lowercase(self):
        _make_plan_file(self.tmp, stem="Model.p02", geom_id="G01")
        result = read_plan_metadata(self._hdf_path("Model.p02"))
        self.assertEqual(result.geom_id, "g01")

    def test_raises_file_not_found_when_sidecar_missing(self):
        hdf_path = os.path.join(self.tmp, "NoSidecar.p01.hdf")
        with self.assertRaises(FileNotFoundError):
            read_plan_metadata(hdf_path)

    def test_raises_value_error_when_geom_file_missing(self):
        path = os.path.join(self.tmp, "NoGeom.p01")
        with open(path, "w") as f:
            f.write("Plan Title=Some Plan\n")  # no Geom File= line
        with self.assertRaises(ValueError):
            read_plan_metadata(self._hdf_path("NoGeom.p01"))

    def test_handles_utf8_bom(self):
        # Some Windows editors write UTF-8 BOM; the reader should handle it.
        path = os.path.join(self.tmp, "BOM.p01")
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
            f.write(b"Plan Title=BOM Plan\nGeom File=g03\n")
        result = read_plan_metadata(self._hdf_path("BOM.p01"))
        self.assertEqual(result.geom_id, "g03")
        self.assertEqual(result.plan_title, "BOM Plan")


if __name__ == "__main__":
    unittest.main()
