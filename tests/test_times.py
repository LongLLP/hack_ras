"""
Tests for hack_ras.results.times and the readers that route through it.

The conventions themselves were measured on real models and are recorded in the
times.py module docstring; what is pinned here is that the code applies them:
start-time origin, both spellings of midnight, case-blind stamp lookup, rounding
to the second, and no time of max for a never-wet cell (but a real one for a
cell that is wet at t = 0 and recedes).
"""
import os
import shutil
import tempfile
import unittest
from datetime import datetime

import numpy as np

try:
    import h5py
    from hack_ras.results import times
    from hack_ras.results.reader import (
        list_areas, list_pipe_networks, read_conduit_profile, read_node_timeseries,
        read_pipe_network, read_simulation_start_time, read_summary_max,
        read_timestamps, read_wse, read_wse_time,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

_P02 = os.path.join(os.path.dirname(__file__), 'data',
                    '2D culvert bridge levee precip pipes', 'Model.p02.hdf')
_SUM = ('Results/Unsteady/Output/Output Blocks/Base Output/Summary Output'
        '/2D Flow Areas/{area}/Maximum Water Surface')


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestStampText(unittest.TestCase):

    def test_parse_either_case_and_bytes(self):
        want = datetime(2025, 1, 1, 12, 37)
        for raw in ('01JAN2025 12:37:00', '01Jan2025 12:37:00',
                    b'01JAN2025 12:37:00', '  01jan2025 12:37:00 '):
            with self.subTest(raw=raw):
                self.assertEqual(times.parse_stamp(raw), want)

    def test_2400_is_the_next_midnight(self):
        self.assertEqual(times.parse_stamp('01JAN2025 24:00:00'),
                         times.parse_stamp('02JAN2025 00:00:00'))

    def test_bad_stamp_raises(self):
        with self.assertRaises(ValueError):
            times.parse_stamp('yesterday')

    def test_format_is_ras_spelling_and_round_trips(self):
        t = datetime(2026, 1, 3, 0, 0)
        self.assertEqual(times.format_stamp(t), '03JAN2026 00:00:00')
        self.assertEqual(times.format_stamp(np.datetime64(t, 'ms')),
                         '03JAN2026 00:00:00')
        self.assertEqual(times.parse_stamp(times.format_stamp(t)), t)

    def test_missing_formats_blank(self):
        self.assertEqual(times.format_stamp(None), '')
        self.assertEqual(times.format_stamp(np.datetime64('NaT')), '')

    def test_to_datetimes_maps_nat_to_none(self):
        arr = np.array(['2025-01-01T10:00', 'NaT'], dtype='datetime64[ms]')
        self.assertEqual(times.to_datetimes(arr), [datetime(2025, 1, 1, 10), None])

    def test_days_count_from_the_start_time_and_round_to_the_second(self):
        start = datetime(2025, 1, 1, 10, 0)
        got = times.days_to_datetime64([0.0, 0.10902778059244156, 0.5 + 0.004 / 86400],
                                       start)
        self.assertEqual(got[0].item(), start)
        self.assertEqual(got[1].item(), datetime(2025, 1, 1, 12, 37))
        self.assertEqual(got[2].item(), datetime(2025, 1, 1, 22, 0))

    def test_dry_tolerance_matches_the_surface_mapper(self):
        """One meaning of 'wet' across the package."""
        try:
            from hack_ras.gis.wse_surface import DRY_TOL
        except ImportError:
            self.skipTest('gis extras not installed')
        self.assertEqual(times.DRY_TOL, DRY_TOL)


@unittest.skipUnless(HAS_RESULTS and os.path.exists(_P02),
                     "hack_ras[results] extras or the p02 fixture missing")
class TestReadersOnFixture(unittest.TestCase):
    """p02 starts at 10:00 -- the case that separates start time from midnight."""

    def test_start_time_and_stamps(self):
        self.assertEqual(read_simulation_start_time(_P02), datetime(2025, 1, 1, 10))
        st = read_timestamps(_P02)
        self.assertEqual(st.dtype, np.dtype('datetime64[ms]'))
        self.assertEqual(st[0].item(), datetime(2025, 1, 1, 10))
        self.assertEqual(st[-1].item(), datetime(2025, 1, 1, 14))

    def test_time_series_dataclasses_carry_datetimes(self):
        net = read_pipe_network(_P02, list_pipe_networks(_P02)[0])
        ts = read_node_timeseries(_P02, net, next(iter(net.nodes)))
        np.testing.assert_array_equal(ts.timestamps, read_timestamps(_P02))

    def test_stamp_lookup_is_spelling_blind(self):
        area = list_areas(_P02)[0]
        want = read_wse(_P02, area, '01JAN2025 11:00:00')
        for when in ('01jan2025 11:00:00', '01Jan2025 11:00:00'):
            with self.subTest(when=when):
                np.testing.assert_array_equal(read_wse(_P02, area, when), want)
        net = list_pipe_networks(_P02)[0]
        conduit = next(iter(read_pipe_network(_P02, net).conduit_index))
        a = read_conduit_profile(_P02, net, conduit, '01JAN2025 11:00:00')
        b = read_conduit_profile(_P02, net, conduit, '01Jan2025 11:00:00')
        np.testing.assert_array_equal(a.wse, b.wse)

    def test_unknown_or_garbled_stamp_raises(self):
        area = list_areas(_P02)[0]
        for when in ('01JAN2025 11:30:00', 'not a stamp'):
            with self.subTest(when=when), self.assertRaises(ValueError):
                read_wse(_P02, area, when)

    def test_summary_max_times_are_datetimes_inside_the_run(self):
        area = list_areas(_P02)[0]
        got = read_summary_max(_P02, area, range(10))
        for idx, (wse, when) in got.items():
            with self.subTest(cell=idx):
                self.assertIsInstance(when, datetime)
                self.assertTrue(datetime(2025, 1, 1, 10) <= when
                                <= datetime(2025, 1, 1, 14))


@unittest.skipUnless(HAS_RESULTS and os.path.exists(_P02),
                     "hack_ras[results] extras or the p02 fixture missing")
class TestNeverWetCells(unittest.TestCase):
    """
    The fixture is rain-on-grid, so every cell gets wet. A TEMP COPY is edited
    through h5py to add the two cases seen on PCA and LAX: a cell that never
    clears its minimum (time 0, meaningless) and a cell that is wet at t = 0
    and recedes (time 0, real).
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.hdf = os.path.join(cls._tmp.name, 'copy.p02.hdf')
        shutil.copyfile(_P02, cls.hdf)
        cls.area = list_areas(cls.hdf)[0]
        cls.dry_cell, cls.wet_cell = 3, 4
        with h5py.File(cls.hdf, 'r+') as f:
            mn = f[f'Geometry/2D Flow Areas/{cls.area}/Cells Minimum Elevation'][()]
            ds = f[_SUM.format(area=cls.area)]
            s = ds[()]
            s[0, cls.dry_cell] = mn[cls.dry_cell] + times.DRY_TOL / 2
            s[1, cls.dry_cell] = 0.0
            s[0, cls.wet_cell] = mn[cls.wet_cell] + 1.0
            s[1, cls.wet_cell] = 0.0
            ds[...] = s

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_read_wse_time_blanks_the_never_wet_cell_and_dummies_only(self):
        """Blanked: the dry cell and the perimeter dummy cells (NaN minimum),
        which have no result at all. Kept: the wet cell whose time is 0."""
        t = read_wse_time(self.hdf, self.area, 'Maximum')
        self.assertTrue(np.isnat(t[self.dry_cell]))
        self.assertEqual(t[self.wet_cell].item(), datetime(2025, 1, 1, 10))
        with h5py.File(self.hdf, 'r') as f:
            mn = f[f'Geometry/2D Flow Areas/{self.area}/Cells Minimum Elevation'][()]
        dummy = np.zeros(len(t), dtype=bool)
        dummy[:len(mn)] = ~np.isfinite(mn[:len(t)])
        dummy[len(mn):] = True
        self.assertGreater(int(dummy.sum()), 0, 'fixture lost its perimeter cells')
        expect = dummy.copy()
        expect[self.dry_cell] = True
        np.testing.assert_array_equal(np.isnat(t), expect)

    def test_read_summary_max_returns_none_for_it(self):
        got = read_summary_max(self.hdf, self.area, [self.dry_cell, self.wet_cell])
        self.assertIsNone(got[self.dry_cell][1])
        self.assertEqual(got[self.wet_cell][1], datetime(2025, 1, 1, 10))


if __name__ == '__main__':
    unittest.main()
