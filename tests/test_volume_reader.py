"""
Tests for read_cell_volume_table and interpolate_cell_volume.
"""
import os
import unittest

import numpy as np

try:
    from hack_ras.results.reader import read_cell_volume_table, interpolate_cell_volume
    from hack_ras.results.model import CellVolumeTable
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "data",
    "2D culvert bridge levee precip pipes",
    "Model.p02.hdf",
)
HAS_HDF = os.path.exists(HDF_FIXTURE)


# ──────────────────────────────────────────────────────────────────────────────
# interpolate_cell_volume — synthetic table, no HDF needed
# ──────────────────────────────────────────────────────────────────────────────

class TestInterpolateCellVolume(unittest.TestCase):

    def _make_table(self):
        # Two cells, each with 3 elevation-volume pairs, packed contiguously.
        # Cell 0: elev [10, 11, 12], vol [0, 100, 300]
        # Cell 1: elev [20, 21, 22], vol [0, 50, 150]
        info = np.array([[0, 3], [3, 3]], dtype=np.int32)
        values = np.array([
            [10.0, 0.0],
            [11.0, 100.0],
            [12.0, 300.0],
            [20.0, 0.0],
            [21.0, 50.0],
            [22.0, 150.0],
        ], dtype=np.float32)
        return CellVolumeTable(info=info, values=values)

    def test_below_min_returns_zero(self):
        t = self._make_table()
        self.assertEqual(interpolate_cell_volume(t, 0, 9.0, 500.0), 0.0)
        self.assertEqual(interpolate_cell_volume(t, 0, 10.0, 500.0), 0.0)

    def test_above_max_extrapolates_linearly(self):
        t = self._make_table()
        # Cell 0: table max is elev=12, vol=300. Plan area = 500 ft².
        # At exactly the table max: no extrapolation needed → 300.0
        self.assertAlmostEqual(interpolate_cell_volume(t, 0, 12.0, 500.0), 300.0, places=3)
        # At 3 ft above the table max: 300 + 3 * 500 = 1800
        self.assertAlmostEqual(interpolate_cell_volume(t, 0, 15.0, 500.0), 1800.0, places=2)
        # At 0.5 ft above: 300 + 0.5 * 500 = 550
        self.assertAlmostEqual(interpolate_cell_volume(t, 0, 12.5, 500.0), 550.0, places=2)

    def test_midrange_interpolation(self):
        t = self._make_table()
        # Between elev 11 (vol 100) and elev 12 (vol 300): midpoint → 200
        result = interpolate_cell_volume(t, 0, 11.5, 500.0)
        self.assertAlmostEqual(result, 200.0, places=2)

    def test_second_cell(self):
        t = self._make_table()
        # Between elev 20 (vol 0) and elev 21 (vol 50): at 20.5 → 25
        result = interpolate_cell_volume(t, 1, 20.5, 200.0)
        self.assertAlmostEqual(result, 25.0, places=2)

    def test_extrapolation_uses_plan_area(self):
        t = self._make_table()
        # Confirm the extrapolation rate equals the plan area:
        # vol at (max_elev + dh) - vol at max_elev == dh * plan_area
        area = 750.0
        dh = 2.0
        v_max  = interpolate_cell_volume(t, 0, 12.0, area)
        v_high = interpolate_cell_volume(t, 0, 12.0 + dh, area)
        self.assertAlmostEqual(v_high - v_max, dh * area, places=2)


# ──────────────────────────────────────────────────────────────────────────────
# read_cell_volume_table — requires HDF fixture
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_RESULTS and HAS_HDF, "results package or HDF fixture unavailable")
class TestReadCellVolumeTable(unittest.TestCase):

    def test_returns_cell_volume_table(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        self.assertIsInstance(tbl, CellVolumeTable)

    def test_info_shape(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        # info is (N_cells, 2): start index and count
        self.assertEqual(tbl.info.ndim, 2)
        self.assertEqual(tbl.info.shape[1], 2)

    def test_values_shape(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        # values is (total_pairs, 2): elevation and volume
        self.assertEqual(tbl.values.ndim, 2)
        self.assertEqual(tbl.values.shape[1], 2)

    def test_info_counts_consistent(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        # Every cell's start + count must be within bounds of values.
        # Perimeter dummy cells have count == 0; allow that.
        n_pairs = tbl.values.shape[0]
        for i in range(tbl.info.shape[0]):
            start, count = int(tbl.info[i, 0]), int(tbl.info[i, 1])
            self.assertGreaterEqual(start, 0)
            self.assertGreaterEqual(count, 0)
            self.assertLessEqual(start + count, n_pairs)

    def test_volumes_non_negative(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        self.assertTrue(np.all(tbl.values[:, 1] >= 0))

    def test_interpolation_on_real_cell(self):
        tbl = read_cell_volume_table(HDF_FIXTURE, "Interior")
        # Cell 0: interpolate at mid-range WSE — result should be > 0.
        # Plan area is only needed for above-max extrapolation; use a plausible
        # value so the call is valid even if mid_wse happens to equal elev[-1].
        start, count = int(tbl.info[0, 0]), int(tbl.info[0, 1])
        elev_vals = tbl.values[start : start + count, 0]
        mid_wse = float((elev_vals[0] + elev_vals[-1]) / 2.0)
        vol = interpolate_cell_volume(tbl, 0, mid_wse, cell_plan_area=1000.0)
        self.assertGreater(vol, 0.0)


if __name__ == "__main__":
    unittest.main()
