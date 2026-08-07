# tests/test_steady_flow.py
"""Steady-flow support across project ops.

Two gaps this pins down, both rooted in the package having assumed
flow == unsteady:

1. Plan-keyed run artifacts. hack_ras knew only the unsteady set
   (.b##/.bco##/.ic.o##) and never the steady set (.O##/.r##), so delete
   stranded them and — worse — renumber left them behind at the old number
   where a later plan could inherit stale outputs.

2. The .prj's steady flow key. RAS registers steady flow as 'Flow File=f##'
   and unsteady as 'Unsteady File=u##'; sync.py looked for a 'Steady File='
   key that RAS never writes, so steady entries silently fell through as
   unrecognised lines and the parser did not track them at all.

The real-model tests use tests/data/'Wisconsin Floodway' (SterpCreek), a
RAS 5.0.3/7.0 steady project with two run plans, so .O01/.O02 and .r01/.r02
are genuine RAS output rather than touched-up placeholders. It is copied to a
temp dir per test because these operations mutate the project.
"""
import os
import shutil
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.health import project_health
from hack_ras.project.parser import parse_project_lines
from hack_ras.project.plans import (
    _family_names,
    _renamed_family_name,
    delete_plan,
    delete_plans,
    renumber_plan,
)
from hack_ras.project.sync import sort_prj_entries, sync_prj
from hack_ras.utils.lines import content_of, read_lines

_FIXTURE = os.path.join(os.path.dirname(__file__), "data", "Wisconsin Floodway")
HAS_FIXTURE = os.path.isfile(os.path.join(_FIXTURE, "SterpCreek.O01"))

CRLF = "\r\n"


def _write(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _prj_entries(path, key):
    return [content_of(l)[len(key):].strip() for l in read_lines(path)
            if content_of(l).startswith(key)]


# ---------------------------------------------------------------------------
# Steady run artifacts (.O## / .r##) — real steady model
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_FIXTURE, "Wisconsin Floodway fixture not present")
class TestSteadyRunArtifacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self._tmp.name, "model")
        self.addCleanup(self._tmp.cleanup)
        shutil.copytree(_FIXTURE, self.folder)
        self.project = RasProject(os.path.join(self.folder, "SterpCreek.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def test_family_names_include_steady_artifacts(self):
        fam = _family_names(self.project, "p01")
        self.assertIn("SterpCreek.O01", fam)
        self.assertIn("SterpCreek.r01", fam)
        # No unsteady artifacts exist here, so none are claimed.
        self.assertNotIn("SterpCreek.b01", fam)
        self.assertNotIn("SterpCreek.bco01", fam)

    def test_delete_plan_removes_steady_artifacts(self):
        report = delete_plan(self.project, "p01")
        for name in ("SterpCreek.O01", "SterpCreek.r01"):
            self.assertIn(name, report["deleted"])
            self.assertFalse(os.path.exists(self.path(name)), name)
        # The other plan's artifacts are untouched.
        self.assertTrue(os.path.isfile(self.path("SterpCreek.O02")))
        self.assertTrue(os.path.isfile(self.path("SterpCreek.r02")))

    def test_renumber_plan_carries_steady_artifacts(self):
        renumber_plan(self.project, "p01", "p03")
        for old, new in (("SterpCreek.O01", "SterpCreek.O03"),
                         ("SterpCreek.r01", "SterpCreek.r03")):
            self.assertTrue(os.path.isfile(self.path(new)), new)
            self.assertFalse(os.path.exists(self.path(old)), old)

    def test_renumber_then_reuse_does_not_inherit_stale_output(self):
        """The bug this guards: if .O01/.r01 stayed behind when p01 moved
        away, a plan later renumbered onto p01 would silently adopt them."""
        renumber_plan(self.project, "p01", "p03")
        self.assertFalse(os.path.exists(self.path("SterpCreek.O01")))
        renumber_plan(self.project, "p02", "p01")
        self.assertTrue(os.path.isfile(self.path("SterpCreek.O01")))
        # p01's output is p02's carried across, not the original p01's.
        with open(self.path("SterpCreek.O01"), "rb") as f:
            got = f.read()
        with open(os.path.join(_FIXTURE, "SterpCreek.O02"), "rb") as f:
            self.assertEqual(got, f.read())

    def test_renamed_family_name_handles_steady_stems(self):
        f = _renamed_family_name
        self.assertEqual(f("SterpCreek.O01", "SterpCreek", "p01", "p07"),
                         "SterpCreek.O07")
        self.assertEqual(f("SterpCreek.r01", "SterpCreek", "p01", "p07"),
                         "SterpCreek.r07")

    def test_delete_unused_flow_drops_steady_flow_file_entry(self):
        """Deleting the last plan using f01 removes the file AND its .prj
        entry — which needs the 'Flow File=' key, not 'Unsteady File='."""
        report = delete_plans(self.project, "p01,p02", delete_unused_flow=True)
        self.assertIn("SterpCreek.f01", report["deleted"])
        self.assertFalse(os.path.exists(self.path("SterpCreek.f01")))
        self.assertIn("Flow File=f01", report["prj_removed"])
        self.assertEqual(_prj_entries(self.project.prj_path, "Flow File="), [])

    def test_delete_one_plan_keeps_still_used_flow(self):
        report = delete_plan(self.project, "p01", delete_unused_flow=True)
        self.assertNotIn("SterpCreek.f01", report["deleted"])
        self.assertTrue(os.path.isfile(self.path("SterpCreek.f01")))
        self.assertEqual(_prj_entries(self.project.prj_path, "Flow File="),
                         ["f01"])


# ---------------------------------------------------------------------------
# 'Flow File=' as the .prj steady key — parser / sync / health
# ---------------------------------------------------------------------------

class _MiniProject(unittest.TestCase):
    """Small synthetic project: one plan on a steady flow."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name
        _write(self.path("Mini.prj"), [
            "Proj Title=Mini", "Current Plan=p01",
            "Geom File=g01", "Flow File=f01", "Plan File=p01",
        ])
        _write(self.path("Mini.p01"),
               ["Plan Title=Only", "Geom File=g01", "Flow File=f01"])
        _write(self.path("Mini.g01"), ["Geom Title=Geom One"])
        _write(self.path("Mini.f01"), ["Flow Title=Steady One"])
        self.project = RasProject(self.path("Mini.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)


class TestParserSteady(_MiniProject):
    def test_flow_file_lands_in_steady_file_ids(self):
        m = self.project.model
        self.assertEqual(m.steady_file_ids, ["f01"])
        self.assertEqual(m.unsteady_file_ids, [])

    def test_both_flow_kinds_kept_apart(self):
        model = parse_project_lines([
            "Proj Title=Mixed", "Flow File=f01", "Flow File=f02",
            "Unsteady File=u01",
        ])
        self.assertEqual(model.steady_file_ids, ["f01", "f02"])
        self.assertEqual(model.unsteady_file_ids, ["u01"])

    def test_resolve_filenames_reports_steady(self):
        names = self.project.model.resolve_filenames({"f01": "Mini.f01"})
        self.assertEqual(names["steady"], ["Mini.f01"])


class TestSyncSteady(_MiniProject):
    def test_removes_entry_when_steady_file_missing(self):
        os.remove(self.path("Mini.f01"))
        report = sync_prj(self.project)
        self.assertEqual(report["steady"], ["f01"])
        self.assertEqual(_prj_entries(self.project.prj_path, "Flow File="), [])

    def test_keeps_entry_when_file_present_but_unused(self):
        """sync's test is 'is the file on disk?', NOT 'does a plan use it?'.
        An unused-but-present flow file is legal and must survive."""
        _write(self.path("Mini.p01"),
               ["Plan Title=Only", "Geom File=g01"])   # drops the f01 reference
        report = sync_prj(self.project)
        self.assertEqual(report["steady"], [])
        self.assertEqual(_prj_entries(self.project.prj_path, "Flow File="),
                         ["f01"])
        self.assertTrue(os.path.isfile(self.path("Mini.f01")))

    def test_sorts_steady_entries(self):
        _write(self.path("Mini.prj"), [
            "Proj Title=Mini", "Current Plan=p01", "Geom File=g01",
            "Flow File=f03", "Flow File=f01", "Flow File=f02",
            "Plan File=p01",
        ])
        project = RasProject(self.path("Mini.prj"))
        result = sort_prj_entries(project, kinds=("steady",))
        self.assertEqual(result["steady"], ["f01", "f02", "f03"])
        self.assertEqual(_prj_entries(project.prj_path, "Flow File="),
                         ["f01", "f02", "f03"])


class TestHealthSteady(_MiniProject):
    def test_steady_flow_in_inventory(self):
        h = project_health(self.project)
        self.assertEqual([u.id for u in h.flows], ["f01"])
        self.assertEqual(h.flows[0].title, "Steady One")
        self.assertEqual(h.flows[0].used_by, ["p01"])

    def test_missing_steady_file_is_a_stale_entry(self):
        os.remove(self.path("Mini.f01"))
        h = project_health(self.project)
        self.assertIn("Mini.f01", h.stale_prj_entries)

    def test_unlisted_steady_file_is_an_orphan(self):
        _write(self.path("Mini.f02"), ["Flow Title=Steady Two"])
        h = project_health(self.project)
        self.assertIn("Mini.f02", h.orphan_files)

    def test_unused_steady_flow_reported(self):
        _write(self.path("Mini.p01"),
               ["Plan Title=Only", "Geom File=g01"])
        h = project_health(self.project)
        self.assertIn("f01", h.unused_flows)


if __name__ == "__main__":
    unittest.main()
