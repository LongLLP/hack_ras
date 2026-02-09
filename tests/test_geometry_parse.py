from pathlib import Path
from hack_ras.geometry.parser import GeometryParser

DATA = Path(__file__).parent / "data" / "beaver.g01"

def _count_xs_headers(path: Path) -> int:
    # Count how many cross sections appear by scanning the raw file for the header line
    count = 0
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("Type RM Length"):
                count += 1
    return count

def test_title_and_xs_count():
    gp = GeometryParser()
    geom = gp.parse_file(str(DATA))
    assert geom.title == "Beaver Cr. - bridge"

    # Number of CrossSection objects should match '# of "Type RM Length"' lines
    expected = _count_xs_headers(DATA)
    # Flatten cross-sections from all rivers/reaches
    actual = sum(len(r.reaches[reach].cross_sections)
                 for r in geom.rivers.values()
                 for reach in r.reaches)
    assert actual == expected

def test_first_xs_has_station_from_rm_header():
    gp = GeometryParser()
    geom = gp.parse_file(str(DATA))
    # Locate first cross section
    first_river = next(iter(geom.rivers.values()))
    first_reach = next(iter(first_river.reaches.values()))
    first_cs = first_reach.cross_sections[0]
    # In your current parser, station is set as the RM text field
    assert first_cs.station == "5.99"