# hack_ras/results/times.py
"""
Every HEC-RAS time convention hack_ras relies on, in one place.

Readers return times as ``numpy.datetime64[ms]`` arrays (or ``datetime`` for a
single value). Strings are only for talking to RAS: `format_stamp` writes RAS's
own spelling, and `stamp_index` finds a stamp given in any spelling.

Measured rules (HEC-RAS 7.0; evidence below)
--------------------------------------------
Origin.  Summary Output time-of-max rows, ``Breach at Time (Days)``, the
computation-block clock and the time-series ``Time`` dataset are decimal days
from the plan's own ``Simulation Start Time`` -- NOT midnight of the start date.
The test fixture starts at 10:00: every plan's largest time-of-max equals its
run length (0.1667 d for 4 h), and the p06 breach set for 12:37 stores
0.10903 d = 2 h 37 m. An earlier note said midnight; Hillside's 00:00 start
could not tell the two apart.

Restart.  A plan started from another plan's restart file counts from its OWN
start. PCA GMF_DFA p07 (restart from p06) runs 0 -> 8 d from 02JAN2026, not
from p06's 01JAN2026.

Midnight.  Plan files spell it both ways -- Hillside ``01JAN2025,2400``, PCA p01
``03JAN2026,0000`` (project/plans.py handles both). Restart FILE NAMES use
``2400`` even when the plan wrote ``0000`` (p01 ends 03JAN 0000 and writes
``GMF_DFA.p01.02JAN2026 2400.rst``), so a restart name's date cannot be
text-matched to its plan. Inside the HDF, stamps and attributes have always
been ``00:00:00`` -- 255 midnight stamps across all 115 unsteady HDFs on disk.
`parse_stamp` accepts ``24:00:00`` anyway; it costs nothing.

Interval.  The HDF's ``Base Output Interval``, and so the time series, is the
plan's MAPPING interval, not its ``Output Interval``: PCA p01 has Mapping 30MIN,
Output 10MIN, and 97 stamps over 2 days. Every 'Maximum from Time Series' value
has that resolution.

Ramp-up.  The 2D initial-conditions ramp-up (``UNET D2 TotalICTime`` per 2D
area) runs before the clock starts and leaves no trace in the output: PCA p17
(a p01 clone with ``HDF Write Warmup=-1`` and a 2 h ramp-up) wrote no extra
datasets, no negative times, and WSE identical to p01. The time series starts
at 0 on every plan seen, although its first value is sometimes stored as -0.0.

Precision.  Summary times are float32 days, whose spacing is ~5 ms at half a
day and ~0.3 s at 30 days, so `days_to_datetime64` rounds to the second.

Never-wet cells.  A 2D cell whose maximum never clears its minimum elevation
still gets a time-of-max, and it means nothing: across 99 plan HDFs, 142,042 of
the cells with time exactly 0 were such dry cells, and 224 more dry cells carry
the first computation step (1 s) instead. Time 0 alone is NOT the test -- 285
cells with time 0 are genuinely wet (they start wet and recede; LAX p01). The
readers report no time (NaT / None) for a cell whose maximum is within
`DRY_TOL` of its minimum elevation, whatever time is stored -- and for every
perimeter dummy cell (NaN minimum elevation), which has no result at all but
still stores time 0 (1,043 of PCA p01's 13,055 cells).

Not verified.  Pre-7.0 unsteady output (none on disk), and written 1D warm-up
steps (GMF_DFA has no 1D reach, so p17 could not exercise ``HDF Write Warmup``
for them).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

STAMP_FORMAT = "%d%b%Y %H:%M:%S"
TS_DATES = ("Results/Unsteady/Output/Output Blocks/Base Output"
            "/Unsteady Time Series/Time Date Stamp")
PLAN_INFO = "Plan Data/Plan Information"
# Wet means the WSE clears the cell minimum by this much. Same value, same
# meaning, as hack_ras.gis.wse_surface.DRY_TOL (a test pins them together).
DRY_TOL = 0.01


def _text(raw) -> str:
    if isinstance(raw, (bytes, np.bytes_)):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw).strip()


def parse_stamp(raw) -> datetime:
    """
    A HEC-RAS time stamp -> ``datetime``.

    Takes bytes or str, any case (``01Jan2025`` and ``01JAN2025``), and the
    ``24:00:00`` spelling of midnight.

    Raises
    ------
    ValueError
        If it is not ``DDMonYYYY HH:MM:SS``.
    """
    s = _text(raw)
    if s.endswith("24:00:00"):
        return datetime.strptime(s[:-8] + "00:00:00", STAMP_FORMAT) + timedelta(days=1)
    return datetime.strptime(s, STAMP_FORMAT)


def parse_stamps(raw) -> np.ndarray:
    """An array of HEC-RAS stamps -> ``datetime64[ms]``."""
    return np.array([parse_stamp(r) for r in np.ravel(raw)], dtype="datetime64[ms]")


def format_stamp(t) -> str:
    """
    ``datetime`` / ``datetime64`` -> RAS's own spelling, ``'01JAN2025 12:37:00'``.

    Empty string for None or NaT, so a missing time writes as a blank cell.
    """
    if t is None:
        return ""
    if isinstance(t, np.datetime64):
        if np.isnat(t):
            return ""
        t = t.astype("datetime64[ms]").item()
    return t.strftime(STAMP_FORMAT).upper()


def to_datetimes(values) -> list:
    """``datetime64`` array -> list of ``datetime`` (None for NaT), for openpyxl."""
    arr = np.asarray(values, dtype="datetime64[ms]")
    return [None if np.isnat(v) else v.item() for v in arr]


def days_to_datetime64(days, start: datetime) -> np.ndarray:
    """
    Decimal days from the simulation START TIME -> ``datetime64[ms]``.

    Rounded to the whole second (see Precision in the module docstring).
    """
    origin = np.datetime64(start, "ms")
    ms = np.round(np.asarray(days, dtype=np.float64) * 86_400.0) * 1000.0
    return origin + ms.astype("timedelta64[ms]")


# ---- readers on an OPEN h5py file ------------------------------------------

def hdf_start_time(hdf) -> datetime:
    """``Simulation Start Time`` of an open plan HDF."""
    return parse_stamp(hdf[PLAN_INFO].attrs["Simulation Start Time"])


def hdf_stamps(hdf) -> np.ndarray:
    """The output time stamps of an open plan HDF, ``datetime64[ms]``."""
    return parse_stamps(hdf[TS_DATES][()])


def hdf_summary_times(hdf, days) -> np.ndarray:
    """Summary Output time-of-max days of an open plan HDF -> ``datetime64[ms]``."""
    return days_to_datetime64(days, hdf_start_time(hdf))


def stamp_index(hdf, when, source: str = "") -> int:
    """
    Position of time stamp `when` in an open plan HDF's output stamps.

    `when` may be any spelling `parse_stamp` accepts, or a ``datetime`` /
    ``datetime64``.

    Raises
    ------
    ValueError
        If it does not parse, or is not one of the output stamps.
    """
    stamps = hdf_stamps(hdf)
    try:
        target = (np.datetime64(when, "ms")
                  if isinstance(when, (datetime, np.datetime64))
                  else np.datetime64(parse_stamp(when), "ms"))
    except ValueError:
        raise ValueError(
            f"{when!r} is not a HEC-RAS time stamp (expected e.g. "
            f"'01JAN2025 12:00:00').") from None
    hits = np.flatnonzero(stamps == target)
    if hits.size == 0:
        raise ValueError(
            f"Time stamp {when!r} not found{' in ' + source if source else ''}. "
            f"The {len(stamps)} output stamps run from "
            f"{format_stamp(stamps[0])} to {format_stamp(stamps[-1])}.")
    return int(hits[0])
