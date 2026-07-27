"""Tests for the Storage Area 2D Points block (2D-mesh cell seeds)."""

from pathlib import Path

from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.writer import GeometryWriter
from hack_ras.geometry.blocks.storage_area_2d import (
    parse_2d_points,
    format_2d_points_header,
    format_2d_points_lines,
)

DATA = Path(__file__).parent / "data"
MODEL_2D = DATA / "2D culvert bridge levee precip pipes" / "Model.g02"
BAXTER_1D = DATA / "Baxter" / "Baxter.g02"


def test_parse_populates_storage_areas():
    geom = GeometryParser().parse_file(str(MODEL_2D))
    by_name = {sa.name: sa for sa in geom.storage_areas_2d}
    assert set(by_name) == {"Interior", "Watershed"}
    assert len(by_name["Interior"].points) == 32
    assert len(by_name["Watershed"].points) == 29
    # each point is a finite (x, y) pair
    for sa in geom.storage_areas_2d:
        for x, y in sa.points:
            assert isinstance(x, float) and isinstance(y, float)


def test_line_span_indices_are_consistent():
    geom = GeometryParser().parse_file(str(MODEL_2D))
    for sa in geom.storage_areas_2d:
        assert geom.raw_lines[sa._header_line].startswith("Storage Area 2D Points=")
        # data span holds exactly the coordinate lines for this block
        n_lines = sa._data_end - sa._data_start
        assert n_lines == (len(sa.points) + 1) // 2  # 2 points per line


def test_format_reproduces_source_bytes():
    """Rewriting an unchanged block yields byte-identical coordinate lines."""
    geom = GeometryParser().parse_file(str(MODEL_2D))
    for sa in geom.storage_areas_2d:
        rebuilt = format_2d_points_lines(sa.points)
        original = geom.raw_lines[sa._data_start:sa._data_end]
        assert len(rebuilt) == len(original)
        assert [r.rstrip("\n") for r in rebuilt] == [o.rstrip("\n") for o in original]


def test_format_header_reproduces_source_bytes():
    """The header helper matches RAS's own spacing for the real block."""
    geom = GeometryParser().parse_file(str(MODEL_2D))
    for sa in geom.storage_areas_2d:
        original = geom.raw_lines[sa._header_line].rstrip("\r\n")
        assert format_2d_points_header(len(sa.points)) == original


def test_format_header_count_changes():
    assert format_2d_points_header(11967) == "Storage Area 2D Points= 11967 "
    assert format_2d_points_header(0) == "Storage Area 2D Points= 0 "


def test_zero_point_storage_area():
    """A 1D storage area has `Storage Area 2D Points= 0` and no data lines."""
    geom = GeometryParser().parse_file(str(BAXTER_1D))
    assert geom.storage_areas_2d, "Baxter.g02 should have storage areas"
    for sa in geom.storage_areas_2d:
        assert sa.points == []
        assert format_2d_points_lines(sa.points) == []
        assert sa._data_start == sa._data_end  # header only


def test_parse_2d_points_consumed_count():
    lines = [
        "Storage Area 2D Points= 3\n",
        # 3 points -> 2 lines (2 pairs then 1 pair)
        f"{1.0:>16g}{2.0:>16g}{3.0:>16g}{4.0:>16g}\n",
        f"{5.0:>16g}{6.0:>16g}\n",
        "Storage Area Mannings=0.06\n",
    ]
    points, consumed = parse_2d_points(lines, 0)
    assert consumed == 3  # header + 2 data lines
    assert points == [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]


def test_roundtrip_with_one_moved_seed(tmp_path):
    """Replace one seed, rewrite the block, confirm only its line changed and
    the point count is preserved."""
    geom = GeometryParser().parse_file(str(MODEL_2D))
    sa = next(sa for sa in geom.storage_areas_2d if sa.points)

    new_points = list(sa.points)
    x, y = new_points[0]
    new_points[0] = (x + 123.456, y - 78.9)

    new_lines = list(geom.raw_lines)
    new_lines[sa._data_start:sa._data_end] = format_2d_points_lines(new_points)
    geom.raw_lines = new_lines

    out = tmp_path / "Model.g99"
    GeometryWriter().write(geom, str(out))

    reparsed = GeometryParser().parse_file(str(out))
    sa2 = next(s for s in reparsed.storage_areas_2d if s.name == sa.name)
    assert len(sa2.points) == len(sa.points)
    assert abs(sa2.points[0][0] - (x + 123.456)) < 1e-6
    assert abs(sa2.points[0][1] - (y - 78.9)) < 1e-6
    # every other seed is unchanged
    for a, b in zip(sa2.points[1:], sa.points[1:]):
        assert abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6
