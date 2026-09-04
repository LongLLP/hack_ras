"""Tests for culvert-group reading (ASCII and HDF).

The expectations for the RAS 7.0 fixtures are the values that were typed into
the GUI, so these tests check the reader against HEC-RAS itself rather than
against a previous run of the reader.
"""

from pathlib import Path

import pytest

from hack_ras.geometry.culverts import (
    CulvertGroup,
    read_culverts,
    read_culverts_ascii,
    read_culverts_hdf,
    match_keyword,
)

try:
    import h5py  # noqa: F401

    HAS_H5PY = True
except ImportError:
    HAS_H5PY = False

DATA = Path(__file__).parent / "data"
STERP_G03 = DATA / "Wisconsin Floodway" / "SterpCreek.g03"
MODEL_G02 = DATA / "2D culvert bridge levee precip pipes" / "Model.g02"
MODEL_G04 = DATA / "2D culvert bridge levee precip pipes" / "Model.g04"


def by_key(groups):
    """Index groups by (rs-or-connection, name)."""
    return {(g.rs or g.connection, g.name): g for g in groups}


# --------------------------------------------------------------------------
# keyword coverage
# --------------------------------------------------------------------------

# Exercises the two layouts and the OLD short forms in one place: a
# single-barrel 1D culvert with a BLANK span, a 15-field grouped form with no
# trailing momentum field and a blank us_distance, plus the lateral and inline
# keywords which no fixture model contains.  Trivially small key=value data,
# per the synthetic-fixture rule in dev_rules.md.
SYNTHETIC = """Geom Title=synthetic
River Reach=Test River      ,Reach A
Type RM Length L Ch R = 1 ,100     ,,,
Culvert=1,5,,50,0.024,0.5,1,2,1,10,20,11.5,30,Single  , 0 ,10
Culvert Bottom n=0.03
BC Culvert Barrel=1,B1,0
Type RM Length L Ch R = 1 ,200     ,,,
Multiple Barrel Culv=2,6,7,175,0.011,0.5,1,8,1,851.49,851.39, 2,Multi   , 0 ,2
    96.5    96.5   103.5   103.5
Culvert Bottom n=0.011
LW Culv=1,5,,10,0.013,0.2,1,1,1,205,205, 1 ,Lat     , 0 ,
     250     250
LW Culv Bottom n=0.013
IW Culv=2,5,10,63,0.015,0.5,1,8,1,210,209.9, 1 ,Inl     , 0 ,10
     700     700
IW Culv Bottom n=0.015
Connection=Conn1           ,1,2
Connection Culv=2,9,13,205,0.013,0.2,1,8,3,740.9,728.6, 2 ,ConnGrp     , 0 ,,-1
      20      20      28      28
Conn Culv Bottom n=0.014
Conn Culvert Barrel=1,Left,2
Conn Culvert Barrel=2,Right,2
"""


@pytest.fixture
def synthetic(tmp_path):
    path = tmp_path / "Synth.g01"
    path.write_text(SYNTHETIC, encoding="latin-1")
    return read_culverts_ascii(str(path))


def test_all_five_keywords_are_read(synthetic):
    assert [g.form for g in synthetic] == [
        "single_barrel",
        "multi_barrel",
        "lateral",
        "inline",
        "connection",
    ]


def test_sibling_lines_are_keyword_prefixed(synthetic):
    # each form finds its own "Bottom n" line despite a different prefix
    assert [g.mann_bottom for g in synthetic] == [0.03, 0.011, 0.013, 0.015, 0.014]


def test_single_barrel_stations_are_inline(synthetic):
    single = synthetic[0]
    # Culvert=...,US inv,US sta,DS inv,DS sta,...  -- inverts are NOT adjacent
    assert (single.us_invert, single.ds_invert) == (10.0, 11.5)
    assert single.barrel_stations == [(20.0, 30.0)]
    assert single.barrels == 1


def test_grouped_form_reads_barrel_count_and_station_line(synthetic):
    multi = synthetic[1]
    assert multi.barrels == 2
    assert (multi.us_invert, multi.ds_invert) == (851.49, 851.39)
    assert multi.barrel_stations == [(96.5, 96.5), (103.5, 103.5)]


def test_optional_trailing_momentum_field(synthetic):
    # 15/16-field short forms simply have no momentum field
    assert [g.use_momentum for g in synthetic] == [False, False, False, False, True]
    # a blank us_distance is None, not 0.0
    assert synthetic[2].us_distance is None
    assert synthetic[3].us_distance == 10.0


def test_match_keyword_does_not_confuse_sibling_lines():
    assert match_keyword("Culvert=1,5,,50") == "Culvert="
    assert match_keyword("Multiple Barrel Culv=2,6") == "Multiple Barrel Culv="
    # sibling lines and unrelated blocks must not read as a new group
    assert match_keyword("Culvert Bottom n=0.03") is None
    assert match_keyword("Conn Culv Bottom n=0.013") is None
    assert match_keyword("Conn Culvert Barrel=1,Left,2") is None
    assert match_keyword("Connection=Conn1") is None


# --------------------------------------------------------------------------
# the golden row -- every field given a distinct value in the RAS 7.0 GUI
# --------------------------------------------------------------------------


def test_golden_row_matches_what_was_typed_into_the_gui():
    group = by_key(read_culverts_ascii(str(STERP_G03)))[("23612", "hack_testing")]
    assert group.form == "single_barrel"
    assert group.shape == 1 and group.shape_name == "Circular"
    assert group.rise == 4.0 and group.diameter == 4.0
    assert group.length == 0.99
    assert group.mann_top == 0.033
    assert group.mann_bottom == 0.022
    assert group.bottom_depth == 0.11
    assert group.depth_blocked == 0.01
    assert group.entrance_loss == 0.8
    assert group.exit_loss == 1.0
    assert group.chart == 1
    assert group.scale == 1
    assert group.us_invert == 852.0
    assert group.ds_invert == 851.0
    assert group.us_distance == 0.9
    assert group.barrels == 1
    assert group.barrel_stations == [(725.0, 726.0)]
    assert group.barrel_names == ["new barrel"]
    assert group.use_momentum is False
    assert group.solution_criteria == 0


# --------------------------------------------------------------------------
# GUI-confirmed enums and behaviors
# --------------------------------------------------------------------------


def test_solution_criteria_codes():
    groups = by_key(read_culverts_ascii(str(STERP_G03)))
    assert groups[("32233", "Culvert #1")].solution_criteria == 1
    assert groups[("32233", "Culvert #1")].solution_criteria_name == "Inlet Control"
    assert groups[("33759", "Culvert #1")].solution_criteria == 2
    assert groups[("33759", "Culvert #1")].solution_criteria_name == "Outlet Control"
    assert groups[("26212", "Culvert #1")].solution_criteria_name == "Computed Flow Control"


def test_use_momentum_is_minus_one_in_ascii():
    groups = by_key(read_culverts_ascii(str(STERP_G03)))
    assert groups[("27637", "Culvert #1")].use_momentum is True
    # its neighbours are untouched
    assert groups[("26212", "Culvert #1")].use_momentum is False


def test_use_momentum_on_a_2d_connection():
    # Model.g04 is a carbon copy of g02 with only the momentum box checked
    assert read_culverts_ascii(str(MODEL_G02))[0].use_momentum is False
    assert read_culverts_ascii(str(MODEL_G04))[0].use_momentum is True


def test_adding_barrels_switches_the_keyword():
    group = by_key(read_culverts_ascii(str(STERP_G03)))[("26212", "Culvert #1")]
    assert group.keyword == "Multiple Barrel Culv="
    assert group.barrels == 7
    assert len(group.barrel_stations) == 7


def test_barrel_count_is_not_inferred_from_name_rows():
    # RAS returned these 7 barrel NAME rows in a rotated order after the save;
    # the count field is authoritative and the names are carried as-is.
    group = by_key(read_culverts_ascii(str(STERP_G03)))[("26212", "Culvert #1")]
    assert group.barrel_names == [f"Barrel #{n}" for n in (2, 3, 4, 5, 6, 7, 1)]
    assert group.barrels == 7


def test_circular_span_is_suppressed():
    groups = read_culverts_ascii(str(STERP_G03))
    circular = [g for g in groups if g.is_circular]
    assert circular, "fixture should contain circular culverts"
    # every circular culvert reports rise/diameter and NO span, whether the
    # ASCII field is blank or (after a re-save) equal to rise
    for g in circular:
        assert g.span is None
        assert g.diameter == g.rise
    # box culverts keep their span
    for g in groups:
        if not g.is_circular:
            assert g.span is not None


def test_partial_label_tables_return_none_for_unverified_codes():
    known = CulvertGroup(name="x", form="f", keyword="k", shape=2, chart=8, scale=1)
    assert known.shape_name == "Box"
    assert known.chart_desc == "8 - Flared wingwalls"
    assert known.scale_desc == "1 - Wingwall flared 30 to 75 deg."
    # unverified codes are not guessed
    unknown = CulvertGroup(name="x", form="f", keyword="k", shape=4, chart=59, scale=2)
    assert unknown.shape_name is None
    assert unknown.chart_desc is None
    assert unknown.scale_desc is None


# --------------------------------------------------------------------------
# ASCII vs HDF
# --------------------------------------------------------------------------

COMPARED_FIELDS = (
    "shape rise span length barrels mann_top mann_bottom bottom_depth "
    "depth_blocked entrance_loss exit_loss chart scale us_invert ds_invert "
    "us_distance use_momentum"
).split()


def _hdf_pairs():
    """Fixture geometries that have both an ASCII file and a culvert HDF table."""
    out = []
    for folder in ("Wisconsin Floodway", "2D culvert bridge levee precip pipes"):
        for ascii_path in sorted((DATA / folder).glob("*.g[0-9][0-9]")):
            hdf_path = Path(str(ascii_path) + ".hdf")
            if hdf_path.is_file():
                out.append(ascii_path)
    return out


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
@pytest.mark.parametrize("ascii_path", _hdf_pairs(), ids=lambda p: p.name)
def test_ascii_agrees_with_hdf(ascii_path):
    """The ASCII fallback must describe the same culverts RAS wrote to the HDF."""
    try:
        hdf_groups = read_culverts_hdf(str(ascii_path) + ".hdf")
    except KeyError:
        pytest.skip("no culvert groups in this HDF")
    ascii_groups = read_culverts_ascii(str(ascii_path))
    assert len(ascii_groups) == len(hdf_groups)

    hdf_by_key = {}
    for g in hdf_groups:
        hdf_by_key.setdefault((g.structure_id, g.name), []).append(g)

    for got in ascii_groups:
        candidates = hdf_by_key.get((got.structure_id, got.name))
        assert candidates, f"{got.structure_id}/{got.name} missing from the HDF"
        expected = candidates.pop(0)
        for name in COMPARED_FIELDS:
            a, b = getattr(got, name), getattr(expected, name)
            if isinstance(a, float) and isinstance(b, float):
                assert a == pytest.approx(b, rel=1e-5, abs=1e-3), name
            else:
                assert a == b, f"{got.structure_id}/{got.name}.{name}"


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
def test_hdf_cannot_supply_solution_criteria():
    # The geometry HDF has no solution-criteria column, so the HDF path reports
    # None rather than implying the "Computed Flow Control" default -- which is
    # why the ASCII reader is primary, not a fallback.
    for group in read_culverts_hdf(str(STERP_G03) + ".hdf"):
        assert group.solution_criteria is None
        assert group.solution_criteria_name is None
    # ...whereas the ASCII carries the real values
    criteria = {g.solution_criteria for g in read_culverts_ascii(str(STERP_G03))}
    assert criteria == {0, 1, 2}


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
def test_float32_rounding_recovers_authored_values():
    # Length 0.99 and n 0.033 are exact decimals in the ASCII but float32 in
    # the HDF; the reader must not surface 0.98999999...
    group = next(
        g for g in read_culverts_hdf(str(STERP_G03) + ".hdf") if g.name == "hack_testing"
    )
    assert group.length == 0.99
    assert group.mann_top == 0.033
    assert group.mann_bottom == 0.022
    assert group.rise == 4.0


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def test_read_culverts_defaults_to_ascii():
    groups = read_culverts(str(STERP_G03))
    assert [g.keyword for g in groups].count("Multiple Barrel Culv=") == 4
    assert all(g.solution_criteria is not None for g in groups)


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
def test_read_culverts_accepts_an_hdf_path_directly():
    assert len(read_culverts(str(STERP_G03) + ".hdf")) == 9


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
def test_prefer_hdf_uses_the_sibling_when_present():
    groups = read_culverts(str(STERP_G03), prefer_hdf=True)
    assert [g.keyword for g in groups] == ["<hdf>"] * 9


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not available")
def test_prefer_hdf_falls_back_when_the_hdf_predates_the_culvert_table():
    """A 5.0.3 geometry HDF has NO culvert table, even with culverts in the ASCII.

    SterpCreek.g01 was written by RAS 5.0.3 and carries three culvert groups in
    its ASCII, but its .hdf has no `Culvert Groups` at all -- the table is a
    7.0-era addition (g02 is the same model re-saved by 7.0 and does have it).
    So prefer_hdf must fall through to the ASCII rather than report nothing.
    """
    sterp_g01 = DATA / "Wisconsin Floodway" / "SterpCreek.g01"
    assert (Path(str(sterp_g01) + ".hdf")).is_file()
    with pytest.raises(KeyError):
        read_culverts_hdf(str(sterp_g01) + ".hdf")
    groups = read_culverts(str(sterp_g01), prefer_hdf=True)
    assert len(groups) == 3
    assert all(g.keyword != "<hdf>" for g in groups)


def test_prefer_hdf_falls_back_when_no_hdf_exists(tmp_path):
    # the RAS 4.1 case: ASCII only, no HDF anywhere
    path = tmp_path / "Old.g01"
    path.write_text(SYNTHETIC, encoding="latin-1")
    groups = read_culverts(str(path), prefer_hdf=True)
    assert len(groups) == 5
    assert groups[0].keyword == "Culvert="
