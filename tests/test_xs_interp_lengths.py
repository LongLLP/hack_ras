"""
Tests for the length helpers in hack_ras.geometry.xs_interp:
cutline_arc_length() and station_length().
"""
import unittest

from hack_ras.geometry.model import CrossSection, XSGISCutLine
from hack_ras.geometry.xs_interp import cutline_arc_length, station_length


def _xs(points=None, sta_elev=None):
    return CrossSection(
        river="R", reach="Rc", station="100",
        cutline=XSGISCutLine(n_points=len(points), points=points) if points else None,
        sta_elev=sta_elev,
    )


class CutlineArcLengthTests(unittest.TestCase):
    def test_3_4_5_triangle(self):
        xs = _xs(points=[(0.0, 0.0), (3.0, 4.0)])
        self.assertAlmostEqual(cutline_arc_length(xs), 5.0)

    def test_multi_segment(self):
        xs = _xs(points=[(0.0, 0.0), (3.0, 4.0), (3.0, 14.0)])
        self.assertAlmostEqual(cutline_arc_length(xs), 15.0)

    def test_no_cutline_raises(self):
        with self.assertRaises(ValueError):
            cutline_arc_length(_xs(points=None, sta_elev=[(0, 1)]))


class StationLengthTests(unittest.TestCase):
    def test_span(self):
        xs = _xs(sta_elev=[(0.0, 10.0), (5.0, 0.0), (10.0, 10.0)])
        self.assertAlmostEqual(station_length(xs), 10.0)

    def test_nonzero_start(self):
        xs = _xs(sta_elev=[(100.0, 10.0), (140.0, 10.0)])
        self.assertAlmostEqual(station_length(xs), 40.0)

    def test_no_sta_elev_raises(self):
        with self.assertRaises(ValueError):
            station_length(_xs(points=[(0, 0), (1, 1)], sta_elev=None))


class MismatchScenarioTests(unittest.TestCase):
    def test_matching_lengths(self):
        # 14-ft cut line, 14-ft station span -> ~0% difference.
        xs = _xs(points=[(0.0, 0.0), (14.0, 0.0)],
                 sta_elev=[(0.0, 5.0), (14.0, 5.0)])
        sta = station_length(xs)
        cut = cutline_arc_length(xs)
        self.assertAlmostEqual((cut - sta) / sta * 100.0, 0.0)

    def test_gross_mismatch(self):
        # 14-ft station span stretched onto a 2886-ft cut line (real g03 case).
        xs = _xs(points=[(0.0, 0.0), (2886.0, 0.0)],
                 sta_elev=[(0.0, 5.0), (14.0, 5.0)])
        sta = station_length(xs)
        cut = cutline_arc_length(xs)
        self.assertGreater((cut - sta) / sta * 100.0, 1000.0)


if __name__ == "__main__":
    unittest.main()
