import os
import tempfile
import unittest

from hack_ras.resolve import find_crs_prj, CrsProjectionFileNotFound

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "2D culvert bridge levee precip pipes")
RASMAP_PRJ = os.path.join(DATA_DIR, "_ESRI projection StatePlane.prj")
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


if __name__ == "__main__":
    unittest.main()
