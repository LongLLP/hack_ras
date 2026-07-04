# hack_ras/geometry/blocks/xs_sta_elev.py

from __future__ import annotations
from typing import List, Tuple
from .base import read_fixed_fields, _fmt


def parse_sta_elev(lines, index):
    """
    Parse #Sta/Elev= block starting at index.
    Returns (List[Tuple[float, float]], lines_consumed) where each tuple is (station, elevation).
    """
    header = lines[index].strip()           # "#Sta/Elev= 43"
    n_pairs = int(header.split("=")[1])
    n_vals = n_pairs * 2

    pairs: List[Tuple[float, float]] = []
    consumed = 1
    gathered = 0
    i = index + 1

    while gathered < n_vals:
        line = lines[i].rstrip("\n")
        fields = read_fixed_fields(line, 8)
        fields = fields[: (n_vals - gathered)]
        fields = [f for f in fields if f]

        floats = list(map(float, fields))
        for j in range(0, len(floats), 2):
            pairs.append((floats[j], floats[j + 1]))

        gathered += len(floats)
        consumed += 1
        i += 1

    return pairs, consumed


def write_sta_elev(sta_elev: List[Tuple[float, float]]) -> List[str]:
    """
    Write a #Sta/Elev= block: (station, elevation) pairs in 8-char
    fixed-width fields, 10 values (5 pairs) per line.
    """
    lines = [f"#Sta/Elev= {len(sta_elev)} \n"]
    values = []
    for sta, elev in sta_elev:
        values.append(sta)
        values.append(elev)
    for i in range(0, len(values), 10):
        lines.append("".join(_fmt(v, 8) for v in values[i : i + 10]) + "\n")
    return lines
