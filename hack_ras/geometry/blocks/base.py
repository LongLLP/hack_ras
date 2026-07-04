# hack_ras/geometry/blocks/base.py

from __future__ import annotations
from typing import List, Optional, Tuple

def read_fixed_fields(line: str, width: int = 16) -> List[str]:
    """
    Utility to break a long RAS fixed-width coordinate line into fields.
    """
    return [line[i:i+width].strip() for i in range(0, len(line), width)]


def _fmt(v: float, width: int = 8) -> str:
    """Format *v* right-justified in *width* characters using 'g' notation."""
    for prec in range(width - 1, 0, -1):
        s = f"{v:.{prec}g}"
        if len(s) <= width:
            return s.rjust(width)
    return f"{v:.2g}".rjust(width)


def _fmt_or_blank(v: Optional[float], width: int = 8) -> str:
    """Format *v* right-justified in *width* characters, or a blank field if None."""
    return " " * width if v is None else _fmt(v, width)


def _write_triplet_lines(values: List[Optional[float]]) -> List[str]:
    """
    Chunk a flat list of 3-field-per-record values (e.g. Manning's n or IFA
    triplets) into 8-char fixed-width lines.  A value of None (e.g. a
    "normal" IFA's blank/infinite-height elevation) is written as a blank
    field rather than formatted.

    HEC-RAS never splits a triplet across two lines — it packs whole triplets,
    up to 3 per line (9 of the 10 available 8-char fields), then wraps.
    Confirmed by exhaustive inspection of every #Mann= and #XS Ineff= block in
    tests/data/Baxter/Baxter.g02 and tests/data/Beaver/beaver.g01: every data
    line is 24, 48, or 72 chars — never 80. A flat 10-values-per-line chunk
    (fine for 2-field #Sta/Elev= pairs) desyncs a triplet's fields across the
    line break whenever the record count isn't a multiple of 10, which
    HEC-RAS's own reader cannot recover from.
    """
    return [
        "".join(_fmt_or_blank(v, 8) for v in values[i : i + 9]) + "\n"
        for i in range(0, len(values), 9)
    ]
