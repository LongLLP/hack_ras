# tests/test_retitle.py
"""Tests for retitle_plan / retitle_geom / retitle_flow.

Synthetic mini project (the operations mutate files): 2 plans, 2 geometries,
2 unsteady flows and 1 steady flow, with a .rasmap carrying all four layer
sections. Real .p##.hdf / .g##.hdf files are built with h5py so the attribute
rewrite — which must preserve each attribute's HDF5 string padding — is
exercised for real rather than mocked.

The point of these tests is the thing that made retitle more than a one-line
edit: a title is stored in up to four places (text file, .rasmap display name,
HDF attributes, and for a plan a separate short identifier), and RAS Mapper
regenerates the .rasmap names it can from the HDFs. See hdf_titles.py.
"""
import os
import tempfile
import unittest

import h5py
import numpy as np

from hack_ras import RasProject
from hack_ras.project.flows import (
    DuplicateFlowTitle,
    FlowFileNotFound,
    retitle_flow,
)
from hack_ras.project.geoms import (
    DuplicateGeomTitle,
    GeomFileNotFound,
    retitle_geom,
)
from hack_ras.project.plans import (
    DuplicatePlanTitle,
    PlanFileNotFound,
    retitle_plan,
)

CRLF = "\r\n"


def _write(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(CRLF.join(lines) + CRLF)


def _read(path):
    with open(path, encoding="latin-1", newline="") as f:
        return f.read()


def _set_str_attr(obj, name, value, strpad):
    """Write a fixed-length string attribute with an explicit padding rule,
    the way HEC-RAS writes them (sized exactly to the string)."""
    raw = value.encode("latin-1")
    tid = h5py.h5t.C_S1.copy()
    tid.set_size(len(raw))
    tid.set_cset(h5py.h5t.CSET_ASCII)
    tid.set_strpad(strpad)
    sid = h5py.h5s.create(h5py.h5s.SCALAR)
    aid = h5py.h5a.create(obj.id, name.encode("latin-1"), tid, sid)
    # Explicit mtype, or HDF5 reserves a NUL byte and drops the last character.
    aid.write(np.array(raw, dtype=f"S{len(raw)}"), mtype=tid)


def _get_str_attr(path, group, name):
    with h5py.File(path, "r") as h:
        v = h[group].attrs[name]
    return v.tobytes().decode("latin-1") if hasattr(v, "tobytes") else str(v)


def _strpad_of(path, group, name):
    with h5py.File(path, "r") as h:
        aid = h5py.h5a.open(h[group].id, name.encode("latin-1"))
        return aid.get_type().get_strpad()


_RASMAP = [
    "<RASMapper>",
    "  <Geometries>",
    '    <Layer Name="Geom One" Type="RASGeometry" Filename=".\\Mini.g01.hdf" />',
    '    <Layer Name="Geom Two" Type="RASGeometry" Filename=".\\Mini.g02.hdf" />',
    "  </Geometries>",
    "  <Plans>",
    '    <Layer Name="Alpha" Type="RASPlan" Filename=".\\Mini.p01" GeometryHDF=".\\Mini.g01.hdf" />',
    '    <Layer Name="Bravo" Type="RASPlan" Filename=".\\Mini.p02" GeometryHDF=".\\Mini.g02.hdf" />',
    "  </Plans>",
    "  <EventConditions>",
    '    <Layer Name="Flow One" Type="RASEventConditions" Filename=".\\Mini.u01.hdf" />',
    '    <Layer Name="Flow Two" Type="RASEventConditions" Filename=".\\Mini.u02.hdf" />',
    "  </EventConditions>",
    "  <Results>",
    # Attribute order varies in real files (Checked/Expanded land between Type
    # and Filename), so the match must not depend on it.
    '    <Layer Name="Alpha SID" Type="RASResults" Checked="True" Expanded="True" Filename=".\\Mini.p01.hdf">',
    # Sub-layers inside a Results block name the PLAN hdf even though they are
    # typed RASEventConditions / RASGeometry / RASPlan — the retitle must never
    # match these. The RASPlan one is the trap: it shares its type with the
    # <Plans> layer and is only distinguished by the .hdf suffix. Observed live
    # (Hillside 2026-08-26); RAS Mapper names these generically.
    '      <Layer Name="Event Conditions" Type="RASEventConditions" Filename=".\\Mini.p01.hdf" />',
    '      <Layer Type="RASGeometry" Checked="True" Filename=".\\Mini.p01.hdf" />',
    '      <Layer Name="Plan" Type="RASPlan" Filename=".\\Mini.p01.hdf" GeometryHDF=".\\Mini.p01.hdf" />',
    "    </Layer>",
    "  </Results>",
    "</RASMapper>",
]

_PLAN_TITLES = {"p01": "Alpha", "p02": "Bravo"}
_GEOM_TITLES = {"g01": "Geom One", "g02": "Geom Two"}
_FLOW_TITLES = {"u01": "Flow One", "u02": "Flow Two", "f01": "Steady One"}


class RetitleBase(unittest.TestCase):
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
            "Unsteady File=u01",
            "Unsteady File=u02",
            "Flow File=f01",
            "Plan File=p01",
            "Plan File=p02",
        ])
        for pid, title in _PLAN_TITLES.items():
            _write(self.path(f"Mini.{pid}"), [
                f"Plan Title={title}",
                # Padded to a fixed width, the way HEC-RAS writes it.
                f"Short Identifier={(title + ' SID').ljust(40)}",
                "Geom File=g01",
                "Flow File=u01",
            ])
        for gid, title in _GEOM_TITLES.items():
            _write(self.path(f"Mini.{gid}"),
                   [f"Geom Title={title}", "Program Version=7.00"])
        for fid, title in _FLOW_TITLES.items():
            _write(self.path(f"Mini.{fid}"),
                   [f"Flow Title={title}", "Program Version=7.00"])

        # p01 has results (Results/Unsteady attrs); p02 was never computed.
        with h5py.File(self.path("Mini.p01.hdf"), "w") as h:
            info = h.create_group("Plan Data/Plan Information")
            _set_str_attr(info, "Plan Name", "Alpha", h5py.h5t.STR_NULLTERM)
            _set_str_attr(info, "Plan Title", "Alpha", h5py.h5t.STR_NULLTERM)
            _set_str_attr(info, "Plan ShortID", "Alpha SID",
                          h5py.h5t.STR_NULLTERM)
            uns = h.create_group("Results/Unsteady")
            _set_str_attr(uns, "Plan Title", "Alpha", h5py.h5t.STR_SPACEPAD)
            _set_str_attr(uns, "Short ID", "Alpha SID", h5py.h5t.STR_SPACEPAD)
        with h5py.File(self.path("Mini.p02.hdf"), "w") as h:
            info = h.create_group("Plan Data/Plan Information")
            # No 'Plan Title' — mirrors a RAS 5.0.3 plan HDF.
            _set_str_attr(info, "Plan Name", "Bravo", h5py.h5t.STR_NULLTERM)
            _set_str_attr(info, "Plan ShortID", "Bravo SID",
                          h5py.h5t.STR_NULLTERM)
        for gid, title in _GEOM_TITLES.items():
            with h5py.File(self.path(f"Mini.{gid}.hdf"), "w") as h:
                g = h.create_group("Geometry")
                _set_str_attr(g, "Title", title, h5py.h5t.STR_NULLTERM)
        for fid in ("u01", "u02"):
            # A real .u##.hdf holds no title attribute at all.
            with h5py.File(self.path(f"Mini.{fid}.hdf"), "w") as h:
                h.create_group("Event Conditions")

        _write(self.path("Mini.rasmap"), _RASMAP)
        self.project = RasProject(self.prj_path)

    def path(self, name):
        return os.path.join(self.folder, name)

    def rasmap(self):
        return _read(self.path("Mini.rasmap"))


class TestRetitlePlan(RetitleBase):
    def test_short_id_defaults_to_title(self):
        report = retitle_plan(self.project, "p01", "Charlie")
        self.assertEqual(report["short_id"], "Charlie")
        self.assertEqual(report["old_title"], "Alpha")
        text = _read(self.path("Mini.p01"))
        self.assertIn(f"Plan Title=Charlie{CRLF}", text)
        self.assertIn(f"Short Identifier={'Charlie'.ljust(40)}{CRLF}", text)

    def test_short_id_can_differ_from_title(self):
        retitle_plan(self.project, "p01", "Charlie", short_id="CHR")
        text = _read(self.path("Mini.p01"))
        self.assertIn(f"Plan Title=Charlie{CRLF}", text)
        self.assertIn(f"Short Identifier={'CHR'.ljust(40)}{CRLF}", text)
        # ...and the two land in the right HDF attributes.
        p = self.path("Mini.p01.hdf")
        self.assertEqual(
            _get_str_attr(p, "Plan Data/Plan Information", "Plan Title"),
            "Charlie")
        self.assertEqual(
            _get_str_attr(p, "Plan Data/Plan Information", "Plan Name"),
            "Charlie")
        self.assertEqual(
            _get_str_attr(p, "Plan Data/Plan Information", "Plan ShortID"),
            "CHR")
        self.assertEqual(_get_str_attr(p, "Results/Unsteady", "Plan Title"),
                         "Charlie")
        self.assertEqual(_get_str_attr(p, "Results/Unsteady", "Short ID"),
                         "CHR")

    def test_short_identifier_field_width_preserved(self):
        retitle_plan(self.project, "p01", "A much longer plan title here")
        for line in _read(self.path("Mini.p01")).splitlines():
            if line.startswith("Short Identifier="):
                self.assertEqual(len(line) - len("Short Identifier="), 40)
                break
        else:
            self.fail("Short Identifier line missing")

    def test_rasmap_plans_gets_title_results_gets_short_id(self):
        report = retitle_plan(self.project, "p01", "Charlie", short_id="CHR")
        text = self.rasmap()
        self.assertIn('<Layer Name="Charlie" Type="RASPlan"', text)
        self.assertIn('<Layer Name="CHR" Type="RASResults"', text)
        self.assertEqual(sorted(report["rasmap_renamed"]),
                         ["Plans/p01", "Results/p01"])
        # The other plan's layer is untouched.
        self.assertIn('<Layer Name="Bravo" Type="RASPlan"', text)

    def test_nested_results_plan_sublayer_never_matched(self):
        # A RASPlan sub-layer lives INSIDE the <Results> block naming
        # Mini.p01.hdf. It shares its type with the <Plans> layer, so only the
        # extension-less key keeps it out. It must keep its generic name.
        retitle_plan(self.project, "p01", "Charlie", short_id="CHR")
        self.assertIn('<Layer Name="Plan" Type="RASPlan" '
                      'Filename=".\\Mini.p01.hdf"', self.rasmap())
        # ...and exactly two layers were renamed, not three.
        self.assertEqual(self.rasmap().count('Name="Charlie"'), 1)
        self.assertEqual(self.rasmap().count('Name="CHR"'), 1)

    def test_plan_without_results_has_no_results_layer(self):
        report = retitle_plan(self.project, "p02", "Delta")
        self.assertEqual(report["rasmap_renamed"], ["Plans/p02"])

    def test_missing_hdf_attribute_is_skipped_not_an_error(self):
        # p02.hdf has no 'Plan Title' (5.0.3 shape) and no Results group.
        report = retitle_plan(self.project, "p02", "Delta", short_id="DLT")
        self.assertEqual(report["hdf_attrs"], [
            "Plan Data/Plan Information/Plan Name",
            "Plan Data/Plan Information/Plan ShortID",
        ])
        p = self.path("Mini.p02.hdf")
        self.assertEqual(
            _get_str_attr(p, "Plan Data/Plan Information", "Plan Name"),
            "Delta")
        self.assertEqual(
            _get_str_attr(p, "Plan Data/Plan Information", "Plan ShortID"),
            "DLT")

    def test_hdf_string_padding_preserved(self):
        # Plan Information is NULLTERM, Results/Unsteady is SPACEPAD; a rewrite
        # must not normalize them to one convention.
        retitle_plan(self.project, "p01", "Charlie")
        p = self.path("Mini.p01.hdf")
        self.assertEqual(
            _strpad_of(p, "Plan Data/Plan Information", "Plan Title"),
            h5py.h5t.STR_NULLTERM)
        self.assertEqual(_strpad_of(p, "Results/Unsteady", "Plan Title"),
                         h5py.h5t.STR_SPACEPAD)

    def test_hdf_attribute_resized_to_new_string(self):
        retitle_plan(self.project, "p01", "A considerably longer title")
        with h5py.File(self.path("Mini.p01.hdf"), "r") as h:
            dt = h["Plan Data/Plan Information"].attrs["Plan Title"].dtype
        self.assertEqual(dt.itemsize, len("A considerably longer title"))

    def test_hdf_string_not_truncated(self):
        # Regression: h5py's inferred memory type reserves a byte for a NUL
        # terminator, so a NULLTERM attribute sized exactly to the string loses
        # its last character ('Alpha' -> 'Alph') unless mtype is passed
        # explicitly. Assert the full string survives in every attribute.
        title = "Exactly This Title"
        retitle_plan(self.project, "p01", title, short_id="ExactShortID")
        p = self.path("Mini.p01.hdf")
        for group, attr, expect in (
            ("Plan Data/Plan Information", "Plan Name", title),
            ("Plan Data/Plan Information", "Plan Title", title),
            ("Plan Data/Plan Information", "Plan ShortID", "ExactShortID"),
            ("Results/Unsteady", "Plan Title", title),
            ("Results/Unsteady", "Short ID", "ExactShortID"),
        ):
            self.assertEqual(_get_str_attr(p, group, attr), expect,
                             f"{group}/{attr}")

    def test_update_hdf_false_leaves_hdf_alone(self):
        report = retitle_plan(self.project, "p01", "Charlie", update_hdf=False)
        self.assertEqual(report["hdf_attrs"], [])
        self.assertEqual(
            _get_str_attr(self.path("Mini.p01.hdf"),
                          "Plan Data/Plan Information", "Plan Title"),
            "Alpha")

    def test_clean_rasmap_false_leaves_rasmap_alone(self):
        retitle_plan(self.project, "p01", "Charlie", clean_rasmap=False)
        self.assertIn('<Layer Name="Alpha" Type="RASPlan"', self.rasmap())

    def test_duplicate_title_refused(self):
        with self.assertRaises(DuplicatePlanTitle):
            retitle_plan(self.project, "p01", "Bravo")
        # nothing written
        self.assertIn("Plan Title=Alpha", _read(self.path("Mini.p01")))

    def test_retitle_to_its_own_title_allowed(self):
        # The plan's own title must not count as a collision with itself.
        retitle_plan(self.project, "p01", "Alpha", short_id="NEWSID")
        self.assertIn(f"Short Identifier={'NEWSID'.ljust(40)}{CRLF}",
                      _read(self.path("Mini.p01")))

    def test_missing_plan(self):
        with self.assertRaises(PlanFileNotFound):
            retitle_plan(self.project, "p09", "Nope")

    def test_orphan_plan_refused(self):
        _write(self.path("Mini.p07"), ["Plan Title=Orphan"])
        with self.assertRaises(ValueError):
            retitle_plan(self.project, "p07", "Nope")

    def test_xml_special_characters_escaped_in_rasmap(self):
        retitle_plan(self.project, "p01", 'Q&A <test> "x"')
        text = self.rasmap()
        self.assertIn('Name="Q&amp;A &lt;test&gt; &quot;x&quot;"', text)
        # The text file keeps the raw string — it is not XML.
        self.assertIn('Plan Title=Q&A <test> "x"', _read(self.path("Mini.p01")))

    def test_number_and_other_lines_untouched(self):
        before = _read(self.path("Mini.p01")).splitlines()
        retitle_plan(self.project, "p01", "Charlie")
        after = _read(self.path("Mini.p01")).splitlines()
        self.assertTrue(os.path.isfile(self.path("Mini.p01")))
        self.assertEqual(before[2:], after[2:])   # Geom File=, Flow File=


class TestRetitleGeom(RetitleBase):
    def test_updates_text_rasmap_and_hdf(self):
        report = retitle_geom(self.project, "g01", "Renamed Geom")
        self.assertIn(f"Geom Title=Renamed Geom{CRLF}",
                      _read(self.path("Mini.g01")))
        self.assertIn('<Layer Name="Renamed Geom" Type="RASGeometry"',
                      self.rasmap())
        self.assertEqual(report["rasmap_renamed"], ["Geometries/g01"])
        self.assertEqual(report["hdf_attrs"], ["Geometry/Title"])
        self.assertEqual(
            _get_str_attr(self.path("Mini.g01.hdf"), "Geometry", "Title"),
            "Renamed Geom")

    def test_results_sublayers_never_matched(self):
        # The RASGeometry sub-layer inside <Results> names Mini.p01.hdf and has
        # no Name at all — renaming a geometry must not touch it.
        retitle_geom(self.project, "g01", "Renamed Geom")
        self.assertIn(
            '<Layer Type="RASGeometry" Checked="True" '
            'Filename=".\\Mini.p01.hdf" />', self.rasmap())

    def test_duplicate_title_refused(self):
        with self.assertRaises(DuplicateGeomTitle):
            retitle_geom(self.project, "g01", "Geom Two")

    def test_missing_geom(self):
        with self.assertRaises(GeomFileNotFound):
            retitle_geom(self.project, "g09", "Nope")

    def test_orphan_geom_refused(self):
        _write(self.path("Mini.g07"), ["Geom Title=Orphan"])
        with self.assertRaises(ValueError):
            retitle_geom(self.project, "g07", "Nope")


class TestRetitleFlow(RetitleBase):
    def test_unsteady_updates_text_and_rasmap(self):
        report = retitle_flow(self.project, "u01", "Renamed Flow")
        self.assertIn(f"Flow Title=Renamed Flow{CRLF}",
                      _read(self.path("Mini.u01")))
        self.assertIn('<Layer Name="Renamed Flow" Type="RASEventConditions"',
                      self.rasmap())
        self.assertEqual(report["rasmap_renamed"], ["EventConditions/u01"])

    def test_results_sublayer_never_matched(self):
        # The RASEventConditions sub-layer inside <Results> names Mini.p01.hdf.
        retitle_flow(self.project, "u01", "Renamed Flow")
        self.assertIn('<Layer Name="Event Conditions" '
                      'Type="RASEventConditions"', self.rasmap())

    def test_steady_has_no_rasmap_layer(self):
        report = retitle_flow(self.project, "f01", "Renamed Steady")
        self.assertIn(f"Flow Title=Renamed Steady{CRLF}",
                      _read(self.path("Mini.f01")))
        self.assertEqual(report["rasmap_renamed"], [])

    def test_duplicate_title_refused_within_kind(self):
        with self.assertRaises(DuplicateFlowTitle):
            retitle_flow(self.project, "u01", "Flow Two")

    def test_steady_and_unsteady_titles_do_not_collide(self):
        # Independent namespaces — a steady flow may take an unsteady's title.
        retitle_flow(self.project, "f01", "Flow Two")
        self.assertIn("Flow Title=Flow Two", _read(self.path("Mini.f01")))

    def test_bare_number_refused(self):
        with self.assertRaises(ValueError):
            retitle_flow(self.project, "01", "Nope")

    def test_missing_flow(self):
        with self.assertRaises(FlowFileNotFound):
            retitle_flow(self.project, "u09", "Nope")


if __name__ == "__main__":
    unittest.main()
