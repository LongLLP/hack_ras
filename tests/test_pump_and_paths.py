"""
Tests for pump-station reading and pipe-network path tracing.

Routing and path profiles are exercised against the real .p##.hdf fixture, whose
pipe network happens to mirror the Hillside model's Armour Rd tail (a J317/J316
break between two runs). The fixture has no pump stations, so the pump reader is
exercised against a synthetic HDF built inline -- which also lets the column
mapping be tested with two stations of DIFFERENT width and column order, the
case that a positional reader would silently get wrong.
"""
import os
import tempfile
import unittest

try:
    import h5py
    import numpy as np
    from hack_ras.results.reader import (
        AmbiguousRoute,
        join_paths,
        list_pipe_networks,
        list_pump_stations,
        peak_flow_resolver,
        pump_stations_for_node,
        read_conduit_profile,
        read_node_points,
        read_path_profile,
        read_pipe_network,
        read_pump_curves,
        read_pump_station,
        pump_station_capacity,
        trace_path,
    )
    from hack_ras.results.model import (
        ConduitPath, PathProfile, PipeConduit, PipeNetwork, PumpCurve,
        PumpStation,
    )
    HAS_RESULTS = True
except ImportError:
    HAS_RESULTS = False

_HDF_FIXTURE = os.path.join(
    os.path.dirname(__file__), 'data',
    '2D culvert bridge levee precip pipes', 'Model.p02.hdf'
)
HAS_HDF_FIXTURE = os.path.exists(_HDF_FIXTURE)

_TS_DATES = ('Results/Unsteady/Output/Output Blocks/Base Output'
             '/Unsteady Time Series/Time Date Stamp')
_PUMP_TS = ('Results/Unsteady/Output/Output Blocks/Base Output'
            '/Unsteady Time Series/Pumping Stations')


def _fake_network(conduits, name='Net1'):
    """Build a PipeNetwork from a {conduit: (us, ds)} mapping, no HDF needed."""
    objs, index, upstream, downstream, nodes = {}, {}, {}, {}, {}
    for i, (cname, (us, ds)) in enumerate(conduits.items()):
        objs[cname] = PipeConduit(name=cname, us_node=us, ds_node=ds)
        index[cname] = i
        upstream.setdefault(ds, []).append(cname)
        downstream.setdefault(us, []).append(cname)
        nodes.setdefault(us, len(nodes))
        nodes.setdefault(ds, len(nodes))
    return PipeNetwork(name=name, nodes=nodes, conduits=objs,
                       conduit_index=index, upstream_of=upstream,
                       downstream_of=downstream)


def _make_pump_hdf(folder):
    """
    Minimal HDF with two pump stations of deliberately different shape.

    'Alpha' writes 7 columns with the per-group columns BEFORE the stage columns;
    'Beta' writes 5 columns in the conventional order. A reader that assumes a
    fixed column order will mis-label Alpha.
    """
    path = os.path.join(folder, 'pumps.p01.hdf')
    T = 4
    with h5py.File(path, 'w') as f:
        f.create_dataset(_TS_DATES, data=np.array(
            [b'01Jan2025 00:00:00', b'01Jan2025 01:00:00',
             b'01Jan2025 02:00:00', b'01Jan2025 03:00:00']))

        st_dtype = np.dtype([
            ('Name', 'S16'), ('Inlet Pipe Node', 'S24'),
            ('Outlet Pipe Node', 'S24'), ('Inlet SA/2D', 'S16'),
            ('Outlet SA/2D', 'S16'), ('Highest Pump Line Elevation', '<f4'),
            ('Pump Groups', '<i4')])
        stations = np.array([
            (b'Alpha', b'Base [J314]', b'', b'', b'Interior', 736.97, 2),
            (b'Beta', b'', b'', b'Interior', b'', 744.54, 1),
        ], dtype=st_dtype)
        f.create_dataset('Geometry/Pump Stations/Attributes', data=stations)

        g_dtype = np.dtype([('Pump Station ID', '<i4'), ('Name', 'S16'),
                            ('Pumps', '<i4')])
        groups = np.array([
            (0, b'GroupA1', 2),
            (0, b'GroupA2', 1),
            (1, b'GroupB1', 3),
        ], dtype=g_dtype)
        f.create_dataset('Geometry/Pump Stations/Pump Groups/Attributes',
                         data=groups)

        p_dtype = np.dtype([('Pump Group ID', '<i4'), ('Name', 'S16'),
                            ('WS On', '<f4'), ('WS Off', '<f4')])
        pumps = np.array([
            (0, b'A1-1', 727.3, 725.3),
            (0, b'A1-2', 728.3, 726.3),
            (1, b'A2-1', 729.3, 727.3),
            (2, b'B1-1', 731.0, 729.0),
            (2, b'B1-2', 732.0, 730.0),
            (2, b'B1-3', 733.0, 731.0),
        ], dtype=p_dtype)
        f.create_dataset('Geometry/Pump Stations/Pump Groups/Pumps/Attributes',
                         data=pumps)

        # Alpha: group columns first, then flow/stage -- unconventional on purpose
        alpha = np.zeros((T, 7), dtype=np.float32)
        alpha[:, 0] = [1, 2, 3, 4]        # GroupA1 cfs
        alpha[:, 1] = [0, 1, 2, 2]        # GroupA1 pumps on
        alpha[:, 2] = [5, 6, 7, 8]        # GroupA2 cfs
        alpha[:, 3] = [0, 0, 1, 1]        # GroupA2 pumps on
        alpha[:, 4] = [6, 8, 10, 12]      # Flow
        alpha[:, 5] = [726.0, 727.0, 728.0, 729.0]   # Stage HW
        alpha[:, 6] = [736.0, 736.5, 737.0, 737.5]   # Stage TW
        d = f.create_dataset(f'{_PUMP_TS}/Alpha/Structure Variables', data=alpha)
        d.attrs['Variable_Unit'] = np.array([
            [b'GroupA1', b'cfs'], [b'GroupA1', b'Pumps on'],
            [b'GroupA2', b'cfs'], [b'GroupA2', b'Pumps on'],
            [b'Flow', b'cfs'], [b'Stage HW', b'ft'], [b'Stage TW', b'ft']])

        beta = np.zeros((T, 5), dtype=np.float32)
        beta[:, 0] = [10, 20, 30, 40]     # Flow
        beta[:, 1] = [700.0, 701.0, 702.0, 703.0]
        beta[:, 2] = [750.0, 750.0, 750.0, 750.0]
        beta[:, 3] = [10, 20, 30, 40]     # GroupB1 cfs
        beta[:, 4] = [1, 2, 3, 3]         # GroupB1 pumps on
        d = f.create_dataset(f'{_PUMP_TS}/Beta/Structure Variables', data=beta)
        d.attrs['Variable_Unit'] = np.array([
            [b'Flow', b'cfs'], [b'Stage HW', b'ft'], [b'Stage TW', b'ft'],
            [b'GroupB1', b'cfs'], [b'GroupB1', b'Pumps on']])

        # Pump curves. Values are PER PUMP; GroupA1 has 2 pumps and GroupB1 has
        # 3, so the group capacities are 2x and 3x these numbers.
        curves = np.array([
            [0.0, 20.0], [10.0, 10.0],            # GroupA1, rows 0-1
            [0.0, 8.0], [10.0, 4.0],              # GroupA2, rows 2-3
            [2.0, 30.0], [12.0, 20.0],            # GroupB1, rows 4-5
        ], dtype=np.float32)
        f.create_dataset(
            'Geometry/Pump Stations/Pump Groups/Efficiency Curves Values',
            data=curves)
        f.create_dataset(
            'Geometry/Pump Stations/Pump Groups/Efficiency Curves Info',
            data=np.array([[0, 2], [2, 2], [4, 2]], dtype=np.int32))
    return path


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestPumpStationReader(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = _make_pump_hdf(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_stations_in_file_order(self):
        self.assertEqual(list_pump_stations(self.path), ['Alpha', 'Beta'])

    def test_returns_pump_station(self):
        self.assertIsInstance(read_pump_station(self.path, 'Alpha'), PumpStation)

    def test_columns_mapped_by_attribute_not_position(self):
        """The whole point: Alpha's Flow is column 4, not column 0."""
        a = read_pump_station(self.path, 'Alpha')
        np.testing.assert_allclose(a.flow, [6, 8, 10, 12])
        np.testing.assert_allclose(a.stage_hw, [726, 727, 728, 729])
        np.testing.assert_allclose(a.stage_tw, [736, 736.5, 737, 737.5])
        b = read_pump_station(self.path, 'Beta')
        np.testing.assert_allclose(b.flow, [10, 20, 30, 40])
        np.testing.assert_allclose(b.stage_hw, [700, 701, 702, 703])

    def test_groups_belong_to_the_right_station(self):
        a = read_pump_station(self.path, 'Alpha')
        b = read_pump_station(self.path, 'Beta')
        self.assertEqual([g.name for g in a.groups], ['GroupA1', 'GroupA2'])
        self.assertEqual([g.name for g in b.groups], ['GroupB1'])

    def test_group_series_and_pump_membership(self):
        a = read_pump_station(self.path, 'Alpha')
        g1, g2 = a.groups
        np.testing.assert_allclose(g1.flow, [1, 2, 3, 4])
        np.testing.assert_allclose(g1.pumps_on, [0, 1, 2, 2])
        np.testing.assert_allclose(g2.flow, [5, 6, 7, 8])
        self.assertEqual([p.name for p in g1.pumps], ['A1-1', 'A1-2'])
        self.assertEqual([p.name for p in g2.pumps], ['A2-1'])
        self.assertAlmostEqual(g1.pumps[0].ws_on, 727.3, places=3)
        self.assertAlmostEqual(g1.pumps[0].ws_off, 725.3, places=3)

    def test_pump_counts(self):
        a = read_pump_station(self.path, 'Alpha')
        self.assertEqual(a.n_pumps, 3)
        self.assertEqual([g.n_pumps for g in a.groups], [2, 1])
        np.testing.assert_allclose(a.pumps_on, [0, 1, 3, 3])

    def test_inlet_node_is_split_into_network_and_node(self):
        a = read_pump_station(self.path, 'Alpha')
        self.assertEqual(a.inlet_network, 'Base')
        self.assertEqual(a.inlet_node, 'J314')
        self.assertEqual(a.outlet_area, 'Interior')
        self.assertIsNone(a.outlet_node)

    def test_station_tied_to_an_area_has_no_pipe_node(self):
        b = read_pump_station(self.path, 'Beta')
        self.assertIsNone(b.inlet_node)
        self.assertIsNone(b.inlet_network)
        self.assertEqual(b.inlet_area, 'Interior')

    def test_highest_pump_line_elevation(self):
        self.assertAlmostEqual(
            read_pump_station(self.path, 'Alpha').highest_pump_line_elev,
            736.97, places=2)

    def test_lookup_by_node(self):
        self.assertEqual(pump_stations_for_node(self.path, 'J314'), ['Alpha'])
        self.assertEqual(pump_stations_for_node(self.path, 'J314', 'Base'),
                         ['Alpha'])
        self.assertEqual(pump_stations_for_node(self.path, 'J314', 'Other'), [])
        self.assertEqual(pump_stations_for_node(self.path, '__none__'), [])

    def test_raises_key_error_for_unknown_station(self):
        with self.assertRaises(KeyError):
            read_pump_station(self.path, '__nope__')


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestPumpStationAbsent(unittest.TestCase):
    """A gravity-flow geometry has no pump stations at all; that is not an error."""

    def test_list_is_empty(self):
        self.assertEqual(list_pump_stations(_HDF_FIXTURE), [])

    def test_lookup_by_node_is_empty(self):
        self.assertEqual(pump_stations_for_node(_HDF_FIXTURE, 'J314'), [])

    def test_read_raises_key_error(self):
        with self.assertRaises(KeyError):
            read_pump_station(_HDF_FIXTURE, 'Anything')


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestTracePathForks(unittest.TestCase):
    """
    Fork handling, on synthetic networks so the topology is explicit.

        A -> B -> C -> E        (via D on the long branch)
        B -> D -> E
        C -> X                  (dead end, must be discarded)
    """

    def setUp(self):
        self.net = _fake_network({
            'c_ab': ('A', 'B'),
            'c_bc': ('B', 'C'),
            'c_ce': ('C', 'E'),
            'c_bd': ('B', 'D'),
            'c_de': ('D', 'E'),
            'c_cx': ('C', 'X'),
        })

    def test_branch_that_cannot_reach_the_target_is_discarded(self):
        """C forks to E and X, but only E reaches the destination -- no ambiguity."""
        path = trace_path(self.net, 'C', 'E')
        self.assertEqual(path.conduits, ['c_ce'])

    def test_genuine_fork_raises_without_a_resolver(self):
        with self.assertRaises(AmbiguousRoute) as cm:
            trace_path(self.net, 'A', 'E')
        msg = str(cm.exception)
        self.assertIn('c_bc', msg)
        self.assertIn('c_bd', msg)

    def test_ambiguous_route_is_a_value_error(self):
        self.assertTrue(issubclass(AmbiguousRoute, ValueError))

    def test_via_pins_the_route_without_needing_results(self):
        self.assertEqual(trace_path(self.net, 'A', 'E', via=['D']).conduits,
                         ['c_ab', 'c_bd', 'c_de'])
        self.assertEqual(trace_path(self.net, 'A', 'E', via=['C']).conduits,
                         ['c_ab', 'c_bc', 'c_ce'])

    def test_resolver_is_used_and_recorded(self):
        def resolver(node, cands):
            chosen = sorted(cands)[-1]
            return chosen, f'{node}: picked {chosen}'
        path = trace_path(self.net, 'A', 'E', resolver=resolver)
        self.assertEqual(path.conduits, ['c_ab', 'c_bd', 'c_de'])
        self.assertEqual(len(path.forks), 1)
        self.assertIn('picked c_bd', path.forks[0])

    def test_unknown_nodes_raise(self):
        for args in (('__no__', 'E'), ('A', '__no__')):
            with self.assertRaises(ValueError):
                trace_path(self.net, *args)
        with self.assertRaises(ValueError):
            trace_path(self.net, 'A', 'E', via=['__no__'])

    def test_unreachable_destination_raises(self):
        with self.assertRaises(ValueError):
            trace_path(self.net, 'E', 'A')

    def test_via_in_the_wrong_order_raises(self):
        with self.assertRaises(ValueError):
            trace_path(self.net, 'A', 'E', via=['E', 'D'])

    def test_path_nodes_and_helpers(self):
        path = trace_path(self.net, 'A', 'E', via=['D'])
        self.assertEqual(path.nodes, ['A', 'B', 'D', 'E'])
        self.assertEqual(len(path), 3)
        self.assertEqual(path.start, 'A')
        self.assertEqual(path.end, 'E')
        self.assertEqual(path.segments, [('A', 'E')])
        self.assertEqual(path.bridges, [])


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestPathsOnRealFixture(unittest.TestCase):

    def setUp(self):
        self.net = read_pipe_network(_HDF_FIXTURE,
                                     list_pipe_networks(_HDF_FIXTURE)[0])

    def test_trace_multi_hop_chain(self):
        path = trace_path(self.net, 'J321', 'J317')
        self.assertEqual(path.conduits, ['C326', 'C292', 'C296', 'C232'])
        self.assertEqual(path.nodes,
                         ['J321', 'J287', 'J288', 'J322', 'J317'])

    def test_read_node_points_covers_every_node(self):
        pts = read_node_points(_HDF_FIXTURE)
        for node in self.net.nodes:
            self.assertIn(node, pts)
            self.assertEqual(len(pts[node]), 2)

    def test_join_paths_bridges_with_true_distance(self):
        """Same shape as the Hillside pond break: J317 -> J316 has no conduit."""
        pts = read_node_points(_HDF_FIXTURE)
        upper = trace_path(self.net, 'J322', 'J317')
        lower = trace_path(self.net, 'J316', 'J314')
        joined = join_paths([upper, lower], pts)
        self.assertEqual(joined.conduits, ['C232', 'C179'])
        self.assertEqual(len(joined.bridges), 1)
        a, b, gap = joined.bridges[0]
        self.assertEqual((a, b), ('J317', 'J316'))
        expected = float(np.hypot(pts['J317'][0] - pts['J316'][0],
                                  pts['J317'][1] - pts['J316'][1]))
        self.assertAlmostEqual(gap, expected, places=6)
        self.assertGreater(gap, 0.0)

    def test_join_paths_rejects_bad_input(self):
        pts = read_node_points(_HDF_FIXTURE)
        with self.assertRaises(ValueError):
            join_paths([], pts)
        other = ConduitPath(network='__other__', conduits=[], nodes=['J1'])
        with self.assertRaises(ValueError):
            join_paths([trace_path(self.net, 'J316', 'J314'), other], pts)

    def test_path_profile_matches_concatenated_conduit_profiles(self):
        path = trace_path(self.net, 'J321', 'J317')
        prof = read_path_profile(_HDF_FIXTURE, self.net, path, 'Maximum')
        self.assertIsInstance(prof, PathProfile)
        expected, offset = [], 0.0
        for c in path.conduits:
            cp = read_conduit_profile(_HDF_FIXTURE, self.net, c, 'Maximum')
            expected.extend(offset + cp.station)
            offset += cp.length
        np.testing.assert_allclose(prof.station, expected)
        self.assertAlmostEqual(prof.total_length, offset, places=6)

    def test_path_profile_station_is_strictly_increasing(self):
        path = trace_path(self.net, 'J321', 'J317')
        prof = read_path_profile(_HDF_FIXTURE, self.net, path, 'Maximum')
        self.assertTrue(np.all(np.diff(prof.station) > 0))

    def test_path_profile_bridge_advances_the_station(self):
        pts = read_node_points(_HDF_FIXTURE)
        upper = trace_path(self.net, 'J322', 'J317')
        lower = trace_path(self.net, 'J316', 'J314')
        joined = join_paths([upper, lower], pts)
        gap = joined.bridges[0][2]
        with_bridge = read_path_profile(_HDF_FIXTURE, self.net, joined, 'Maximum')
        c232 = read_conduit_profile(_HDF_FIXTURE, self.net, 'C232', 'Maximum')
        c179 = read_conduit_profile(_HDF_FIXTURE, self.net, 'C179', 'Maximum')
        self.assertAlmostEqual(with_bridge.total_length,
                               c232.length + gap + c179.length, places=5)
        # the first C179 face must sit past C232's length plus the gap
        first_c179 = with_bridge.station[len(c232.station)]
        self.assertAlmostEqual(first_c179,
                               c232.length + gap + c179.station[0], places=5)

    def test_path_profile_arrays_are_consistent(self):
        path = trace_path(self.net, 'J321', 'J317')
        prof = read_path_profile(_HDF_FIXTURE, self.net, path, 'Maximum')
        F = len(prof.station)
        for arr in (prof.invert, prof.crown, prof.wse, prof.velocity,
                    prof.flow, prof.conduit_of):
            self.assertEqual(len(arr), F)
        np.testing.assert_allclose(prof.depth, prof.wse - prof.invert)
        self.assertEqual(prof.is_surcharged.dtype, np.bool_)
        g = 9.80665 if prof.si_units else 32.174
        np.testing.assert_allclose(prof.energy_grade - prof.wse,
                                   prof.velocity ** 2 / (2.0 * g))
        self.assertEqual(set(prof.conduit_of), set(path.conduits))

    def test_path_profile_labels_the_end_nodes(self):
        path = trace_path(self.net, 'J321', 'J317')
        prof = read_path_profile(_HDF_FIXTURE, self.net, path, 'Maximum')
        self.assertEqual(prof.node_at[0], 'J321')
        self.assertEqual(prof.node_at[len(prof.station) - 1], 'J317')

    def test_path_profile_rejects_a_foreign_network(self):
        path = trace_path(self.net, 'J321', 'J317')
        foreign = ConduitPath(network='__other__', conduits=path.conduits,
                              nodes=path.nodes)
        with self.assertRaises(ValueError):
            read_path_profile(_HDF_FIXTURE, self.net, foreign, 'Maximum')

    def test_path_profile_rejects_conduits_absent_from_this_plan(self):
        """
        Guards the gravity-vs-pumped mix-up: a pumped geometry drops the outfall
        conduits, so a path traced on gravity geometry must not read silently.
        """
        bogus = ConduitPath(network=self.net.name,
                            conduits=['C326', '__not_here__'],
                            nodes=['J321', 'J287', '?'])
        with self.assertRaises(ValueError) as cm:
            read_path_profile(_HDF_FIXTURE, self.net, bogus, 'Maximum')
        self.assertIn('__not_here__', str(cm.exception))

    def test_peak_flow_resolver_returns_choice_and_note(self):
        resolver = peak_flow_resolver(_HDF_FIXTURE, self.net)
        chosen, note = resolver('J288', ['C296'])
        self.assertEqual(chosen, 'C296')
        self.assertIn('C296', note)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
class TestPumpCurves(unittest.TestCase):
    """
    Pump curves and head-dependent station capacity.

    The critical property is that the tabulated flow is PER PUMP: a group of
    three pumps sharing one curve delivers three times it. That was established
    empirically -- on a real model `group flow / curve(head)` equals the
    `pumps_on` count exactly (1.00, 2.00, 3.00, no scatter) -- and summing curves
    without the multiplier understates a multi-pump station threefold.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = _make_pump_hdf(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_a_curve_per_group_of_that_station(self):
        a = read_pump_curves(self.path, 'Alpha')
        b = read_pump_curves(self.path, 'Beta')
        self.assertEqual(sorted(a), ['GroupA1', 'GroupA2'])
        self.assertEqual(sorted(b), ['GroupB1'])
        self.assertIsInstance(a['GroupA1'], PumpCurve)

    def test_curve_values_and_orientation(self):
        c = read_pump_curves(self.path, 'Alpha')['GroupA1']
        np.testing.assert_allclose(c.head, [0.0, 10.0])
        np.testing.assert_allclose(c.flow, [20.0, 10.0])
        self.assertTrue(np.all(np.diff(c.head) > 0))
        self.assertTrue(np.all(np.diff(c.flow) < 0))

    def test_curve_carries_the_pump_count(self):
        a = read_pump_curves(self.path, 'Alpha')
        b = read_pump_curves(self.path, 'Beta')
        self.assertEqual(a['GroupA1'].n_pumps, 2)
        self.assertEqual(a['GroupA2'].n_pumps, 1)
        self.assertEqual(b['GroupB1'].n_pumps, 3)

    def test_capacity_is_per_pump_and_interpolates(self):
        c = read_pump_curves(self.path, 'Alpha')['GroupA1']
        self.assertAlmostEqual(float(c.capacity(0.0)), 20.0)
        self.assertAlmostEqual(float(c.capacity(5.0)), 15.0)
        self.assertAlmostEqual(float(c.capacity(10.0)), 10.0)

    def test_group_capacity_applies_the_pump_count(self):
        c = read_pump_curves(self.path, 'Alpha')['GroupA1']
        self.assertAlmostEqual(float(c.group_capacity(5.0)), 30.0)   # 2 x 15
        b = read_pump_curves(self.path, 'Beta')['GroupB1']
        self.assertAlmostEqual(float(b.group_capacity(2.0)), 90.0)   # 3 x 30

    def test_capacity_clamps_rather_than_extrapolating(self):
        c = read_pump_curves(self.path, 'Alpha')['GroupA1']
        self.assertAlmostEqual(float(c.capacity(-50.0)), 20.0)
        self.assertAlmostEqual(float(c.capacity(999.0)), 10.0)

    def test_in_range_flags_clamped_heads(self):
        c = read_pump_curves(self.path, 'Alpha')['GroupA1']
        np.testing.assert_array_equal(
            c.in_range([-1.0, 0.0, 5.0, 10.0, 11.0]),
            [False, True, True, True, False])

    def test_station_capacity_sums_group_capacities(self):
        curves = read_pump_curves(self.path, 'Alpha')
        cap, clamped = pump_station_capacity(curves, 0.0)
        # GroupA1 2 x 20 = 40, GroupA2 1 x 8 = 8
        self.assertAlmostEqual(float(cap[0]), 48.0)
        self.assertEqual(clamped, 0)

    def test_station_capacity_counts_clamped_heads(self):
        curves = read_pump_curves(self.path, 'Beta')     # curve covers 2..12
        cap, clamped = pump_station_capacity(curves, [0.0, 2.0, 12.0, 20.0])
        self.assertEqual(clamped, 2)
        self.assertAlmostEqual(float(cap[0]), 90.0)      # clamped to head 2
        self.assertAlmostEqual(float(cap[-1]), 60.0)     # clamped to head 12

    def test_station_capacity_shape_follows_head(self):
        curves = read_pump_curves(self.path, 'Alpha')
        cap, _ = pump_station_capacity(curves, [0.0, 5.0, 10.0])
        self.assertEqual(cap.shape, (3,))
        np.testing.assert_allclose(cap, [48.0, 36.0, 24.0])

    def test_station_capacity_rejects_empty_curves(self):
        with self.assertRaises(ValueError):
            pump_station_capacity({}, 0.0)

    def test_unknown_station_raises_key_error(self):
        with self.assertRaises(KeyError):
            read_pump_curves(self.path, '__nope__')

    def test_curves_and_groups_agree_on_names(self):
        """A curve must exist for every group the results reader reports."""
        for station in ('Alpha', 'Beta'):
            ps = read_pump_station(self.path, station)
            curves = read_pump_curves(self.path, station)
            self.assertEqual(sorted(g.name for g in ps.groups), sorted(curves))
            for g in ps.groups:
                self.assertEqual(g.n_pumps, curves[g.name].n_pumps)


@unittest.skipUnless(HAS_RESULTS, "hack_ras[results] extras not installed")
@unittest.skipUnless(HAS_HDF_FIXTURE, "no .p##.hdf fixture at tests/data/")
class TestPumpCurvesAbsent(unittest.TestCase):

    def test_gravity_geometry_raises_key_error(self):
        with self.assertRaises(KeyError):
            read_pump_curves(_HDF_FIXTURE, 'Anything')


if __name__ == '__main__':
    unittest.main()
