# hack_ras/geometry/blocks/xs_mann.py

from __future__ import annotations
from typing import List, Tuple
from .base import read_fixed_fields
from ..model import ManningDef


def _is_data_line(line: str) -> bool:
    """
    Return True if *line* looks like a numeric data line rather than a block
    header.  Data lines contain only numbers, spaces, '.', '+', '-'.
    Block headers start with a letter or '#' (e.g. 'Bank Sta=', '#XS Ineff=').
    Blank lines are treated as data (they simply yield no fields).
    """
    stripped = line.strip()
    if not stripped:
        return True
    first = stripped[0]
    return first.isdigit() or first in '.+-'


def _read_n_floats(lines: list, index: int, n_vals: int) -> Tuple[List[float], int]:
    """
    Starting at lines[index], read exactly *n_vals* floats from 8-char
    fixed-width data lines.  Stops early if a non-numeric line is reached.
    Returns (floats_read, lines_consumed).
    """
    all_floats: List[float] = []
    consumed = 0
    i = index
    while len(all_floats) < n_vals and i < len(lines):
        line = lines[i].rstrip("\n")
        if not _is_data_line(line):
            break
        fields = read_fixed_fields(line, 8)
        fields = [f for f in fields if f.strip()]
        remaining = n_vals - len(all_floats)
        all_floats.extend(float(f) for f in fields[:remaining])
        consumed += 1
        i += 1
    return all_floats, consumed


def parse_mann(lines: list, index: int) -> Tuple[ManningDef, int]:
    """
    Parse a #Mann= block and return a ManningDef.

    Header format: #Mann= N , method , flag

    All methods store data as N entries of three 8-char fixed-width fields each:
        station   n_value   position_code

    position_code is informational and is discarded on parse.

    method=0:  "Horizontal Variation in n-values" OFF.  Always N=3; stations
               are the XS left edge, left bank, and right bank (LOB/CH/ROB).
    method=-1: "Horizontal Variation in n-values" ON (modern convention).
               Arbitrary N entries at user-defined stations.
    method=1:  Same semantics as method=-1; legacy convention found in older
               files.  Parsed identically; written as -1 in new output.
    """
    header = lines[index].strip()
    parts = header.split("=", 1)[1].split(",")
    n = int(parts[0].strip())
    method = int(parts[1].strip()) if len(parts) > 1 else 0

    floats, consumed = _read_n_floats(lines, index + 1, n * 3)

    entries: List[Tuple[float, float]] = []
    for j in range(0, len(floats), 3):
        station = floats[j]
        n_val = floats[j + 1] if j + 1 < len(floats) else 0.0
        # floats[j + 2] is the position_code — discarded
        entries.append((station, n_val))

    return ManningDef(method=method, entries=entries), 1 + consumed
