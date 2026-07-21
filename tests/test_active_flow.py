"""
Tests for hack_ras.geometry.active_flow

These are hermetic: they build tiny station-elevation profiles and IneffArea
objects directly, so no HEC-RAS files are required.
"""
import unittest

from hack_ras.geometry.active_flow import (
    active_flow_segments,
    subtract_intervals,
    wetted_segments,
)
from hack_ras.geometry.model import (
    BlockObstructArea,
    BlockedObstructions,
    IneffArea,
    IneffFlowAreas,
    Levee,
)


def _normal_ifa(left_end, right_start, elev_left=None, elev_right=None,
                perm_left=False, perm_right=False):
    """A 'normal' IFA with a left area [0.0, left_end] and right [right_start, 0.0]."""
    return IneffFlowAreas(
        ifa_type="normal",
        areas=[
            IneffArea(start_sta=0.0, end_sta=left_end,
                      elevation=elev_left, permanent=perm_left),
            IneffArea(start_sta=right_start, end_sta=0.0,
                      elevation=elev_right, permanent=perm_right),
        ],
    )


def _round(segs, nd=3):
    return [(round(a, nd), round(b, nd)) for a, b in segs]


class WettedSegmentsTests(unittest.TestCase):
    def test_simple_v_channel(self):
        # V from (0,10) down to (10,0) up to (20,10); wse=5 -> wet [5, 15]
        stae = [(0, 10), (10, 0), (20, 10)]
        self.assertEqual(_round(wetted_segments(stae, 5.0)), [(5.0, 15.0)])

    def test_disconnected_flow(self):
        # Two low pockets separated by a high ridge above the wse.
        stae = [(0, 10), (5, 0), (10, 10), (15, 0), (20, 10)]
        segs = wetted_segments(stae, 5.0)
        self.assertEqual(_round(segs), [(2.5, 7.5), (12.5, 17.5)])

    def test_nonzero_start_station(self):
        # Same V but stationing starts at 100 (not zero).
        stae = [(100, 10), (110, 0), (120, 10)]
        self.assertEqual(_round(wetted_segments(stae, 5.0)), [(105.0, 115.0)])

    def test_dry_section(self):
        stae = [(0, 10), (10, 8), (20, 10)]
        self.assertEqual(wetted_segments(stae, 5.0), [])

    def test_endpoints_below_water(self):
        # First and last vertices already below the wse.
        stae = [(0, 0), (10, 0)]
        self.assertEqual(_round(wetted_segments(stae, 5.0)), [(0.0, 10.0)])


class SubtractIntervalsTests(unittest.TestCase):
    def test_middle_removed(self):
        self.assertEqual(subtract_intervals([(0, 10)], [(3, 6)]), [(0, 3), (6, 10)])

    def test_no_overlap(self):
        self.assertEqual(subtract_intervals([(0, 10)], [(20, 30)]), [(0, 10)])

    def test_full_cover(self):
        self.assertEqual(subtract_intervals([(2, 8)], [(0, 10)]), [])

    def test_slivers_dropped(self):
        # A blocker that leaves a sub-epsilon remainder on the left.
        out = subtract_intervals([(0, 10)], [(1e-9, 10)])
        self.assertEqual(out, [])

    def test_multiple_blockers(self):
        self.assertEqual(
            subtract_intervals([(0, 10)], [(1, 2), (5, 6)]),
            [(0, 1), (2, 5), (6, 10)],
        )


class ActiveFlowSegmentsTests(unittest.TestCase):
    def test_no_ineff_equals_wetted(self):
        stae = [(0, 10), (10, 0), (20, 10)]
        self.assertEqual(_round(active_flow_segments(stae, 5.0)), [(5.0, 15.0)])

    def test_ineff_clips_both_sides(self):
        # Wide flat channel wet [1,19]; blank IFAs block [0,5] and [15,20].
        stae = [(0, 10), (1, 0), (19, 0), (20, 10)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0)   # elevation None -> always block
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(5.0, 15.0)])

    def test_ground_clips_within_gap(self):
        # IFA gap is [5,15] but water only reaches [8,12] on the ground -> ground wins.
        stae = [(0, 20), (5, 20), (8, 0), (12, 0), (15, 20), (20, 20)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0)
        # wet segment is [7.25, 12.75]; gap [5,15] doesn't cut it further
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(7.25, 12.75)])

    def test_blank_ineff_never_overtops(self):
        # Blank (None) elevation blocks even when deeply submerged.
        stae = [(0, 0), (20, 0)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0)  # None elevation
        self.assertEqual(_round(active_flow_segments(stae, 100.0, ineff=ineff)),
                         [(5.0, 15.0)])

    def test_overtopped_ineff_conveys(self):
        # Right IFA has a real trigger elevation of 4; wse=5 > 4 -> it conveys.
        stae = [(0, 0), (20, 0)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0,
                            elev_left=None, elev_right=4.0)
        # Left blank IFA still blocks [0,5]; right IFA overtopped -> active to end.
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(5.0, 20.0)])

    def test_not_overtopped_when_wse_at_elevation(self):
        # wse == elevation is NOT overtopping (strict >), so it still blocks.
        stae = [(0, 0), (20, 0)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0,
                            elev_left=None, elev_right=5.0)
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(5.0, 15.0)])

    def test_permanent_overtopped_also_conveys_for_top_width(self):
        # A permanent IFA that is overtopped still contributes its surface width.
        stae = [(0, 0), (20, 0)]
        ineff = _normal_ifa(left_end=5.0, right_start=15.0,
                            elev_left=None, elev_right=4.0, perm_right=True)
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(5.0, 20.0)])

    def test_blank_normal_ifa_skipped(self):
        # A normal IFA area with both stations 0.0 carries no info and is ignored.
        ineff = IneffFlowAreas(
            ifa_type="normal",
            areas=[IneffArea(0.0, 0.0, None, False),
                   IneffArea(15.0, 0.0, None, False)],
        )
        stae = [(0, 0), (20, 0)]
        # Only the right IFA [15,20] blocks.
        self.assertEqual(_round(active_flow_segments(stae, 5.0, ineff=ineff)),
                         [(0.0, 15.0)])

    def test_none_features_no_effect(self):
        # Explicit None for every feature equals the no-feature result.
        stae = [(0, 10), (10, 0), (20, 10)]
        self.assertEqual(
            _round(active_flow_segments(stae, 5.0, ineff=None, levee=None,
                                        blocked_obstructions=None)),
            [(5.0, 15.0)],
        )

    def test_dry_returns_empty(self):
        stae = [(0, 10), (10, 8), (20, 10)]
        self.assertEqual(active_flow_segments(stae, 5.0), [])


# Flat channel, fully wetted [0, 20] at wse=5 -- keeps levee/obstruction tests simple.
_FLAT = [(0, 0), (20, 0)]


class LeveeTests(unittest.TestCase):
    def test_left_levee_not_overtopped_clips(self):
        lev = Levee(left_sta=5.0, left_elev=None)          # blank elev -> never overtops
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, levee=lev)),
                         [(5.0, 20.0)])

    def test_left_levee_overtopped_no_effect(self):
        lev = Levee(left_sta=5.0, left_elev=4.0)           # wse 5 > 4 -> overtopped
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, levee=lev)),
                         [(0.0, 20.0)])

    def test_right_levee_not_overtopped_clips(self):
        lev = Levee(right_sta=15.0, right_elev=6.0)        # wse 5 <= 6 -> blocks
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, levee=lev)),
                         [(0.0, 15.0)])

    def test_both_levees(self):
        lev = Levee(left_sta=5.0, left_elev=6.0, right_sta=15.0, right_elev=6.0)
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, levee=lev)),
                         [(5.0, 15.0)])

    def test_levee_behind_ineff_is_redundant(self):
        # Left IFA blocks [0,8]; left levee at 5 is inboard-redundant -> IFA binds.
        ineff = _normal_ifa(left_end=8.0, right_start=100.0)   # right area off-section
        lev = Levee(left_sta=5.0, left_elev=None)
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, ineff=ineff, levee=lev)),
                         [(8.0, 20.0)])


class BlockedObstructionTests(unittest.TestCase):
    def _multi(self, *areas):
        return BlockedObstructions(obstr_type="multiple_block",
                                   areas=[BlockObstructArea(*a) for a in areas])

    def test_obstruction_not_submerged_splits_flow(self):
        obstr = self._multi((8.0, 12.0, None))             # blank elev -> always blocks
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, blocked_obstructions=obstr)),
                         [(0.0, 8.0), (12.0, 20.0)])       # disconnected active flow

    def test_obstruction_submerged_no_effect(self):
        obstr = self._multi((8.0, 12.0, 4.0))              # wse 5 > 4 -> submerged
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, blocked_obstructions=obstr)),
                         [(0.0, 20.0)])

    def test_normal_obstruction_right_sentinel(self):
        # Normal: left area blank (skip), right area [10, 0->max] blocks to the edge.
        obstr = BlockedObstructions(obstr_type="normal", areas=[
            BlockObstructArea(0.0, 0.0, None),             # blank left -> skipped
            BlockObstructArea(10.0, 0.0, None),            # right: 0.0 end -> max_sta (20)
        ])
        self.assertEqual(_round(active_flow_segments(_FLAT, 5.0, blocked_obstructions=obstr)),
                         [(0.0, 10.0)])


class MultipleBlockSentinelTests(unittest.TestCase):
    """The 0.0 edge sentinel must apply ONLY to 'normal' type, not 'multiple_block'."""

    # XS with a NEGATIVE starting station -> min_sta = -50, so a literal 0.0 is
    # distinct from the edge and exposes the sentinel bug.
    STAE = [(-50.0, 0.0), (200.0, 0.0)]

    def test_multiple_block_station_is_literal(self):
        ineff = IneffFlowAreas(ifa_type="multiple_block",
                               areas=[IneffArea(0.0, 60.0, None, False)])
        # Literal block [0,60] -> active is [-50,0] and [60,200].
        self.assertEqual(_round(active_flow_segments(self.STAE, 5.0, ineff=ineff)),
                         [(-50.0, 0.0), (60.0, 200.0)])

    def test_normal_still_resolves_sentinel(self):
        # Same 0.0 start, but 'normal' -> resolves to the left edge (-50).
        ineff = IneffFlowAreas(ifa_type="normal", areas=[
            IneffArea(0.0, 60.0, None, False),             # left: 0.0 -> min_sta (-50)
            IneffArea(0.0, 0.0, None, False),              # blank right -> skipped
        ])
        self.assertEqual(_round(active_flow_segments(self.STAE, 5.0, ineff=ineff)),
                         [(60.0, 200.0)])


if __name__ == "__main__":
    unittest.main()
