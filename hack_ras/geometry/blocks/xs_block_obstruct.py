# hack_ras/geometry/blocks/xs_block_obstruct.py

from __future__ import annotations
from typing import List, Optional
from ..model import BlockObstructArea, BlockedObstructions
from .base import read_fixed_fields


def _parse_station_field(raw: str) -> float:
    """Blank field -> 0.0 (XS edge sentinel for 'normal'); otherwise float."""
    return 0.0 if raw == "" else float(raw)


def _parse_elevation_field(raw: str) -> Optional[float]:
    """Blank field -> None; otherwise float (top-of-obstruction elevation)."""
    return None if raw == "" else float(raw)


def parse_block_obstruct(lines, index):
    """
    Parse a ``#Block Obstruct=`` block.  Returns ``(BlockedObstructions, lines_consumed)``.

    File format (same triplet layout as ``#XS Ineff=``, but with NO trailing
    ``Permanent`` block — obstructions are always solid):

        #Block Obstruct= N , flag
        <data lines: N*3 fields of 8 chars each — start_sta, end_sta, elevation per area>

    ``flag`` 0 -> "normal" (one left of the channel, one right, with 0.0 station
    sentinels for the XS edges); flag -1 -> "multiple_block" (N arbitrary blocks
    with literal stations).
    """
    header = lines[index].strip()            # "#Block Obstruct= 2 , 0"
    parts = header.split("=", 1)[1].split(",")
    n_areas = int(parts[0])
    flag = int(parts[1])
    obstr_type = "normal" if flag == 0 else "multiple_block"

    n_fields = n_areas * 3
    raw: List[str] = []
    consumed = 1
    i = index + 1
    while len(raw) < n_fields:
        fields = read_fixed_fields(lines[i].rstrip("\n"), 8)
        raw.extend(fields[: n_fields - len(raw)])
        consumed += 1
        i += 1

    areas: List[BlockObstructArea] = []
    for k in range(n_areas):
        areas.append(BlockObstructArea(
            start_sta=_parse_station_field(raw[k * 3].strip()),
            end_sta=_parse_station_field(raw[k * 3 + 1].strip()),
            elevation=_parse_elevation_field(raw[k * 3 + 2].strip()),
        ))

    return BlockedObstructions(obstr_type=obstr_type, areas=areas), consumed
