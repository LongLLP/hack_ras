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
    Full set of MergeConfigs matching xsedit_config.json (all 10 configured XS),
    as recorded in 'Sterp Creek inputs and outputs.xlsx'.

    Geom A = g02 (master), Geom B = g01, master_source = 'A'.

    Four of the ten configs are trivial (_is_trivial_config returns True for
    them: 43084, 42893, 42528, 41868) and will be written verbatim from g02.
    The remaining six are genuinely merged.
    """
    T = Transform  # alias for brevity

    return {
        # --- Sterp West / Upper ---
        _norm_key("Sterp West", "Upper", "43320"): MergeConfig(
            transform_a=T(),
            transform_b=T(h_offset=-523.0),
            breakpoints=[0.0, 111.0, 117.4, 455.8023],
            segment_sources=["A", "B", "A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "43170"): MergeConfig(
            transform_a=T(h_offset=-222.0, v_offset=-1.0),
            transform_b=T(),
            breakpoints=[0.0, 88.0, 107.0, 689.8838],
            segment_sources=["A", "B", "A"],
            cutline_source="A",
            preserve_cutline=True,
        ),
        _norm_key("Sterp West", "Upper", "43084"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 100.0],
            segment_sources=["A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "42998"): MergeConfig(
            transform_a=T(),
            transform_b=T(h_offset=158.0, v_offset=-2.0),
            breakpoints=[0.0, 261.5, 277.0, 300.0, 305.0, 669.6589],
            segment_sources=["A", "B", "A", "B", "A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "42893"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 620.56],
            segment_sources=["A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "42788"): MergeConfig(
            transform_a=T(h_offset=-20.0),
            transform_b=T(),
            breakpoints=[0.0, 833.0729],
            segment_sources=["A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "42528"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 1125.249],
            segment_sources=["A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "42268"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 1366.524],
            segment_sources=["A"],
            cutline_source="B",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "41868"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 1643.446],
            segment_sources=["A"],
            cutline_source="A",
            preserve_cutline=False,
        ),
        _norm_key("Sterp West", "Upper", "41468"): MergeConfig(
            transform_a=T(),
            transform_b=T(),
            breakpoints=[0.0, 1966.335],
            segment_sources=["B"],
            cutline_source="B",
            preserve_cutline=False,
        ),
    }


# Keys for all XS that have an explicit config — excluded from the verbatim
# pass-through test, which only checks unconfigured cross-sections.
_CONFIGURED_KEYS = {
    _norm_key("Sterp West", "Upper", rs)
    for rs in ("43320", "43170", "43084", "42998", "42893",
               "42788", "42528", "42268", "41868", "41468")
}


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

    for river in geom_a.rivers.values():
        for rch in river.reaches.values():
            for xs_a in rch.cross_sections:
                k = _norm_key(xs_a.river, xs_a.reach, xs_a.station)
                if k in _CONFIGURED_KEYS:
                    continue
                xs_out = idx_out[k]
                lines_a = _xs_raw_lines(geom_a, xs_a)
                lines_out = _xs_raw_lines(geom_out, xs_out)
                assert lines_a == lines_out, (
                    f"XS {xs_a.river.strip()}/{xs_a.reach.strip()}/{xs_a.station.strip()} "
                    f"was unexpectedly modified"
                )
