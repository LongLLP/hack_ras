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
        read_conduit_profile,
    )
    from hack_ras.results.model import (
        PipeNetwork,
        PipeConduit,
        NodeTimeSeries,
        ConduitTimeSeries,
        ConduitProfile,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

# Path to a real plan HDF fixture — tests that need it are skipped when absent.
_HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'data',
    '2D culvert bridge levee precip pipes', 'Model.p02.hdf'
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


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestReadConduitProfile(unittest.TestCase):
    """
    Along-conduit profile reader.

    The station-direction and global-vs-local-index conventions asserted here were
    established empirically against the Hillside model (215 conduits): see
    docs/ai_context.md, 'Pipe Network Geometry & Results'.
    """

    def setUp(self):
        networks = list_pipe_networks(_HDF_FIXTURE)
        if not networks:
            self.skipTest("no pipe networks in fixture")
        self.net_name = networks[0]
        self.network = read_pipe_network(_HDF_FIXTURE, self.net_name)
        if not self.network.conduit_index:
            self.skipTest("no conduits in first pipe network")
        self.conduit_name = next(iter(self.network.conduit_index))

    def _profile(self, when='Maximum'):
        return read_conduit_profile(
            _HDF_FIXTURE, self.network, self.conduit_name, when)

    def test_returns_conduit_profile(self):
        self.assertIsInstance(self._profile(), ConduitProfile)

    def test_all_arrays_same_length(self):
        pr = self._profile()
        F = len(pr.station)
        self.assertGreater(F, 0)
        for arr in (pr.invert, pr.wse, pr.velocity, pr.flow, pr.face_indices):
            self.assertEqual(len(arr), F)

    def test_arrays_are_float64(self):
        pr = self._profile()
        for arr in (pr.station, pr.invert, pr.wse, pr.velocity, pr.flow):
            self.assertEqual(arr.dtype, np.float64)

    def test_station_is_ascending(self):
        pr = self._profile()
        self.assertTrue(np.all(np.diff(pr.station) >= 0))

    def test_station_within_conduit_length(self):
        pr = self._profile()
        self.assertGreaterEqual(pr.station[0], 0.0)
        self.assertLessEqual(pr.station[-1], pr.length + 1e-6)

    def test_station_increases_from_us_to_ds(self):
        """
        Face invert must trend US->DS in the same SENSE as the conduit's own
        US/DS elevations.

        Compare signs rather than assuming downhill: the fixture contains an
        adverse-slope conduit (C232, US 726.25 -> DS 727.52) whose invert
        legitimately RISES with station. That conduit is the control case -- an
        assertion that the invert always falls would pass for the wrong reason on
        a purely downhill network.
        """
        checked = 0
        for name in self.network.conduit_index:
            pr = read_conduit_profile(_HDF_FIXTURE, self.network, name)
            if len(pr.station) < 2:
                continue
            geom_drop = pr.us_invert - pr.ds_invert
            face_drop = pr.invert[0] - pr.invert[-1]
            if abs(geom_drop) < 0.02:
                continue      # too flat to judge
            self.assertEqual(
                np.sign(geom_drop), np.sign(face_drop),
                f"{name}: face invert trends opposite to the conduit's "
                f"US({pr.us_invert})->DS({pr.ds_invert}) elevations -- "
                f"station direction is not US->DS")
            checked += 1
        self.assertGreater(checked, 0, "no conduit had enough slope to check")

    def test_adverse_slope_conduit_is_present_and_handled(self):
        """Pin the control case, so the slope test cannot silently lose its teeth."""
        adverse = [
            n for n in self.network.conduit_index
            if (lambda pr: pr.ds_invert - pr.us_invert > 0.02)(
                read_conduit_profile(_HDF_FIXTURE, self.network, n))
        ]
        self.assertTrue(
            adverse,
            "fixture no longer contains an adverse-slope conduit; "
            "test_station_increases_from_us_to_ds is weakened")
        pr = read_conduit_profile(_HDF_FIXTURE, self.network, adverse[0])
        self.assertTrue(np.all(np.diff(pr.station) >= 0))
        self.assertGreater(pr.invert[-1], pr.invert[0])

    def test_accepts_network_name_string(self):
        by_obj = self._profile()
        by_str = read_conduit_profile(
            _HDF_FIXTURE, self.net_name, self.conduit_name, 'Maximum')
        np.testing.assert_array_equal(by_obj.station, by_str.station)
        np.testing.assert_array_equal(by_obj.wse, by_str.wse)

    def test_timestamp_selector_matches_face_timeseries(self):
        """A snapshot profile must equal the raw Face dataset row at those faces."""
        import h5py
        base = ("Results/Unsteady/Output/Output Blocks/Base Output"
                "/Unsteady Time Series")
        with h5py.File(_HDF_FIXTURE, 'r') as hdf:
            stamps = [t.decode().strip()
                      for t in hdf[f'{base}/Time Date Stamp'][()]]
            raw = hdf[f'{base}/Pipe Networks/{self.net_name}'
                      '/Face Water Surface'][1, :]
        pr = self._profile(stamps[1])
        np.testing.assert_allclose(pr.wse, raw[pr.face_indices], rtol=0, atol=0)

    def test_maximum_envelope_at_least_matches_a_snapshot(self):
        import h5py
        base = ("Results/Unsteady/Output/Output Blocks/Base Output"
                "/Unsteady Time Series")
        with h5py.File(_HDF_FIXTURE, 'r') as hdf:
            stamps = [t.decode().strip()
                      for t in hdf[f'{base}/Time Date Stamp'][()]]
        mx = self._profile('Maximum')
        # Summary and time-series values are both stored float32, so allow a few
        # ULP at elevation magnitudes (~750 ft -> ~1e-4 ft per ULP).
        tol = 8.0 * np.spacing(np.abs(mx.wse).max().astype(np.float32))
        for stamp in stamps:
            snap = self._profile(stamp)
            self.assertTrue(np.all(snap.wse <= mx.wse + tol))

    def test_minimum_envelope_never_exceeds_maximum(self):
        """
        The only ordering guarantee RAS actually honours for the Minimum envelope.

        Deliberately NOT asserted: that Minimum bounds the output time series from
        below. It does not. On dry / near-dry faces RAS's Minimum Face Water
        Surface sits ABOVE the smallest value in Unsteady Time Series -- by up to
        0.278 ft on this fixture (5 of 93 faces) and 3.185 ft on the Hillside p10
        model (58 of 2850 faces), because the two are referenced to different
        dry-bed elevations. Maximum has no such problem (0 violations on both).
        """
        mn = self._profile('Minimum')
        mx = self._profile('Maximum')
        self.assertTrue(np.all(mn.wse <= mx.wse))
        self.assertTrue(np.all(mn.flow <= mx.flow))

    def test_derived_properties(self):
        pr = self._profile()
        np.testing.assert_allclose(pr.depth, pr.wse - pr.invert)
        np.testing.assert_allclose(pr.crown, pr.invert + pr.rise)
        np.testing.assert_allclose(pr.station_from_ds, pr.length - pr.station)
        self.assertEqual(pr.is_surcharged.dtype, np.bool_)
        # EG is never below WS, and the gap is exactly the velocity head.
        g = 9.80665 if pr.si_units else 32.174
        np.testing.assert_allclose(
            pr.energy_grade - pr.wse, pr.velocity ** 2 / (2.0 * g))

    def test_us_ds_nodes_match_pipe_network(self):
        pr = self._profile()
        conduit = self.network.conduits[self.conduit_name]
        self.assertEqual(pr.us_node, conduit.us_node)
        self.assertEqual(pr.ds_node, conduit.ds_node)

    def test_face_inverts_lie_between_the_node_inverts(self):
        """Faces stop short of both ends, so every face invert is interior."""
        for name in self.network.conduit_index:
            pr = read_conduit_profile(_HDF_FIXTURE, self.network, name)
            lo, hi = sorted((pr.us_invert, pr.ds_invert))
            self.assertTrue(
                np.all(pr.invert >= lo - 0.01) and np.all(pr.invert <= hi + 0.01),
                f"{name}: face invert outside the US/DS invert range")

    def test_raises_key_error_for_unknown_conduit(self):
        with self.assertRaises(KeyError):
            read_conduit_profile(
                _HDF_FIXTURE, self.network, "__no_such_conduit__")

    def test_raises_key_error_for_unknown_network(self):
        with self.assertRaises(KeyError):
            read_conduit_profile(
                _HDF_FIXTURE, "__no_such_network__", self.conduit_name)

    def test_raises_value_error_for_unknown_timestamp(self):
        with self.assertRaises(ValueError):
            read_conduit_profile(
                _HDF_FIXTURE, self.network, self.conduit_name, '01JAN1900 00:00:00')


if __name__ == '__main__':
    unittest.main()
