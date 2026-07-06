# hack_ras/geometry/blocks/xs_gis.py

from __future__ import annotations
from typing import List, Tuple
from ..model import XSGISCutLine
from .base import read_fixed_fields, _fmt

def parse_cutline(lines, index):
    """
    Parse XS GIS Cut Line block starting at index.
    Returns (XSGISCutLine, lines_consumed)
    """
    header = lines[index].strip()  # XS GIS Cut Line=6
    n_pairs = int(header.split("=")[1])
    n_vals = n_pairs * 2

    points: List[Tuple[float, float]] = []
    consumed = 1
    gathered = 0
    i = index + 1

    while gathered < n_vals:
        line = lines[i].rstrip("\n")
        fields = read_fixed_fields(line, 16)
        fields = fields[: (n_vals - gathered)]  # don't over-read
        fields = [f for f in fields if f]  # skip empty partial fields at line breaks

        floats = list(map(float, fields))
        for j in range(0, len(floats), 2):
            x = floats[j]
            y = floats[j+1]
            points.append((x, y))

        gathered += len(floats)
        consumed += 1
        i += 1

    return XSGISCutLine(n_pairs, points), consumed


def write_cutline(cutline: XSGISCutLine) -> List[str]:
    """Write an XS GIS Cut Line= block in HEC-RAS native format.

    Format confirmed against the RAS-authored fixture
    tests/data/XSCutLines stress test/XSCut_stress_test.g01 (coordinates
    entered in the RAS GUI with more digits than a field can hold), which
    this writer reproduces byte-for-byte:

    - 16-char fixed-width fields, right-justified, 4 fields (2 XY pairs) per
      64-char line.  Wrapping ALWAYS lands on a field boundary — a value is
      never split across lines.  Adjacent full-width values have no
      separating whitespace, so the block can only be read as fixed columns,
      never whitespace-split.
    - Each value carries as many digits as fit its field (up to 15
      significant figures), trailing zeros stripped.  RAS itself *truncates*
      digits that don't fit (...36345064855 → ...36345064) where _fmt
      rounds; the difference can only show up on computed values carrying
      more precision than any RAS-written file can store — every value
      parsed from a file round-trips exactly.
    """
    lines = [f"XS GIS Cut Line={cutline.n_points}\n"]
    values: List[float] = []
    for x, y in cutline.points:
        values.extend([x, y])
    for i in range(0, len(values), 4):
        lines.append("".join(_fmt(v, 16) for v in values[i : i + 4]) + "\n")
    return lines