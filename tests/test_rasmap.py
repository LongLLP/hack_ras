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

from hack_ras.project.rasmap import source_data_folders

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
    with open(path, "w", encoding="latin-1") as f:
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


if __name__ == "__main__":
    unittest.main()
