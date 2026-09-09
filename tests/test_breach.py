"""Tests for breach definitions (plan ASCII) and realised breaches (HDF).

``Model.p06`` is a fingerprint plan authored in the RAS 7.0 GUI specifically
for these tests: every breach control was given a distinct, recognizable value
so each ``Breach Geom`` position is individually identifiable.  The
expectations below are the numbers typed into the GUI, so these tests check the
decoder against HEC-RAS itself rather than against a previous run of the
decoder.  Its side slopes are deliberately asymmetric (2 and 3), which is what
pins down field order and exercises the top-width geometry — every breach in
the other sample models uses vertical walls, where the check is vacuous.

GUI values for p06 (Breach Field Fingerprint):
    Center Station 1234, Final Bottom Width 111, Final Bottom Elevation 742,
    Left Side Slope 2, Right Side Slope 3, Failure Mode Piping,
    Piping Coefficient 0.44, Initial Piping Elev 750.5,
    Breach Formation Time 3.75 hr, Breach Weir Coef 2.11
"""

import unittest
from pathlib import Path

from hack_ras.geometry import conn_interp as CI
from hack_ras.geometry.parser import GeometryParser
from hack_ras.project.breach import (
    BREACH_GEOM_FIELDS,
    TRIGGER_SET_TIME,
    TRIGGER_WS_ELEV,
    TRIGGER_WS_ELEV_DURATION,
    BreachDefinition,
    decode_trigger,
    read_breach_definitions,
    read_plan_breach_summary,
)

try:
    import h5py  # noqa: F401

    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

DATA = Path(__file__).parent / "data"
FIXTURE = DATA / "2D culvert bridge levee precip pipes"
P02 = FIXTURE / "Model.p02"
P04 = FIXTURE / "Model.p04"
P06 = FIXTURE / "Model.p06"
P02_HDF = FIXTURE / "Model.p02.hdf"
P06_HDF = FIXTURE / "Model.p06.hdf"
G02 = FIXTURE / "Model.g02"


class TestBreachGeomFingerprint(unittest.TestCase):
    """Every Breach Geom field, against the values typed into the GUI."""

    @classmethod
    def setUpClass(cls):
        cls.breaches = read_breach_definitions(str(P06))

    def test_one_breach_on_the_levee_connection(self):
        self.assertEqual(len(self.breaches), 1)
        b = self.breaches[0]
        self.assertTrue(b.is_connection)
        self.assertEqual(b.connection, "Levee")
        self.assertEqual(b.target, "Levee")
        self.assertEqual(b.river, "")
        self.assertEqual(b.reach, "")

    def test_every_geom_field(self):
        b = self.breaches[0]
        self.assertEqual(b.center_station, 1234.0)
        self.assertEqual(b.bottom_width, 111.0)
        self.assertEqual(b.bottom_elev, 742.0)
        self.assertEqual(b.left_slope, 2.0)
        self.assertEqual(b.right_slope, 3.0)
        self.assertTrue(b.piping)
        self.assertEqual(b.piping_coef, 0.44)
        self.assertEqual(b.initial_piping_elev, 750.5)
        self.assertEqual(b.formation_time, 3.75)
        self.assertEqual(b.weir_coef, 2.11)

    def test_side_slopes_are_not_interchangeable(self):
        # asymmetric on purpose: swapping fields 4 and 5 must be detectable
        b = self.breaches[0]
        self.assertNotEqual(b.left_slope, b.right_slope)

    def test_field_name_table_matches_the_dataclass(self):
        for name in BREACH_GEOM_FIELDS:
            self.assertTrue(hasattr(self.breaches[0], name), name)

    def test_method_is_user_entered_data(self):
        b = self.breaches[0]
        self.assertEqual(b.method, 0)
        self.assertEqual(b.method_name, "User Entered Data")
        # formation time is live under User Entered Data
        self.assertEqual(b.active_formation_time, 3.75)

    def test_piping_fields_are_reported_active(self):
        self.assertEqual(
            self.breaches[0].active_piping_fields,
            {"piping_coef": 0.44, "initial_piping_elev": 750.5,
             "initial_piping_diameter": None},
        )

    def test_top_width_uses_both_side_slopes(self):
        b = self.breaches[0]
        # depth 10 -> 111 + (2+3)*10
        self.assertAlmostEqual(b.breach_depth(752.0), 10.0)
        self.assertAlmostEqual(b.top_width(752.0), 161.0)

    def test_top_width_of_a_vertical_walled_breach_is_the_bottom_width(self):
        b = BreachDefinition(bottom_width=45.0, bottom_elev=749.0)
        self.assertAlmostEqual(b.top_width(758.74), 45.0)

    def test_breach_invert_above_the_crest_does_not_shrink_the_width(self):
        b = self.breaches[0]
        self.assertLess(b.breach_depth(700.0), 0)
        self.assertAlmostEqual(b.top_width(700.0), 111.0)

    def test_missing_geometry_returns_none_rather_than_guessing(self):
        empty = BreachDefinition()
        self.assertIsNone(empty.top_width(750.0))
        self.assertIsNone(empty.breach_depth(750.0))


class TestTriggerDecode(unittest.TestCase):
    """All three 'Trigger Failure at' modes, and the stored-but-inactive trap."""

    def test_set_time(self):
        t = decode_trigger("Breach Start=False,756.8,01JAN2025,1237,False,,,0")
        self.assertEqual(t.mode, TRIGGER_SET_TIME)
        self.assertEqual(t.set_time, "01JAN2025 1237")
        # field 2 holds 756.8 but the mode does not use it
        self.assertEqual(t.ws_elev, 756.8)
        self.assertIsNone(t.starting_ws)
        self.assertIsNone(t.immediate_initiation_ws)

    def test_ws_elev(self):
        t = decode_trigger("Breach Start=True,758.02,01JAN2025,1213,False,,,0")
        self.assertEqual(t.mode, TRIGGER_WS_ELEV)
        self.assertEqual(t.starting_ws, 758.02)
        self.assertIsNone(t.immediate_initiation_ws)
        # date/time are stored but unused in this mode
        self.assertEqual(t.set_time, "")

    def test_ws_elev_plus_duration(self):
        t = decode_trigger("Breach Start=False,758.89,,,True,758.02,0.69,0")
        self.assertEqual(t.mode, TRIGGER_WS_ELEV_DURATION)
        self.assertEqual(t.threshold_ws, 758.02)
        self.assertEqual(t.duration_hours, 0.69)
        self.assertEqual(t.immediate_initiation_ws, 758.89)
        self.assertIsNone(t.starting_ws)
        self.assertFalse(t.accumulate_duration)

    def test_accumulate_duration_checkbox(self):
        checked = decode_trigger(
            "Breach Start=False,1425,01JAN2026,0300,True,1421,2,-1")
        self.assertTrue(checked.accumulate_duration)

    def test_duration_mode_wins_when_both_flags_are_set(self):
        t = decode_trigger("Breach Start=True,10,,,True,20,1,0")
        self.assertEqual(t.mode, TRIGGER_WS_ELEV_DURATION)

    def test_describe_mentions_only_the_active_mode(self):
        t = decode_trigger("Breach Start=False,756.8,01JAN2025,1237,False,,,0")
        self.assertIn("Set Time", t.describe())
        self.assertNotIn("756.8", t.describe())

    def test_fixture_p06_trigger(self):
        t = read_breach_definitions(str(P06))[0].trigger
        self.assertEqual(t.mode, TRIGGER_SET_TIME)
        self.assertEqual(t.set_time, "01JAN2025 1237")


class TestPlanScanning(unittest.TestCase):
    def test_plan_without_a_breach_yields_nothing(self):
        # RAS writes no Breach lines at all for a plan with no breach
        self.assertEqual(read_breach_definitions(str(P04)), [])
        self.assertEqual(read_plan_breach_summary(str(P04)), "")

    def test_overtopping_plan_reports_no_active_piping_fields(self):
        b = read_breach_definitions(str(P02))[0]
        self.assertFalse(b.piping)
        self.assertEqual(b.active_piping_fields, {})
        # ... even though a coefficient is still on disk
        self.assertIsNotNone(b.piping_coef)

    def test_progression_curve_is_read_as_pairs(self):
        b = read_breach_definitions(str(P06))[0]
        self.assertEqual(b.progression, [(0.0, 0.0), (1.0, 1.0)])
        self.assertEqual(b.downcutting, [(0.0, 0.0), (1.0, 1.0)])
        self.assertEqual(b.widening, [(0.0, 0.0), (1.0, 1.0)])

    def test_summary_is_ascii_only(self):
        # printed to a Windows console, which mangles non-ASCII
        read_breach_definitions(str(P06))
        summary = read_plan_breach_summary(str(P06))
        self.assertTrue(summary)
        summary.encode("ascii")


class TestMultipleBreachesInOnePlan(unittest.TestCase):
    """A plan may define several breaches, each with its own curves.

    Synthetic (and trivially small, per dev_rules) because no committed fixture
    has two breach locations.  The layout mirrors Model_Hillside
    Current_Model_extra p25, which breaches
    two levees at once, where the two groups carried different progression and
    downcutting tables — which is what proves the curves are per-breach rather
    than plan-global.
    """

    PLAN = "\n".join([
        "Plan Title=Two breaches",
        "Geom File=g02",
        "Breach Loc=                ,                ,        ,True,First Levee     ",
        "Breach Method= 0 ",
        "Breach Geom=100,10,700,1,2,True,0.3,705,1.5,2.6",
        "Breach Start=True,710,,,False,,,0",
        "Breach Progression= 2 ",
        "       0       0       1       1",
        "Mass Wasting Options= 0 ",
        "Breach Loc=                ,                ,        ,True,Second Levee    ",
        "Breach Method= 1 ",
        "Breach Geom=200,20,800,0,0,False,0.5,,,2",
        "Breach Start=False,,01JAN2025,1204,False,,,0",
        "Breach Progression= 3 ",
        "       0       0     0.5     0.7       1       1",
        "Starting Notch Depth= 1 ",
        "Initial Piping Diameter= 9.9 ",
        "Mass Wasting Options= 0 ",
        "Calibration Method= 0 ",
        "",
    ])

    def setUp(self):
        import tempfile

        self.tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".p01", delete=False, encoding="latin-1", newline="")
        self.tmp.write(self.PLAN)
        self.tmp.close()
        self.breaches = read_breach_definitions(self.tmp.name)

    def tearDown(self):
        import os

        os.unlink(self.tmp.name)

    def test_both_breaches_are_read_in_file_order(self):
        self.assertEqual([b.connection for b in self.breaches],
                         ["First Levee", "Second Levee"])

    def test_each_breach_keeps_its_own_geometry_and_method(self):
        first, second = self.breaches
        self.assertEqual((first.method, first.center_station,
                          first.left_slope, first.right_slope),
                         (0, 100.0, 1.0, 2.0))
        self.assertEqual((second.method, second.center_station,
                          second.left_slope, second.right_slope),
                         (1, 200.0, 0.0, 0.0))

    def test_curves_are_per_breach_not_plan_global(self):
        first, second = self.breaches
        self.assertEqual(len(first.progression), 2)
        self.assertEqual(len(second.progression), 3)
        self.assertEqual(second.progression[1], (0.5, 0.7))

    def test_simplified_physical_extras_attach_to_the_right_breach(self):
        first, second = self.breaches
        self.assertIsNone(first.starting_notch_depth)
        self.assertIsNone(first.initial_piping_diameter)
        self.assertEqual(second.starting_notch_depth, 1.0)
        self.assertEqual(second.initial_piping_diameter, 9.9)

    def test_each_breach_keeps_its_own_trigger(self):
        first, second = self.breaches
        self.assertEqual(first.trigger.mode, TRIGGER_WS_ELEV)
        self.assertEqual(first.trigger.starting_ws, 710.0)
        self.assertEqual(second.trigger.mode, TRIGGER_SET_TIME)
        self.assertEqual(second.trigger.set_time, "01JAN2025 1204")

    def test_formation_time_is_inactive_under_simplified_physical(self):
        second = self.breaches[1]
        self.assertIsNone(second.active_formation_time)


@unittest.skipUnless(HAS_H5PY, "h5py required")
class TestRealisedBreach(unittest.TestCase):
    """What the solver actually opened, from Breaching Variables."""

    @classmethod
    def setUpClass(cls):
        from hack_ras.results.reader import read_breach_state

        cls.read_breach_state = staticmethod(read_breach_state)
        conn = GeometryParser().parse_file(str(G02)).connections["Levee"]
        cls.conn = conn
        cls.crest = CI.elev_at(conn, 1234.0)

    def test_plan_breach_data_confirms_the_ascii_field_order(self):
        from hack_ras.results.reader import read_plan_breach_data

        rows = read_plan_breach_data(str(P06_HDF))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # HDF mirrors Breach Geom fields 2, 4 and 5 independently of the GUI
        self.assertEqual(row["name"], "Levee")
        self.assertEqual(row["kind"], "Connection")
        self.assertEqual(row["bottom_width"], 111.0)
        self.assertEqual(row["side_slopes"], (2.0, 3.0))

    def test_plan_breach_data_is_empty_without_a_breach(self):
        from hack_ras.results.reader import read_plan_breach_data

        self.assertEqual(read_plan_breach_data(str(FIXTURE / "Model.p04.hdf")),
                         [])

    def test_breach_connections_listed(self):
        from hack_ras.results.reader import list_breach_connections

        self.assertEqual(list_breach_connections(str(P06_HDF)), ["Levee"])

    def test_piping_breach_carries_a_tenth_column(self):
        from hack_ras.results.reader import read_structure_timeseries

        piping = read_structure_timeseries(str(P06_HDF), "Levee")["breaching"]
        overtopping = read_structure_timeseries(str(P02_HDF), "Levee")["breaching"]
        # column count is not fixed: reading Variable_Unit is what makes this
        # work, and a hardcoded 9 would mislabel every piping column
        self.assertIn("Top-Elevation", piping)
        self.assertNotIn("Top-Elevation", overtopping)
        self.assertEqual(len(piping), 10)
        self.assertEqual(len(overtopping), 9)

    def test_realised_breach_is_the_widest_state_reached(self):
        state = self.read_breach_state(str(P06_HDF), "Levee",
                                       crest_elev=self.crest)
        self.assertTrue(state.fired)
        self.assertEqual(state.center_station, 1234.0)
        self.assertEqual(state.breach_at, "01JAN2025 12:37:00")
        self.assertEqual(state.hdf_path_kind, "SA 2D Area Conn")
        # the run ends before the breach finishes forming, so the realised
        # geometry falls short of the plan's terminal geometry
        self.assertLess(state.bottom_width, 111.0)
        self.assertGreater(state.bottom_elev, 742.0)
        self.assertLess(state.top_width, 194.7)
        self.assertGreater(state.max_flow, 0.0)

    def test_side_slopes_grow_with_the_breach(self):
        state = self.read_breach_state(str(P06_HDF), "Levee",
                                       crest_elev=self.crest)
        # partway to the plan's 2 and 3, and still asymmetric
        self.assertGreater(state.right_slope, state.left_slope)
        self.assertLess(state.left_slope, 2.0)
        self.assertLess(state.right_slope, 3.0)

    def test_top_width_needs_a_crest_and_says_so_by_returning_none(self):
        state = self.read_breach_state(str(P06_HDF), "Levee")
        self.assertTrue(state.fired)
        self.assertIsNone(state.top_width)
        self.assertIsNotNone(state.bottom_width)

    def test_top_width_exceeds_bottom_width_when_slopes_are_nonzero(self):
        state = self.read_breach_state(str(P06_HDF), "Levee",
                                       crest_elev=self.crest)
        self.assertGreater(state.top_width, state.bottom_width)

    def test_vertical_walled_breach_top_equals_bottom(self):
        state = self.read_breach_state(str(P02_HDF), "Levee",
                                       crest_elev=CI.elev_at(self.conn, 2010.0))
        self.assertEqual(state.left_slope, 0.0)
        self.assertEqual(state.right_slope, 0.0)
        self.assertAlmostEqual(state.top_width, state.bottom_width)

    def test_missing_breach_output_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.read_breach_state(str(FIXTURE / "Model.p04.hdf"), "Levee")

    def test_unknown_connection_raises_key_error(self):
        from hack_ras.results.reader import read_structure_timeseries

        with self.assertRaises(KeyError):
            read_structure_timeseries(str(P06_HDF), "No Such Levee")

    def test_interior_connection_is_found_under_its_prefixed_group(self):
        """An interior connection's group is named '<area> <connection>'."""
        from hack_ras.results.reader import read_structure_timeseries

        ts = read_structure_timeseries(str(P06_HDF), "Watershed Culvert")
        self.assertTrue(ts["structure"])
        self.assertIn(ts["kind"], ("SA 2D Area Conn", "2D Hyd Conn"))


if __name__ == "__main__":
    unittest.main()
