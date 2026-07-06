# tests/test_plan_ops.py
"""Tests for hack_ras/project/plans.py — renumber, gap insert, clone.

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
    clone_plan,
    insert_plan_gap,
    renumber_plan,
)

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


if __name__ == "__main__":
    unittest.main()
