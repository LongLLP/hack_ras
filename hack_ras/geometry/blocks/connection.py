# hack_ras/geometry/blocks/connection.py
"""Parsers for the ``Connection=`` block family (SA/2D connections).

A connection block is a header line followed by loose ``Conn*`` /
``Connection*`` key lines and three fixed-width data blocks:

    Connection=L4 Ozark-Holmes ,2769228.5307336,1086596.4776751
    Connection Desc=
    Connection Line=17
      <17 XY pairs, 16-char fields, 2 pairs per line>
    Connection Centerline Profile=0
    Connection Last Edited Time=Sep/04/2026 10:13:42
    Connection Up SA=RockCr
    Connection Dn SA=Interior
    Conn Routing Type= 1
    Conn Weir WD=2
    Conn Weir Coef=2.6
    Conn Weir SE= 59
      <59 station/elevation pairs, 8-char fields, 5 pairs per line>

The three data blocks reuse formats this package already reads, so the field
mechanics are shared rather than re-implemented:

* ``Connection Line=`` is byte-identical in layout to ``XS GIS Cut Line=``
  (16-char right-justified fields, four per line, wrapping only on field
  boundaries) — :func:`hack_ras.geometry.blocks.xs_gis.parse_cutline` reads it.
* ``Conn Weir SE=`` and ``Connection Centerline Profile=`` use the
  ``#Sta/Elev=`` layout (8-char fields, ten per line) —
  :func:`hack_ras.geometry.blocks.xs_sta_elev.parse_sta_elev` reads them.

Both header forms tolerate the padding RAS writes (``Conn Weir SE= 59 ``).
A zero-count block is header-only, which is common: ``Conn Weir SE= 0`` on a
bridge connection and ``Connection Centerline Profile=0`` on nearly every
connection (208 of 221 across Model_Hillside, Model_PCA, Model_LAX and the test
fixtures).

The header's trailing two fields are the label anchor RAS draws the connection
name at, NOT a geometry point — they are carried on the model for round-trip
fidelity but must never be treated as part of the centerline.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import xs_gis, xs_sta_elev


def parse_connection_header(line: str) -> Tuple[str, Optional[Tuple[float, float]]]:
    """Parse a ``Connection=name,x,y`` header line.

    Returns ``(name, label_xy)``.  ``label_xy`` is ``None`` when the header
    carries no coordinates or they are not numeric — RAS writes the label
    anchor for GUI drawing only, and a hand-edited file may omit it.
    """
    body = line.split("=", 1)[1]
    parts = body.split(",")
    name = parts[0].strip()
    label_xy: Optional[Tuple[float, float]] = None
    if len(parts) >= 3:
        try:
            label_xy = (float(parts[1]), float(parts[2]))
        except ValueError:
            label_xy = None
    return name, label_xy


def parse_connection_line(lines, index):
    """Parse a ``Connection Line= N`` block starting at *index*.

    Returns ``(points, lines_consumed)`` — N ``(x, y)`` tuples in projected map
    units, the connection's centerline.  Delegates the fixed-width read to the
    cut-line parser (identical layout; see the module docstring).
    """
    cutline, consumed = xs_gis.parse_cutline(lines, index)
    return cutline.points, consumed


def parse_conn_sta_elev(lines, index):
    """Parse a ``Conn Weir SE= N`` or ``Connection Centerline Profile= N`` block.

    Returns ``(pairs, lines_consumed)`` — N ``(station, elevation)`` tuples.
    Stations are distance along the connection centerline, not a fractional
    system (see :mod:`hack_ras.geometry.conn_interp`).
    """
    return xs_sta_elev.parse_sta_elev(lines, index)


def parse_float_field(line: str) -> Optional[float]:
    """Value of a single-value ``Key=value`` line, or None if blank/non-numeric."""
    text = line.split("=", 1)[1].strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_text_field(line: str) -> str:
    """Value of a single-value ``Key=value`` line as stripped text.

    Storage-area names are padded to 16 characters in the file; stripping here
    means a caller comparing against an HDF ``S16`` name needs no extra work.
    """
    return line.split("=", 1)[1].strip()
