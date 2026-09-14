# tests/test_flow_ops_fixture.py
"""Integration tests: flow-ops on real HEC-RAS-authored models.

Two fixtures, one per flow kind, because the two kinds differ in exactly the
places a synthetic mock would let us get wrong:

- UNSTEADY — tests/data/'2D culvert bridge levee precip pipes' (built by the
  user in the RAS 7.0 GUI). u02 is shared by plans p02 and p04, u04 by p05, and
  the .prj deliberately still lists 'Unsteady File=u03' whose file the user
  deleted in the GUI — with a matching stale RASEventConditions layer left in
  the .rasmap. That stale pair is load-bearing here: it is what proves
  renumbering into a stale slot is refused until sync_prj runs, on a genuine
  GUI leftover rather than a hand-made one. The .rasmap also has three
  results-nested RASEventConditions layers naming Base.p##.hdf, which the
  Base.u## token keying must never touch.

- STEADY — tests/data/'Wisconsin Floodway' (RAS 5.0.3). f01 is shared by BOTH
  plans, and the model has NO .rasmap and NO .f01.hdf, so it exercises the
  single-file family, the .prj's 'Flow File=' key, and the no-rasmap path.

Both are copied to a temp dir per test because these operations mutate the
project.
"""
import os
import shutil
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.flows import (
    FlowIdInUse,
    FlowInUse,
    clone_flow,
    compact_flows,
    delete_flow,
    renumber_flows,
    reorder_flows,
)
from hack_ras.project.sync import sync_prj
from hack_ras.utils.lines import content_of, read_lines

_UNSTEADY_FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                                 "2D culvert bridge levee precip pipes")
_STEADY_FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                               "Wisconsin Floodway")
HAS_UNSTEADY = os.path.isfile(os.path.join(_UNSTEADY_FIXTURE, "Model.u02"))
HAS_STEADY = os.path.isfile(os.path.join(_STEADY_FIXTURE, "SterpCreek.f01"))


def _plan_flow(folder, plan_file):
    for line in read_lines(os.path.join(folder, plan_file)):
        c = content_of(line)
        if c.startswith("Flow File="):
            return c[len("Flow File="):].strip()
    return None


@unittest.skipUnless(HAS_UNSTEADY, "2D culvert model fixture not present")
class TestUnsteadyFlowOpsOnRealModel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self._tmp.name, "model")
        self.addCleanup(self._tmp.cleanup)
        shutil.copytree(
            _UNSTEADY_FIXTURE, self.folder,
            ignore=shutil.ignore_patterns(
                "Terrain", "Land_Classification", "*.backup"),
        )
        self.project = RasProject(os.path.join(self.folder, "Model.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def rasmap(self):
        with open(self.path("Model.rasmap"), encoding="latin-1") as f:
            return f.read()

    def test_stale_prj_slot_blocks_renumber_until_sync(self):
        # 'Unsteady File=u03' is listed but its file was deleted in the GUI
        self.assertIn("u03", self.project.model.unsteady_file_ids)
        self.assertFalse(os.path.exists(self.path("Model.u03")))
        with self.assertRaises(FlowIdInUse):
            renumber_flows(self.project, {"u04": "u03"})

        report = sync_prj(self.project)
        self.assertIn("u03", report["unsteady"])
        self.assertNotIn("u03", self.project.model.unsteady_file_ids)

        renumber_flows(self.project, {"u04": "u03"})
        self.assertTrue(os.path.isfile(self.path("Model.u03")))
        self.assertTrue(os.path.isfile(self.path("Model.u03.hdf")))
        self.assertEqual(_plan_flow(self.folder, "Model.p05"), "u03")

    def test_swap_on_real_files_repoints_shared_flow_and_rasmap(self):
        # u02 is shared by p02, p04, p06 and p07; u04 belongs to p05
        report = renumber_flows(self.project, {"u02": "u04", "u04": "u02"})
        self.assertEqual(_plan_flow(self.folder, "Model.p02"), "u04")
        self.assertEqual(_plan_flow(self.folder, "Model.p04"), "u04")
        self.assertEqual(_plan_flow(self.folder, "Model.p05"), "u02")
        self.assertEqual(_plan_flow(self.folder, "Model.p06"), "u04")
        self.assertEqual(_plan_flow(self.folder, "Model.p07"), "u04")
        self.assertEqual(len(report["plan_refs"]), 5)
        # both RAS-authored .hdf sidecars followed their flow file
        self.assertEqual(len(report["files"]), 4)
        self.assertEqual([n for n in os.listdir(self.folder)
                          if n.endswith(".renumtmp")], [])

    def test_results_nested_event_conditions_survive_a_renumber(self):
        before = self.rasmap()
        self.assertEqual(before.count("RASEventConditions"), 8)
        renumber_flows(self.project, {"u04": "u09"})
        after = self.rasmap()
        # the results-nested EC layers name Base.p##.hdf and are untouched
        for pid in ("p02", "p04", "p05", "p06", "p07"):
            self.assertEqual(before.count(f"Model.{pid}.hdf"),
                             after.count(f"Model.{pid}.hdf"))
        self.assertIn("Model.u09.hdf", after)
        self.assertNotIn("Model.u04.hdf", after)
        self.assertEqual(after.count("RASEventConditions"), 8)

    def test_delete_shared_flow_refused_then_forced(self):
        with self.assertRaises(FlowInUse) as ctx:
            delete_flow(self.project, "u02")
        self.assertIn("p02", str(ctx.exception))

        report = delete_flow(self.project, "u02", force=True)
        self.assertEqual(sorted(report["referencing_plans"]),
                         ["p02", "p04", "p06", "p07"])
        self.assertEqual(report["rasmap_removed"], ["u02"])
        self.assertFalse(os.path.exists(self.path("Model.u02")))
        self.assertFalse(os.path.exists(self.path("Model.u02.hdf")))
        self.assertNotIn("Model.u02.hdf", self.rasmap())

    def test_clone_real_flow_then_compact_the_stale_gap(self):
        new = clone_flow(self.project, "u02", "Cloned event", new_id="u05")
        self.assertEqual(new, "u05")
        self.assertIn("Flow Title=Cloned event",
                      "".join(read_lines(self.path("Model.u05"))))
        # RAS regenerates the sidecar — the clone must not fabricate one
        self.assertFalse(os.path.exists(self.path("Model.u05.hdf")))

        sync_prj(self.project)                      # drop the stale u03 entry
        mapping = compact_flows(self.project, kinds=("unsteady",))
        self.assertEqual(mapping, {"u02": "u01", "u04": "u02", "u05": "u03"})
        self.assertEqual(_plan_flow(self.folder, "Model.p02"), "u01")
        self.assertEqual(_plan_flow(self.folder, "Model.p05"), "u02")


@unittest.skipUnless(HAS_STEADY, "Wisconsin Floodway fixture not present")
class TestSteadyFlowOpsOnRealModel(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self._tmp.name, "model")
        self.addCleanup(self._tmp.cleanup)
        shutil.copytree(_STEADY_FIXTURE, self.folder)
        self.project = RasProject(os.path.join(self.folder, "SterpCreek.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def prj(self):
        return "".join(read_lines(self.path("SterpCreek.prj")))

    def test_steady_family_is_a_single_file_and_no_rasmap_exists(self):
        self.assertFalse(os.path.exists(self.path("SterpCreek.f01.hdf")))
        self.assertFalse(os.path.exists(self.path("SterpCreek.rasmap")))

        report = renumber_flows(self.project, {"f01": "f02"})
        self.assertEqual(report["files"],
                         [("SterpCreek.f01", "SterpCreek.f02")])
        self.assertEqual(report["rasmap_tokens"], 0)
        # f01 is shared by BOTH plans — both references follow
        self.assertEqual(_plan_flow(self.folder, "SterpCreek.p01"), "f02")
        self.assertEqual(_plan_flow(self.folder, "SterpCreek.p02"), "f02")
        self.assertEqual(len(report["plan_refs"]), 2)
        self.assertIn("Flow File=f02", self.prj())
        self.assertNotIn("Unsteady File=", self.prj())

    def test_reorder_and_compact_round_trip_on_a_steady_model(self):
        renumber_flows(self.project, {"f01": "f04"})
        self.assertEqual(compact_flows(self.project, kinds=("steady",)),
                         {"f04": "f01"})
        self.assertEqual(_plan_flow(self.folder, "SterpCreek.p01"), "f01")
        self.assertEqual(reorder_flows(self.project, ["f01"]), {})

    def test_clone_steady_flow_registers_under_the_flow_file_key(self):
        new = clone_flow(self.project, "f01", "Cloned steady profiles")
        self.assertEqual(new, "f02")
        prj_lines = [content_of(l) for l in read_lines(self.path("SterpCreek.prj"))]
        self.assertEqual([l for l in prj_lines if l.startswith("Flow File=")],
                         ["Flow File=f01", "Flow File=f02"])
        self.assertNotIn("Unsteady File=f02", self.prj())


if __name__ == "__main__":
    unittest.main()
