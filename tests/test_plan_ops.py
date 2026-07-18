# tests/test_plan_ops.py
"""Tests for hack_ras/project/plans.py — renumber (single/bulk), gap insert,
clone, delete — and project/sync.py.

Fixture files are synthesized in tmp_path (trivially small key=value content,
CRLF-terminated like real HEC-RAS files) because these operations mutate the
project in place.
"""
import os
import unittest
import tempfile

from hack_ras import RasProject
from hack_ras.project.plans import (
    DuplicatePlanTitle,
    PlanFileNotFound,
    PlanIdInUse,
    PlanRunActive,
    _warn_breach_triggers,
    clone_plan,
    delete_plan,
    insert_plan_gap,
    renumber_plan,
    renumber_plans,
)
from hack_ras.project.sync import sort_prj_entries, sync_prj

CRLF = "\r\n"


def _write(path, lines):
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _read(path):
    with open(path, "r", encoding="ascii", newline="") as f:
        return f.read()


def _plan_lines(title, breach_time):
    return [
        f"Plan Title={title}",
        f"Short Identifier={title.ljust(24)}",
        "Geom File=g01",
        "Flow File=u01",
        "Breach Loc=                ,                ,        ,True,L4 Test",
        f"Breach Start=False,,01JAN2025,{breach_time},False,,,0",
        "Run HTab=-1 ",
    ]


class PlanOpsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.prj_path = os.path.join(self.folder, "Mini.prj")
        _write(self.prj_path, [
            "Proj Title=Mini",
            "Current Plan=p03",
            "Geom File=g01",
            "Unsteady File=u01",
            "Plan File=p01",
            "Plan File=p02",
            "Plan File=p03",
            "DSS File=dss",
        ])
        _write(os.path.join(self.folder, "Mini.p01"), _plan_lines("Breach 1212", "1212"))
        _write(os.path.join(self.folder, "Mini.p02"), _plan_lines("Breach 1211", "1211"))
        _write(os.path.join(self.folder, "Mini.p03"), _plan_lines("Breach 1210", "1210"))
        # results sidecar for p02 only
        with open(os.path.join(self.folder, "Mini.p02.hdf"), "wb") as f:
            f.write(b"fake hdf")
        self.project = RasProject(self.prj_path)

    def path(self, name):
        return os.path.join(self.folder, name)


class TestRenumberPlan(PlanOpsBase):
    def test_renames_file_hdf_and_prj_entry(self):
        renumber_plan(self.project, "p02", "p05")
        self.assertFalse(os.path.exists(self.path("Mini.p02")))
        self.assertFalse(os.path.exists(self.path("Mini.p02.hdf")))
        self.assertTrue(os.path.isfile(self.path("Mini.p05")))
        self.assertTrue(os.path.isfile(self.path("Mini.p05.hdf")))
        self.assertEqual(self.project.model.plan_file_ids, ["p01", "p05", "p03"])
        # p02 was not the current plan — Current Plan is untouched
        self.assertIn(f"Current Plan=p03{CRLF}", _read(self.prj_path))

    def test_updates_current_plan_when_it_moves(self):
        renumber_plan(self.project, "p03", "p07")
        self.assertIn(f"Current Plan=p07{CRLF}", _read(self.prj_path))

    def test_target_in_use_raises_and_changes_nothing(self):
        before = _read(self.prj_path)
        with self.assertRaises(PlanIdInUse):
            renumber_plan(self.project, "p01", "p03")
        self.assertEqual(_read(self.prj_path), before)
        self.assertTrue(os.path.isfile(self.path("Mini.p01")))

    def test_orphan_on_disk_raises(self):
        _write(self.path("Mini.p09"), _plan_lines("Orphan", "1200"))
        with self.assertRaises(ValueError):
            renumber_plan(self.project, "p09", "p10")

    def test_missing_file_raises(self):
        with self.assertRaises(PlanFileNotFound):
            renumber_plan(self.project, "p08", "p10")

    def test_untouched_lines_are_byte_identical(self):
        before = _read(self.prj_path).split(CRLF)
        renumber_plan(self.project, "p02", "p05")
        after = _read(self.prj_path).split(CRLF)
        changed = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(changed, [("Plan File=p02", "Plan File=p05")])


class TestInsertPlanGap(PlanOpsBase):
    def test_shifts_plans_at_and_above(self):
        mapping = insert_plan_gap(self.project, "p02", 2)
        self.assertEqual(mapping, {"p02": "p04", "p03": "p05"})
        self.assertEqual(self.project.model.plan_file_ids, ["p01", "p04", "p05"])
        self.assertTrue(os.path.isfile(self.path("Mini.p04.hdf")))
        self.assertIn(f"Current Plan=p05{CRLF}", _read(self.prj_path))
        # gap is genuinely free
        self.assertFalse(os.path.exists(self.path("Mini.p02")))
        self.assertFalse(os.path.exists(self.path("Mini.p03")))

    def test_overflow_raises_before_touching_files(self):
        before = _read(self.prj_path)
        with self.assertRaises(ValueError):
            insert_plan_gap(self.project, "p01", 97)
        self.assertEqual(_read(self.prj_path), before)
        self.assertTrue(os.path.isfile(self.path("Mini.p03")))

    def test_orphan_target_on_disk_raises_before_touching_files(self):
        _write(self.path("Mini.p05"), _plan_lines("Orphan", "1200"))
        before = _read(self.prj_path)
        with self.assertRaises(PlanIdInUse):
            insert_plan_gap(self.project, "p02", 2)
        self.assertEqual(_read(self.prj_path), before)
        self.assertTrue(os.path.isfile(self.path("Mini.p02")))


class TestClonePlan(PlanOpsBase):
    def test_clone_with_breach_edit(self):
        new = clone_plan(
            self.project, "p03", "Breach 1209",
            line_edits={"Breach Start=": "Breach Start=False,,01JAN2025,1209,False,,,0"},
        )
        self.assertEqual(new, "p04")
        text = _read(self.path("Mini.p04"))
        self.assertIn(f"Plan Title=Breach 1209{CRLF}", text)
        self.assertIn(f"Short Identifier={'Breach 1209'.ljust(24)}{CRLF}", text)
        self.assertIn(f"Breach Start=False,,01JAN2025,1209,False,,,0{CRLF}", text)
        self.assertEqual(
            self.project.model.plan_file_ids, ["p01", "p02", "p03", "p04"]
        )
        # everything except title/short-id/breach line matches the source
        src = _read(self.path("Mini.p03")).split(CRLF)
        out = text.split(CRLF)
        changed = [i for i, (a, b) in enumerate(zip(src, out)) if a != b]
        self.assertEqual(changed, [0, 1, 5])

    def test_explicit_id_inserted_in_sorted_prj_position(self):
        renumber_plan(self.project, "p03", "p05")  # leave a hole at p03
        clone_plan(self.project, "p01", "Breach 1209", new_id="p03")
        self.assertEqual(
            self.project.model.plan_file_ids, ["p01", "p02", "p03", "p05"]
        )

    def test_duplicate_title_raises(self):
        with self.assertRaises(DuplicatePlanTitle):
            clone_plan(self.project, "p03", "Breach 1211")
        self.assertFalse(os.path.exists(self.path("Mini.p04")))

    def test_taken_id_raises(self):
        with self.assertRaises(PlanIdInUse):
            clone_plan(self.project, "p03", "Breach 1209", new_id="p01")

    def test_unmatched_line_edit_prefix_raises(self):
        with self.assertRaises(ValueError):
            clone_plan(
                self.project, "p03", "Breach 1209",
                line_edits={"No Such Key=": "No Such Key=1"},
            )
        self.assertFalse(os.path.exists(self.path("Mini.p04")))
        # nothing was appended to the .prj
        self.assertEqual(self.project.model.plan_file_ids, ["p01", "p02", "p03"])


# ---------------------------------------------------------------------------
# Richer fixture: artifacts, restart files/references, rasmap, sim windows
# ---------------------------------------------------------------------------

def _rich_plan_lines(title, geom, flow,
                     sim="01JAN2025,0000,03JAN2025,0000",
                     breach="Breach Start=False,1420,01JAN2025,0300,False,,,0"):
    return [
        f"Plan Title={title}",
        f"Short Identifier={title.ljust(24)}",
        f"Simulation Date={sim}",
        f"Geom File={geom}",
        f"Flow File={flow}",
        breach,
        "Run HTab=-1 ",
    ]


def _u_lines(title, restart=None):
    lines = [f"Flow Title={title}", "Program Version=7.00"]
    if restart:
        lines += ["Use Restart=-1 ", f"Restart Filename={restart}"]
    else:
        lines += ["Use Restart= 0 "]
    return lines


_RASMAP = [
    "<RASMapper>",
    "  <Plans>",
    '    <Layer Name="Alpha" Type="RASPlan" Filename=".\\Mini.p01" '
    'GeometryHDF=".\\Mini.g01.hdf" />',
    '    <Layer Name="Bravo" Type="RASPlan" Filename=".\\Mini.p02" '
    'GeometryHDF=".\\Mini.g02.hdf" />',
    "  </Plans>",
    "  <Results>",
    '    <Layer Name="Bravo" Type="RASResults" Filename=".\\Mini.p02.hdf" />',
    "  </Results>",
    "</RASMapper>",
]


class RichProjectBase(unittest.TestCase):
    """Mini project with two geometries, three flow files, run artifacts for
    p02, a restart file p02 wrote, restart references (one plan-numbered, one
    'banana.rst'), and a rasmap."""

    RST = "Mini.p02.02JAN2025 2400.rst"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.prj_path = os.path.join(self.folder, "Mini.prj")
        _write(self.prj_path, [
            "Proj Title=Mini",
            "Current Plan=p03",
            "Geom File=g01",
            "Geom File=g02",
            "Unsteady File=u01",
            "Unsteady File=u02",
            "Unsteady File=u03",
            "Plan File=p01",
            "Plan File=p02",
            "Plan File=p03",
            "DSS File=dss",
        ])
        _write(self.path("Mini.p01"), _rich_plan_lines("Alpha", "g01", "u01"))
        _write(self.path("Mini.p02"), _rich_plan_lines("Bravo", "g02", "u02"))
        _write(self.path("Mini.p03"), _rich_plan_lines("Charlie", "g01", "u03"))
        _write(self.path("Mini.u01"), _u_lines("Flow A"))
        _write(self.path("Mini.u02"), _u_lines("Flow B", restart=self.RST))
        _write(self.path("Mini.u03"), _u_lines("Flow C", restart="banana.rst"))
        for name in ("Mini.g01", "Mini.g02", "Mini.x01", "Mini.x02",
                     "Mini.u01.hdf", "Mini.u02.hdf", "Mini.u03.hdf",
                     "Mini.g01.hdf", "Mini.g02.hdf",
                     "Mini.p02.hdf", "Mini.b02", "Mini.bco02", "Mini.ic.o02",
                     self.RST):
            with open(self.path(name), "wb") as f:
                f.write(b"x")
        _write(self.path("Mini.rasmap"), _RASMAP)
        self.project = RasProject(self.prj_path)

    def path(self, name):
        return os.path.join(self.folder, name)


class TestRenumberPlansBulk(RichProjectBase):
    def test_chain_renames_artifacts_refs_and_rasmap_once(self):
        # chain: p02 -> p05 while p03 -> p02 (p02's slot is freed in-flight)
        report = renumber_plans(self.project, {"p02": "p05", "p03": "p02"})

        for name in ("Mini.p05", "Mini.p05.hdf", "Mini.b05", "Mini.bco05",
                     "Mini.ic.o05", "Mini.p05.02JAN2025 2400.rst", "Mini.p02"):
            self.assertTrue(os.path.isfile(self.path(name)), name)
        self.assertFalse(os.path.exists(self.path("Mini.p03")))
        self.assertFalse(os.path.exists(self.path("Mini.b02")))
        # titles followed their files
        self.assertIn("Plan Title=Bravo", _read(self.path("Mini.p05")))
        self.assertIn("Plan Title=Charlie", _read(self.path("Mini.p02")))
        # restart reference rewritten exactly once (chain-safe): -> p05
        self.assertIn("Restart Filename=Mini.p05.02JAN2025 2400.rst",
                      _read(self.path("Mini.u02")))
        # arbitrary-named restart untouched
        self.assertIn("Restart Filename=banana.rst",
                      _read(self.path("Mini.u03")))
        # prj: entries replaced in place, Current Plan followed p03 -> p02
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p01", "p05", "p02"])
        self.assertIn(f"Current Plan=p02{CRLF}", _read(self.prj_path))
        # rasmap tokens remapped (2x Filename for p02 + 1x p02.hdf)
        rm = _read(self.path("Mini.rasmap"))
        self.assertIn('Filename=".\\Mini.p05"', rm)
        self.assertIn('Filename=".\\Mini.p05.hdf"', rm)
        self.assertNotIn("Mini.p03", rm)  # never existed in rasmap
        self.assertEqual(report["rasmap_tokens"], 2)
        self.assertEqual(len(report["restart_refs"]), 1)

    def test_swap_cycle_via_temp(self):
        renumber_plans(self.project, {"p01": "p02", "p02": "p01"})
        self.assertIn("Plan Title=Bravo", _read(self.path("Mini.p01")))
        self.assertIn("Plan Title=Alpha", _read(self.path("Mini.p02")))
        # p02's artifacts followed it to p01
        self.assertTrue(os.path.isfile(self.path("Mini.b01")))
        self.assertFalse(os.path.exists(self.path("Mini.b02")))
        self.assertIn("Restart Filename=Mini.p01.02JAN2025 2400.rst",
                      _read(self.path("Mini.u02")))
        # no temp files left behind
        leftovers = [n for n in os.listdir(self.folder) if "renumtmp" in n]
        self.assertEqual(leftovers, [])

    def test_duplicate_target_raises(self):
        with self.assertRaises(ValueError):
            renumber_plans(self.project, {"p01": "p05", "p02": "p05"})

    def test_active_run_refuses_and_changes_nothing(self):
        with open(self.path("Mini.p02.tmp.hdf"), "wb") as f:
            f.write(b"running")
        before = _read(self.prj_path)
        with self.assertRaises(PlanRunActive):
            renumber_plans(self.project, {"p02": "p05"})
        self.assertEqual(_read(self.prj_path), before)
        self.assertTrue(os.path.isfile(self.path("Mini.p02")))

    def test_stray_artifact_collision_detected_before_any_rename(self):
        # a stray b05 exists although plan p05 does not
        with open(self.path("Mini.b05"), "wb") as f:
            f.write(b"stray")
        before = _read(self.prj_path)
        with self.assertRaises(PlanIdInUse):
            renumber_plans(self.project, {"p02": "p05"})
        self.assertEqual(_read(self.prj_path), before)
        self.assertTrue(os.path.isfile(self.path("Mini.p02")))
        self.assertTrue(os.path.isfile(self.path("Mini.b02")))


class TestSyncPrj(RichProjectBase):
    def test_removes_stale_entries_and_fixes_current_plan(self):
        # add stale entries and point Current Plan at a phantom plan
        lines = _read(self.prj_path).split(CRLF)
        lines = [("Current Plan=p09" if l.startswith("Current Plan=") else l)
                 for l in lines]
        idx = lines.index("Plan File=p03")
        lines[idx + 1:idx + 1] = ["Plan File=p09", "Plan File=p10"]
        lines.insert(lines.index("Geom File=g02") + 1, "Geom File=g07")
        lines.insert(lines.index("Unsteady File=u03") + 1, "Unsteady File=u09")
        _write(self.prj_path, [l for l in lines if l])
        self.project = RasProject(self.prj_path)

        report = sync_prj(self.project)
        self.assertEqual(report["plan"], ["p09", "p10"])
        self.assertEqual(report["geom"], ["g07"])
        self.assertEqual(report["unsteady"], ["u09"])
        self.assertEqual(report["current_plan"], ("p09", "p01"))
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p01", "p02", "p03"])
        text = _read(self.prj_path)
        self.assertIn(f"Current Plan=p01{CRLF}", text)
        self.assertNotIn("p09", text)
        self.assertNotIn("g07", text)

    def test_clean_project_is_a_no_op(self):
        before = _read(self.prj_path)
        report = sync_prj(self.project)
        self.assertEqual(_read(self.prj_path), before)
        self.assertEqual(report["plan"], [])
        self.assertIsNone(report["current_plan"])

    def test_unblocks_renumber_into_stale_slot(self):
        lines = _read(self.prj_path).split(CRLF)
        idx = lines.index("Plan File=p03")
        lines.insert(idx + 1, "Plan File=p05")  # stale: no Mini.p05 on disk
        _write(self.prj_path, [l for l in lines if l])
        self.project = RasProject(self.prj_path)

        with self.assertRaises(PlanIdInUse):
            renumber_plan(self.project, "p02", "p05")
        sync_prj(self.project)
        renumber_plan(self.project, "p02", "p05")  # now succeeds
        self.assertTrue(os.path.isfile(self.path("Mini.p05")))


class TestDeletePlan(RichProjectBase):
    def test_deletes_family_and_prj_entry_and_warns_on_restart_ref(self):
        report = delete_plan(self.project, "p02")
        for name in ("Mini.p02", "Mini.p02.hdf", "Mini.b02", "Mini.bco02",
                     "Mini.ic.o02", self.RST):
            self.assertFalse(os.path.exists(self.path(name)), name)
        self.assertEqual(self.project.model.plan_file_ids, ["p01", "p03"])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("u02", report["warnings"][0])
        # flags off: geometry and flow files untouched
        self.assertTrue(os.path.isfile(self.path("Mini.g02")))
        self.assertTrue(os.path.isfile(self.path("Mini.u02")))
        self.assertIsNone(report["current_plan"])

    def test_optional_unused_geom_and_flow_cleanup(self):
        report = delete_plan(self.project, "p02",
                             delete_unused_geom=True, delete_unused_flow=True)
        # g02/u02 were only used by p02 -> gone, with sidecars and x02
        for name in ("Mini.g02", "Mini.g02.hdf", "Mini.x02",
                     "Mini.u02", "Mini.u02.hdf"):
            self.assertFalse(os.path.exists(self.path(name)), name)
        text = _read(self.prj_path)
        self.assertNotIn("Geom File=g02", text)
        self.assertNotIn("Unsteady File=u02", text)
        self.assertIn("Geom File=g01", text)
        self.assertIn("Mini.x02", report["deleted"])

    def test_shared_geom_is_kept(self):
        delete_plan(self.project, "p01",
                    delete_unused_geom=True, delete_unused_flow=True)
        # g01 still used by p03 -> kept; u01 was p01-only -> gone
        self.assertTrue(os.path.isfile(self.path("Mini.g01")))
        self.assertIn("Geom File=g01", _read(self.prj_path))
        self.assertFalse(os.path.exists(self.path("Mini.u01")))

    def test_current_plan_repointed(self):
        report = delete_plan(self.project, "p03")
        self.assertEqual(report["current_plan"], ("p03", "p01"))
        self.assertIn(f"Current Plan=p01{CRLF}", _read(self.prj_path))

    def test_active_run_refuses(self):
        with open(self.path("Mini.p02.tmp.hdf"), "wb") as f:
            f.write(b"running")
        with self.assertRaises(PlanRunActive):
            delete_plan(self.project, "p02")
        self.assertTrue(os.path.isfile(self.path("Mini.p02")))

    def test_orphan_raises(self):
        _write(self.path("Mini.p09"), _rich_plan_lines("Orphan", "g01", "u01"))
        with self.assertRaises(ValueError):
            delete_plan(self.project, "p09")


class TestSortPrjEntries(RichProjectBase):
    def test_sorts_each_kind_within_its_own_lines(self):
        # produce the real-world unsorted shape: renumber into freed slots,
        # and scramble the geom/unsteady entry order by hand
        renumber_plans(self.project, {"p01": "p05", "p02": "p01"})
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p05", "p01", "p03"])
        lines = _read(self.prj_path).split(CRLF)
        g = [i for i, l in enumerate(lines) if l.startswith("Geom File=")]
        lines[g[0]], lines[g[1]] = lines[g[1]], lines[g[0]]
        u = [i for i, l in enumerate(lines) if l.startswith("Unsteady File=")]
        lines[u[0]], lines[u[2]] = lines[u[2]], lines[u[0]]
        _write(self.prj_path, [l for l in lines if l])
        self.project = RasProject(self.prj_path)
        before = _read(self.prj_path).split(CRLF)

        result = sort_prj_entries(self.project)
        self.assertEqual(result["plan"], ["p01", "p03", "p05"])
        self.assertEqual(result["geom"], ["g01", "g02"])
        self.assertEqual(result["unsteady"], ["u01", "u02", "u03"])
        self.assertEqual(result["steady"], [])
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p01", "p03", "p05"])
        self.assertEqual(self.project.model.geom_file_ids, ["g01", "g02"])
        after = _read(self.prj_path).split(CRLF)
        changed = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertTrue(changed)
        self.assertTrue(all(
            b.startswith(("Plan File=", "Geom File=", "Unsteady File="))
            for b, _ in changed))

    def test_kinds_selects_groups(self):
        renumber_plans(self.project, {"p01": "p05", "p02": "p01"})
        result = sort_prj_entries(self.project, kinds=("geom",))
        self.assertEqual(result, {"geom": ["g01", "g02"]})
        # plans were not requested -> still unsorted
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p05", "p01", "p03"])

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            sort_prj_entries(self.project, kinds=("flow",))

    def test_sorted_prj_is_a_no_op(self):
        before = _read(self.prj_path)
        result = sort_prj_entries(self.project)
        self.assertEqual(result["plan"], ["p01", "p02", "p03"])
        self.assertEqual(_read(self.prj_path), before)


class TestBreachTriggerWarning(RichProjectBase):
    WINDOW = "02JAN2025,1600,10JAN2025,1200"

    def test_set_time_outside_window_warns_on_clone(self):
        with self.assertLogs("hack_ras.project.plans", level="WARNING") as cm:
            clone_plan(
                self.project, "p02", "Bravo Breach",
                line_edits={"Simulation Date=":
                            f"Simulation Date={self.WINDOW}"},
            )
        self.assertTrue(any("outside the simulation window" in m
                            for m in cm.output))

    def test_set_time_inside_window_is_silent(self):
        lines = _rich_plan_lines("T", "g01", "u01")
        self.assertEqual(_warn_breach_triggers(lines, "x"), [])

    def test_ws_modes_are_not_checked(self):
        for breach in (
            "Breach Start=True,1420,01JAN2024,0300,False,,,0",
            "Breach Start=False,1425,01JAN2024,0300,True,1421,2,-1",
        ):
            lines = _rich_plan_lines("T", "g01", "u01", sim=self.WINDOW,
                                     breach=breach)
            self.assertEqual(_warn_breach_triggers(lines, "x"), [], breach)

    def test_set_time_outside_window_flagged_directly(self):
        lines = _rich_plan_lines("T", "g01", "u01", sim=self.WINDOW)
        warnings = _warn_breach_triggers(lines, "Mini.p08")
        self.assertEqual(len(warnings), 1)
        self.assertIn("Mini.p08", warnings[0])

    def test_malformed_lines_never_raise(self):
        lines = _rich_plan_lines(
            "T", "g01", "u01", sim="garbage",
            breach="Breach Start=nonsense",
        )
        self.assertEqual(_warn_breach_triggers(lines, "x"), [])


if __name__ == "__main__":
    unittest.main()
