from pathlib import Path

import pytest

from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.merge import (
    MergeConfig,
    Transform,
    _norm_key,
    _xs_raw_lines,
    write_merged_geometry,
)

# Test data lives in the sibling RAS_xsedit repo.
DATA_DIR = (
    Path(__file__).parent.parent.parent
    / "RAS_xsedit" / "tests" / "data" / "Sterp Creek"
)

skip_if_no_data = pytest.mark.skipif(
    not DATA_DIR.is_dir(),
    reason="Sterp Creek test data not found (expected sibling RAS_xsedit repo)",
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sterp_geoms():
    parser = GeometryParser()
    return (
        parser.parse_file(str(DATA_DIR / "SterpCreek.g02")),
        parser.parse_file(str(DATA_DIR / "SterpCreek.g01")),
    )


def _build_index(geom):
    """Return {norm_key: CrossSection} for every XS in *geom*."""
    idx = {}
    for river in geom.rivers.values():
        for rch in river.reaches.values():
            for xs in rch.cross_sections:
                idx[_norm_key(xs.river, xs.reach, xs.station)] = xs
    return idx


def _sterp_configs(geom_a):
    """
    MergeConfig for Sterp West / Upper / RS 43320, matching the inputs recorded
    in 'Sterp Creek inputs and outputs.xlsx':
      - Geom A = g02 (master, identity transform)
      - Geom B = g01 (h_offset = -523)
      - Segments: [0, 111) → A,  [111, 117.4) → B,  [117.4, end] → A
      - Manning's n source = A,  cut-line source = A,  preserve_cutline = False
    """
    idx_a = _build_index(geom_a)
    xs_a = idx_a[_norm_key("Sterp West", "Upper", "43320")]
    end_sta = xs_a.sta_elev[-1][0]

    config = MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(h_offset=-523.0),
        breakpoints=[0.0, 111.0, 117.4, end_sta],
        segment_sources=["A", "B", "A"],
        mann_option="A",
        cutline_source="A",
        preserve_cutline=False,
    )
    return {_norm_key("Sterp West", "Upper", "43320"): config}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_merge_matches_known_good_output(tmp_path, sterp_geoms):
    """Full output must match the known-good g03 produced by the tool."""
    geom_a, geom_b = sterp_geoms
    out = tmp_path / "SterpCreek.g03"

    write_merged_geometry(
        geom_a, geom_b,
        _sterp_configs(geom_a),
        str(out),
        title="Merged Geometry",
        master_source="A",
    )

    expected = (DATA_DIR / "SterpCreek.g03").read_text(encoding="utf-8", errors="ignore")
    actual = out.read_text(encoding="utf-8", errors="ignore")
    assert actual == expected


@skip_if_no_data
def test_reach_order_preserved(tmp_path, sterp_geoms):
    """Reaches in the output must appear in the same order as the master (g02)."""
    geom_a, geom_b = sterp_geoms
    out = tmp_path / "SterpCreek.g03"

    write_merged_geometry(
        geom_a, geom_b,
        _sterp_configs(geom_a),
        str(out),
        title="Merged Geometry",
        master_source="A",
    )

    def reach_order(path):
        prefix = "River Reach="
        return [
            line.strip()[len(prefix):]
            for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip().startswith(prefix)
        ]

    assert reach_order(out) == reach_order(DATA_DIR / "SterpCreek.g02")


@skip_if_no_data
def test_unmodified_xs_pass_through_verbatim(tmp_path, sterp_geoms):
    """
    Every XS that was not given a merge config must be written byte-for-byte
    identical to the master (g02).  This is the primary regression guard against
    spurious changes to unmodified content.
    """
    geom_a, geom_b = sterp_geoms
    out = tmp_path / "SterpCreek.g03"

    write_merged_geometry(
        geom_a, geom_b,
        _sterp_configs(geom_a),
        str(out),
        title="Merged Geometry",
        master_source="A",
    )

    parser = GeometryParser()
    geom_out = parser.parse_file(str(out))
    idx_out = _build_index(geom_out)

    modified_key = _norm_key("Sterp West", "Upper", "43320")

    for river in geom_a.rivers.values():
        for rch in river.reaches.values():
            for xs_a in rch.cross_sections:
                k = _norm_key(xs_a.river, xs_a.reach, xs_a.station)
                if k == modified_key:
                    continue
                xs_out = idx_out[k]
                lines_a = _xs_raw_lines(geom_a, xs_a)
                lines_out = _xs_raw_lines(geom_out, xs_out)
                assert lines_a == lines_out, (
                    f"XS {xs_a.river.strip()}/{xs_a.reach.strip()}/{xs_a.station.strip()} "
                    f"was unexpectedly modified"
                )
