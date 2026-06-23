# hack_ras/geometry/blocks/xs_bank_sta.py

from __future__ import annotations
from typing import Tuple


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
