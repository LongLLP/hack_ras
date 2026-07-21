"""
Tests for hack_ras.results.reader.read_steady_profile_wse

A tiny synthetic steady-flow plan HDF5 is built in a temp file so the test is
hermetic.  It deliberately includes a *scrambled* 'Cross Section Variables'
dataset to assert the reader reads WSE from the standalone 'Water Surface'
dataset (aligned by geometry name arrays) and ignores the misaligned one.
"""
import os
import tempfile
import unittest

try:
    import h5py
    import numpy as np
    from hack_ras.results.reader import read_steady_profile_wse
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

_XS_BASE = ("/Results/Steady/Output/Output Blocks/Base Output/"
            "Steady Profiles/Cross Sections")
_PROFILE_NAMES = ("/Results/Steady/Output/Output Blocks/Base Output/"
                  "Steady Profiles/Profile Names")


def _write_synthetic_hdf(path, layout="flat", file_version="HEC-RAS 5.0.3 September 2016"):
    """
    Build a minimal steady-flow HDF.

    layout='flat'      -> 5.x flat River/Reach/Station name arrays
    layout='compound'  -> 6.0+ compound Cross Sections/Attributes
    layout='none'      -> only the compound table absent AND the flat arrays absent
                          (exercises the assume-latest + warn fallback -> KeyError)
    """
    profiles = ["100-year", "500-year"]
    rivers = ["RiverA", "RiverA", "RiverA"]
    reaches = ["Reach 1", "Reach 1", "Reach 1"]
    stations = ["300", "200", "100"]
    water_surface = np.array([[872.88, 863.44, 860.80],
                              [875.10, 866.00, 861.50]], dtype=np.float32)
    with h5py.File(path, "w") as h:
        dt = h5py.string_dtype("ascii")
        if file_version is not None:
            h.attrs["File Version"] = np.bytes_(file_version.encode("ascii"))
        h.create_dataset(_PROFILE_NAMES, data=np.array(profiles, dtype=dt))
        h.create_dataset(f"{_XS_BASE}/Water Surface", data=water_surface)
        # Scrambled variable block the reader must NOT use.
        bad = np.full((2, 34, 3), 999.0, dtype=np.float32)
        h.create_dataset(f"{_XS_BASE}/Cross Section Variables", data=bad)
        gx = "/Geometry/Cross Sections"
        if layout == "flat":
            h.create_dataset(f"{gx}/River Names", data=np.array(rivers, dtype=dt))
            h.create_dataset(f"{gx}/Reach Names", data=np.array(reaches, dtype=dt))
            h.create_dataset(f"{gx}/River Stations", data=np.array(stations, dtype=dt))
        elif layout == "compound":
            comp = np.dtype([("River", "S16"), ("Reach", "S16"), ("RS", "S8")])
            arr = np.array(list(zip(rivers, reaches, stations)), dtype=comp)
            h.create_dataset(f"{gx}/Attributes", data=arr)
        elif layout == "none":
            pass  # neither layout present


@unittest.skipUnless(HAS_H5PY, "h5py/numpy not installed")
class SteadyWseReaderTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".hdf")
        os.close(fd)
        _write_synthetic_hdf(self.path)
        self.res = read_steady_profile_wse(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_profile_names(self):
        self.assertEqual(self.res.profile_names, ["100-year", "500-year"])

    def test_wse_aligned_by_name(self):
        self.assertAlmostEqual(
            self.res.get_wse("RiverA", "Reach 1", "300", "100-year"), 872.88, places=2)
        self.assertAlmostEqual(
            self.res.get_wse("RiverA", "Reach 1", "100", "100-year"), 860.80, places=2)
        self.assertAlmostEqual(
            self.res.get_wse("RiverA", "Reach 1", "200", "500-year"), 866.00, places=2)

    def test_ignores_scrambled_variables(self):
        # If the reader had used 'Cross Section Variables' it would return 999.0.
        for sta in ("300", "200", "100"):
            self.assertNotAlmostEqual(
                self.res.get_wse("RiverA", "Reach 1", sta, "100-year"), 999.0, places=2)

    def test_whitespace_tolerant_keys(self):
        self.assertAlmostEqual(
            self.res.get_wse("  RiverA ", " Reach 1", " 300 ", "100-year"), 872.88, places=2)

    def test_missing_xs_returns_none(self):
        self.assertIsNone(self.res.get_wse("RiverA", "Reach 1", "999", "100-year"))


@unittest.skipUnless(HAS_H5PY, "h5py/numpy not installed")
class SteadyWseLayoutTests(unittest.TestCase):
    """
    The reader must handle both the 5.x flat and 6.0+ compound geometry layouts.

    The flat (5.0.3) and compound (7.0) paths are exercised on *real* fixtures in
    test_levee_obstruct.py (SterpCreek p01 / p02).  Only the unknown-layout
    fallback — which no real fixture can produce — is tested synthetically here.
    """

    def _read(self, layout, file_version="HEC-RAS 5.0.3 September 2016"):
        fd, path = tempfile.mkstemp(suffix=".hdf")
        os.close(fd)
        _write_synthetic_hdf(path, layout=layout, file_version=file_version)
        self.addCleanup(os.remove, path)
        return read_steady_profile_wse(path)

    def test_unknown_layout_warns_then_raises(self):
        # No known layout present: assume-latest logs a warning, then the missing
        # 'Attributes' dataset makes it fail loudly rather than returning wrong data.
        with self.assertLogs(level="WARNING") as cm:
            with self.assertRaises(KeyError):
                self._read("none", file_version="HEC-RAS 9.9 Future")
        self.assertTrue(any("9.9" in m or "no recognised geometry layout" in m
                            for m in cm.output))


if __name__ == "__main__":
    unittest.main()
