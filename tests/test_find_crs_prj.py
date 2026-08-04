import os
import tempfile
import unittest

from hack_ras.resolve import (
    CrsProjectionFileNotFound,
    find_crs_prj,
    read_crs_wkt,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "2D culvert bridge levee precip pipes")
RASMAP_PRJ = os.path.join(DATA_DIR, "Terrain",
                          "_ESRI projection StatePlane.prj")
HAS_FIXTURE = os.path.isfile(RASMAP_PRJ)


class TestFindCrsPrjViaRasmap(unittest.TestCase):

    @unittest.skipUnless(HAS_FIXTURE, "test fixture not present")
    def test_rasmap_path_returned(self):
        result = find_crs_prj(DATA_DIR)
        self.assertEqual(os.path.normcase(result), os.path.normcase(os.path.abspath(RASMAP_PRJ)))

    @unittest.skipUnless(HAS_FIXTURE, "test fixture not present")
    def test_specified_takes_precedence(self):
        result = find_crs_prj(DATA_DIR, specified=RASMAP_PRJ)
        self.assertEqual(os.path.normcase(result), os.path.normcase(os.path.abspath(RASMAP_PRJ)))


class TestFindCrsPrjFallback(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_fallback_when_no_rasmap(self):
        prj = os.path.join(self.tmp, "myproj.prj")
        with open(prj, "w") as f:
            f.write('PROJCS["NAD_1983",GEOGCS["GCS_North_American_1983"]]\n')
        result = find_crs_prj(self.tmp)
        self.assertEqual(os.path.normcase(result), os.path.normcase(os.path.abspath(prj)))

    def test_raises_when_nothing_found(self):
        with self.assertRaises(CrsProjectionFileNotFound):
            find_crs_prj(self.tmp)


class TestReadCrsWkt(unittest.TestCase):
    """read_crs_wkt = find_crs_prj + read the file, so scripts stop hand-reading it."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_stripped_contents(self):
        wkt = 'PROJCS["NAD_1983",GEOGCS["GCS_North_American_1983"]]'
        with open(os.path.join(self.tmp, "myproj.prj"), "w") as f:
            f.write(wkt + "\n\n")
        self.assertEqual(read_crs_wkt(self.tmp), wkt)

    def test_specified_file_is_used(self):
        wkt = 'PROJCS["Explicit"]'
        named = os.path.join(self.tmp, "named.prj")
        with open(named, "w") as f:
            f.write(wkt)
        with open(os.path.join(self.tmp, "other.prj"), "w") as f:
            f.write('PROJCS["Other"]')
        self.assertEqual(read_crs_wkt(self.tmp, specified=named), wkt)

    def test_raises_when_nothing_found(self):
        with self.assertRaises(CrsProjectionFileNotFound):
            read_crs_wkt(self.tmp)

    @unittest.skipUnless(HAS_FIXTURE, "test fixture not present")
    def test_real_fixture_is_parseable_wkt(self):
        wkt = read_crs_wkt(DATA_DIR)
        self.assertTrue(wkt.startswith("PROJCS["), wkt[:40])
        self.assertNotIn("\n", wkt.strip("\n"))

    @unittest.skipUnless(HAS_FIXTURE, "test fixture not present")
    def test_ras_project_crs_wkt_matches(self):
        from hack_ras import RasProject
        prj = os.path.join(DATA_DIR, "Model.prj")
        if not os.path.isfile(prj):
            self.skipTest("Model.prj not present")
        project = RasProject(prj)
        self.assertEqual(project.crs_wkt(), read_crs_wkt(DATA_DIR))
        with open(project.crs_prj(), encoding="utf-8", errors="ignore") as f:
            self.assertEqual(project.crs_wkt(), f.read().strip())


if __name__ == "__main__":
    unittest.main()
