# tests/test_plan_settings.py
"""Plan settings API — intervals, simulation time window, title/short ID.

Runs on a temp copy of the HEC-RAS-authored fixture model
tests/data/'2D culvert bridge levee precip pipes' (see dev_rules.md), so the
key names, the `Simulation Date=` layout and the `Short Identifier=` padding
are RAS's own rather than something a test invented. Its p02/p04/p05/p06 all
sit at 10SEC computation / 1HOUR output.

The steady fixture (Wisconsin Floodway) covers the other real-world shape: a
plan that carries all four interval lines but a blank `Simulation Date=,,,`.
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from hack_ras import RasProject
from hack_ras.project.plan_settings import (
    _ALLOWED,
    read_plan_settings,
    set_plan_settings,
)
from hack_ras.project.plans import PlanFileNotFound, PlanRunActive
from hack_ras.utils.lines import read_lines

_DATA = os.path.join(os.path.dirname(__file__), "data")
_FIXTURE = os.path.join(_DATA, "2D culvert bridge levee precip pipes")
_STEADY = os.path.join(_DATA, "Wisconsin Floodway")
HAS_FIXTURE = os.path.isfile(os.path.join(_FIXTURE, "Model.p02"))
HAS_STEADY = os.path.isfile(os.path.join(_STEADY, "SterpCreek.p01"))


@unittest.skipUnless(HAS_FIXTURE, "extended model fixture not present")
class TestPlanSettings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, "model")
        shutil.copytree(
            _FIXTURE, self.folder,
            ignore=shutil.ignore_patterns(
                "Terrain", "Land_Classification", "*.backup"),
        )
        self.project = RasProject(os.path.join(self.folder, "Model.prj"))

    def plan_lines(self, pid):
        return read_lines(os.path.join(self.folder, f"Model.{pid}"))

    def raw_bytes(self, pid):
        with open(os.path.join(self.folder, f"Model.{pid}"), "rb") as f:
            return f.read()

    # --- read -------------------------------------------------------------

    def test_read_matches_the_fixture_as_ras_wrote_it(self):
        s = read_plan_settings(self.project, "p02")
        self.assertEqual(s.plan_id, "p02")
        self.assertEqual(s.title, "Test Model")
        self.assertEqual(s.short_id, "Test Model")
        self.assertEqual(s.computation_interval, "10SEC")
        self.assertEqual(s.mapping_interval, "1HOUR")
        self.assertEqual(s.hydrograph_interval, "1HOUR")
        self.assertEqual(s.detailed_interval, "1HOUR")
        self.assertEqual(s.window_raw, "01JAN2025,1000,01JAN2025,1400")
        self.assertEqual(s.start, datetime(2025, 1, 1, 10, 0))
        self.assertEqual(s.end, datetime(2025, 1, 1, 14, 0))
        self.assertEqual(s.missing_keys, [])

    def test_read_accepts_loose_plan_ids(self):
        for spec in ("p02", "P2", "2", 2):
            self.assertEqual(read_plan_settings(self.project, spec).plan_id,
                             "p02")

    def test_read_reports_a_short_id_that_differs_from_the_title(self):
        s = read_plan_settings(self.project, "p06")
        self.assertEqual(s.title, "Breach Field Fingerprint")
        self.assertEqual(s.short_id, "Fingerprint")

    def test_read_missing_plan_raises(self):
        with self.assertRaises(PlanFileNotFound):
            read_plan_settings(self.project, "p99")

    # --- write: intervals -------------------------------------------------

    def test_output_intervals_sets_all_three_but_not_the_computation_step(self):
        report = set_plan_settings(self.project, "2", output_intervals="5MIN")
        self.assertEqual(report["plans"], ["p02"])
        s = read_plan_settings(self.project, "p02")
        self.assertEqual(
            (s.mapping_interval, s.hydrograph_interval, s.detailed_interval),
            ("5MIN", "5MIN", "5MIN"))
        self.assertEqual(s.computation_interval, "10SEC")
        self.assertEqual(report["changed"]["p02"], {
            "Mapping Interval": ("1HOUR", "5MIN"),
            "Output Interval": ("1HOUR", "5MIN"),
            "Instantaneous Interval": ("1HOUR", "5MIN"),
        })

    def test_each_keyword_writes_its_own_ras_key(self):
        set_plan_settings(
            self.project, "p02",
            computation_interval="3SEC", mapping_interval="1HOUR",
            hydrograph_interval="10MIN", detailed_interval="30SEC")
        keys = {}
        for line in self.plan_lines("p02"):
            key, sep, val = line.rstrip("\r\n").partition("=")
            if sep and "Interval" in key:
                keys.setdefault(key, val.strip())
        self.assertEqual(keys["Computation Interval"], "3SEC")
        self.assertEqual(keys["Mapping Interval"], "1HOUR")
        self.assertEqual(keys["Output Interval"], "10MIN")
        self.assertEqual(keys["Instantaneous Interval"], "30SEC")

    def test_wq_output_interval_is_never_touched(self):
        set_plan_settings(self.project, "p02", hydrograph_interval="5MIN")
        wq = [l.rstrip("\r\n") for l in self.plan_lines("p02")
              if l.startswith("WQ Output Interval=")]
        self.assertEqual(wq, ["WQ Output Interval=15MIN"])

    def test_interval_values_are_case_and_space_insensitive(self):
        set_plan_settings(self.project, "p02", output_intervals=" 5 min ")
        self.assertEqual(
            read_plan_settings(self.project, "p02").mapping_interval, "5MIN")

    def test_bulk_spec_edits_every_selected_plan(self):
        report = set_plan_settings(self.project, "2,4-6",
                                   output_intervals="15MIN")
        self.assertEqual(report["plans"], ["p02", "p04", "p05", "p06"])
        for pid in report["plans"]:
            self.assertEqual(
                read_plan_settings(self.project, pid).mapping_interval,
                "15MIN")

    def test_setting_the_value_already_there_reports_unchanged(self):
        report = set_plan_settings(self.project, "p02",
                                   output_intervals="1HOUR")
        self.assertEqual(report["changed"], {})
        self.assertEqual(report["unchanged"], ["p02"])

    def test_a_no_op_call_leaves_the_file_byte_identical(self):
        before = self.raw_bytes("p02")
        set_plan_settings(self.project, "p02", output_intervals="1HOUR")
        self.assertEqual(self.raw_bytes("p02"), before)

    def test_only_the_edited_lines_change(self):
        before = self.plan_lines("p02")
        set_plan_settings(self.project, "p02", computation_interval="3SEC")
        after = self.plan_lines("p02")
        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(differing), 1)
        self.assertTrue(after[differing[0]].startswith("Computation Interval="))

    def test_bom_survives_an_edit(self):
        # The fixture plans carry no BOM; force one on so the round trip is
        # actually exercised (nine live Model_Hillside plans have one).
        path = os.path.join(self.folder, "Model.p02")
        with open(path, "rb") as f:
            body = f.read()
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" + body)
        set_plan_settings(self.project, "p02", computation_interval="3SEC")
        self.assertTrue(self.raw_bytes("p02").startswith(b"\xef\xbb\xbf"))
        self.assertEqual(
            read_plan_settings(self.project, "p02").computation_interval,
            "3SEC")

    # --- write: simulation time window ------------------------------------

    def test_start_and_end_from_datetimes(self):
        report = set_plan_settings(
            self.project, "p02",
            start=datetime(2025, 1, 2, 1, 1), end=datetime(2025, 1, 3, 23, 30))
        s = read_plan_settings(self.project, "p02")
        self.assertEqual(s.window_raw, "02JAN2025,0101,03JAN2025,2330")
        self.assertEqual(s.start, datetime(2025, 1, 2, 1, 1))
        self.assertEqual(s.end, datetime(2025, 1, 3, 23, 30))
        self.assertIn("Simulation Date", report["changed"]["p02"])

    def test_end_alone_leaves_the_stored_start_text_untouched(self):
        set_plan_settings(self.project, "p02", end="01JAN2025,2000")
        self.assertEqual(read_plan_settings(self.project, "p02").window_raw,
                         "01JAN2025,1000,01JAN2025,2000")

    def test_window_strings_accept_a_space_and_lowercase_month(self):
        set_plan_settings(self.project, "p02", start="01jan2025 900")
        self.assertEqual(read_plan_settings(self.project, "p02").window_raw,
                         "01JAN2025,0900,01JAN2025,1400")

    def test_ras_end_of_day_2400_is_written_as_typed(self):
        # RAS's own idiom for midnight-at-the-end; a datetime cannot express
        # it, so the string form has to survive verbatim.
        set_plan_settings(self.project, "p02", end="01JAN2025,2400")
        s = read_plan_settings(self.project, "p02")
        self.assertEqual(s.window_raw, "01JAN2025,1000,01JAN2025,2400")
        self.assertEqual(s.end, datetime(2025, 1, 2, 0, 0))

    def test_a_backwards_window_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "p02", end="01JAN2025,0900")
        self.assertIn("before it", str(ctx.exception))

    def test_an_equal_window_is_refused(self):
        with self.assertRaises(ValueError):
            set_plan_settings(self.project, "p02", start="01JAN2025,1400")

    # --- write: title / short id ------------------------------------------

    def test_title_change_delegates_and_keeps_the_existing_short_id(self):
        report = set_plan_settings(self.project, "p06", title="Renamed Plan",
                                   output_intervals="5MIN")
        self.assertEqual(report["retitled"]["p06"]["new_title"], "Renamed Plan")
        s = read_plan_settings(self.project, "p06")
        self.assertEqual(s.title, "Renamed Plan")
        self.assertEqual(s.short_id, "Fingerprint")   # not reset to the title
        self.assertEqual(s.mapping_interval, "5MIN")

    def test_short_id_change_keeps_the_title_and_the_field_padding(self):
        width_before = len(
            [l for l in self.plan_lines("p06")
             if l.startswith("Short Identifier=")][0].rstrip("\r\n"))
        set_plan_settings(self.project, "p06", short_id="FP2")
        s = read_plan_settings(self.project, "p06")
        self.assertEqual(s.short_id, "FP2")
        self.assertEqual(s.title, "Breach Field Fingerprint")
        width_after = len(
            [l for l in self.plan_lines("p06")
             if l.startswith("Short Identifier=")][0].rstrip("\r\n"))
        self.assertEqual(width_after, width_before)

    def test_a_title_change_across_several_plans_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "2,4", title="Same Name")
        self.assertIn("one plan at a time", str(ctx.exception))

    # --- validation: nothing is written when anything is wrong ------------

    def test_an_interval_ras_does_not_offer_is_refused(self):
        for bad in ("7MIN", "300SEC", "5MINS", "5", "banana", "1MONTH",
                    "5HOUR", "2DAY"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    set_plan_settings(self.project, "p02",
                                      output_intervals=bad)

    def test_every_allowed_value_is_accepted_by_its_own_field(self):
        for attr, values in _ALLOWED.items():
            for value in values:
                with self.subTest(attr=attr, value=value):
                    set_plan_settings(self.project, "p02", **{attr: value})
                    self.assertEqual(
                        getattr(read_plan_settings(self.project, "p02"), attr),
                        value)

    def test_the_four_dropdowns_do_not_share_one_list(self):
        # Each of these is offered by some fields and not others; a pooled
        # allow-list would wave all of them through everywhere.
        cases = [
            ("0.1SEC", {"computation_interval", "mapping_interval"}),
            ("Max Profile", {"mapping_interval", "detailed_interval"}),
            ("1YEAR", {"mapping_interval", "hydrograph_interval",
                       "detailed_interval"}),
        ]
        for value, ok_attrs in cases:
            for attr in _ALLOWED:
                with self.subTest(value=value, attr=attr):
                    if attr in ok_attrs:
                        set_plan_settings(self.project, "p02",
                                          **{attr: value})
                    else:
                        with self.assertRaises(ValueError):
                            set_plan_settings(self.project, "p02",
                                              **{attr: value})

    def test_max_profile_is_stored_with_ras_own_spelling(self):
        for typed in ("Max Profile", "max profile", "MAXPROFILE"):
            with self.subTest(typed=typed):
                set_plan_settings(self.project, "p02", mapping_interval=typed)
                self.assertEqual(
                    read_plan_settings(self.project, "p02").mapping_interval,
                    "Max Profile")

    def test_the_error_names_the_fields_that_do_offer_the_value(self):
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "p02",
                              computation_interval="Max Profile")
        msg = str(ctx.exception)
        self.assertIn("mapping_interval", msg)
        self.assertIn("detailed_interval", msg)

    def test_output_intervals_rejects_a_value_only_some_of_the_three_take(self):
        # 'Max Profile' is offered for mapping and detailed but not hydrograph,
        # so setting all three at once to it must fail rather than half-apply.
        before = self.raw_bytes("p02")
        with self.assertRaises(ValueError):
            set_plan_settings(self.project, "p02",
                              output_intervals="Max Profile")
        self.assertEqual(self.raw_bytes("p02"), before)

    def test_output_intervals_cannot_be_combined_with_the_individual_ones(self):
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "p02", output_intervals="5MIN",
                              mapping_interval="1HOUR")
        self.assertIn("output_intervals=", str(ctx.exception))

    def test_a_malformed_date_is_refused(self):
        for bad in ("01JAN2025", "2025-01-01,1000", "01XXX2025,1000",
                    "01JAN2025,9999"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    set_plan_settings(self.project, "p02", start=bad)

    def test_a_call_with_nothing_to_change_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "p02")
        self.assertIn("nothing to change", str(ctx.exception))

    def test_an_empty_spec_is_refused(self):
        with self.assertRaises(ValueError):
            set_plan_settings(self.project, [], output_intervals="5MIN")

    def test_a_missing_plan_in_the_spec_writes_nothing(self):
        before = self.raw_bytes("p02")
        with self.assertRaises(PlanFileNotFound):
            # p03 is listed in the .prj but its file was deleted in the GUI
            set_plan_settings(self.project, "2-4", output_intervals="5MIN")
        self.assertEqual(self.raw_bytes("p02"), before)

    def test_an_unlisted_plan_on_disk_is_refused_as_an_orphan(self):
        shutil.copyfile(os.path.join(self.folder, "Model.p02"),
                        os.path.join(self.folder, "Model.p09"))
        with self.assertRaises(ValueError) as ctx:
            set_plan_settings(self.project, "p09", output_intervals="5MIN")
        self.assertIn("orphan", str(ctx.exception))

    def test_a_plan_mid_run_is_refused_and_nothing_is_written(self):
        before = self.raw_bytes("p02")
        open(os.path.join(self.folder, "Model.p04.tmp.hdf"), "wb").close()
        with self.assertRaises(PlanRunActive):
            set_plan_settings(self.project, "2,4", output_intervals="5MIN")
        self.assertEqual(self.raw_bytes("p02"), before)


@unittest.skipUnless(HAS_STEADY, "Wisconsin Floodway fixture not present")
class TestSteadyPlanSettings(unittest.TestCase):
    """A steady plan carries the interval lines but a blank time window."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, "model")
        shutil.copytree(_STEADY, self.folder)
        self.project = RasProject(
            os.path.join(self.folder, "SterpCreek.prj"))

    def test_blank_window_reads_as_none_without_raising(self):
        s = read_plan_settings(self.project, "p01")
        self.assertEqual(s.window_raw, ",,,")
        self.assertIsNone(s.start)
        self.assertIsNone(s.end)
        self.assertEqual(s.computation_interval, "1HOUR")
        self.assertEqual(s.missing_keys, [])

    def test_filling_in_one_half_of_a_blank_window_is_not_range_checked(self):
        # Nothing to compare against, so it must not be rejected.
        set_plan_settings(self.project, "p01", start="01JAN2025,1000")
        self.assertEqual(read_plan_settings(self.project, "p01").window_raw,
                         "01JAN2025,1000,,")


if __name__ == "__main__":
    unittest.main()
