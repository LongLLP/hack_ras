# hack_ras/geometry/blocks/xs_gis.py

from __future__ import annotations
from typing import List, Tuple
from ..model import XSGISCutLine
from .base import read_fixed_fields

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

        floats = list(map(float, fields))
        for j in range(0, len(floats), 2):
            x = floats[j]
            y = floats[j+1]
            points.append((x, y))

        gathered += len(floats)
        consumed += 1
        i += 1

    return XSGISCutLine(n_pairs, points), consumed