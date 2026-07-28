# tests/test_health.py
"""Tests for hack_ras.project.health.project_health / format_health."""
import os
import shutil
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.health import project_health, format_health
from hack_ras.project.rasmap import remove_plans_from_rasmap

try:
    import h5py  # noqa: F401
    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

CRLF = "\r\n"


def _write(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _touch(path, data=b"x"):
    with open(path, "wb") as f:
        f.write(data)


class TestHealthyProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name
        _write(self.path("Mini.prj"), [
            "Proj Title=Mini", "Current Plan=p01",
            "Geom File=g01", "Unsteady File=u01", "Plan File=p01",
        ])
        _write(self.path("Mini.p01"),
               ["Plan Title=Only", "Geom File=g01", "Flow File=u01"])
        _write(self.path("Mini.g01"), ["Geom Title=Geom One"])
        _write(self.path("Mini.u01"), ["Flow Title=Flow One"])
        _write(self.path("Mini.rasmap"), [
            "<RASMapper>",
            "  <Geometries>",
            '    <Layer Name="G" Type="RASGeometry" Filename=".\\Mini.g01.hdf" />',
            "  </Geometries>",
            "  <Plans>",
            '    <Layer Name="Only" Type="RASPlan" Filename=".\\Mini.p01" GeometryHDF=".\\Mini.g01.hdf" />',
            "  </Plans>",
            "</RASMapper>",
        ])
        _touch(self.path("Mini.g01.hdf"))
        self.project = RasProject(self.path("Mini.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def test_clean_project_reports_ok(self):
        h = project_health(self.project)
        self.assertTrue(h.ok, msg=h.issues)
        self.assertEqual(h.issues, {})
        self.assertEqual([p.id for p in h.plans], ["p01"])
        self.assertEqual(h.geometries[0].used_by, ["p01"])
        self.assertEqual(h.flows[0].used_by, ["p01"])
        self.assertEqual(h.current_plan, "p01")
        self.assertIn("Health: OK", format_health(h))


class TestUnhealthyProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name
        # p08 listed but file missing (stale); p09 on disk but not listed
        # (orphan); u04 & g03 listed but used by no plan; p01/p02 share a title.
        _write(self.path("Mini.prj"), [
            "Proj Title=Mini", "Current Plan=p01",
            "Geom File=g01", "Geom File=g02", "Geom File=g03",
            "Unsteady File=u01", "Unsteady File=u02", "Unsteady File=u03",
            "Unsteady File=u04",
            "Plan File=p01", "Plan File=p02", "Plan File=p03", "Plan File=p08",
        ])
        _write(self.path("Mini.p01"),
               ["Plan Title=Dup", "Geom File=g01", "Flow File=u01"])
        _write(self.path("Mini.p02"),
               ["Plan Title=Dup", "Geom File=g01", "Flow File=u02"])
        _write(self.path("Mini.p03"),
               ["Plan Title=Charlie", "Geom File=g02", "Flow File=u03"])
        _write(self.path("Mini.p09"),
               ["Plan Title=Orphan", "Geom File=g01", "Flow File=u01"])
        for gid, t in (("g01", "One"), ("g02", "Two"), ("g03", "Three")):
            _write(self.path(f"Mini.{gid}"), [f"Geom Title={t}"])
        for uid, t in (("u01", "F1"), ("u02", "F2"), ("u03", "F3"),
                       ("u04", "F4")):
            _write(self.path(f"Mini.{uid}"), [f"Flow Title={t}"])
        _touch(self.path("Mini.g01.hdf"))
        _touch(self.path("Mini.g02.hdf"))
        _touch(self.path("Mini.g03.hdf"))
        _touch(self.path("Mini.p02.tmp.hdf"))          # active run
        _write(self.path("Mini.rasmap"), [
            "<RASMapper>",
            "  <Geometries>",
            '    <Layer Name="G1" Type="RASGeometry" Filename=".\\Mini.g01.hdf" />',
            '    <Layer Name="G2" Type="RASGeometry" Filename=".\\Mini.g02.hdf" />',
            '    <Layer Name="G3" Type="RASGeometry" Filename=".\\Mini.g03.hdf" />',
            "  </Geometries>",
            "  <Plans>",
            '    <Layer Name="A" Type="RASPlan" Filename=".\\Mini.p01" GeometryHDF=".\\Mini.g01.hdf" />',
            '    <Layer Name="B" Type="RASPlan" Filename=".\\Mini.p02" GeometryHDF=".\\Mini.g01.hdf" />',
            '    <Layer Name="B dup" Type="RASPlan" Filename=".\\Mini.p02" GeometryHDF=".\\Mini.g01.hdf" />',
            '    <Layer Name="C" Type="RASPlan" Filename=".\\Mini.p03" GeometryHDF=".\\Mini.g02.hdf" />',
            "  </Plans>",
            "  <Results>",
            '    <Layer Name="Ghost" Type="RASResults" Filename=".\\Mini.p07.hdf" />',
            "  </Results>",
            "</RASMapper>",
        ])
        self.project = RasProject(self.path("Mini.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def test_flags_every_issue(self):
        h = project_health(self.project)
        self.assertFalse(h.ok)
        self.assertIn("Mini.p09", h.orphan_files)
        self.assertIn("Mini.p08", h.stale_prj_entries)
        self.assertTrue(any("Mini.p02" in d for d in h.rasmap_duplicate_layers))
        self.assertTrue(any("Mini.p07.hdf" in m
                            for m in h.rasmap_missing_file_layers))
        self.assertTrue(any("Dup" in d for d in h.duplicate_titles))
        self.assertIn("g03", h.unused_geometries)
        self.assertIn("u04", h.unused_flows)
        self.assertIn("p02", h.active_runs)
        # format renders an issue summary
        text = format_health(h)
        self.assertIn("issue(s)", text)
        self.assertIn("Orphan files", text)


@unittest.skipUnless(
    HAS_H5PY and os.path.isfile(os.path.join(
        os.path.dirname(__file__), "data",
        "2D culvert bridge levee precip pipes", "Model.p04.hdf")),
    "2D culvert fixture / h5py not available")
class TestHealthOnRealFixture(unittest.TestCase):
    _FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                            "2D culvert bridge levee precip pipes")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, "model")
        shutil.copytree(self._FIXTURE, self.folder,
                        ignore=shutil.ignore_patterns(
                            "Terrain", "Land_Classification", "*.backup"))
        self.project = RasProject(os.path.join(self.folder, "Model.prj"))

    def path(self, name):
        return os.path.join(self.folder, name)

    def test_has_results_and_unlisted_flag(self):
        h = project_health(self.project)
        res = {p.id: p.has_results for p in h.plans}
        self.assertTrue(res["p02"] and res["p04"] and res["p05"])
        self.assertEqual(h.unlisted_results, [])   # all listed in the rasmap
        # drop p04's rasmap layers -> its computed results are now "unlisted"
        remove_plans_from_rasmap(self.path("Model.rasmap"), "Model", ["p04"])
        self.assertEqual(project_health(self.project).unlisted_results, ["p04"])


if __name__ == "__main__":
    unittest.main()
