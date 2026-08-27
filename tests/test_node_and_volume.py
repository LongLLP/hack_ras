"""
Tests for pipe-node rims, node maxima, and volume accounting.

The real .p##.hdf fixture carries pipe nodes, node results and volume accounting,
so most of this runs against it. It has NO node with a terrain override, which is
the case that separates the two rim sources -- that path is exercised against a
synthetic HDF built inline.
"""
import os
import tempfile
import unittest

try:
    import h5py
    import numpy as np
    from hack_ras.results.reader import (
        list_pipe_networks,
        list_volume_accounting,
        read_node_max_wse,
        read_node_rims,
        read_node_timeseries,
        read_pipe_network,
        read_volume_accounting,
    )
    from hack_ras.results.model import NodeMaxWse, NodeRims, VolumeAccounting
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

_HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'data',
    '2D culvert bridge levee precip pipes', 'Model.p02.hdf'
)
HAS_HDF_FIXTURE = os.path.exists(_HDF_FIXTURE)

_NODE_ATTRS = 'Geometry/Pipe Nodes/Attributes'


def _make_rim_hdf(folder):
    """
    Pipe nodes with a deliberate mix of override / no-override.

    N1 has no override, so both rim sources agree.
    N2 has an override BELOW its DEM terrain, the real-world pattern; note that
    Invert + Depth reproduces the override, not the terrain, which is how RAS
    itself treats it.
    """
    path = os.path.join(folder, 'rims.p01.hdf')
    dt = np.dtype([
        ('Name', 'S12'), ('Node Type', 'S16'), ('Invert Elevation', '<f4'),
        ('Terrain Elevation', '<f4'), ('Terrain Elevation Override', '<f4'),
        ('Depth', '<f4')])
    rows = np.array([
        (b'N1', b'Junction', 700.0, 710.0, np.nan, 10.0),
        (b'N2', b'External', 715.21, 728.67, 719.00, 3.79),
        (b'N3', b'Closed', 690.0, 705.5, np.nan, 15.5),
    ], dtype=dt)
    with h5py.File(path, 'w') as f:
        f.create_dataset(_NODE_ATTRS, data=rows)
    return path


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestNodeRimsOverride(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = _make_rim_hdf(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_node_rims(self):
        self.assertIsInstance(read_node_rims(self.path), NodeRims)

    def test_default_source_is_override(self):
        self.assertEqual(read_node_rims(self.path).source, 'override')

    def test_override_source_prefers_override_and_falls_back(self):
        r = read_node_rims(self.path, 'override')
        np.testing.assert_allclose(r.rim, [710.0, 719.00, 705.5], atol=1e-3)

    def test_terrain_source_ignores_the_override(self):
        r = read_node_rims(self.path, 'terrain')
        np.testing.assert_allclose(r.rim, [710.0, 728.67, 705.5], atol=1e-3)

    def test_the_two_sources_differ_only_where_an_override_is_set(self):
        a = read_node_rims(self.path, 'override')
        b = read_node_rims(self.path, 'terrain')
        differ = a.rim != b.rim
        np.testing.assert_array_equal(differ, a.has_override)

    def test_invert_plus_depth_reproduces_the_override_rim(self):
        """RAS measures Depth to the effective rim, i.e. to the override."""
        r = read_node_rims(self.path, 'override')
        np.testing.assert_allclose(r.invert + r.depth, r.rim, atol=1e-3)

    def test_using_terrain_would_understate_exceedance(self):
        """The override sits below the DEM here, so 'terrain' is the laxer rim."""
        a = read_node_rims(self.path, 'override')
        b = read_node_rims(self.path, 'terrain')
        i = int(np.where(a.has_override)[0][0])
        self.assertLess(a.rim[i], b.rim[i])

    def test_node_types_are_reported_unfiltered(self):
        r = read_node_rims(self.path)
        self.assertEqual(r.node_types, ['Junction', 'External', 'Closed'])

    def test_as_dict(self):
        d = read_node_rims(self.path, 'override').as_dict()
        self.assertAlmostEqual(d['N2'], 719.00, places=2)

    def test_bad_source_raises_value_error(self):
        with self.assertRaises(ValueError):
            read_node_rims(self.path, 'dem')

    def test_missing_node_table_raises_key_error(self):
        empty = os.path.join(self.tmp.name, 'empty.p01.hdf')
        with h5py.File(empty, 'w') as f:
            f.create_group('Geometry')
        with self.assertRaises(KeyError):
            read_node_rims(empty)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestNodeRimsOnFixture(unittest.TestCase):

    def test_sources_agree_when_no_override_is_set(self):
        a = read_node_rims(_HDF_FIXTURE, 'override')
        b = read_node_rims(_HDF_FIXTURE, 'terrain')
        if a.has_override.any():
            self.skipTest('fixture now carries an override')
        np.testing.assert_allclose(a.rim, b.rim)

    def test_covers_every_node_in_the_network(self):
        net = read_pipe_network(_HDF_FIXTURE, list_pipe_networks(_HDF_FIXTURE)[0])
        rims = read_node_rims(_HDF_FIXTURE).as_dict()
        for node in net.nodes:
            self.assertIn(node, rims)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestNodeMaxWse(unittest.TestCase):

    def setUp(self):
        self.net = read_pipe_network(_HDF_FIXTURE,
                                     list_pipe_networks(_HDF_FIXTURE)[0])

    def test_returns_node_max_wse(self):
        self.assertIsInstance(read_node_max_wse(_HDF_FIXTURE, self.net),
                              NodeMaxWse)

    def test_accepts_a_network_name_string(self):
        a = read_node_max_wse(_HDF_FIXTURE, self.net)
        b = read_node_max_wse(_HDF_FIXTURE, self.net.name)
        np.testing.assert_allclose(a.wse, b.wse)
        self.assertEqual(a.names, b.names)

    def test_matches_the_per_node_time_series(self):
        """The whole point: a vectorised max must equal the per-node reader."""
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        for node in self.net.nodes:
            ts = read_node_timeseries(_HDF_FIXTURE, self.net, node)
            i = mx.names.index(node)
            self.assertAlmostEqual(mx.wse[i], float(ts.wse.max()), places=6,
                                   msg=f'{node} max mismatch')
            self.assertEqual(mx.time_index[i], int(np.argmax(ts.wse)),
                             f'{node} time-of-max mismatch')

    def test_covers_every_node_exactly_once(self):
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        self.assertEqual(sorted(mx.names), sorted(self.net.nodes))
        self.assertEqual(len(mx.names), len(set(mx.names)))
        self.assertEqual(len(mx.wse), len(mx.names))

    def test_time_of_max_returns_a_real_timestamp(self):
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        stamp = mx.time_of_max(mx.names[0])
        self.assertIn(stamp, list(mx.timestamps))

    def test_time_of_max_raises_for_unknown_node(self):
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        with self.assertRaises(KeyError):
            mx.time_of_max('__nope__')

    def test_as_dict(self):
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        d = mx.as_dict()
        self.assertEqual(len(d), len(mx.names))
        self.assertAlmostEqual(d[mx.names[0]], float(mx.wse[0]))

    def test_unknown_network_raises(self):
        with self.assertRaises((KeyError, ValueError)):
            read_node_max_wse(_HDF_FIXTURE, '__no_such_network__')

    def test_rim_exceedance_end_to_end(self):
        """Items 6's actual question, with the decided '>=' comparison."""
        mx = read_node_max_wse(_HDF_FIXTURE, self.net)
        rims = read_node_rims(_HDF_FIXTURE, 'override')
        lookup = rims.as_dict()
        types = dict(zip(rims.names, rims.node_types))
        rows = [(n, w, lookup[n], types[n])
                for n, w in zip(mx.names, mx.wse) if n in lookup]
        self.assertEqual(len(rows), len(mx.names))
        exceed = [r for r in rows if r[1] >= r[2]]
        # '>=' must be at least as inclusive as '>'
        strict = [r for r in rows if r[1] > r[2]]
        self.assertGreaterEqual(len(exceed), len(strict))
        for _, _, _, node_type in rows:
            self.assertTrue(node_type)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestVolumeAccounting(unittest.TestCase):

    def test_lists_kinds_and_names(self):
        listing = list_volume_accounting(_HDF_FIXTURE)
        self.assertIn('2D', listing)
        self.assertTrue(listing['2D'])

    def test_reads_a_2d_area(self):
        name = list_volume_accounting(_HDF_FIXTURE)['2D'][0]
        v = read_volume_accounting(_HDF_FIXTURE, name, '2D')
        self.assertIsInstance(v, VolumeAccounting)
        self.assertEqual(v.name, name)
        self.assertEqual(v.kind, '2D')
        self.assertTrue(np.isfinite(v.vol_ending))
        self.assertTrue(np.isfinite(v.cum_inflow))
        self.assertTrue(v.units)

    def test_matches_the_raw_attributes(self):
        name = list_volume_accounting(_HDF_FIXTURE)['2D'][0]
        v = read_volume_accounting(_HDF_FIXTURE, name, '2D')
        with h5py.File(_HDF_FIXTURE, 'r') as h:
            attrs = dict(h['Results/Unsteady/Summary/Volume Accounting'
                           f'/Volume Accounting 2D/{name}'].attrs)
        self.assertAlmostEqual(v.vol_ending, float(attrs['Vol Ending']), places=4)
        self.assertAlmostEqual(v.cum_inflow, float(attrs['Cum Inflow']), places=4)
        self.assertAlmostEqual(v.error_percent, float(attrs['Error Percent']),
                               places=6)

    def test_reads_a_pipe_network(self):
        names = list_volume_accounting(_HDF_FIXTURE).get('Pipe Networks') or []
        if not names:
            self.skipTest('no pipe-network volume accounting in fixture')
        v = read_volume_accounting(_HDF_FIXTURE, names[0], 'Pipe Networks')
        self.assertEqual(v.kind, 'Pipe Networks')
        # pipe networks carry no precipitation
        self.assertTrue(np.isnan(v.precip_excess))

    def test_unknown_name_raises_key_error_listing_what_exists(self):
        with self.assertRaises(KeyError) as cm:
            read_volume_accounting(_HDF_FIXTURE, '__nope__', '2D')
        self.assertIn('Available', str(cm.exception))

    def test_unknown_kind_raises_key_error(self):
        with self.assertRaises(KeyError):
            read_volume_accounting(_HDF_FIXTURE, 'Interior', '__nope__')


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestVolumeAccountingAbsent(unittest.TestCase):

    def test_empty_listing_when_no_volume_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'bare.p01.hdf')
            with h5py.File(path, 'w') as f:
                f.create_group('Results')
            self.assertEqual(list_volume_accounting(path), {})
            with self.assertRaises(KeyError):
                read_volume_accounting(path, 'Interior', '2D')


if __name__ == '__main__':
    unittest.main()
