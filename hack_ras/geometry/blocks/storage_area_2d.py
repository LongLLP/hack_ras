# hack_ras/geometry/blocks/storage_area_2d.py

from __future__ import annotations
from typing import List, Tuple
from .base import read_fixed_fields, _fmt


def parse_2d_points(lines, index):
    """Parse a ``Storage Area 2D Points= N`` block starting at *index*.

    The block header gives N, the number of 2D-mesh **cell-seed points** (cell
    centers) for the storage area.  The coordinate data that follows uses the
    same fixed-width layout as ``XS GIS Cut Line=``: 16-char right-justified
    fields, 4 fields (two XY pairs) per line, wrapping only on field
    boundaries.  A value is never split across a line break.

    Returns ``(points, lines_consumed)`` where ``points`` is a list of
    ``(x, y)`` tuples of length N.  ``N == 0`` (a 1D storage area, or a 2D area
    before its mesh is generated) is handled: the block is the header line only
    and ``points`` is empty.
    """
    header = lines[index].strip()  # e.g. "Storage Area 2D Points= 11970"
    n_points = int(header.split("=", 1)[1].strip())
    n_vals = n_points * 2

    points: List[Tuple[float, float]] = []
    consumed = 1
    gathered = 0
    i = index + 1

    while gathered < n_vals:
        fields = read_fixed_fields(lines[i].rstrip("\n"), 16)
        fields = fields[: (n_vals - gathered)]  # don't over-read the last line
        fields = [f for f in fields if f]        # skip empty partial fields

        floats = list(map(float, fields))
        for j in range(0, len(floats), 2):
            points.append((floats[j], floats[j + 1]))

        gathered += len(floats)
        consumed += 1
        i += 1

    return points, consumed


def format_2d_points_header(n_points: int) -> str:
    """Return the ``Storage Area 2D Points=`` header line for *n_points*.

    Reproduces HEC-RAS's own spacing — a single space after ``=`` and a single
    trailing space before the line ending (e.g. ``Storage Area 2D Points= 11970 ``,
    confirmed byte-for-byte against RAS-authored geometry).  Use this only when
    the seed count changes (e.g. dropping surplus cells); when the count is
    unchanged, leave the original header line untouched to keep the diff minimal.
    The line ending is left off — the writer supplies it.
    """
    return f"Storage Area 2D Points= {n_points} "


def format_2d_points_lines(points: List[Tuple[float, float]]) -> List[str]:
    """Return the coordinate **data lines** (no header) for a 2D-points block
    in HEC-RAS native format.

    Reuses the shared 16-char ``_fmt`` formatter (the same one that reproduces
    RAS-authored ``XS GIS Cut Line=`` blocks byte-for-byte): 4 fields, i.e. two
    XY pairs, per 64-char line.  Because every value parsed from a RAS file
    round-trips exactly through ``_fmt``, rewriting an unchanged point yields
    its original bytes — a script that moves only a few seeds produces a diff
    touching only those seeds' lines.

    An empty ``points`` list returns ``[]`` (a zero-point block is header-only).
    """
    flat: List[float] = []
    for x, y in points:
        flat.extend([x, y])
    return [
        "".join(_fmt(v, 16) for v in flat[i : i + 4]) + "\n"
        for i in range(0, len(flat), 4)
    ]
