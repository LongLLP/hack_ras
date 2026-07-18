# tests/test_plan_ops_fixture.py
"""Integration test: the full plan-ops sequence on a real HEC-RAS-authored
model — tests/data/'2D culvert bridge levee precip pipes' (rebuilt by the user
in the RAS 7.0 GUI on 2026-07-17).

Fixture contents (load-bearing for these tests — see dev_rules.md):
- 3 plans on disk: p02 (levee BREACH, WS Elev trigger, writes a restart),
  p04 (writes the restart u04 consumes), p05 (consumes it via u04).
- The .prj deliberately still lists 'Plan File=p03' and 'Unsteady File=u03'
  whose files the user deleted in the GUI workflow — a genuine stale-prj
  state that sync_prj must clean before renumbering into those slots is
  possible.
- Every surviving plan was run, so the real artifacts exist
  (.b##/.bco##/.ic.o##/.p##.hdf/.rst/.rasmap/.x##).

Unlike the synthetic fixtures in test_plan_ops.py (which test the logic), this
verifies our assumptions about what RAS actually writes: artifact naming, rst
naming, prj/rasmap formatting, and a real 'Breach Start=' line with blank
inactive fields. The fixture is copied to a temp dir per test because these
operations mutate the project; the Terrain and Land_Classification rasters are
not needed by any plan op and are skipped for speed.
"""
import os
import shutil
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.plans import (
    PlanIdInUse,
    clone_plan,
    delete_plan,
    renumber_plans,
)
from hack_ras.project.sync import sort_prj_entries, sync_prj

_FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                        "2D culvert bridge levee precip pipes")
HAS_FIXTURE = os.path.isfile(os.path.join(_FIXTURE, "Model.b02"))

P02_RST = "Model.p02.01JAN2025 1400.rst"   # written by the breach plan
P04_RST = "Model.p04.01JAN2025 1600.rst"   # consumed by u04


@unittest.skipUnless(HAS_FIXTURE, "extended model fixture not present")
class TestPlanOpsOnRealModel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self._tmp.name, "model")
        self.addCleanup(self._tmp.cleanup)
        shutil.copytree(
            _FIXTURE, self.folder,
            ignore=shutil.ignore_patterns(
                "Terrain", "Land_Classification", "*.backup"),
        )
        self.project = RasProject(os.path.join(self.folder, "Model.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def read(self, name):
        with open(self.path(name), encoding="latin-1", newline="") as f:
            return f.read()

    def test_full_sequence_on_ras_authored_files(self):
        # fixture sanity: prj lists 4 plans but p03/u03 files are gone
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p02", "p03", "p04", "p05"])
        self.assertFalse(os.path.exists(self.path("Model.p03")))
        self.assertFalse(os.path.exists(self.path("Model.u03")))
        self.assertIn("Breach Start=True,756.8,,,False,,,0",
                      self.read("Model.p02"))
        self.assertIn(f"Restart Filename={P04_RST}", self.read("Model.u04"))

        # --- stale prj blocks renumbering into the p03 slot ---
        with self.assertRaises(PlanIdInUse):
            renumber_plans(self.project, {"p04": "p03"})

        # --- sync_prj cleans the genuine stale state the GUI left behind ---
        report = sync_prj(self.project)
        self.assertEqual(report["plan"], ["p03"])
        self.assertEqual(report["unsteady"], ["u03"])
        self.assertIsNone(report["current_plan"])  # p05 still exists
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p02", "p04", "p05"])

        # --- bulk renumber: chain p02->p01, p04->p02, p05->p03 ---
        report = renumber_plans(self.project, {
            "p02": "p01", "p04": "p02", "p05": "p03"})

        # breach plan family (incl. its rst) followed to p01
        for name in ("Model.p01", "Model.p01.hdf", "Model.b01",
                     "Model.bco01", "Model.ic.o01",
                     "Model.p01.01JAN2025 1400.rst",
                     "Model.p02.01JAN2025 1600.rst"):
            self.assertTrue(os.path.isfile(self.path(name)), name)
        for gone in ("Model.p04", "Model.p05", "Model.b04", "Model.b05",
                     P02_RST, P04_RST):
            self.assertFalse(os.path.exists(self.path(gone)), gone)
        self.assertIn("Breach Start=True,756.8,,,False,,,0",
                      self.read("Model.p01"))
        # x## files are geometry-keyed and must be untouched
        self.assertTrue(os.path.isfile(self.path("Model.x02")))
        self.assertTrue(os.path.isfile(self.path("Model.x03")))
        # u04's restart reference followed p04 -> p02, chain-safely
        self.assertIn("Restart Filename=Model.p02.01JAN2025 1600.rst",
                      self.read("Model.u04"))
        # prj followed (Current Plan was p05)
        self.assertEqual(self.project.model.plan_file_ids,
                         ["p01", "p02", "p03"])
        self.assertIn("Current Plan=p03", self.read("Model.prj"))
        # rasmap tokens remapped; old numbers gone
        self.assertGreater(report["rasmap_tokens"], 0)
        rasmap = self.read("Model.rasmap")
        self.assertNotIn("Model.p04", rasmap)
        self.assertNotIn("Model.p05", rasmap)
        # no temp files left behind
        self.assertEqual(
            [n for n in os.listdir(self.folder) if "renumtmp" in n], [])

        # --- housekeeping passes are clean no-ops now ---
        self.assertEqual(sync_prj(self.project)["plan"], [])
        self.assertEqual(sort_prj_entries(self.project)["plan"],
                         ["p01", "p02", "p03"])

        # --- clone the breach plan: WS Elev trigger -> no warning ---
        with self.assertNoLogs("hack_ras.project.plans", level="WARNING"):
            created = clone_plan(self.project, "p01", "Breach Clone WS",
                                 new_id="p04")
        self.assertEqual(created, "p04")
        self.assertIn("Breach Start=True,756.8,,,False,,,0",
                      self.read("Model.p04"))

        # --- clone with a Set Time trigger outside the window -> warning ---
        with self.assertLogs("hack_ras.project.plans", level="WARNING") as cm:
            clone_plan(
                self.project, "p01", "Breach Clone Set Time",
                line_edits={"Breach Start=":
                            "Breach Start=False,756.8,01JAN2024,0300,False,,,0"},
                new_id="p05",
            )
        self.assertTrue(any("outside the simulation window" in m
                            for m in cm.output))

        # --- delete old p05 (now p03) with unused-flow cleanup ---
        result = delete_plan(self.project, "p03",
                             delete_unused_geom=True, delete_unused_flow=True)
        for gone in ("Model.p03", "Model.p03.hdf", "Model.b03",
                     "Model.u04", "Model.u04.hdf"):
            self.assertFalse(os.path.exists(self.path(gone)), gone)
        self.assertTrue(os.path.isfile(self.path("Model.g03")))  # shared
        text = self.read("Model.prj")
        self.assertNotIn("Unsteady File=u04", text)
        self.assertIn("Geom File=g03", text)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["current_plan"], ("p03", "p01"))

    def test_delete_warns_when_restart_source_is_deleted(self):
        # u04 consumes p04's restart; deleting p04 orphans it.
        # Deliberately NO sync_prj first — the stale u03 prj entry (file
        # missing) must be skipped gracefully by the reference scan.
        result = delete_plan(self.project, "p04")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("u04", result["warnings"][0])
        self.assertFalse(os.path.exists(self.path(P04_RST)))
        self.assertIsNone(result["current_plan"])  # p05 was current? no: p05
        self.assertIn("Current Plan=p05", self.read("Model.prj"))


if __name__ == "__main__":
    unittest.main()
