"""
Tests for hack_ras.results.reader.read_steady_xs_results

The real fixtures are the two Wisconsin Floodway plans: SterpCreek.p01.hdf
(RAS 5.0.3, flat geometry name arrays, four Additional Variables) and
SterpCreek.p02.hdf (RAS 7.0, compound Cross Sections/Attributes, ~50 Additional
Variables including 'Velocity Total').  They are the same model run in two
versions, so the version-dependent velocity routes can be compared against each
other on identical hydraulics.

A synthetic file covers the two things no real fixture can show cleanly: that the
misaligned 'Cross Section Variables' dataset is excluded, and that HEC-RAS's
~3.4e38 undefined sentinel becomes nan.
"""
import os
import tempfile
import unittest
from pathlib import Path

try:
    import h5py
    import numpy as np
    from hack_ras.results.reader import read_steady_xs_results
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

DATA = Path(__file__).parent / "data" / "Wisconsin Floodway"
HDF_503 = DATA / "SterpCreek.p01.hdf"
HDF_70 = DATA / "SterpCreek.p02.hdf"
HAS_FIXTURES = HDF_503.exists() and HDF_70.exists()

_XS_BASE = ("/Results/Steady/Output/Output Blocks/Base Output/"
            "Steady Profiles/Cross Sections")
_PROFILE_NAMES = ("/Results/Steady/Output/Output Blocks/Base Output/"
                  "Steady Profiles/Profile Names")


@unittest.skipUnless(HAS_H5PY and HAS_FIXTURES, "h5py or SterpCreek fixtures missing")
class SteadyXsResultsFixtureTests(unittest.TestCase):
    """Both real layouts, and the two velocity routes cross-checked."""

    @classmethod
    def setUpClass(cls):
        cls.r503 = read_steady_xs_results(str(HDF_503))
        cls.r70 = read_steady_xs_results(str(HDF_70))

    def test_profiles_and_keys_match_across_versions(self):
        self.assertEqual(self.r503.profile_names, ["100-year"])
        self.assertEqual(self.r503.profile_names, self.r70.profile_names)
        self.assertEqual(self.r503.keys, self.r70.keys)
        self.assertEqual(len(self.r503.keys), 73)

    def test_arrays_are_profile_by_xs(self):
        for res in (self.r503, self.r70):
            shape = (len(res.profile_names), len(res.keys))
            for name, arr in res.values.items():
                self.assertEqual(arr.shape, shape, name)

    def test_core_variables_present_in_both(self):
        for name in ("Water Surface", "Flow", "Area Flow Total",
                     "Top Width Total"):
            self.assertTrue(self.r503.has(name), name)
            self.assertTrue(self.r70.has(name), name)

    def test_cross_section_variables_excluded(self):
        # Present in the 5.0.3 file but its declared shape lies about its layout.
        with h5py.File(str(HDF_503), "r") as h:
            self.assertIn("Cross Section Variables", h[_XS_BASE])
        self.assertFalse(self.r503.has("Cross Section Variables"))

    def test_only_70_has_velocity_total(self):
        self.assertFalse(self.r503.has("Velocity Total"))
        self.assertTrue(self.r70.has("Velocity Total"))

    def test_wse_agrees_with_read_steady_profile_wse(self):
        from hack_ras.results.reader import read_steady_profile_wse
        ref = read_steady_profile_wse(str(HDF_503))
        for key in self.r503.keys:
            self.assertAlmostEqual(
                self.r503.get("Water Surface", *key, "100-year"),
                ref.get_wse(*key, "100-year"), places=6, msg=str(key))

    def test_velocity_routes_agree_across_versions(self):
        """5.0.3's derived Flow/Area matches 7.0's stored 'Velocity Total'."""
        compared = 0
        for key in self.r503.keys:
            derived = self.r503.mean_velocity(*key, "100-year")
            stored = self.r70.mean_velocity(*key, "100-year")
            if derived is None or stored is None or np.isnan(stored):
                continue
            self.assertAlmostEqual(derived, stored, places=4, msg=str(key))
            compared += 1
        self.assertGreater(compared, 50)

    def test_mean_velocity_equals_flow_over_area(self):
        key = self.r503.keys[0]
        flow = self.r503.get("Flow", *key, "100-year")
        area = self.r503.get("Area Flow Total", *key, "100-year")
        self.assertAlmostEqual(self.r503.mean_velocity(*key, "100-year"),
                               flow / area, places=9)

    def test_find_keys_infers_reach_from_river_and_station(self):
        river, reach, station = self.r503.keys[0]
        # Numeric station (as an Excel cell would hold it) finds the string key.
        hits = self.r503.find_keys(river, float(station))
        self.assertEqual(hits, [(river, reach, station)])
        # Whitespace-padded river name still matches.
        self.assertEqual(self.r503.find_keys(f"  {river} ", station),
                         [(river, reach, station)])

    def test_find_keys_reach_filters(self):
        river, reach, station = self.r503.keys[0]
        self.assertEqual(self.r503.find_keys(river, station, reach),
                         [(river, reach, station)])
        self.assertEqual(self.r503.find_keys(river, station, "No Such Reach"), [])

    def test_find_keys_reach_whitespace_and_case_insensitive(self):
        """RAS pads reach names ('Upper Reach  B'); a typed name must still match."""
        river, reach, station = self.r503.keys[0]
        for typed in (reach.upper(), reach.lower(), f"  {reach}  ",
                      " ".join(reach.split()), reach.replace(" ", "   ")):
            self.assertEqual(self.r503.find_keys(river, station, typed),
                             [(river, reach, station)], msg=typed)

    def test_find_keys_blank_reach_is_treated_as_omitted(self):
        river, reach, station = self.r503.keys[0]
        for blank in (None, "", "   "):
            self.assertEqual(self.r503.find_keys(river, station, blank),
                             [(river, reach, station)], msg=repr(blank))

    def test_reaches_of(self):
        river = self.r503.keys[0][0]
        reaches = self.r503.reaches_of(river)
        self.assertIn(self.r503.keys[0][1], reaches)
        self.assertEqual(len(reaches), len(set(reaches)))
        self.assertEqual(self.r503.reaches_of("No Such River"), [])

    def test_find_keys_unknown_returns_empty(self):
        self.assertEqual(self.r503.find_keys("No Such River", 100.0), [])
        river = self.r503.keys[0][0]
        self.assertEqual(self.r503.find_keys(river, -1.0), [])

    def test_get_unknown_xs_returns_none(self):
        self.assertIsNone(
            self.r503.get("Water Surface", "No Such River", "R", "1", "100-year"))

    def test_get_unknown_variable_raises(self):
        key = self.r503.keys[0]
        with self.assertRaises(KeyError):
            self.r503.get("Velocity Total", *key, "100-year")


@unittest.skipUnless(HAS_H5PY, "h5py/numpy not installed")
class SteadyXsResultsSyntheticTests(unittest.TestCase):
    """Sentinel masking and shape filtering, on a file built for the purpose."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".hdf")
        os.close(fd)
        dt = h5py.string_dtype("ascii")
        with h5py.File(self.path, "w") as h:
            h.attrs["File Version"] = np.bytes_(b"HEC-RAS 5.0.3 September 2016")
            h.create_dataset(_PROFILE_NAMES,
                             data=np.array(["100-year", "500-year"], dtype=dt))
            h.create_dataset(f"{_XS_BASE}/Water Surface",
                             data=np.array([[872.88, 3.4e38, 860.80],
                                            [875.10, 866.00, 861.50]],
                                           dtype=np.float32))
            h.create_dataset(f"{_XS_BASE}/Flow",
                             data=np.array([[200.0, 200.0, 0.0]] * 2,
                                           dtype=np.float32))
            h.create_dataset(f"{_XS_BASE}/Additional Variables/Area Flow Total",
                             data=np.array([[50.0, 100.0, 0.0]] * 2,
                                           dtype=np.float32))
            # Wrong shape: must be filtered out, like the real 5.x block.
            h.create_dataset(f"{_XS_BASE}/Cross Section Variables",
                             data=np.full((2, 34, 3), 999.0, dtype=np.float32))
            gx = "/Geometry/Cross Sections"
            h.create_dataset(f"{gx}/River Names",
                             data=np.array(["RiverA"] * 3, dtype=dt))
            h.create_dataset(f"{gx}/Reach Names",
                             data=np.array(["Reach 1"] * 3, dtype=dt))
            h.create_dataset(f"{gx}/River Stations",
                             data=np.array(["300", "200", "100"], dtype=dt))
        self.res = read_steady_xs_results(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_variables_found(self):
        self.assertEqual(self.res.variable_names(),
                         ["Area Flow Total", "Flow", "Water Surface"])

    def test_undefined_sentinel_becomes_nan(self):
        val = self.res.get("Water Surface", "RiverA", "Reach 1", "200", "100-year")
        self.assertTrue(np.isnan(val))
        self.assertAlmostEqual(
            self.res.get("Water Surface", "RiverA", "Reach 1", "300", "100-year"),
            872.88, places=2)

    def test_derived_velocity(self):
        self.assertAlmostEqual(
            self.res.mean_velocity("RiverA", "Reach 1", "300", "100-year"),
            4.0, places=6)

    def test_zero_area_velocity_is_none(self):
        self.assertIsNone(
            self.res.mean_velocity("RiverA", "Reach 1", "100", "100-year"))


@unittest.skipUnless(HAS_H5PY, "h5py/numpy not installed")
class SteadyXsAmbiguousStationTests(unittest.TestCase):
    """
    HEC-RAS allows the same station on two reaches of one river, so find_keys
    must report the ambiguity rather than silently pick one.  No real fixture
    has this, so it is built here.
    """

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".hdf")
        os.close(fd)
        dt = h5py.string_dtype("ascii")
        with h5py.File(self.path, "w") as h:
            h.attrs["File Version"] = np.bytes_(b"HEC-RAS 5.0.3 September 2016")
            h.create_dataset(_PROFILE_NAMES, data=np.array(["100-year"], dtype=dt))
            h.create_dataset(f"{_XS_BASE}/Water Surface",
                             data=np.array([[860.0, 870.0]], dtype=np.float32))
            gx = "/Geometry/Cross Sections"
            h.create_dataset(f"{gx}/River Names",
                             data=np.array(["RiverA", "RiverA"], dtype=dt))
            h.create_dataset(f"{gx}/Reach Names",
                             data=np.array(["Main", "Trib  A"], dtype=dt))
            h.create_dataset(f"{gx}/River Stations",
                             data=np.array(["500", "500"], dtype=dt))
        self.res = read_steady_xs_results(self.path)

    def tearDown(self):
        os.remove(self.path)

    def test_ambiguous_without_reach(self):
        hits = self.res.find_keys("RiverA", 500)
        self.assertEqual(len(hits), 2)
        self.assertEqual({k[1] for k in hits}, {"Main", "Trib  A"})

    def test_reach_resolves_the_ambiguity(self):
        self.assertEqual(self.res.find_keys("RiverA", 500, "Main"),
                         [("RiverA", "Main", "500")])
        # Typed with single spacing; RAS stores 'Trib  A'.
        self.assertEqual(self.res.find_keys("RiverA", 500, "Trib A"),
                         [("RiverA", "Trib  A", "500")])

    def test_values_differ_per_reach(self):
        self.assertAlmostEqual(
            self.res.get("Water Surface", "RiverA", "Main", "500", "100-year"),
            860.0, places=3)
        self.assertAlmostEqual(
            self.res.get("Water Surface", "RiverA", "Trib  A", "500", "100-year"),
            870.0, places=3)


if __name__ == "__main__":
    unittest.main()
