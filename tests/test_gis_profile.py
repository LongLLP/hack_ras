"""
Tests for hack_ras.gis.profile

compute_profile_stations and assign_wse operate on shapely geometry and numpy
arrays, so these tests run without any HEC-RAS files.
"""
import unittest

try:
    import numpy as np
    from shapely.geometry import LineString, Point, Polygon
    import geopandas as gpd
    from hack_ras.results.model import AreaGeometry
    from hack_ras.gis.profile import (
        _as_linestring,
        _boundary_crossings,
        compute_profile_stations,
        assign_wse,
    )
    from hack_ras.gis.model import ProfilePoint
    HAS_GIS = True
except ImportError:
    HAS_GIS = False


def _make_area(cell_centers, polys, boundary):
    """Build a minimal AreaGeometry for testing."""
    n = len(cell_centers)
    min_elevations = np.zeros(n)
    cell_gdf = gpd.GeoDataFrame(
        [{"cell_idx": i, "geometry": polys[i]} for i in range(n) if polys[i] is not None],
        geometry="geometry",
    )
    return AreaGeometry(
        cell_centers=np.array(cell_centers, dtype=float),
        min_elevations=min_elevations,
        polygons=polys,
        boundary=boundary,
        cell_gdf=cell_gdf,
    )


@unittest.skipUnless(HAS_GIS, "hack_ras[gis] extras not installed")
class TestAsLinestring(unittest.TestCase):

    def test_linestring_passthrough(self):
        ls = LineString([(0, 0), (1, 1)])
        self.assertIs(_as_linestring(ls), ls)

    def test_unsupported_type_raises(self):
        with self.assertRaises(ValueError):
            _as_linestring(Point(0, 0))


@unittest.skipUnless(HAS_GIS, "hack_ras[gis] extras not installed")
class TestBoundaryCrossings(unittest.TestCase):

    def test_two_crossings(self):
        # Line runs east-west through a unit square; should cross at x=0 and x=1
        square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        line = LineString([(-1, 0.5), (2, 0.5)])
        stations = _boundary_crossings(line, square)
        self.assertEqual(len(stations), 2)
        # Stations should be ~1.0 (entry) and ~2.0 (exit) along the 3-unit line
        self.assertAlmostEqual(stations[0], 1.0, places=2)
        self.assertAlmostEqual(stations[1], 2.0, places=2)

    def test_no_crossing(self):
        square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        line = LineString([(5, 0), (6, 0)])
        self.assertEqual(_boundary_crossings(line, square), [])


@unittest.skipUnless(HAS_GIS, "hack_ras[gis] extras not installed")
class TestComputeProfileStations(unittest.TestCase):

    def _simple_setup(self):
        """Two cells side-by-side in a 2x1 area."""
        boundary = Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])
        poly_left  = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly_right = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
        centers = [(0.5, 0.5), (1.5, 0.5)]
        ag = _make_area(centers, [poly_left, poly_right], boundary)
        return ag

    def test_returns_profile_points(self):
        ag = self._simple_setup()
        line = LineString([(0.5, 0.5), (1.5, 0.5)])
        pts = compute_profile_stations(line, {"Area1": ag})
        self.assertGreater(len(pts), 0)
        for p in pts:
            self.assertIsInstance(p, ProfilePoint)
            self.assertIsNone(p.wse)  # not yet assigned

    def test_sorted_by_station(self):
        ag = self._simple_setup()
        line = LineString([(0, 0.5), (2, 0.5)])
        pts = compute_profile_stations(line, {"Area1": ag})
        stations = [p.station for p in pts]
        self.assertEqual(stations, sorted(stations))


@unittest.skipUnless(HAS_GIS, "hack_ras[gis] extras not installed")
class TestAssignWse(unittest.TestCase):

    def _make_pts(self):
        return [
            ProfilePoint(station=0.0, area="A", cell_idx=0, point_type="cell"),
            ProfilePoint(station=1.0, area="A", cell_idx=None, point_type="boundary"),
            ProfilePoint(station=2.0, area="A", cell_idx=1, point_type="cell"),
        ]

    def test_wet_cells_assigned(self):
        pts = self._make_pts()
        wse      = np.array([10.0, 9.0])
        min_elev = np.array([5.0,  4.0])
        result = assign_wse(pts, {"A": wse}, {"A": min_elev})
        self.assertEqual(result[0].wse, 10.0)
        self.assertEqual(result[0].status, "wet")
        self.assertEqual(result[2].wse, 9.0)
        self.assertEqual(result[2].status, "wet")

    def test_dry_cell_gets_min_elev(self):
        pts = self._make_pts()
        min_elev = np.array([5.0, 4.0])
        wse      = min_elev.copy()   # WSE == min_elev → dry
        result = assign_wse(pts, {"A": wse}, {"A": min_elev})
        self.assertEqual(result[0].wse, 5.0)
        self.assertEqual(result[0].status, "dry")

    def test_boundary_point_interpolated(self):
        pts = self._make_pts()
        wse      = np.array([10.0, 8.0])
        min_elev = np.array([5.0,  4.0])
        result = assign_wse(pts, {"A": wse}, {"A": min_elev})
        # Station 1.0 is halfway between station 0.0 (wse=10) and 2.0 (wse=8)
        self.assertAlmostEqual(result[1].wse, 9.0, places=5)
        self.assertEqual(result[1].status, "interpolated")

    def test_returns_new_list(self):
        pts = self._make_pts()
        wse = np.array([10.0, 9.0])
        min_elev = np.array([5.0, 4.0])
        result = assign_wse(pts, {"A": wse}, {"A": min_elev})
        # Original objects unchanged
        self.assertIsNone(pts[0].wse)
        # Returned objects are different instances
        self.assertIsNot(result[0], pts[0])


if __name__ == "__main__":
    unittest.main()
