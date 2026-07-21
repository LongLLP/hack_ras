"""
Levee and blocked-obstruction parsing, lossless roundtrip, and an end-to-end
active-flow check against real HEC-RAS output.

Fixture: tests/data/Wisconsin Floodway/SterpCreek.g01 (+ .p01.hdf) — a RAS 5.0.3
steady model whose 'Sterp West / Upper' reach carries hand-placed levees,
"normal" and "multiple-block" blocked obstructions, and IFAs, documented in
Model_DCRA/Images.  The plan HDF's Additional Variables/Top Width Total is the
active (effective) top width RAS itself computed, used here as ground truth.
"""
import unittest
from pathlib import Path

from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.writer import GeometryWriter

DATA = Path(__file__).parent / "data" / "Wisconsin Floodway"
G01 = DATA / "SterpCreek.g01"
P01_HDF = DATA / "SterpCreek.p01.hdf"
# g02/p02 are a carbon copy of g01/p01, run in RAS 7.0 -> a real 7.0 steady
# results fixture with the compound geometry layout and identical features.
G02 = DATA / "SterpCreek.g02"
P02_HDF = DATA / "SterpCreek.p02.hdf"

try:
    import h5py  # noqa: F401
    import numpy as np
    from hack_ras.geometry.active_flow import active_flow_segments
    from hack_ras.results.reader import read_steady_profile_wse
    HAS_HDF = True
except ImportError:
    HAS_HDF = False


def _upper_xs(gfile=G01):
    geom = GeometryParser().parse_file(str(gfile))
    reach = geom.rivers["Sterp West"].reaches["Upper"]
    return {c.station: c for c in reach.cross_sections}


class LeveeParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xs = _upper_xs()

    def test_both_sides(self):
        lev = self.xs["43320"].levee
        self.assertAlmostEqual(lev.left_sta, 60.0)
        self.assertAlmostEqual(lev.left_elev, 875.0)
        self.assertAlmostEqual(lev.right_sta, 200.0)
        self.assertAlmostEqual(lev.right_elev, 874.0)

    def test_left_only(self):
        lev = self.xs["42528"].levee
        self.assertAlmostEqual(lev.left_sta, 250.0)
        self.assertAlmostEqual(lev.left_elev, 866.5)
        self.assertIsNone(lev.right_sta)
        self.assertIsNone(lev.right_elev)

    def test_right_only(self):
        lev = self.xs["40641"].levee
        self.assertIsNone(lev.left_sta)
        self.assertAlmostEqual(lev.right_sta, 1500.0)
        self.assertAlmostEqual(lev.right_elev, 866.0)

    def test_no_levee_is_none(self):
        self.assertIsNone(self.xs["40200"].levee)


class BlockObstructParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.xs = _upper_xs()

    def test_normal_obstruction(self):
        obstr = self.xs["41868"].blocked_obstructions
        self.assertEqual(obstr.obstr_type, "normal")
        self.assertEqual(len(obstr.areas), 2)
        left, right = obstr.areas
        self.assertEqual((left.start_sta, left.end_sta), (0.0, 0.0))   # blank left
        self.assertAlmostEqual(right.start_sta, 550.0)
        self.assertEqual(right.end_sta, 0.0)                           # 0.0 -> right edge
        self.assertAlmostEqual(right.elevation, 865.0)

    def test_multiple_block_obstruction(self):
        obstr = self.xs["40813"].blocked_obstructions
        self.assertEqual(obstr.obstr_type, "multiple_block")
        self.assertEqual(len(obstr.areas), 2)
        a, b = obstr.areas
        self.assertEqual((a.start_sta, a.end_sta, a.elevation), (900.0, 1500.0, 864.5))
        self.assertEqual((b.start_sta, b.end_sta, b.elevation), (1500.0, 1700.0, 866.0))

    def test_no_obstruction_is_none(self):
        self.assertIsNone(self.xs["40200"].blocked_obstructions)


class RoundtripTests(unittest.TestCase):
    def test_lossless_roundtrip(self, ):
        import tempfile, os
        original = G01.read_text(encoding="utf-8", errors="ignore")
        geom = GeometryParser().parse_file(str(G01))
        fd, out = tempfile.mkstemp(suffix=".g01"); os.close(fd)
        try:
            GeometryWriter().write(geom, out)
            written = Path(out).read_text(encoding="utf-8", errors="ignore")
        finally:
            os.remove(out)
        self.assertEqual(written, original)


def _ras_top_width_total(hdf_path):
    """{river_station: active top width} from a steady plan HDF, version-aware
    on the XS name index (5.x flat vs 6.0+ compound)."""
    import h5py
    from hack_ras.results.reader import read_xs_name_index
    b = ("/Results/Steady/Output/Output Blocks/Base Output/"
         "Steady Profiles/Cross Sections")
    with h5py.File(hdf_path, "r") as f:
        stations = [k[2] for k in read_xs_name_index(f, str(hdf_path))]
        twt = f[b + "/Additional Variables/Top Width Total"][0, :]
    return {rs: float(twt[i]) for i, rs in enumerate(stations)}


def _active_width(c, res):
    wse = res.get_wse("Sterp West", "Upper", c.station, "100-year")
    segs = active_flow_segments(c.sta_elev, wse, ineff=c.ineff,
                                levee=c.levee,
                                blocked_obstructions=c.blocked_obstructions)
    return sum(b - a for a, b in segs)


@unittest.skipUnless(HAS_HDF, "h5py/numpy not installed")
class ActiveFlowFixtureTests(unittest.TestCase):
    """
    End-to-end: active top width from geometry + WSE == RAS Top Width Total,
    on BOTH the 5.0.3 fixture (p01, flat geometry layout) and the 7.0 fixture
    (p02, compound layout) — a carbon copy run in RAS 7.0.
    """

    # (label, geometry file, plan HDF)
    CASES = [("5.0.3", G01, P01_HDF), ("7.0", G02, P02_HDF)]

    def test_all_xs_match_ras_active_top_width(self):
        for label, gfile, hdf in self.CASES:
            xs = _upper_xs(gfile)
            res = read_steady_profile_wse(str(hdf))
            tw = _ras_top_width_total(hdf)
            checked = 0
            for rs, c in xs.items():
                if c.sta_elev is None:        # bridge nodes have no station/elevation
                    continue
                checked += 1
                self.assertAlmostEqual(
                    _active_width(c, res), tw[rs], delta=0.5,
                    msg=f"[{label}] RS {rs}: active width != RAS Top Width Total")
            self.assertEqual(checked, 33, msg=label)   # 35 nodes minus 2 bridges

    def test_feature_cross_sections(self):
        expected = {"43320": 119.47,   # right levee clips behind-IFA active
                    "42528": 83.66,     # overtopped left levee -> no effect
                    "41868": 376.31,    # multi-block IFA + overtopped obstruction
                    "40813": 909.75,    # one obstruction pierces surface (-200 ft)
                    "40641": 139.82}     # levee behind IFA -> no active effect
        for label, gfile, hdf in self.CASES:
            xs = _upper_xs(gfile)
            res = read_steady_profile_wse(str(hdf))
            for rs, exp in expected.items():
                self.assertAlmostEqual(_active_width(xs[rs], res), exp, delta=0.5,
                                       msg=f"[{label}] RS {rs}")

    def test_versions_are_as_expected(self):
        # Guards that p02 really is the 7.0/compound fixture (and p01 the 5.0.3 one),
        # so the compound-layout WSE path is genuinely exercised on real data.
        from hack_ras.version import RasVersion
        self.assertTrue(RasVersion.from_hdf(str(P01_HDF)) < RasVersion(6, 0))
        self.assertTrue(RasVersion.from_hdf(str(P02_HDF)) >= RasVersion(6, 0))


if __name__ == "__main__":
    unittest.main()
