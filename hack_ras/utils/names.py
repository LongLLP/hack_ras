# hack_ras/utils/names.py
"""
Normalising HEC-RAS river and reach names for comparison.

River and reach names appear in HEC-RAS files inside fixed-width fields, so they
arrive padded — and the padding is not only on the outside.  A reach the GUI shows
as "Upper Reach B" is stored as ``'Upper Reach  B'`` with two interior spaces
(real case: Starkweather ``StarkweatherW``).  Any name a human types — into a
spreadsheet, a YAML config, an Excel shift table — must therefore be compared
loosely, or a correct-looking entry silently fails to match.

Use :func:`normalize_name` on BOTH sides of every river/reach comparison.  Never
compare the raw strings, and never normalise only the value read from the file.
"""
from __future__ import annotations


def normalize_name(value) -> str:
    """
    Comparison key for a river or reach name: outer whitespace stripped, interior
    whitespace runs collapsed to one space, case-folded.

    >>> normalize_name("  Upper   Reach  B ") == normalize_name("upper reach b")
    True
    """
    return " ".join(str(value).strip().split()).lower()
