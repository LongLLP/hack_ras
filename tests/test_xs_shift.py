import logging
import math
from pathlib import Path

import pytest

from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.writer import GeometryWriter
from hack_ras.geometry.shift import (
    _normalize_rs,
    build_translation_dict,
    shift_polyline,
    shift_xs_cutlines,
)

BAXTER = Path(__file__).parent / "data" / "Baxter" / "Baxter.g02"

_RIVER = "Baxter River"
_REACH = "Upper Reach"
# Normalized RS for the first XS with a cut line (stored as "84816." in the file)
_RS = "84816"
_TRANS = {("baxter river", "upper reach", _RS): 10.0}


def _parse():
    return GeometryParser().parse_file(str(BAXTER))


def _arc_len(pts):
    return sum(
        math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        for i in range(1, len(pts))
    )


# ---------------------------------------------------------------------------
# shift_polyline — unit tests
# ---------------------------------------------------------------------------

def test_shift_straight_line_preserves_length():
    pts = [(0.0, 0.0), (100.0, 0.0)]
    result = shift_polyline(pts, 10.0)
    assert abs(_arc_len(result) - _arc_len(pts)) < 1e-6


def test_shift_straight_line_new_start():
    pts = [(0.0, 0.0), (100.0, 0.0)]
    result = shift_polyline(pts, 10.0)
    assert abs(result[0][0] - 10.0) < 1e-6
    assert abs(result[0][1]) < 1e-6


def test_shift_zero_returns_original():
    pts = [(0.0, 0.0), (50.0, 0.0), (100.0, 10.0)]
    result = shift_polyline(pts, 0.0)
    assert result == pts


def test_shift_negative_retreats_start():
    pts = [(0.0, 0.0), (100.0, 0.0)]
    result = shift_polyline(pts, -10.0)
    assert abs(result[0][0] - (-10.0)) < 1e-6
    assert abs(_arc_len(result) - _arc_len(pts)) < 1e-6


def test_shift_right_angle_past_vertex():
    # (0,0) -> (50,0) -> (50,50), total length = 100
    pts = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0)]
    result = shift_polyline(pts, 60.0)
    assert abs(_arc_len(result) - 100.0) < 1e-6
    # New start: 10 units past corner at (50, 0), i.e. (50, 10)
    assert abs(result[0][0] - 50.0) < 1e-6
    assert abs(result[0][1] - 10.0) < 1e-6


def test_shift_exceeds_total_length():
    pts = [(0.0, 0.0), (100.0, 0.0)]
    result = shift_polyline(pts, 150.0)
    assert len(result) == 2
    assert abs(_arc_len(result) - 100.0) < 1e-6
    assert result[0][0] > 100.0
    assert result[1][0] > result[0][0]


# ---------------------------------------------------------------------------
# _normalize_rs — unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (100.5, "100.5"),
    (100, "100"),
    ("100.50", "100.5"),
    ("1,234", "1234"),
    ("100*", "100"),
    ("100.00*", "100"),
    (None, None),
    ("5.99", "5.99"),
    ("84816.", "84816"),
])
def test_normalize_rs(value, expected):
    assert _normalize_rs(value) == expected


# ---------------------------------------------------------------------------
# build_translation_dict — unit tests
# ---------------------------------------------------------------------------

def _make_df(river, reach, rs, translation):
    import pandas as pd
    return pd.DataFrame({
        "River": [river],
        "Reach": [reach],
        "River Station": [rs],
        "Translation": [translation],
    })


def test_build_translation_dict_basic():
    df = _make_df("My River", "My Reach", 100, 5.0)
    d = build_translation_dict(df)
    assert ("my river", "my reach", "100") in d
    assert d[("my river", "my reach", "100")] == 5.0


def test_build_translation_dict_missing_column():
    import pandas as pd
    df = pd.DataFrame({"River": ["R"], "Reach": ["Re"], "Translation": [1.0]})
    with pytest.raises(ValueError, match="missing required columns"):
        build_translation_dict(df)


def test_build_translation_dict_duplicate_warns(caplog):
    import pandas as pd
    df = pd.DataFrame({
        "River": ["R", "R"],
        "Reach": ["Re", "Re"],
        "River Station": [100, 100],
        "Translation": [1.0, 2.0],
    })
    with caplog.at_level(logging.WARNING):
        d = build_translation_dict(df)
    assert d[("r", "re", "100")] == 2.0
    assert any("Duplicate" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# shift_xs_cutlines — integration tests using Baxter.g02
# ---------------------------------------------------------------------------

def test_shift_xs_cutlines_basic():
    geom = _parse()
    result = shift_xs_cutlines(geom, _TRANS)

    orig_xs = geom.rivers[_RIVER].reaches[_REACH].cross_sections[0]
    new_xs = result.rivers[_RIVER].reaches[_REACH].cross_sections[0]

    assert new_xs.cutline is not None
    # Start point must have moved
    assert new_xs.cutline.points[0] != orig_xs.cutline.points[0]
    # Arc length must be preserved
    assert abs(_arc_len(new_xs.cutline.points) - _arc_len(orig_xs.cutline.points)) < 1e-3


def test_shift_xs_cutlines_preserves_unshifted():
    geom = _parse()
    result = shift_xs_cutlines(geom, _TRANS)

    # First coordinate line of the unshifted RS 84000 block must pass through unchanged
    unshifted_coord = (
        "      6451085.83      2049558.96      6450742.65       2049935.6\n"
    )
    assert unshifted_coord in result.raw_lines


def test_shift_xs_cutlines_gis_line_count():
    geom = _parse()
    result = shift_xs_cutlines(geom, _TRANS)

    orig_count = sum(1 for l in geom.raw_lines if l.startswith("XS GIS Cut Line="))
    result_count = sum(1 for l in result.raw_lines if l.startswith("XS GIS Cut Line="))
    assert orig_count == result_count


def test_shift_xs_cutlines_new_title():
    geom = _parse()
    result = shift_xs_cutlines(geom, {}, new_title="Modified")
    assert result.title == "Modified"
    assert any(l.startswith("Geom Title=Modified") for l in result.raw_lines)


def test_shift_xs_cutlines_does_not_mutate_original():
    geom = _parse()
    orig_lines = list(geom.raw_lines)
    shift_xs_cutlines(geom, _TRANS)
    assert geom.raw_lines == orig_lines


def test_shift_xs_cutlines_unmatched_warns(caplog):
    geom = _parse()
    bad_trans = {("baxter river", "upper reach", "99999"): 5.0}
    with caplog.at_level(logging.WARNING):
        shift_xs_cutlines(geom, bad_trans)
    assert any("not matched" in r.message for r in caplog.records)


def test_shift_xs_cutlines_roundtrip(tmp_path):
    geom = _parse()
    result = shift_xs_cutlines(geom, _TRANS)

    out_path = tmp_path / "shifted.g02"
    GeometryWriter().write(result, str(out_path))

    re_parsed = GeometryParser().parse_file(str(out_path))
    assert re_parsed.title == geom.title

    orig_xs = geom.rivers[_RIVER].reaches[_REACH].cross_sections[0]
    new_xs = re_parsed.rivers[_RIVER].reaches[_REACH].cross_sections[0]
    assert new_xs.cutline is not None
    assert new_xs.cutline.points[0] != orig_xs.cutline.points[0]
    assert abs(_arc_len(new_xs.cutline.points) - _arc_len(orig_xs.cutline.points)) < 1e-3


# ---------------------------------------------------------------------------
# Cut line writer — regression tests for the 65-char wrap bug, using the
# HEC-RAS-authored stress-test fixture (all values entered in the RAS GUI)
# ---------------------------------------------------------------------------

STRESS = (
    Path(__file__).parent / "data" / "XSCutLines stress test"
    / "XSCut_stress_test.g01"
)

_STRESS_RIVER = "FakeRiver"
_STRESS_REACH = "FakeReach"


def _parse_stress():
    return GeometryParser().parse_file(str(STRESS))


def test_parse_organic_packed_cutline():
    # The RS 3000 block has data lines where full-width values touch with no
    # separating whitespace — readable only as fixed 16-char columns.
    geom = _parse_stress()
    xs3000, xs2000, xs1000 = (
        geom.rivers[_STRESS_RIVER].reaches[_STRESS_REACH].cross_sections
    )
    assert [xs.cutline.n_points for xs in (xs3000, xs2000, xs1000)] == [11, 9, 12]
    assert xs3000.cutline.points[4] == (1675511.41439722, 5603765.76142893)
    assert xs1000.cutline.points[0] == (99.8812, 71.4451)


def test_write_cutline_reproduces_organic_bytes():
    # write_cutline must reproduce every RAS-authored cut line block
    # byte-for-byte, including the fully packed high-precision lines.
    from hack_ras.geometry.blocks.xs_gis import parse_cutline, write_cutline

    geom = _parse_stress()
    raw = geom.raw_lines
    checked = 0
    for i, line in enumerate(raw):
        if line.startswith("XS GIS Cut Line="):
            cl, consumed = parse_cutline(raw, i)
            assert write_cutline(cl) == raw[i : i + consumed]
            checked += 1
    assert checked == 3


def test_write_cutline_never_splits_fields():
    # The old 65-char flat wrap split digits across line breaks for cut lines
    # with 7+ points; parse_cutline could not read that output back.
    from hack_ras.geometry.blocks.xs_gis import parse_cutline, write_cutline
    from hack_ras.geometry.model import XSGISCutLine

    pts = [(6451252.62 + i * 10.0, 2049658.46 + i * 7.0) for i in range(8)]
    block = write_cutline(XSGISCutLine(len(pts), pts))
    assert all(len(l.rstrip("\n")) % 16 == 0 for l in block[1:])
    cl, consumed = parse_cutline(block, 0)
    assert consumed == len(block)
    assert cl.points == pts


def test_shift_stress_cutlines_roundtrip(tmp_path):
    # Shift all three stress-test XS (7-digit, 5-digit, and 2-digit
    # coordinates; 11, 9, and 12 points) and re-parse the written file.
    geom = _parse_stress()
    trans = {
        ("fakeriver", "fakereach", "3000"): 25.0,
        ("fakeriver", "fakereach", "2000"): 25.0,
        ("fakeriver", "fakereach", "1000"): 5.0,
    }
    result = shift_xs_cutlines(geom, trans)
    out_path = tmp_path / "shifted.g01"
    GeometryWriter().write(result, str(out_path))
    re_parsed = GeometryParser().parse_file(str(out_path))

    orig_reach = geom.rivers[_STRESS_RIVER].reaches[_STRESS_REACH]
    new_reach = re_parsed.rivers[_STRESS_RIVER].reaches[_STRESS_REACH]
    assert len(new_reach.cross_sections) == 3
    for orig_xs, new_xs in zip(orig_reach.cross_sections, new_reach.cross_sections):
        assert new_xs.cutline is not None
        assert new_xs.cutline.points[0] != orig_xs.cutline.points[0]
        assert abs(
            _arc_len(new_xs.cutline.points) - _arc_len(orig_xs.cutline.points)
        ) < 1e-3
