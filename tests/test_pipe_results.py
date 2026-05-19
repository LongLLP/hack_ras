"""
Tests for pipe network support in hack_ras.results.

list_pipe_networks is tested with a synthetic minimal HDF5 created inline
(no fixture required).

read_pipe_network, read_node_timeseries, and read_conduit_timeseries require a
real .p##.hdf fixture and are skipped when none is available.
"""
import os
import tempfile
import unittest

try:
    import h5py
    import numpy as np
    from hack_ras.results.reader import (
        list_pipe_networks,
        read_pipe_network,
        read_node_timeseries,
        read_conduit_timeseries,
    )
    from hack_ras.results.model import (
        PipeNetwork,
        PipeConduit,
        NodeTimeSeries,
        ConduitTimeSeries,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

# Path to a real plan HDF fixture — tests that need it are skipped when absent.
_HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'data',
    '2D_culvert_bridge_levee_precip_and_pipes.p01.hdf'
)
HAS_HDF_FIXTURE = os.path.exists(_HDF_FIXTURE)


def _make_minimal_hdf(folder, networks=("Net1", "Net2")):
    """Create a minimal HDF5 with only a Geometry/Pipe Networks group structure."""
    path = os.path.join(folder, "fake.p01.hdf")
    with h5py.File(path, "w") as f:
        grp = f.create_group("Geometry/Pipe Networks")
        for n in networks:
            grp.create_group(n)
    return path


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestListPipeNetworks(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_empty_when_no_pipe_networks_group(self):
        path = os.path.join(self.tmp, "no_pipes.p01.hdf")
        with h5py.File(path, "w") as f:
            f.create_group("Geometry/2D Flow Areas")
        result = list_pipe_networks(path)
        self.assertEqual(result, [])

    def test_returns_network_names(self):
        path = _make_minimal_hdf(self.tmp, networks=("Storm", "Sanitary"))
        result = list_pipe_networks(path)
        self.assertCountEqual(result, ["Storm", "Sanitary"])

    def test_returns_empty_list_when_group_absent(self):
        path = os.path.join(self.tmp, "empty.p01.hdf")
        with h5py.File(path, "w") as f:
            pass
        result = list_pipe_networks(path)
        self.assertEqual(result, [])

    def test_single_network(self):
        path = _make_minimal_hdf(self.tmp, networks=("Main",))
        result = list_pipe_networks(path)
        self.assertEqual(result, ["Main"])


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestReadPipeNetwork(unittest.TestCase):

    def setUp(self):
        self.networks = list_pipe_networks(_HDF_FIXTURE)
        self.skipTest("no pipe networks in fixture") if not self.networks else None

    def _network(self):
        return read_pipe_network(_HDF_FIXTURE, self.networks[0])

    def test_returns_pipe_network_instance(self):
        result = self._network()
        self.assertIsInstance(result, PipeNetwork)

    def test_name_matches_requested(self):
        result = self._network()
        self.assertEqual(result.name, self.networks[0])

    def test_node_keys_are_strings(self):
        result = self._network()
        for key in result.nodes:
            self.assertIsInstance(key, str)

    def test_node_values_are_ints(self):
        result = self._network()
        for val in result.nodes.values():
            self.assertIsInstance(val, int)

    def test_conduits_values_are_pipe_conduit(self):
        result = self._network()
        for val in result.conduits.values():
            self.assertIsInstance(val, PipeConduit)

    def test_conduit_index_keys_match_conduits(self):
        result = self._network()
        self.assertEqual(set(result.conduit_index), set(result.conduits))

    def test_upstream_of_values_are_lists(self):
        result = self._network()
        for val in result.upstream_of.values():
            self.assertIsInstance(val, list)

    def test_downstream_of_values_are_lists(self):
        result = self._network()
        for val in result.downstream_of.values():
            self.assertIsInstance(val, list)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestReadNodeTimeseries(unittest.TestCase):

    def setUp(self):
        networks = list_pipe_networks(_HDF_FIXTURE)
        if not networks:
            self.skipTest("no pipe networks in fixture")
        self.network = read_pipe_network(_HDF_FIXTURE, networks[0])
        if not self.network.nodes:
            self.skipTest("no nodes in first pipe network")
        self.node_name = next(iter(self.network.nodes))

    def _ts(self):
        return read_node_timeseries(_HDF_FIXTURE, self.network, self.node_name)

    def test_returns_node_time_series(self):
        self.assertIsInstance(self._ts(), NodeTimeSeries)

    def test_all_arrays_same_length_as_timestamps(self):
        ts = self._ts()
        T = len(ts.timestamps)
        self.assertEqual(len(ts.depth), T)
        self.assertEqual(len(ts.wse), T)
        self.assertEqual(len(ts.inlet_flow), T)
        self.assertEqual(len(ts.flow_in), T)
        self.assertEqual(len(ts.flow_out), T)

    def test_flow_arrays_are_float64(self):
        ts = self._ts()
        self.assertEqual(ts.flow_in.dtype, np.float64)
        self.assertEqual(ts.flow_out.dtype, np.float64)

    def test_raises_key_error_for_unknown_node(self):
        with self.assertRaises(KeyError):
            read_node_timeseries(_HDF_FIXTURE, self.network, "__no_such_node__")


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestReadConduitTimeseries(unittest.TestCase):

    def setUp(self):
        networks = list_pipe_networks(_HDF_FIXTURE)
        if not networks:
            self.skipTest("no pipe networks in fixture")
        self.network = read_pipe_network(_HDF_FIXTURE, networks[0])
        if not self.network.conduit_index:
            self.skipTest("no conduits in first pipe network")
        self.conduit_name = next(iter(self.network.conduit_index))

    def _ts(self):
        return read_conduit_timeseries(_HDF_FIXTURE, self.network, self.conduit_name)

    def test_returns_conduit_time_series(self):
        self.assertIsInstance(self._ts(), ConduitTimeSeries)

    def test_all_arrays_same_length_as_timestamps(self):
        ts = self._ts()
        T = len(ts.timestamps)
        self.assertEqual(len(ts.flow_us), T)
        self.assertEqual(len(ts.flow_ds), T)
        self.assertEqual(len(ts.vel_us), T)
        self.assertEqual(len(ts.vel_ds), T)

    def test_arrays_are_float64(self):
        ts = self._ts()
        self.assertEqual(ts.flow_us.dtype, np.float64)
        self.assertEqual(ts.vel_ds.dtype, np.float64)

    def test_raises_key_error_for_unknown_conduit(self):
        with self.assertRaises(KeyError):
            read_conduit_timeseries(_HDF_FIXTURE, self.network, "__no_such_conduit__")


if __name__ == '__main__':
    unittest.main()
