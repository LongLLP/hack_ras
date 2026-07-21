"""
Tests for hack_ras.version.RasVersion (parsing, comparison, HDF read).
"""
import os
import tempfile
import unittest

from hack_ras.version import RasVersion

try:
    import h5py
    import numpy as np
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False


class ParseTests(unittest.TestCase):
    def test_parse_7_0(self):
        v = RasVersion.parse("HEC-RAS 7.0 April 2026")
        self.assertEqual((v.major, v.minor, v.patch), (7, 0, 0))
        self.assertEqual(v.raw, "HEC-RAS 7.0 April 2026")

    def test_parse_5_0_3(self):
        v = RasVersion.parse("HEC-RAS 5.0.3 September 2016")
        self.assertEqual((v.major, v.minor, v.patch), (5, 0, 3))

    def test_parse_bare(self):
        self.assertEqual(RasVersion.parse("6.1"), RasVersion(6, 1))

    def test_parse_no_number_raises(self):
        with self.assertRaises(ValueError):
            RasVersion.parse("HEC-RAS (no version here)")


class CompareTests(unittest.TestCase):
    def test_threshold(self):
        self.assertFalse(RasVersion.parse("HEC-RAS 5.0.3 ...") >= RasVersion(6, 0))
        self.assertTrue(RasVersion.parse("HEC-RAS 7.0 ...") >= RasVersion(6, 0))

    def test_raw_excluded_from_equality(self):
        self.assertEqual(RasVersion(7, 0, raw="HEC-RAS 7.0 April 2026"),
                         RasVersion(7, 0, raw="something else"))

    def test_patch_ordering(self):
        self.assertTrue(RasVersion(5, 0, 3) > RasVersion(5, 0, 0))


@unittest.skipUnless(HAS_H5PY, "h5py not installed")
class FromHdfTests(unittest.TestCase):
    def _tmp_hdf(self, file_version=None):
        fd, path = tempfile.mkstemp(suffix=".hdf")
        os.close(fd)
        with h5py.File(path, "w") as h:
            if file_version is not None:
                h.attrs["File Version"] = np.bytes_(file_version.encode("ascii"))
        self.addCleanup(os.remove, path)
        return path

    def test_reads_version(self):
        p = self._tmp_hdf("HEC-RAS 7.0 April 2026")
        self.assertEqual(RasVersion.from_hdf(p), RasVersion(7, 0))

    def test_missing_attr_returns_none(self):
        p = self._tmp_hdf(None)
        self.assertIsNone(RasVersion.from_hdf(p))

    def test_unparseable_returns_none(self):
        p = self._tmp_hdf("HEC-RAS beta")
        self.assertIsNone(RasVersion.from_hdf(p))


if __name__ == "__main__":
    unittest.main()
