# tests/test_geom_ops.py
"""Tests for hack_ras.project.geoms — geometry renumber/delete/clone/compact.

Synthetic mini project (the operations mutate files, so a checked-in fixture
doesn't fit): 3 geometries and 4 plans, where g01 is shared by two plans, g02
by two plans, and g03 is listed but used by NO plan (a realistic leftover, and
the target of the unreferenced-delete test). The .rasmap carries the geometry
in both forms it appears in real files: <Geometries> RASGeometry layers and
GeometryHDF= attributes on the <Plans> layers.
"""
import os
import tempfile
import unittest

from hack_ras import RasProject
from hack_ras.project.geoms import (
    DuplicateGeomTitle,
    GeomFileNotFound,
    GeomIdInUse,
    GeomInUse,
    GeomRunActive,
    clone_geom,
    compact_geoms,
    delete_geom,
    delete_geoms,
    insert_geom_gap,
    renumber_geom,
    renumber_geoms,
)

CRLF = "\r\n"


def _write(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _read(path):
    with open(path, encoding="latin-1", newline="") as f:
        return f.read()


_RASMAP = [
    "<RASMapper>",
    "  <Geometries>",
    '    <Layer Name="Geom One" Type="RASGeometry" Filename=".\\Mini.g01.hdf" />',
    '    <Layer Name="Geom Two" Type="RASGeometry" Filename=".\\Mini.g02.hdf" />',
    '    <Layer Name="Geom Three" Type="RASGeometry" Filename=".\\Mini.g03.hdf" />',
    "  </Geometries>",
    "  <Plans>",
    '    <Layer Name="Alpha" Type="RASPlan" Filename=".\\Mini.p01" GeometryHDF=".\\Mini.g01.hdf" />',
    '    <Layer Name="Bravo" Type="RASPlan" Filename=".\\Mini.p02" GeometryHDF=".\\Mini.g01.hdf" />',
    '    <Layer Name="Charlie" Type="RASPlan" Filename=".\\Mini.p03" GeometryHDF=".\\Mini.g02.hdf" />',
    '    <Layer Name="Delta" Type="RASPlan" Filename=".\\Mini.p04" GeometryHDF=".\\Mini.g02.hdf" />',
    "  </Plans>",
    "</RASMapper>",
]

_TITLES = {"g01": "Geom One", "g02": "Geom Two", "g03": "Geom Three"}
_PLAN_GEOM = {"p01": "g01", "p02": "g01", "p03": "g02", "p04": "g02"}


class GeomProjectBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.prj_path = self.path("Mini.prj")
        _write(self.prj_path, [
            "Proj Title=Mini",
            "Current Plan=p01",
            "Geom File=g01",
            "Geom File=g02",
            "Geom File=g03",
            "Plan File=p01",
            "Plan File=p02",
            "Plan File=p03",
            "Plan File=p04",
        ])
        for gid, title in _TITLES.items():
            _write(self.path(f"Mini.{gid}"),
                   [f"Geom Title={title}", "Program Version=7.00"])
        for pid, gid in _PLAN_GEOM.items():
            _write(self.path(f"Mini.{pid}"),
                   [f"Plan Title={pid}", f"Geom File={gid}", "Flow File=u01"])
        for name in ("Mini.g01.hdf", "Mini.g02.hdf", "Mini.g03.hdf",
                     "Mini.x01", "Mini.x02", "Mini.x03"):
            with open(self.path(name), "wb") as f:
                f.write(b"x")
        _write(self.path("Mini.rasmap"), _RASMAP)
        self.project = RasProject(self.prj_path)

    def path(self, name):
        return os.path.join(self.folder, name)

    def geom_of(self, pid):
        for line in _read(self.path(f"Mini.{pid}")).splitlines():
            if line.startswith("Geom File="):
                return line[len("Geom File="):].strip()
        return None


class TestRenumberGeoms(GeomProjectBase):
    def test_bulk_chain_rewrites_all_references(self):
        # chain: g02 -> g05 while g03 -> g02 (g02's slot freed in-flight)
        report = renumber_geoms(self.project, {"g02": "g05", "g03": "g02"})

        # files: family renamed
        for name in ("Mini.g05", "Mini.g05.hdf", "Mini.x05",
                     "Mini.g02", "Mini.g02.hdf", "Mini.x02"):
            self.assertTrue(os.path.isfile(self.path(name)), name)
        self.assertFalse(os.path.exists(self.path("Mini.g03")))
        # g05 carries the old g02 title; g02 now carries old g03's
        self.assertIn("Geom Title=Geom Two", _read(self.path("Mini.g05")))
        self.assertIn("Geom Title=Geom Three", _read(self.path("Mini.g02")))
        # .prj entries replaced in place
        self.assertEqual(self.project.model.geom_file_ids, ["g01", "g05", "g02"])
        # EVERY plan that used g02 followed it to g05 (the cross-reference)
        self.assertEqual(self.geom_of("p03"), "g05")
        self.assertEqual(self.geom_of("p04"), "g05")
        self.assertEqual(self.geom_of("p01"), "g01")   # untouched
        self.assertEqual(len(report["plan_refs"]), 2)
        # rasmap: g02->g05 (Geometries + 2 plan GeometryHDF = 3),
        #         g03->g02 (Geometries only = 1) -> 4 tokens
        rm = _read(self.path("Mini.rasmap"))
        self.assertEqual(report["rasmap_tokens"], 4)
        self.assertIn('Filename=".\\Mini.g05.hdf"', rm)
        self.assertIn('GeometryHDF=".\\Mini.g05.hdf"', rm)
        self.assertNotIn("Mini.g03.hdf", rm)
        self.assertEqual([n for n in os.listdir(self.folder)
                          if "renumtmp" in n], [])

    def test_single(self):
        renumber_geom(self.project, "g02", "g07")
        self.assertTrue(os.path.isfile(self.path("Mini.g07")))
        self.assertEqual(self.geom_of("p03"), "g07")
        self.assertEqual(self.geom_of("p04"), "g07")

    def test_swap_cycle_via_temp(self):
        renumber_geoms(self.project, {"g01": "g02", "g02": "g01"})
        # contents swapped
        self.assertIn("Geom Title=Geom Two", _read(self.path("Mini.g01")))
        self.assertIn("Geom Title=Geom One", _read(self.path("Mini.g02")))
        # plan refs swapped
        self.assertEqual(self.geom_of("p01"), "g02")
        self.assertEqual(self.geom_of("p03"), "g01")
        self.assertEqual([n for n in os.listdir(self.folder)
                          if "renumtmp" in n], [])

    def test_target_in_use_raises(self):
        with self.assertRaises(GeomIdInUse):
            renumber_geom(self.project, "g01", "g02")

    def test_orphan_raises(self):
        _write(self.path("Mini.g09"), ["Geom Title=Orphan"])
        with self.assertRaises(ValueError):
            renumber_geom(self.project, "g09", "g08")

    def test_active_run_refuses(self):
        # p03 uses g02; a .p03.tmp.hdf means it is mid-run
        with open(self.path("Mini.p03.tmp.hdf"), "wb") as f:
            f.write(b"running")
        with self.assertRaises(GeomRunActive):
            renumber_geom(self.project, "g02", "g05")


class TestInsertGapAndCompact(GeomProjectBase):
    def test_insert_gap(self):
        mapping = insert_geom_gap(self.project, "g02", 1)
        self.assertEqual(mapping, {"g02": "g03", "g03": "g04"})
        self.assertEqual(self.geom_of("p03"), "g03")
        self.assertTrue(os.path.isfile(self.path("Mini.g04")))

    def test_compact_fills_gap(self):
        renumber_geom(self.project, "g03", "g09")   # geoms: g01, g02, g09
        mapping = compact_geoms(self.project)
        self.assertEqual(mapping, {"g09": "g03"})
        self.assertEqual(self.project.model.geom_file_ids,
                         ["g01", "g02", "g03"])

    def test_compact_noop_when_contiguous(self):
        self.assertEqual(compact_geoms(self.project), {})


class TestCloneGeom(GeomProjectBase):
    def test_clone_with_explicit_id(self):
        created = clone_geom(self.project, "g01", "Cloned Geom", new_id="g07")
        self.assertEqual(created, "g07")
        self.assertIn("Geom Title=Cloned Geom", _read(self.path("Mini.g07")))
        self.assertIn("Geom File=g07", _read(self.prj_path))

    def test_clone_default_id_is_next_free(self):
        self.assertEqual(clone_geom(self.project, "g01", "Next"), "g04")

    def test_clone_duplicate_title_raises(self):
        with self.assertRaises(DuplicateGeomTitle):
            clone_geom(self.project, "g01", "Geom Two")


class TestDeleteGeom(GeomProjectBase):
    def test_delete_unreferenced(self):
        # g03 is listed but used by no plan
        report = delete_geom(self.project, "g03")
        for name in ("Mini.g03", "Mini.g03.hdf", "Mini.x03"):
            self.assertFalse(os.path.exists(self.path(name)), name)
        self.assertEqual(report["referencing_plans"], [])
        self.assertEqual(report["rasmap_removed"], ["g03"])
        self.assertNotIn("Geom File=g03", _read(self.prj_path))
        self.assertNotIn("Mini.g03.hdf", _read(self.path("Mini.rasmap")))

    def test_delete_refuses_when_referenced(self):
        with self.assertRaises(GeomInUse):
            delete_geom(self.project, "g02")
        self.assertTrue(os.path.isfile(self.path("Mini.g02")))   # untouched

    def test_delete_force_deletes_and_warns(self):
        report = delete_geom(self.project, "g02", force=True)
        self.assertFalse(os.path.exists(self.path("Mini.g02")))
        self.assertEqual(report["referencing_plans"], ["p03", "p04"])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("missing geometry", report["warnings"][0])
        self.assertEqual(report["rasmap_removed"], ["g02"])
        rm = _read(self.path("Mini.rasmap"))
        # the <Geometries> layer for g02 is gone...
        self.assertNotIn('Name="Geom Two" Type="RASGeometry"', rm)
        # ...but p03/p04's plan layers still (brokenly) reference it via
        # GeometryHDF -- the documented consequence of a forced delete.
        self.assertEqual(rm.count('GeometryHDF=".\\Mini.g02.hdf"'), 2)

    def test_delete_can_leave_rasmap_alone(self):
        report = delete_geom(self.project, "g03", clean_rasmap=False)
        self.assertEqual(report["rasmap_removed"], [])
        self.assertIn("Mini.g03.hdf", _read(self.path("Mini.rasmap")))

    def test_delete_orphan_raises(self):
        _write(self.path("Mini.g09"), ["Geom Title=Orphan"])
        with self.assertRaises(ValueError):
            delete_geom(self.project, "g09")

    def test_delete_active_run_refuses(self):
        with open(self.path("Mini.p03.tmp.hdf"), "wb") as f:
            f.write(b"running")
        with self.assertRaises(GeomRunActive):
            delete_geom(self.project, "g02", force=True)

    def test_delete_missing_raises(self):
        with self.assertRaises(GeomFileNotFound):
            delete_geom(self.project, "g08")


class TestDeleteGeomsBulk(GeomProjectBase):
    def test_bulk_delete_unreferenced_by_spec(self):
        # g03 is unused; delete via a spec string
        report = delete_geoms(self.project, "g03")
        self.assertEqual(report["deleted_geoms"], ["g03"])
        self.assertEqual(self.project.model.geom_file_ids, ["g01", "g02"])
        self.assertEqual(report["rasmap_removed"], ["g03"])

    def test_bulk_refuses_if_any_referenced_and_nothing_deleted(self):
        with self.assertRaises(GeomInUse):
            delete_geoms(self.project, "g02,g03")   # g02 is referenced
        # fail-fast: neither was deleted
        self.assertTrue(os.path.isfile(self.path("Mini.g02")))
        self.assertTrue(os.path.isfile(self.path("Mini.g03")))

    def test_bulk_force_deletes_referenced(self):
        report = delete_geoms(self.project, "02-03", force=True)
        self.assertEqual(report["deleted_geoms"], ["g02", "g03"])
        self.assertEqual(report["referencing_plans"], {"g02": ["p03", "p04"]})
        self.assertTrue(report["warnings"])
        self.assertEqual(self.project.model.geom_file_ids, ["g01"])

    def test_bulk_fail_fast_on_missing(self):
        with self.assertRaises(GeomFileNotFound):
            delete_geoms(self.project, "03,09")   # g09 absent
        self.assertTrue(os.path.isfile(self.path("Mini.g03")))


if __name__ == "__main__":
    unittest.main()
