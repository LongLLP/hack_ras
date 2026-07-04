# hack_ras/geometry/blocks/xs_ineff.py

from __future__ import annotations
from typing import List, Optional
from ..model import IneffArea, IneffFlowAreas
from .base import read_fixed_fields, _write_triplet_lines


def _parse_station_field(raw: str) -> float:
    """Blank field → 0.0 (means XS leftmost or rightmost); otherwise float."""
    return 0.0 if raw == "" else float(raw)


def _parse_elevation_field(raw: str) -> Optional[float]:
    """Blank field → None (infinite height); otherwise float."""
    return None if raw == "" else float(raw)


def parse_ineff(lines, index):
    """
    Parse #XS Ineff= block (including the Permanent Ineff= block that follows).
    Returns (IneffFlowAreas, lines_consumed).

    File format:
        #XS Ineff= N , flag
        <data lines: N*3 fields of 8 chars each — start_sta, end_sta, elevation per area>
        Permanent Ineff=
        <flags line: T or F per area, 8-char fields>
    """
    header = lines[index].strip()           # "#XS Ineff= 2 , 0"
    parts = header.split("=", 1)[1].split(",")
    n_areas = int(parts[0])
    flag = int(parts[1])
    ifa_type = "normal" if flag == 0 else "multiple_block"

    n_fields = n_areas * 3
    raw_triplets: List[str] = []
    consumed = 1
    i = index + 1

    # Read station/elevation data lines until we have n_fields values.
    # Stop early if we hit "Permanent Ineff=" (shouldn't happen, but guard anyway).
    while len(raw_triplets) < n_fields:
        line = lines[i].rstrip("\n")
        if line.strip().startswith("Permanent Ineff"):
            break
        fields = read_fixed_fields(line, 8)
        need = n_fields - len(raw_triplets)
        raw_triplets.extend(fields[:need])
        consumed += 1
        i += 1

    # Advance past "Permanent Ineff=" header line
    while not lines[i].strip().startswith("Permanent Ineff"):
        i += 1
        consumed += 1
    consumed += 1   # consume the "Permanent Ineff=" line itself
    i += 1

    # Read the T/F flags line
    flags_line = lines[i].rstrip("\n")
    flag_fields = read_fixed_fields(flags_line, 8)
    flag_fields = [f for f in flag_fields if f]
    consumed += 1

    # Build IneffArea objects
    areas: List[IneffArea] = []
    for k in range(n_areas):
        start_raw = raw_triplets[k * 3].strip()
        end_raw   = raw_triplets[k * 3 + 1].strip()
        elev_raw  = raw_triplets[k * 3 + 2].strip()

        permanent = (flag_fields[k].strip().upper() == "T") if k < len(flag_fields) else False

        areas.append(IneffArea(
            start_sta=_parse_station_field(start_raw),
            end_sta=_parse_station_field(end_raw),
            elevation=_parse_elevation_field(elev_raw),
            permanent=permanent,
        ))

    return IneffFlowAreas(ifa_type=ifa_type, areas=areas), consumed


def write_ineff(ineff: IneffFlowAreas) -> List[str]:
    """Write a #XS Ineff= block from an IneffFlowAreas (plus its paired
    Permanent Ineff= line).

    The flag (0 for 'normal', -1 for 'multiple_block') is written verbatim
    from ineff.ifa_type, preserving whichever format the chosen source used
    -- see merge_ineff() in geometry/merge.py for why "normal" areas are not
    converted.  A None elevation ("normal"-type infinite height) is written
    as a blank field.
    """
    areas = ineff.areas
    flag = 0 if ineff.ifa_type == 'normal' else -1
    lines = [f"#XS Ineff= {len(areas)} ,{flag} \n"]
    values: List[Optional[float]] = []
    for area in areas:
        values.extend([area.start_sta, area.end_sta, area.elevation])
    lines.extend(_write_triplet_lines(values))
    lines.append("Permanent Ineff=\n")
    flags = "".join(f"{'T' if a.permanent else 'F':>8}" for a in areas)
    lines.append(flags + "\n")
    return lines
