import json
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

# The GUI "Save Config…" export that produced the known-good SterpCreek.g03.
CONFIG_JSON = DATA_DIR.parent.parent / "xsedit_config.json"

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


def _transform(d):
    """Build a Transform from a config-JSON dict, ignoring unknown keys
    (older configs may still carry e.g. h_scale — dropped, like the GUI does)."""
    return Transform(
        h_offset=d.get("h_offset", 0.0),
        v_offset=d.get("v_offset", 0.0),
        v_scale=d.get("v_scale", 1.0),
    )


def _sterp_configs():
    """
    Load (title, {norm_key: MergeConfig}) from the authoritative GUI config
    record, RAS_xsedit/tests/xsedit_config.json — the exact "Save Config…"
    export the user loaded in the XS Editor to produce the known-good
    SterpCreek.g03, then verified in HEC-RAS (2026-07-06; see
    RAS_xsedit/tests/Test_notes.txt for the per-XS review).

    Geom A = g02 (master), Geom B = g01.  Six configured XS:
      43320 — B/A/B/A/gap/A/B, transform B h -349 / v -0.2, IFAs and cut line
              from B (blend extension allowed; no extension expected)
      43170 — A>B>A, no length change
      42998 — A>B>A truncated on the right, IFAs from B, cut line preserved
              verbatim
      42893 — source B where B has no such XS: unsatisfiable, exports as a
              verbatim copy of A (the GUI warns about these at export time)
      42788 — extended flat to -50, truncated at 850, cut line from B with
              blend extension
      42528 — A-only XS (not in g01), all-A config truncated on both sides
              (47..1100 of 0..1125.25) — exercises the honored-A-only-trim
              path added 2026-07-07 (user-verified in HEC-RAS)
    """
    cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    configs = {}
    for x in cfg["cross_sections"]:
        key = _norm_key(x["river"], x["reach"], x["station"])
        configs[key] = MergeConfig(
            transform_a=_transform(x.get("transform_a", {})),
            transform_b=_transform(x.get("transform_b", {})),
            breakpoints=x["breakpoints"],
            segment_sources=x["segment_sources"],
            cutline_source=x.get("cutline_source", "A"),
            ineff_source=x.get("ineff_source", "A"),
            preserve_cutline=x.get("preserve_cutline", False),
            blend_cutline=x.get("blend_cutline", False),
            blend_cutline_threshold_pct=x.get("blend_cutline_threshold_pct", 10.0),
            blend_cutline_search_radius=x.get("blend_cutline_search_radius", 20.0),
        )
    return cfg["title"], configs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_merge_matches_known_good_output(tmp_path, sterp_geoms):
    """Full output must match the known-good g03 produced by the tool."""
    geom_a, geom_b = sterp_geoms
    out = tmp_path / "SterpCreek.g03"

    title, configs = _sterp_configs()
    write_merged_geometry(geom_a, geom_b, configs, str(out), title=title)

    expected = (DATA_DIR / "SterpCreek.g03").read_text(encoding="utf-8", errors="ignore")
    actual = out.read_text(encoding="utf-8", errors="ignore")
    assert actual == expected


@skip_if_no_data
def test_reach_order_preserved(tmp_path, sterp_geoms):
    """Reaches in the output must appear in the same order as the master (g02)."""
    geom_a, geom_b = sterp_geoms
    out = tmp_path / "SterpCreek.g03"

    title, configs = _sterp_configs()
    write_merged_geometry(geom_a, geom_b, configs, str(out), title=title)

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

    title, configs = _sterp_configs()
    write_merged_geometry(geom_a, geom_b, configs, str(out), title=title)

    parser = GeometryParser()
    geom_out = parser.parse_file(str(out))
    idx_out = _build_index(geom_out)

    for river in geom_a.rivers.values():
        for rch in river.reaches.values():
            for xs_a in rch.cross_sections:
                k = _norm_key(xs_a.river, xs_a.reach, xs_a.station)
                if k in configs:
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
# A-only XS (no B counterpart): all-A configs honored, B-referencing configs
# fall back to a raw pass-through
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_a_only_xs_all_a_config_is_honored(tmp_path, sterp_geoms):
    """
    RS 42893 exists in A (g02) but not in B (g01).  A truncating all-A config
    on it is fully satisfiable from A's own data and must be honored, not
    silently discarded because the XS lacks a B counterpart.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42893")  # A spans 0 .. 620.56
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[50.0, 600.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "a_only_trim.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    assert xs_out.sta_elev[0][0] == 50.0
    assert xs_out.sta_elev[-1][0] == 600.0


@skip_if_no_data
def test_a_only_xs_b_referencing_config_passes_through_verbatim(tmp_path, sterp_geoms):
    """
    A config that requests Geometry B data for an A-only XS is unsatisfiable:
    the XS must be written as a verbatim copy of A (never with B segments
    silently emptied or substituted).  The GUI warns about these at export.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42893")
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[50.0, 600.0],   # non-trivial on its own...
        segment_sources=["A"],
        ineff_source="B",            # ...but requests B data
    )}
    out = tmp_path / "a_only_b_ref.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    geom_out = GeometryParser().parse_file(str(out))
    xs_a = _build_index(geom_a)[key]
    xs_out = _build_index(geom_out)[key]
    assert _xs_raw_lines(geom_a, xs_a) == _xs_raw_lines(geom_out, xs_out)


# ---------------------------------------------------------------------------
# Manning method 0 preserved for all-A edits of a method-0 cross-section
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_all_a_trim_keeps_manning_method0(tmp_path, sterp_geoms):
    """
    RS 42528's source A block is method 0 (LOB/Ch/ROB).  A pure trim takes
    nothing from B, so the output must stay method 0 — three positional
    n-values keyed to the merged left edge and the output bank stations —
    instead of flipping to horizontal variation (-1).
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42528")  # A: 0..1125.25, banks 266.81/304.83
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[47.0, 1100.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "m0_trim.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    mann = xs_out.manning_def
    assert mann is not None and mann.method == 0
    stations = [s for s, _ in mann.entries]
    values = [n for _, n in mann.entries]
    assert stations == [47.0, 266.81, 304.83]
    assert values == [0.07, 0.045, 0.07]
    assert xs_out.bank_stations == (266.81, 304.83)


@skip_if_no_data
def test_trim_through_bank_keeps_method0_bank_at_edge(tmp_path, sterp_geoms):
    """
    Truncating through the right bank (304.83) keeps method 0: the bank snaps
    to the surviving right edge, the ROB region becomes zero-width, and the
    third n-entry follows the snapped bank.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42528")
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[47.0, 300.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "m0_bankcut.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    mann = xs_out.manning_def
    assert mann is not None and mann.method == 0
    right_edge = xs_out.sta_elev[-1][0]
    assert xs_out.bank_stations[1] == right_edge
    assert mann.entries[2][0] == right_edge
    assert mann.entries[1][0] == 266.81  # left bank untouched


@skip_if_no_data
def test_all_a_gap_keeps_manning_method0(tmp_path, sterp_geoms):
    """A gap segment (source None) still takes nothing from B → method 0 kept."""
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42528")
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[47.0, 500.0, 600.0, 1100.0],
        segment_sources=["A", None, "A"],
    )}
    out = tmp_path / "m0_gap.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    mann = xs_out.manning_def
    assert mann is not None and mann.method == 0
    assert [s for s, _ in mann.entries] == [47.0, 266.81, 304.83]


# ---------------------------------------------------------------------------
# Method -1 blocks must define an n-value on the XS's first station
# ---------------------------------------------------------------------------

@skip_if_no_data
def test_extension_gets_manning_entry_at_first_station(tmp_path, sterp_geoms):
    """
    RS 42788 extended flat to -50 (A's n-data starts at 0): HEC-RAS refuses
    to run a method -1 block with no n-value on the first station, so the
    earliest n-value must be extended to the new left edge.
    """
    geom_a, geom_b = sterp_geoms
    key = _norm_key("Sterp West", "Upper", "42788")
    configs = {key: MergeConfig(
        transform_a=Transform(),
        transform_b=Transform(),
        breakpoints=[-50.0, 0.0, 719.2291754477893, 850.0],
        segment_sources=["A", "A", "B"],
    )}
    out = tmp_path / "ext_mann.g99"
    write_merged_geometry(geom_a, geom_b, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    mann = xs_out.manning_def
    assert mann is not None and mann.method == -1
    first_sta = xs_out.sta_elev[0][0]
    assert first_sta == -50.0
    assert mann.entries[0][0] == first_sta, "first station must carry an n-value"
    # The inserted entry extends the earliest existing value (A's, at sta 0)
    assert mann.entries[0][1] == mann.entries[1][1]
    assert mann.entries[0][1] > 0


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


MASSIVE_G01 = Path(__file__).parent / "data" / "Massive XS stations" / "Massive.g01"


def test_bank_sta_matches_block_station_stretched_xs(tmp_path):
    """
    End-to-end on the RAS-authored 'Massive XS stations' fixture, RS 500
    (LOB/Channel/ROB scaled x1000 in the RAS GUI: stations -350 .. 1127361,
    banks 451530.8 / 474140.8).  RAS itself never writes a station that needs
    more than 8 characters — it rounds to fit the field (the user entered
    451530.795 and RAS saved 451530.8 in both #Sta/Elev= and Bank Sta=) — so
    overflow can only come from our own pipeline: the h_offset below pushes
    stations like 451530.8 to 451531.13, which no longer fits an 8-char
    field.  After the rebuild the Bank Sta= values must parse to stations
    that exist exactly in the written #Sta/Elev= block.
    """
    geom = GeometryParser().parse_file(str(MASSIVE_G01))
    key = _norm_key("WideRiver", "WideReach", "500")
    # Truncate the right end so the XS is genuinely rebuilt, and shift by a
    # value that overflows the 8-char station fields.
    configs = {key: MergeConfig(
        transform_a=Transform(h_offset=0.33),
        transform_b=Transform(),
        breakpoints=[-349.67, 1127000.0],
        segment_sources=["A"],
    )}
    out = tmp_path / "stretched.g99"
    write_merged_geometry(geom, geom, configs, str(out), title="t")

    xs_out = _build_index(GeometryParser().parse_file(str(out)))[key]
    assert xs_out.sta_elev[-1][0] == 1127000.0
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
