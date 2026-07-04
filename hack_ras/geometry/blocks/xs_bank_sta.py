# hack_ras/geometry/blocks/xs_bank_sta.py

from __future__ import annotations
from typing import Tuple

from .base import _fmt


def parse_bank_sta(lines: list, index: int) -> tuple:
    """
    Parse Bank Sta= line.

    Format: Bank Sta=866,948
    Returns ((left_sta, right_sta), 1).
    """
    line = lines[index].strip()
    val_str = line.split("=", 1)[1]
    parts = val_str.split(",")
    return (float(parts[0].strip()), float(parts[1].strip())), 1


def write_bank_sta(bank_stations: Tuple[float, float]) -> str:
    """Write the Bank Sta= line.

    The line itself is comma-separated (not an 8-char block), but each value is
    rendered with the same 8-char formatter as the #Sta/Elev= block so the bank
    station text always matches a station in that block exactly, up to the same
    precision limit the 8-char field imposes.  A plain ``:g`` here (6 significant
    digits) mangled stations like 10251.75 into 10251.8 while the block said
    10251.75.
    """
    left, right = bank_stations
    return f"Bank Sta={_fmt(left, 8).strip()},{_fmt(right, 8).strip()}\n"
