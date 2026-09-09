"""Tests for SA/2D connection geometry parsing and station mapping.

The stationing expectations here encode HEC-RAS's own contract for a
weir-mode connection — station/elevation length must match GIS centerline
length to within 1 ft or 0.5%, whichever is smaller — which was verified by
deliberately truncating a levee profile and watching RAS refuse to run the
model.  Bridge-mode connections are exempt; see
``hack_ras.geometry.conn_interp`` for the survey behind that split.
"""

import math
import numpy as np
import os
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
MODEL_P02_HDF = FIXTURE / "Model.p02.hdf"


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


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestSa2dResultLayouts(unittest.TestCase):
    """The two connection modes write different result layouts.

    A ``Weir/Gate/Culverts`` connection writes ``HW TW Cells`` plus
    ``HW TW Segments`` weir stationing; a RAS 7.0 ``Bridge Opening`` connection
    writes only ``Cell WS US`` / ``Cell WS DS`` under the same group, with no
    stationing of any kind.  ``read_sa2d_connection`` must read both.
    """

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_sa2d_connection

        cls.read = staticmethod(read_sa2d_connection)
        cls.hdf = str(MODEL_P02_HDF)

    def test_both_modes_are_listed(self):
        from hack_ras.results.reader import list_sa2d_connections

        self.assertEqual(sorted(list_sa2d_connections(self.hdf)),
                         ["Levee", "Watershed Bridge", "Watershed Culvert"])

    def test_weir_mode_cells_carry_stations(self):
        conn = self.read(self.hdf, "Watershed Culvert")
        self.assertEqual([c.cell_idx for c in conn.hw_cells], [11, 13])
        self.assertEqual([c.cell_idx for c in conn.tw_cells], [10, 12])
        for cell in conn.hw_cells + conn.tw_cells:
            self.assertFalse(math.isnan(cell.station))

    def test_bridge_mode_is_read_and_stationed_from_the_geometry(self):
        """The results group has no stationing; 2DBR US/DS Cells supplies it."""
        from hack_ras.results.reader import read_bridge_cells

        conn = self.read(self.hdf, "Watershed Bridge")
        self.assertEqual(sorted(c.cell_idx for c in conn.hw_cells), [22, 24])
        self.assertEqual(sorted(c.cell_idx for c in conn.tw_cells), [21, 23])

        for cell in conn.hw_cells + conn.tw_cells:
            self.assertFalse(math.isnan(cell.station))
            self.assertLessEqual(cell.station_start, cell.station)
            self.assertLessEqual(cell.station, cell.station_end)

        # The stations are the geometry's, not invented here.
        want = read_bridge_cells(self.hdf, "Bridge").stations("us")
        for cell in conn.hw_cells:
            self.assertAlmostEqual(cell.station, want[cell.cell_idx][1], places=4)

    def test_bridge_cells_sort_by_station_like_weir_cells(self):
        conn = self.read(self.hdf, "Watershed Bridge")
        for cells in (conn.hw_cells, conn.tw_cells):
            stations = [c.station for c in cells]
            self.assertEqual(stations, sorted(stations))

    def test_bridge_mode_wse_aligns_with_timestamps_and_cells(self):
        conn = self.read(self.hdf, "Watershed Bridge")
        for cell in conn.hw_cells + conn.tw_cells:
            self.assertEqual(cell.wse.shape, (len(conn.timestamps),))
        # Cell WS US is the headwater side, Cell WS DS the tailwater side, so
        # the bridge loses head in the direction RAS says it does.
        self.assertGreater(max(c.wse.max() for c in conn.hw_cells),
                           max(c.wse.max() for c in conn.tw_cells))

    def test_bridge_mode_wse_matches_the_summary_maximum_per_cell(self):
        """Columns map positionally onto Headwater/Tailwater Cells.

        The fixture writes only five output steps, so a summary maximum taken at
        sub-step accuracy can sit above the time-series peak — hence the loose
        bound.  The mapping itself was pinned exactly on a 145-step run
        (LAX_River_2D p19, Brdg2_GRST: 20 of 20 columns to 6e-5 ft).
        """
        from hack_ras.results.reader import read_summary_max

        conn = self.read(self.hdf, "Watershed Bridge")
        cells = conn.hw_cells + conn.tw_cells
        summary = read_summary_max(self.hdf, "Watershed",
                                   [c.cell_idx for c in cells])
        for cell in cells:
            ts_max = float(cell.wse.max())
            sum_max = summary[cell.cell_idx][0]
            self.assertGreaterEqual(sum_max + 1e-3, ts_max)
            self.assertLess(sum_max - ts_max, 2.0)

    def test_missing_connection_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.read(self.hdf, "No Such Connection")


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestBridgeCells(unittest.TestCase):
    """``read_bridge_cells`` filters the flat 2DBR tables to one structure."""

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_bridge_cells

        cls.read = staticmethod(read_bridge_cells)
        cls.cells = read_bridge_cells(str(MODEL_P02_HDF), "Bridge")

    def test_footprint_carries_per_cell_chords(self):
        self.assertEqual(len(self.cells.footprint), 2)
        self.assertEqual(sorted(self.cells.cells), [21, 22])
        # High chord is the deck, above the low chord, on every cell.
        for hi, lo in zip(self.cells.high_chord, self.cells.low_chord):
            self.assertGreater(hi, lo)

    def test_us_and_ds_cells_are_stationed(self):
        for side in ("us", "ds"):
            stations = self.cells.stations(side)
            self.assertTrue(stations)
            for lo, mid, hi in stations.values():
                self.assertLessEqual(lo, mid)
                self.assertLessEqual(mid, hi)
                self.assertAlmostEqual(mid, (lo + hi) / 2.0, places=6)

    def test_side_must_be_us_or_ds(self):
        with self.assertRaises(ValueError):
            self.cells.stations("upstream")

    def test_weir_mode_connection_has_no_bridge_footprint(self):
        with self.assertRaises(KeyError):
            self.read(str(MODEL_P02_HDF), "Levee")

    def test_missing_connection_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.read(str(MODEL_P02_HDF), "No Such Bridge")

    def test_reads_a_geometry_hdf_too(self):
        # 2DBR tables live under Geometry, so a .g##.hdf is enough.
        geom_hdf = FIXTURE / "Model.g02.hdf"
        if not geom_hdf.exists():
            self.skipTest("no Model.g02.hdf fixture")
        self.assertEqual(sorted(self.read(str(geom_hdf), "Bridge").cells),
                         [21, 22])


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestStructureProfiles(unittest.TestCase):
    """``Table Info`` has nine profile slots; all of them are readable."""

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_structure_profiles

        cls.read = staticmethod(read_structure_profiles)

    def test_bridge_exposes_deck_profiles_the_centerline_reader_omits(self):
        from hack_ras.results.reader import read_connection_centerline

        profiles = self.read(str(MODEL_P02_HDF), "Bridge")
        # The deck: BR Weir is the high chord, BR Lid the low chord.  Both are
        # invisible to read_connection_centerline, which reads only Centerline.
        for key in ("US BR", "US BR Weir", "US BR Lid",
                    "DS BR", "DS BR Weir", "DS BR Lid"):
            self.assertIn(key, profiles)
            self.assertEqual(profiles[key].shape[1], 2)
            self.assertGreater(profiles[key].shape[0], 0)

        # Cross-check the deck against the per-cell chords in 2DBR Cells: each
        # chord value must fall inside its profile's elevation range.
        from hack_ras.results.reader import read_bridge_cells

        cells = read_bridge_cells(str(MODEL_P02_HDF), "Bridge")
        weir = profiles["US BR Weir"][:, 1]
        lid = profiles["US BR Lid"][:, 1]
        for hi in np.unique(cells.high_chord):
            self.assertTrue(weir.min() <= hi <= weir.max(), hi)
        for lo in np.unique(cells.low_chord):
            self.assertTrue(lid.min() <= lo <= lid.max(), lo)

        # And confirm the omission is real, not a fixture quirk.
        centerline = read_connection_centerline(str(MODEL_P02_HDF), "Bridge")
        self.assertLess(len(centerline.profile), profiles["US BR"].shape[0])

    def test_weir_and_lid_need_not_share_a_station_range(self):
        """Guards the trap: the weir's global min can sit below the lid's max.

        In this fixture the lid covers only the opening while the weir covers
        the approaches too, so they must be compared at a shared station.
        """
        profiles = self.read(str(MODEL_P02_HDF), "Bridge")
        weir, lid = profiles["US BR Weir"], profiles["US BR Lid"]
        self.assertLess(weir[:, 0].min(), lid[:, 0].min())
        self.assertGreater(weir[:, 0].max(), lid[:, 0].max())
        self.assertLess(weir[:, 1].min(), lid[:, 1].max())

        # Within the lid's own extent, the deck really is above the low chord.
        lo, hi = lid[:, 0].min(), lid[:, 0].max()
        inside = weir[(weir[:, 0] >= lo) & (weir[:, 0] <= hi)]
        self.assertTrue(len(inside))
        self.assertGreater(inside[:, 1].min(), lid[:, 1].max() - 1e-9)

    def test_weir_mode_connection_has_only_a_centerline(self):
        profiles = self.read(str(MODEL_P02_HDF), "Levee")
        self.assertEqual(list(profiles), ["Centerline"])
        self.assertEqual(profiles["Centerline"].shape[1], 2)

    def test_centerline_slot_agrees_with_the_centerline_reader(self):
        from hack_ras.results.reader import read_connection_centerline

        profiles = self.read(str(MODEL_P02_HDF), "Levee")
        hdf = read_connection_centerline(str(MODEL_P02_HDF), "Levee")
        self.assertEqual(profiles["Centerline"].shape, hdf.profile.shape)
        self.assertTrue((profiles["Centerline"] == hdf.profile).all())

    def test_empty_slots_are_omitted_not_zero_length(self):
        for value in self.read(str(MODEL_P02_HDF), "Bridge").values():
            self.assertGreater(value.shape[0], 0)

    def test_missing_connection_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.read(str(MODEL_P02_HDF), "No Such Structure")


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestCulvertGroupResults(unittest.TestCase):
    """Per-group culvert flow, which Structure Variables only reports summed."""

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_culvert_group_results

        cls.read = staticmethod(read_culvert_group_results)

    def test_group_names_match_the_geometry_side(self):
        import h5py

        groups = self.read(str(MODEL_P02_HDF), "Watershed Culvert")
        self.assertEqual(list(groups), ["Culvert #1"])
        with h5py.File(str(MODEL_P02_HDF), "r") as hdf:
            attrs = hdf["Geometry/Structures/Culvert Groups/Attributes"][()]
        names = {r["Name"].decode().strip() for r in attrs}
        self.assertTrue(set(groups) <= names, (set(groups), names))

    def test_columns_come_from_the_dataset_metadata(self):
        group = self.read(str(MODEL_P02_HDF), "Watershed Culvert")["Culvert #1"]
        self.assertEqual(group.columns,
                         ("Culvert Flow", "Stage HW", "Stage TW"))
        for name in group.columns:
            self.assertEqual(group.values[name].shape,
                             (len(group.timestamps),))
        # The convenience properties address the same arrays.
        self.assertIs(group.flow, group.values["Culvert Flow"])
        self.assertIs(group.stage_hw, group.values["Stage HW"])
        self.assertIs(group.stage_tw, group.values["Stage TW"])

    def test_group_flow_is_bounded_by_the_connection_total(self):
        from hack_ras.results.reader import read_structure_timeseries

        groups = self.read(str(MODEL_P02_HDF), "Watershed Culvert")
        total = read_structure_timeseries(
            str(MODEL_P02_HDF), "Watershed Culvert")["structure"]
        summed = sum(g.flow for g in groups.values())
        self.assertTrue(
            np.allclose(summed, total["Total Culvert Flow"], atol=1e-3),
            f"summed groups != Total Culvert Flow",
        )

    def test_a_connection_with_no_culverts_returns_empty_not_an_error(self):
        self.assertEqual(self.read(str(MODEL_P02_HDF), "Watershed Bridge"), {})
        self.assertEqual(self.read(str(MODEL_P02_HDF), "Levee"), {})

    def test_missing_connection_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.read(str(MODEL_P02_HDF), "No Such Culvert")


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestStructureTimeseries(unittest.TestCase):
    """One reader serves every connection; a breach is just one of the cases."""

    def test_the_breach_named_function_is_gone(self):
        import hack_ras.results.reader as reader

        self.assertFalse(hasattr(reader, "read_breach_timeseries"))

    def test_every_connection_in_the_fixture_reads(self):
        from hack_ras.results.reader import read_structure_timeseries

        for conn in ("Watershed Culvert", "Watershed Bridge", "Levee"):
            out = read_structure_timeseries(str(MODEL_P02_HDF), conn)
            self.assertEqual(sorted(out),
                             ["breaching", "kind", "structure",
                              "timestamps", "weir"], conn)
            self.assertTrue(out["structure"], conn)

    def test_works_without_any_breach(self):
        from hack_ras.results.reader import read_structure_timeseries

        out = read_structure_timeseries(str(MODEL_P02_HDF), "Watershed Culvert")
        self.assertIsNone(out["breaching"])
        self.assertIn("Total Culvert Flow", out["structure"])
        self.assertIsNotNone(out["weir"])

    def test_reads_a_bridge_which_has_no_weir(self):
        from hack_ras.results.reader import read_structure_timeseries

        out = read_structure_timeseries(str(MODEL_P02_HDF), "Watershed Bridge")
        self.assertIsNone(out["weir"])
        self.assertIsNone(out["breaching"])
        self.assertEqual(
            sorted(out["structure"]),
            ["Drag Factor", "Error HW", "Flow", "Head loss",
             "Stage HW", "Stage TW"],
        )


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestSa2dAreaLookup(unittest.TestCase):
    """``read_sa2d_areas`` resolves both modes, by two different routes.

    Weir mode goes through the ``Node Pointer`` group attribute; a Bridge
    Opening group has no such attribute, so it falls back to matching the
    connection name against the ``Connection`` field.
    """

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_sa2d_areas

        cls.areas = staticmethod(read_sa2d_areas)
        cls.hdf = str(MODEL_P02_HDF)

    def test_weir_mode_spans_two_different_meshes(self):
        # The Levee route (Node Pointer) is the pre-existing behaviour.
        self.assertEqual(self.areas(self.hdf, "Levee"),
                         ("Watershed", "Interior"))

    def test_weir_mode_interior_connection_is_area_prefixed(self):
        self.assertEqual(self.areas(self.hdf, "Watershed Culvert"),
                         ("Watershed", "Watershed"))

    def test_bridge_mode_resolves_without_a_node_pointer(self):
        import h5py

        base = ("Results/Unsteady/Output/Output Blocks/Base Output"
                "/Unsteady Time Series/SA 2D Area Conn/Watershed Bridge")
        with h5py.File(self.hdf, "r") as hdf:
            self.assertNotIn("Node Pointer", hdf[base].attrs)

        self.assertEqual(self.areas(self.hdf, "Watershed Bridge"),
                         ("Watershed", "Watershed"))

    def test_missing_connection_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.areas(self.hdf, "No Such Connection")

    def test_truncated_name_collision_is_reported_not_guessed(self):
        """The name fallback has no tiebreaker, so it must refuse to guess.

        ``Connection`` is an S16 field, so two longer names collide once
        truncated.  Synthetic because no real model in tests/data has a
        collision, and the branch is unreachable without one.
        """
        import tempfile

        import h5py
        import numpy as np

        dtype = np.dtype([("Connection", "S16"), ("SNN ID", "<i4"),
                          ("US SA/2D", "S16"), ("DS SA/2D", "S16")])
        rows = np.array([(b"Brdg_Overflow_Ea", 1, b"Mesh A", b"Mesh A"),
                         (b"Brdg_Overflow_Ea", 2, b"Mesh B", b"Mesh B")],
                        dtype=dtype)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Collide.p01.hdf")
            with h5py.File(path, "w") as hdf:
                hdf.create_dataset("Geometry/Structures/Attributes", data=rows)
                # A bridge-mode results group: no Node Pointer attribute.
                hdf.create_group(
                    "Results/Unsteady/Output/Output Blocks/Base Output"
                    "/Unsteady Time Series/SA 2D Area Conn/Brdg_Overflow_Ea"
                )
            with self.assertRaises(ValueError) as ctx:
                self.areas(path, "Brdg_Overflow_Ea")
        self.assertIn("ambiguous", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
