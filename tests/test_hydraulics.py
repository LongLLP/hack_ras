"""
Tests for hack_ras.results.hydraulics.critical_depth.

Each case is checked against a closed-form answer rather than a stored number:
a circle flowing half full has A = pi D^2 / 8 and T = D, so the discharge that
makes D/2 critical is sqrt(g A^3 / T); a box has yc = (q^2 / g)^(1/3) outright.
"""
import math
import unittest

import numpy as np

from hack_ras.results.hydraulics import critical_depth

G = 32.174


class TestCriticalDepth(unittest.TestCase):

    def test_circle_half_full(self):
        for d in (1.25, 3.0, 6.0):
            area, top = math.pi * d * d / 8.0, d
            q = math.sqrt(G * area ** 3 / top)
            with self.subTest(d=d):
                self.assertAlmostEqual(
                    float(critical_depth(q, 'Circular', d)), d / 2.0, places=9)

    def test_circle_any_depth_round_trips(self):
        """Pick a depth, compute its critical Q, solve back for the depth."""
        d = 4.0
        for y in (0.2, 1.0, 2.7, 3.6, 3.95):
            theta = 2.0 * math.acos(1.0 - 2.0 * y / d)
            area = d * d / 8.0 * (theta - math.sin(theta))
            top = d * math.sin(theta / 2.0)
            q = math.sqrt(G * area ** 3 / top)
            with self.subTest(y=y):
                self.assertAlmostEqual(
                    float(critical_depth(q, 'Circular', d)), y, places=8)

    def test_circle_never_exceeds_the_diameter(self):
        yc = critical_depth([1e3, 1e5], 'Circular', 3.0)
        self.assertTrue(np.all(yc < 3.0))

    def test_box_closed_form_and_cap(self):
        q, b, rise = 80.0, 4.0, 5.0
        want = ((q / b) ** 2 / G) ** (1.0 / 3.0)
        self.assertAlmostEqual(float(critical_depth(q, 'Box', rise, b)), want,
                               places=12)
        self.assertEqual(float(critical_depth(1e4, 'Box', rise, b)), rise)

    def test_sign_is_ignored_and_zero_flow_is_zero(self):
        yc = critical_depth([-50.0, 50.0, 0.0], 'Circular', 6.0)
        self.assertEqual(yc[0], yc[1])
        self.assertEqual(yc[2], 0.0)

    def test_arrays_broadcast_against_per_face_sections(self):
        yc = critical_depth([30.0, 30.0], 'Circular', [3.0, 6.0])
        self.assertGreater(yc[0], yc[1])  # the smaller pipe runs deeper

    def test_si_units_use_si_gravity(self):
        d = 1.0
        area = math.pi * d * d / 8.0
        q = math.sqrt(9.80665 * area ** 3 / d)
        self.assertAlmostEqual(
            float(critical_depth(q, 'Circular', d, si_units=True)), 0.5,
            places=9)

    def test_shape_name_is_case_and_space_tolerant(self):
        self.assertEqual(float(critical_depth(20.0, ' circular ', 3.0)),
                         float(critical_depth(20.0, 'Circular', 3.0)))

    def test_unsupported_shape_raises(self):
        with self.assertRaises(ValueError):
            critical_depth(10.0, 'Arch', 3.0, 4.0)

    def test_box_without_span_raises(self):
        with self.assertRaises(ValueError):
            critical_depth(10.0, 'Box', 3.0)


if __name__ == '__main__':
    unittest.main()
