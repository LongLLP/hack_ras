"""Tests for hack_ras.project.rasmap.source_data_folders.

The real 2D-culvert fixture .rasmap references Terrain / Land_Classification /
Features via non-results layers but has no RASResultsMap layers pointing into a
subfolder, so a small synthetic .rasmap covers the two behaviours it cannot:
a folder referenced only by RASResultsMap is NOT protected, and a folder
referenced by both a results map and a source layer stays protected.
"""
import os
import tempfile
import unittest

from hack_ras.project.rasmap import (
    remove_flows_from_rasmap,
    remove_geoms_from_rasmap,
    remove_plans_from_rasmap,
    renumber_geoms_in_rasmap,
    result_plan_ids,
    sort_rasmap_layers,
    source_data_folders,
)

_FIXTURE = os.path.join(os.path.dirname(__file__), "data",
                        "2D culvert bridge levee precip pipes", "Model.rasmap")
HAS_FIXTURE = os.path.isfile(_FIXTURE)

# Minimal synthetic .rasmap: three source layers, one no-subfolder geometry
# layer, a results-only folder (10-year), and a results map written INTO the
# terrain folder (the Short-ID collision case).
_SYNTHETIC = "\n".join([
    "<RASMapper>",
    '  <Layer Name="Terrain_Full" Type="TerrainLayer" Filename=".\\Terrain\\Terrain_Full.hdf" />',
    '  <Layer Name="LandCover" Type="LandCoverLayer" Filename=".\\Land Classification\\LandCover.hdf" />',
    '  <Layer Name="Profile Lines" Type="PolylineFeatureLayer" Filename=".\\Features\\Profile Lines.shp" />',
    '  <Layer Name="nval" Type="RASGeometry" Filename=".\\Model.g01.hdf" />',
    '  <Layer Name="WSE" Type="RASResultsMap" Filename=".\\10-year\\WSE (Max).vrt" />',
    '  <Layer Name="WSE" Type="RASResultsMap" Filename=".\\Terrain\\WSE (Max).vrt" />',
    "</RASMapper>",
])


def _write(text):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "Model.rasmap")
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write(text)
    return path


class SourceDataFoldersTest(unittest.TestCase):

    @unittest.skipUnless(HAS_FIXTURE, "2D culvert rasmap fixture not present")
    def test_real_rasmap(self):
        self.assertEqual(source_data_folders(_FIXTURE),
                         {"Terrain", "Land_Classification", "Features"})

    def test_results_only_folder_not_protected(self):
        self.assertNotIn("10-year", source_data_folders(_write(_SYNTHETIC)))

    def test_source_layers_protected_including_collision(self):
        got = source_data_folders(_write(_SYNTHETIC))
        # Terrain is referenced by BOTH a TerrainLayer and a RASResultsMap;
        # the source reference wins.
        self.assertEqual(got, {"Terrain", "Land Classification", "Features"})

    def test_no_subfolder_layers_ignored(self):
        rasmap = _write("\n".join([
            "<RASMapper>",
            '  <Layer Name="nval" Type="RASGeometry" Filename=".\\Model.g01.hdf" />',
            '  <Layer Name="plan" Type="RASPlan" Filename=".\\Model.p01" />',
            "</RASMapper>",
        ]))
        self.assertEqual(source_data_folders(rasmap), set())


# A .rasmap modelled on real RAS output: <Plans> RASPlan layers with nested
# Encroachment children, and <Results> RASResults layers each wrapping a nested
# "Plan" RASPlan sub-layer that uses the .p##.hdf token. Plans/Results are in a
# deliberately non-numeric order to exercise the sort. CRLF line endings, as RAS
# writes.
_MODEL = "\r\n".join([
    "<RASMapper>",
    "  <Geometries>",
    '    <Layer Name="G1" Type="RASGeometry" Filename=".\\M.g01.hdf" />',
    '    <Layer Name="G2" Type="RASGeometry" Filename=".\\M.g02.hdf" />',
    "  </Geometries>",
    '  <Plans Expanded="True">',
    '    <Layer Name="Base" Type="RASPlan" Filename=".\\M.p01" GeometryHDF=".\\M.g01.hdf">',
    '      <Layer Name="Encroachments" Type="RASEncroachments" Filename=".\\M.p01" />',
    "    </Layer>",
    '    <Layer Name="Third" Type="RASPlan" Filename=".\\M.p03" GeometryHDF=".\\M.g01.hdf">',
    '      <Layer Name="Encroachments" Type="RASEncroachments" Filename=".\\M.p03" />',
    "    </Layer>",
    '    <Layer Name="Second" Type="RASPlan" Filename=".\\M.p02" GeometryHDF=".\\M.g01.hdf">',
    '      <Layer Name="Encroachments" Type="RASEncroachments" Filename=".\\M.p02" />',
    "    </Layer>",
    "  </Plans>",
    "  <EventConditions>",
    '    <Layer Name="FlowA" Type="RASEventConditions" Filename=".\\M.u01.hdf" />',
    '    <Layer Name="FlowB" Type="RASEventConditions" Filename=".\\M.u02.hdf" />',
    '    <Layer Name="FlowC" Type="RASEventConditions" Filename=".\\M.u03.hdf" />',
    "  </EventConditions>",
    '  <Results Checked="True">',
    '    <Layer Name="Third" Type="RASResults" Filename=".\\M.p03.hdf">',
    '      <Layer Name="Event Conditions" Type="RASEventConditions" Filename=".\\M.p03.hdf" />',
    '      <Layer Name="Geometry" Type="RASGeometry" Filename=".\\M.p03.hdf" />',
    '      <Layer Name="Plan" Type="RASPlan" Filename=".\\M.p03.hdf" GeometryHDF=".\\M.p03.hdf" />',
    "    </Layer>",
    '    <Layer Name="Base" Type="RASResults" Filename=".\\M.p01.hdf">',
    '      <Layer Name="Plan" Type="RASPlan" Filename=".\\M.p01.hdf" GeometryHDF=".\\M.p01.hdf" />',
    "    </Layer>",
    "  </Results>",
    "</RASMapper>",
    "",
])


class RemovePlansFromRasmapTest(unittest.TestCase):

    def test_removes_plan_and_result_subtrees(self):
        path = _write(_MODEL)
        removed = remove_plans_from_rasmap(path, "M", ["p03"])
        self.assertEqual(removed, {"plans": ["p03"], "results": ["p03"]})
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        # p03's RASPlan block (and its Encroachment child) and RASResults block
        # (and its nested Plan sub-layer) are gone...
        self.assertNotIn("M.p03", text)
        self.assertNotIn('Name="Third"', text)
        # ...siblings and other sections untouched, CRLF preserved.
        self.assertIn('Name="Base" Type="RASPlan" Filename=".\\M.p01"', text)
        self.assertIn('Name="Second" Type="RASPlan" Filename=".\\M.p02"', text)
        self.assertIn("M.u01.hdf", text)                 # EventConditions kept
        self.assertIn("\r\n", text)
        # nested "Plan" sub-layer of a KEPT result (p01.hdf token) survives —
        # keying RASPlan removal to the extension-less name never matched it.
        self.assertEqual(text.count('Filename=".\\M.p01.hdf"'), 2)

    def test_removes_all_duplicate_layers(self):
        # Two RASPlan layers pointing at the same p02 (a self-healed duplicate,
        # exactly what number-reuse produced in the GMF_DFA rasmap).
        dup = "\r\n".join([
            "<RASMapper>",
            "  <Plans>",
            '    <Layer Name="A" Type="RASPlan" Filename=".\\M.p02" GeometryHDF=".\\M.g01.hdf" />',
            '    <Layer Name="A (dup)" Type="RASPlan" Filename=".\\M.p02" GeometryHDF=".\\M.g01.hdf" />',
            '    <Layer Name="Keep" Type="RASPlan" Filename=".\\M.p01" GeometryHDF=".\\M.g01.hdf" />',
            "  </Plans>",
            "</RASMapper>",
            "",
        ])
        path = _write(dup)
        removed = remove_plans_from_rasmap(path, "M", ["p02"])
        self.assertEqual(removed["plans"], ["p02", "p02"])
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        self.assertNotIn("M.p02", text)
        self.assertIn("M.p01", text)

    def test_missing_id_is_noop(self):
        path = _write(_MODEL)
        before = open(path, encoding="latin-1", newline="").read()
        self.assertEqual(remove_plans_from_rasmap(path, "M", ["p09"]),
                         {"plans": [], "results": []})
        self.assertEqual(open(path, encoding="latin-1", newline="").read(),
                         before)


class RemoveFlowsFromRasmapTest(unittest.TestCase):

    def test_removes_event_conditions_layers(self):
        path = _write(_MODEL)
        removed = remove_flows_from_rasmap(path, "M", ["u02", "u03"])
        self.assertEqual(sorted(removed), ["u02", "u03"])
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        self.assertNotIn("M.u02.hdf", text)
        self.assertNotIn("M.u03.hdf", text)
        self.assertIn("M.u01.hdf", text)                      # other flow kept
        # the nested "Event Conditions" sub-layer of a Results block uses the
        # p##.hdf token and is in <Results>, not <EventConditions> — untouched.
        self.assertIn('Name="Event Conditions" Type="RASEventConditions" '
                      'Filename=".\\M.p03.hdf"', text)

    def test_missing_flow_is_noop(self):
        path = _write(_MODEL)
        before = open(path, encoding="latin-1", newline="").read()
        self.assertEqual(remove_flows_from_rasmap(path, "M", ["u09"]), [])
        self.assertEqual(open(path, encoding="latin-1", newline="").read(),
                         before)


class RemoveGeomsFromRasmapTest(unittest.TestCase):

    def test_removes_geometries_layer_only(self):
        path = _write(_MODEL)
        removed = remove_geoms_from_rasmap(path, "M", ["g02"])
        self.assertEqual(removed, ["g02"])
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        self.assertNotIn("M.g02.hdf", text)
        self.assertIn("M.g01.hdf", text)                      # sibling kept
        # the nested RASGeometry sub-layer of a Results block names p##.hdf and
        # lives in <Results>, not <Geometries> — untouched.
        self.assertIn('Name="Geometry" Type="RASGeometry" '
                      'Filename=".\\M.p03.hdf"', text)

    def test_missing_geom_is_noop(self):
        path = _write(_MODEL)
        before = open(path, encoding="latin-1", newline="").read()
        self.assertEqual(remove_geoms_from_rasmap(path, "M", ["g09"]), [])
        self.assertEqual(open(path, encoding="latin-1", newline="").read(),
                         before)


class ResultPlanIdsTest(unittest.TestCase):

    def test_lists_result_plan_ids(self):
        path = _write(_MODEL)
        # <Results> has RASResults layers for p03 (Third) and p01 (Base).
        self.assertEqual(result_plan_ids(path, "M"), {"p01", "p03"})


class RenumberGeomsInRasmapTest(unittest.TestCase):

    def test_remaps_geometry_tokens_one_pass(self):
        # _MODEL: <Geometries> has g01, and all three plan layers carry
        # GeometryHDF=".\M.g01.hdf". Renumber g01 -> g05 hits both forms.
        path = _write(_MODEL)
        n = renumber_geoms_in_rasmap(path, "M", {"g01": "g05"})
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        self.assertEqual(n, 4)   # 1 <Geometries> layer + 3 plan GeometryHDF
        self.assertNotIn("M.g01.hdf", text)
        self.assertIn('Filename=".\\M.g05.hdf"', text)
        self.assertEqual(text.count('GeometryHDF=".\\M.g05.hdf"'), 3)
        self.assertIn("M.g02.hdf", text)             # unrelated geom untouched
        # plan HDF tokens (.p##.hdf) must never be caught by the .g## pattern
        self.assertIn("M.p01.hdf", text)
        self.assertIn("M.p03.hdf", text)


class SortRasmapLayersTest(unittest.TestCase):

    def test_sorts_plans_and_results_by_number(self):
        path = _write(_MODEL)
        result = sort_rasmap_layers(path, "M")
        self.assertEqual(result, {"plans": ["p01", "p02", "p03"],
                                  "results": ["p01", "p03"]})
        with open(path, encoding="latin-1", newline="") as f:
            text = f.read()
        # Plans now ascending: Base(1), Second(2), Third(3).
        self.assertLess(text.index('Name="Base" Type="RASPlan"'),
                        text.index('Name="Second" Type="RASPlan"'))
        self.assertLess(text.index('Name="Second" Type="RASPlan"'),
                        text.index('Name="Third" Type="RASPlan"'))
        # Results ascending: Base(1) before Third(3).
        self.assertLess(text.index('Name="Base" Type="RASResults"'),
                        text.index('Name="Third" Type="RASResults"'))
        # each nested Encroachment child moved with its parent (still 1 each)
        self.assertEqual(text.count('Filename=".\\M.p02"'), 2)

    def test_already_sorted_is_noop(self):
        path = _write(_MODEL)
        sort_rasmap_layers(path, "M")
        after_first = open(path, encoding="latin-1", newline="").read()
        sort_rasmap_layers(path, "M")
        self.assertEqual(
            open(path, encoding="latin-1", newline="").read(), after_first)


if __name__ == "__main__":
    unittest.main()
