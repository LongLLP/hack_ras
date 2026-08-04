"""
Tests for hack_ras.gis.clip.PolygonProbe

Synthetic geometry throughout: the point is the coincident-boundary arithmetic,
which is clearest on a rectangle whose dimensions are known exactly.  The real
case that motivated the module (a floodway polygon digitized ~1 ft off a cross
section, measuring 53 ft where the truth was 117 ft) is reproduced in miniature
by `test_line_just_outside_a_near_parallel_edge`.
"""
import unittest

try:
    from shapely.geometry import LineString, Polygon
    from hack_ras.gis.clip import (
        COINCIDENT_FACTOR,
        DEFAULT_TOL,
        LineInPolygon,
        PolygonProbe,
    )
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False


@unittest.skipUnless(HAS_SHAPELY, "shapely not installed")
class PolygonProbeTests(unittest.TestCase):
    """A 100 x 40 rectangle spanning x=0..100, y=0..40."""

    def setUp(self):
        self.box = Polygon([(0, 0), (100, 0), (100, 40), (0, 40)])
        self.probe = PolygonProbe(self.box, tol=1.0)

    def test_perpendicular_crossing(self):
        # Vertical line at x=50 crosses the 40-ft height cleanly.
        m = self.probe.measure(LineString([(50, -20), (50, 60)]))
        self.assertAlmostEqual(m.length, 40.0, places=6)
        self.assertFalse(m.coincident)
        self.assertFalse(m.empty)
        # Near the boundary only where it enters and leaves: 2 x 2*tol.
        self.assertAlmostEqual(m.along_boundary, 4.0, places=6)
        self.assertAlmostEqual(m.clean_crossing, 4.0, places=6)
        # Buffering out by tol adds tol at each end.
        self.assertAlmostEqual(m.widened_length, 42.0, places=6)
        self.assertEqual(m.tol, 1.0)

    def test_clean_crossing_stays_clean_at_other_tolerances(self):
        for tol in (0.25, 0.5, 2.0, 5.0):
            m = PolygonProbe(self.box, tol=tol).measure(
                LineString([(50, -20), (50, 60)]))
            self.assertAlmostEqual(m.along_boundary, 4.0 * tol, places=6)
            self.assertFalse(m.coincident, msg=f"tol={tol}")

    def test_line_outside_polygon(self):
        m = self.probe.measure(LineString([(200, -20), (200, 60)]))
        self.assertEqual(m.length, 0.0)
        self.assertTrue(m.empty)
        self.assertEqual(m.along_boundary, 0.0)
        self.assertFalse(m.coincident)

    def test_line_running_along_an_edge_is_flagged(self):
        # Sits exactly on the y=40 edge for 100 ft.
        m = self.probe.measure(LineString([(0, 40), (100, 40)]))
        self.assertTrue(m.coincident)
        self.assertGreater(m.along_boundary, COINCIDENT_FACTOR * self.probe.tol)

    def test_line_just_outside_a_near_parallel_edge(self):
        """
        The real failure mode: the line is 0.5 ft OUTSIDE the x=100 edge over most
        of its run, and only dips inside near the top.  Measured length is a small
        sliver; the widened length shows what it should have been.
        """
        line = LineString([(100.5, 0), (100.5, 30), (99.0, 32), (99.0, 40)])
        m = self.probe.measure(line)
        self.assertTrue(m.coincident)
        self.assertLess(m.length, 15.0)              # sliver only
        self.assertGreater(m.widened_length, 35.0)   # the honest width
        self.assertGreater(m.widened_length, 2 * m.length)

    def test_multipolygon(self):
        far = Polygon([(200, 0), (240, 0), (240, 40), (200, 40)])
        probe = PolygonProbe(self.box.union(far), tol=1.0)
        m = probe.measure(LineString([(-10, 20), (250, 20)]))
        self.assertAlmostEqual(m.length, 100.0 + 40.0, places=6)
        self.assertFalse(m.coincident)
        # Four boundary crossings now, not two.
        self.assertAlmostEqual(m.along_boundary, 8.0, places=6)

    def test_buffers_are_built_once(self):
        probe = PolygonProbe(self.box, tol=1.0)
        self.assertIs(probe.widened, probe.widened)
        self.assertGreater(probe.widened.area, self.box.area)
        self.assertTrue(probe.near_boundary.contains(
            LineString([(0, 40), (100, 40)])))

    def test_default_tol(self):
        self.assertEqual(PolygonProbe(self.box).tol, DEFAULT_TOL)

    def test_bad_tol_raises(self):
        for bad in (0, -1, -0.5):
            with self.assertRaises(ValueError):
                PolygonProbe(self.box, tol=bad)

    def test_result_is_a_dataclass_with_the_documented_fields(self):
        m = self.probe.measure(LineString([(50, -20), (50, 60)]))
        self.assertIsInstance(m, LineInPolygon)
        for field in ("length", "along_boundary", "widened_length",
                      "clean_crossing", "tol", "coincident"):
            self.assertTrue(hasattr(m, field), field)


if __name__ == "__main__":
    unittest.main()
