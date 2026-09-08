"""Tests for SA/2D connection geometry parsing and station mapping.

The stationing expectations here encode HEC-RAS's own contract for a
weir-mode connection — station/elevation length must match GIS centerline
length to within 1 ft or 0.5%, whichever is smaller — which was verified by
deliberately truncating a levee profile and watching RAS refuse to run the
model.  Bridge-mode connections are exempt; see
``hack_ras.geometry.conn_interp`` for the survey behind that split.
"""

import math
import unittest
from pathlib import Path

from hack_ras.geometry import conn_interp as CI
from hack_ras.geometry.model import Connection
from hack_ras.geometry.parser import GeometryParser

try:
    import h5py  # noqa: F401

    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

DATA = Path(__file__).parent / "data"
FIXTURE = DATA / "2D culvert bridge levee precip pipes"
MODEL_G02 = FIXTURE / "Model.g02"
MODEL_P06_HDF = FIXTURE / "Model.p06.hdf"


class TestConnectionParse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geom = GeometryParser().parse_file(str(MODEL_G02))

    def test_all_three_connections_are_found(self):
        self.assertEqual(sorted(self.geom.connections),
                         ["Bridge", "Culvert", "Levee"])

    def test_levee_centerline_and_profile(self):
        levee = self.geom.connections["Levee"]
        self.assertEqual(len(levee.centerline), 13)
        self.assertEqual(len(levee.weir_profile), 47)
        # stations ascend from 0; elevations are real ground values
        self.assertEqual(levee.weir_profile[0][0], 0.0)
        self.assertGreater(levee.weir_profile[-1][0], 2000.0)
        self.assertTrue(all(b[0] >= a[0] for a, b in
                            zip(levee.weir_profile, levee.weir_profile[1:])))

    def test_header_coordinates_are_a_label_anchor_not_geometry(self):
        levee = self.geom.connections["Levee"]
        # the anchor RAS draws the name at is NOT the first centerline vertex
        self.assertIsNotNone(levee.label_xy)
        self.assertNotEqual(levee.label_xy, levee.centerline[0])

    def test_up_and_dn_area_names_are_stripped(self):
        levee = self.geom.connections["Levee"]
        self.assertEqual(levee.up_sa, "Watershed")
        self.assertEqual(levee.dn_sa, "Interior")

    def test_weir_parameters_and_mode(self):
        levee = self.geom.connections["Levee"]
        self.assertEqual(levee.routing_type, 1)
        self.assertEqual(levee.mode, "Weir/Gate/Culverts")
        self.assertTrue(levee.is_weir_mode)
        self.assertIsNotNone(levee.weir_coef)

    def test_zero_count_profile_block_is_header_only(self):
        # the Bridge connection carries 'Conn Weir SE= 0'
        bridge = self.geom.connections["Bridge"]
        self.assertEqual(bridge.weir_profile, [])
        self.assertEqual(len(bridge.centerline), 2)

    def test_terrain_profile_is_empty_when_ras_writes_zero_points(self):
        for conn in self.geom.connections.values():
            self.assertEqual(conn.terrain_profile, [])

    def test_raw_line_bounds_bracket_the_block(self):
        levee = self.geom.connections["Levee"]
        start, end = levee._raw_line_start, levee._raw_line_end
        self.assertTrue(0 <= start < end <= len(self.geom.raw_lines))
        self.assertTrue(
            self.geom.raw_lines[start].startswith("Connection=Levee"))

    def test_one_connection_per_header_line_and_no_block_bleed(self):
        # A connection block's data lines must not be mistaken for another
        # block's, and every 'Connection=' header must yield exactly one entry.
        headers = [ln for ln in self.geom.raw_lines
                   if ln.startswith("Connection=")]
        self.assertEqual(len(self.geom.connections), len(headers))
        # storage areas share the file and must still parse
        self.assertTrue(self.geom.storage_areas_2d)
        self.assertTrue(any(sa.points for sa in self.geom.storage_areas_2d))


def _straight_connection(length, profile_span, routing_type=1):
    """A two-point horizontal connection with a chosen stationing span."""
    return Connection(
        name="synthetic",
        centerline=[(0.0, 0.0), (length, 0.0)],
        weir_profile=[(0.0, 100.0), (profile_span, 100.0)],
        routing_type=routing_type,
    )


class TestStationingContract(unittest.TestCase):
    def test_tolerance_is_one_foot_for_long_connections(self):
        conn = _straight_connection(2000.0, 2000.0)
        self.assertAlmostEqual(CI.station_tolerance(conn), 1.0)

    def test_tolerance_is_half_a_percent_below_200_feet(self):
        # 0.5% of 100 ft = 0.5 ft, which is smaller than 1 ft, so it governs
        conn = _straight_connection(100.0, 100.0)
        self.assertAlmostEqual(CI.station_tolerance(conn), 0.5)

    def test_drift_sign_and_pass(self):
        conn = _straight_connection(1000.0, 1000.4)
        self.assertAlmostEqual(CI.station_drift(conn), 0.4)
        self.assertTrue(CI.stationing_ok(conn))
        self.assertAlmostEqual(CI.check_stationing(conn), 0.4)

    def test_truncated_weir_profile_is_rejected(self):
        # the failure mode observed live: profile truncated in the GUI, model
        # then refuses to run in HEC-RAS
        conn = _straight_connection(2454.73, 2004.915)
        self.assertFalse(CI.stationing_ok(conn))
        with self.assertRaises(CI.ConnectionStationMismatch) as ctx:
            CI.check_stationing(conn)
        self.assertIn("2004.915", str(ctx.exception))

    def test_bridge_mode_is_exempt(self):
        # 12 of 32 bridge-mode connections in shipped models break the rule,
        # by up to 419.7 ft, in geometries that run
        conn = _straight_connection(492.872, 912.600, routing_type=32)
        self.assertEqual(conn.mode, "Bridge Opening")
        self.assertFalse(CI.stationing_enforced(conn))
        self.assertTrue(CI.stationing_ok(conn))
        self.assertAlmostEqual(CI.check_stationing(conn), 419.728, places=3)

    def test_unknown_routing_type_is_not_claimed_to_be_enforced(self):
        conn = _straight_connection(100.0, 500.0, routing_type=99)
        self.assertIsNone(conn.mode)
        self.assertFalse(CI.stationing_enforced(conn))

    def test_missing_geometry_raises_value_error_not_mismatch(self):
        no_line = Connection(name="x", weir_profile=[(0.0, 1.0), (10.0, 1.0)])
        with self.assertRaises(ValueError):
            CI.centerline_length(no_line)
        no_profile = Connection(name="x", centerline=[(0.0, 0.0), (10.0, 0.0)])
        with self.assertRaises(ValueError):
            CI.profile_length(no_profile)

    def test_real_weir_connections_all_satisfy_the_rule(self):
        geom = GeometryParser().parse_file(str(MODEL_G02))
        checked = 0
        for conn in geom.connections.values():
            if not (conn.is_weir_mode and conn.weir_profile
                    and len(conn.centerline) >= 2):
                continue
            CI.check_stationing(conn)   # raises on violation
            checked += 1
        self.assertGreater(checked, 0)


class TestStationMapping(unittest.TestCase):
    """Stations are walked as arc length, NOT mapped fractionally like an XS."""

    def setUp(self):
        # an L-shaped line: 300 ft east, then 400 ft north (700 ft total)
        self.conn = Connection(
            name="L",
            centerline=[(0.0, 0.0), (300.0, 0.0), (300.0, 400.0)],
            weir_profile=[(0.0, 10.0), (700.0, 24.0)],
            routing_type=1,
        )

    def test_station_is_a_distance_not_a_fraction(self):
        # 350 ft along means 50 ft up the second leg, whatever the total is
        x, y = CI.station_to_xy(self.conn, 350.0)
        self.assertAlmostEqual(x, 300.0)
        self.assertAlmostEqual(y, 50.0)

    def test_endpoints_and_clamping(self):
        self.assertEqual(CI.station_to_xy(self.conn, 0.0), (0.0, 0.0))
        self.assertEqual(CI.station_to_xy(self.conn, 700.0), (300.0, 400.0))
        self.assertEqual(CI.station_to_xy(self.conn, -50.0), (0.0, 0.0))
        self.assertEqual(CI.station_to_xy(self.conn, 9999.0), (300.0, 400.0))

    def test_clip_length_equals_requested_width(self):
        pts = CI.clip_polyline(self.conn, 100.0, 160.0)
        arc = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        self.assertAlmostEqual(arc, 60.0)

    def test_clip_across_a_bend_keeps_the_vertex(self):
        pts = CI.clip_polyline(self.conn, 250.0, 350.0)
        self.assertIn((300.0, 0.0), pts)
        arc = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        self.assertAlmostEqual(arc, 100.0)

    def test_clip_clamps_at_the_ends(self):
        pts = CI.clip_polyline(self.conn, -100.0, 50.0)
        arc = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        self.assertAlmostEqual(arc, 50.0)

    def test_clamp_station(self):
        self.assertEqual(CI.clamp_station(self.conn, -5.0), 0.0)
        self.assertEqual(CI.clamp_station(self.conn, 5000.0), 700.0)
        self.assertEqual(CI.clamp_station(self.conn, 123.0), 123.0)

    def test_crest_elevation_interpolates_and_clamps(self):
        self.assertAlmostEqual(CI.elev_at(self.conn, 0.0), 10.0)
        self.assertAlmostEqual(CI.elev_at(self.conn, 350.0), 17.0)
        self.assertAlmostEqual(CI.elev_at(self.conn, 700.0), 24.0)
        self.assertAlmostEqual(CI.elev_at(self.conn, 1e6), 24.0)

    def test_terrain_profile_absence_is_explicit(self):
        with self.assertRaises(ValueError):
            CI.terrain_elev_at(self.conn, 100.0)


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestAsciiMatchesHdf(unittest.TestCase):
    """The geometry HDF is what RAS computed with; the ASCII parse must agree."""

    def test_levee_centerline_and_profile_match_the_hdf(self):
        import numpy as np

        from hack_ras.results.reader import read_connection_centerline

        conn = GeometryParser().parse_file(str(MODEL_G02)).connections["Levee"]
        hdf = read_connection_centerline(str(MODEL_P06_HDF), "Levee")

        self.assertEqual(len(hdf.points), len(conn.centerline))
        for (ax, ay), (hx, hy) in zip(conn.centerline, hdf.points):
            self.assertAlmostEqual(ax, hx, places=4)
            self.assertAlmostEqual(ay, hy, places=4)

        ascii_len = CI.centerline_length(conn)
        hdf_len = float(np.hypot(np.diff(hdf.points[:, 0]),
                                 np.diff(hdf.points[:, 1])).sum())
        self.assertAlmostEqual(ascii_len, hdf_len, places=3)

        self.assertEqual(hdf.mode, conn.mode)
        self.assertEqual(hdf.us_area, conn.up_sa)
        self.assertEqual(hdf.ds_area, conn.dn_sa)
        self.assertEqual(len(hdf.profile), len(conn.weir_profile))

    def test_missing_connection_raises_key_error(self):
        from hack_ras.results.reader import read_connection_centerline

        with self.assertRaises(KeyError):
            read_connection_centerline(str(MODEL_P06_HDF), "No Such Levee")


if __name__ == "__main__":
    unittest.main()
