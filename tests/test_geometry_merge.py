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

    Geom A = g02 (master), Geom B = g01.

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


# ---------------------------------------------------------------------------
# Truncation / extension of an all-A cross-section (must NOT be trivial)
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_truncated_all_a_config_is_honored(tmp_path, sterp_geoms):
    """
    A single-segment-A config whose breakpoints don't span A's full extent is
    a real edit: the exported XS must be truncated, not passed through
    verbatim.  Regression test for the bug where _is_trivial_config ignored
    breakpoint values entirely.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp East", "East Branch", "17318.34")  # A spans 0 .. 942.14
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[100.0, 800.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "truncated.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    assert xs_out.sta_elev[0][0] == 100.0
    assert xs_out.sta_elev[-1][0] == 800.0
    # Bank stations must still land exactly on stations in the truncated block
    stations = {s for s, _ in xs_out.sta_elev}
    if xs_out.bank_stations:
        assert xs_out.bank_stations[0] in stations
        assert xs_out.bank_stations[1] in stations


@skip_if_no_data
def test_full_extent_all_a_config_stays_verbatim(tmp_path, sterp_geoms):
    """
    A single-segment-A config whose breakpoints DO span A's full extent is
    still a trivial pass-through — byte-for-byte identical to the source.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp East", "East Branch", "17318.34")
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[0.0, 942.14],
        segment_sources=["A"],
    )}
    out = tmp_path / "fullspan.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    geom_out = GeometryParser().parse_file(str(out))
    xs_a = _build_index(geom_a)[key]
    xs_out = _build_index(geom_out)[key]
    assert _xs_raw_lines(geom_a, xs_a) == _xs_raw_lines(geom_out, xs_out)


# ---------------------------------------------------------------------------
# Bank Sta= formatting must match the #Sta/Elev= block's 8-char fields
# ---------------------------------------------------------------------------

def test_write_bank_sta_line_precision():
    from hack_ras.geometry.blocks.xs_bank_sta import write_bank_sta
    # 8 significant characters survive intact (the old :g mangled these to
    # 10251.8 / 10380.2)
    assert write_bank_sta((10251.75, 10380.25)) == "Bank Sta=10251.75,10380.25\n"
    # A value that cannot fit an 8-char field is shortened exactly the way the
    # #Sta/Elev= block's own formatter shortens it
    assert write_bank_sta((112421.75, 112421.75)) == "Bank Sta=112421.8,112421.8\n"


@skip_if_no_data
@pytest.mark.skipif(
    not (DATA_DIR / "SterpCreek.g04").is_file(),
    reason="SterpCreek.g04 (stretched-stationing fixture) not present — it was a "
           "temporary file; retarget this test when g02 gains a stretched XS",
)
def test_bank_sta_matches_block_station_stretched_xs(tmp_path):
    """
    End-to-end on SterpCreek.g04's RS 43320 (stationing stretched to the limits
    of HEC-RAS's 8-character fields, stations -350 .. 112421, banks at
    44838/47099): after a rebuild, the Bank Sta= values must parse to stations
    that exist exactly in the written #Sta/Elev= block.
    """
    parser = GeometryParser()
    geom_a = parser.parse_file(str(DATA_DIR / "SterpCreek.g04"))
    geom_b = parser.parse_file(str(DATA_DIR / "SterpCreek.g01"))
    key = _norm_key("Sterp West", "Upper", "43320")
    # Truncate slightly so the XS is genuinely rebuilt (not passed through)
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[-350.0, 112000.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "stretched.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    assert xs_out.sta_elev[-1][0] == 112000.0
    stations = {s for s, _ in xs_out.sta_elev}
    assert xs_out.bank_stations[0] in stations
    assert xs_out.bank_stations[1] in stations


# ---------------------------------------------------------------------------
# Manning's n: at a snapped-station collision the later value wins
# ---------------------------------------------------------------------------

def test_merge_manning_boundary_collision_keeps_new_segment_value():
    """
    When the previous segment's last n-entry and the next segment's opening
    n-entry snap to the same output station, the NEW segment's value must win
    (matching the 'vertex belongs to the segment that starts there' rule).
    """
    from hack_ras.geometry.model import CrossSection, ManningDef
    from hack_ras.geometry.merge import merge_manning

    xs_a = CrossSection(
        river="R", reach="RC", station="1",
        manning_def=ManningDef(method=-1, entries=[(0.0, 0.05), (99.999, 0.06)]),
    )
    xs_b = CrossSection(
        river="R", reach="RC", station="1",
        manning_def=ManningDef(method=-1, entries=[(0.0, 0.03)]),
    )
    config = MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[0.0, 100.0, 200.0],
        segment_sources=["A", "B"],
    )
    # Output block has a station at exactly 100.0; A's 99.999 entry and B's
    # segment-opening entry at 100.0 both snap onto it.
    merged_se = [(0.0, 1.0), (100.0, 1.0), (200.0, 1.0)]

    result = merge_manning(xs_a, xs_b, config, merged_se)
    entries = dict(result.entries)
    assert entries[100.0] == 0.03  # B's opening value, not A's 0.06
