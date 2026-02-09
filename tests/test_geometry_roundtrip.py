from pathlib import Path
from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.writer import GeometryWriter

DATA = Path(__file__).parent / "data" / "beaver.g01"

def test_roundtrip_is_lossless(tmp_path):
    # Read the original file as raw text to compare exact bytes
    original = DATA.read_text(encoding="utf-8", errors="ignore")

    gp = GeometryParser()
    geom = gp.parse_file(str(DATA))

    out = tmp_path / "out.g01"
    GeometryWriter().write(geom, str(out))

    written = out.read_text(encoding="utf-8", errors="ignore")
    assert written == original