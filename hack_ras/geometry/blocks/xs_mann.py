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

    Standard method (method=0, 'lob_ch_rob'):
        Exactly 3 floats — n_lob, n_channel, n_rob — with no station column.
        N is always 3 in practice (one value per zone).

    Horizontal Variation method (method=1, 'horizontal'):
        N entries × 3 fields each: station, n_value, position_code.
        Position codes are discarded; only (station, n_value) pairs are kept.
    """
    header = lines[index].strip()
    parts = header.split("=", 1)[1].split(",")
    n = int(parts[0].strip())
    method = int(parts[1].strip()) if len(parts) > 1 else 1

    if method == 0:
        # Standard LOB/channel/ROB: read 3 plain n-values, no station column.
        floats, consumed = _read_n_floats(lines, index + 1, 3)
        n_lob = floats[0] if len(floats) > 0 else 0.0
        n_channel = floats[1] if len(floats) > 1 else 0.0
        n_rob = floats[2] if len(floats) > 2 else 0.0
        return ManningDef(
            method='lob_ch_rob',
            n_lob=n_lob,
            n_channel=n_channel,
            n_rob=n_rob,
        ), 1 + consumed
    else:
        # Horizontal Variation: N entries × 3 fields (station, n, position_code).
        floats, consumed = _read_n_floats(lines, index + 1, n * 3)
        entries: List[Tuple[float, float]] = []
        for j in range(0, len(floats), 3):
            station = floats[j]
            n_val = floats[j + 1] if j + 1 < len(floats) else 0.0
            entries.append((station, n_val))
        return ManningDef(method='horizontal', entries=entries), 1 + consumed
