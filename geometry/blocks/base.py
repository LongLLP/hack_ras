# hack_ras/geometry/blocks/base.py

from __future__ import annotations
from typing import List, Tuple

def read_fixed_fields(line: str, width: int = 16) -> List[str]:
    """
    Utility to break a long RAS fixed-width coordinate line into fields.
    """
    return [line[i:i+width].strip() for i in range(0, len(line), width)]
