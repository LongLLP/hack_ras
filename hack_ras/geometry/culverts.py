# hack_ras/geometry/culverts.py

"""Read culvert groups from a geometry file — ASCII or HDF.

Culverts are read from the ASCII ``.g##`` by default and from the sibling
``.g##.hdf`` only when asked, because **the HDF is not a superset**:
``Geometry/Structures/Culvert Groups/Attributes`` has no solution-criteria
column and the field appears nowhere else under ``Geometry/Structures``, so an
HDF-only read silently loses it.  What the HDF *does* add is RAS's own decoded
enum labels (``Shape Name``, ``Chart Desc``, ``Scale Desc``).  Pre-5.0 HEC-RAS
(4.1 and older) wrote no HDF at all, which is the other reason the ASCII path is
primary rather than a fallback.

Five ASCII keywords carry culvert groups.  ``grep "^Culvert="`` alone finds a
minority of them — ``Multiple Barrel Culv=`` is the most common form in
practice, and RAS rewrites the keyword automatically when a group's barrel
count crosses 1:

===========================  ===========================================
keyword                      context
===========================  ===========================================
``Multiple Barrel Culv=``    1D bridge/culvert node, >1 barrel
``Culvert=``                 1D bridge/culvert node, single barrel
``Connection Culv=``         SA/2D connection
``LW Culv=``                 lateral structure
``IW Culv=``                 inline structure
===========================  ===========================================

The four "grouped" forms share ONE comma-separated layout::

    <kw>=shape,rise,span,length,n_top,ent_loss,exit_loss,chart,scale,
         US_inv,DS_inv,barrels,name,solution_criteria,us_distance[,use_momentum]

followed by a station line holding two 8-char fixed-width fields per barrel
(upstream station, downstream station), 10 fields per line, wrapping only on
field boundaries.

``Culvert=`` is the lone outlier: it has no barrel-count field and no station
line.  Its single barrel's stations sit inline instead, at positions 11 and 13,
which shifts everything after by one — name 14, solution criteria 15,
us_distance 16, use_momentum 17.

The trailing ``use_momentum`` field is OPTIONAL and absent in older files
(15-field grouped / 16-field single), so field count is never assumed.

Sibling lines follow the group and are keyword-prefixed (``Culvert ``,
``Conn Culv ``, ``LW Culv ``, ``IW Culv ``).  All three were verified exact
against the HDF; each has a defined meaning when absent:

==================  =========================  =======================
sibling             HDF column                 absent means
==================  =========================  =======================
``Bottom n``        ``Mann Bottom``            equals ``Mann Top``
``Bottom Depth``    ``Depth for Bottom Mann``  0.0
``Depth Blocked``   ``Depth Blocked``          0.0
==================  =========================  =======================

Barrel *name* rows are keyword-specific (``BC Culvert Barrel=`` for 1D,
``Conn Culvert Barrel=`` for 2D; lateral/inline structures have none).  They
reorder unpredictably on save when barrels share identical stations, so the
barrel COUNT is authoritative and is never inferred from the name rows.

Field positions 1-13 and 15 were confirmed uniquely against RAS-written HDF
over 220 culvert groups spanning four keywords; the two flag fields
(solution criteria, use momentum) were confirmed by a purpose-built RAS 7.0 GUI
experiment, preserved as the ``SterpCreek.g03`` and ``Model.g04`` fixtures.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .blocks.base import read_fixed_fields


# --------------------------------------------------------------------------
# keyword tables
# --------------------------------------------------------------------------

# form name -> the four grouped keywords (shared layout, barrel count + station line)
_GROUPED_KEYWORDS: Dict[str, str] = {
    "Connection Culv=": "connection",
    "Multiple Barrel Culv=": "multi_barrel",
    "LW Culv=": "lateral",
    "IW Culv=": "inline",
}

# the single-barrel 1D outlier (inline stations, no barrel count)
_SINGLE_KEYWORD = "Culvert="
_SINGLE_FORM = "single_barrel"

# Longest-first so "Multiple Barrel Culv=" is tested before "Culvert=" can be
# considered, and so no keyword is shadowed by a prefix of another.
_ALL_KEYWORDS: Tuple[str, ...] = tuple(
    sorted(list(_GROUPED_KEYWORDS) + [_SINGLE_KEYWORD], key=len, reverse=True)
)

_SIBLING_PREFIX: Dict[str, str] = {
    "Connection Culv=": "Conn Culv ",
    "Multiple Barrel Culv=": "Culvert ",
    "LW Culv=": "LW Culv ",
    "IW Culv=": "IW Culv ",
    "Culvert=": "Culvert ",
}

_BARREL_NAME_PREFIXES = ("BC Culvert Barrel=", "Conn Culvert Barrel=")

# Lines that end a culvert group's region.
_BOUNDARIES = ("River Reach=", "Type RM Length", "Connection=")

# Decoded labels harvested from RAS-written HDF (`Shape Name`, `Chart Desc`,
# `Scale Desc`).  DELIBERATELY PARTIAL: only codes actually observed are
# mapped, and everything else resolves to None rather than a guessed label.
# HEC-RAS defines more shapes and ~60 FHWA charts than appear here.
SHAPE_NAMES: Dict[int, str] = {
    1: "Circular",
    2: "Box",
    5: "Arch",
    9: "Conspan Arch",
}

CHART_DESCS: Dict[int, str] = {
    1: "1 - Concrete Pipe Culvert",
    2: "2 - Corrugated Metal Pipe Culvert",
    8: "8 - Flared wingwalls",
    41: "41- Arch; Corrugated metal",
    60: "60- Span/Rise ratio approximate 2:1",
}

SCALE_DESCS: Dict[Tuple[int, int], str] = {
    (1, 1): "1 - Square edge entrance with headwall",
    (1, 3): "3 - Groove end entrance; pipe projecting from fill",
    (2, 1): "1 - Headwall",
    (2, 3): "3 - Pipe projecting from fill",
    (8, 1): "1 - Wingwall flared 30 to 75 deg.",
    (8, 2): "2 - Wingwall flared 90 or 15 deg.",
    (8, 3): "3 - Wingwall flared 0 deg. (sides extended straight)",
    (41, 1): "1 - 90 Degree headwall",
    (60, 1): "1 - 0 degree wing wall angle",
}

# Confirmed in the RAS 7.0 GUI by setting each option and re-reading the file.
SOLUTION_CRITERIA: Dict[int, str] = {
    0: "Computed Flow Control",
    1: "Inlet Control",
    2: "Outlet Control",
}

CIRCULAR_SHAPE = 1


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


@dataclass
class CulvertGroup:
    """One culvert group — the unit RAS calls a "Culvert Group" in its editor.

    ``barrels`` identical barrels share every dimension and coefficient here;
    only their stations differ (``barrel_stations``).
    """

    name: str
    form: str  # connection | multi_barrel | lateral | inline | single_barrel
    keyword: str  # the ASCII keyword this group was read from

    # structure identity — exactly one addressing scheme is populated
    connection: Optional[str] = None  # SA/2D connection name
    river: Optional[str] = None
    reach: Optional[str] = None
    rs: Optional[str] = None

    # barrel geometry
    shape: int = 0
    rise: Optional[float] = None
    span: Optional[float] = None  # None when the shape is circular — see below
    length: Optional[float] = None
    barrels: int = 1

    # roughness / losses
    mann_top: Optional[float] = None
    mann_bottom: Optional[float] = None
    bottom_depth: float = 0.0
    depth_blocked: float = 0.0
    entrance_loss: Optional[float] = None
    exit_loss: Optional[float] = None

    # FHWA inlet configuration
    chart: Optional[int] = None
    scale: Optional[int] = None

    # elevations / placement
    us_invert: Optional[float] = None
    ds_invert: Optional[float] = None
    us_distance: Optional[float] = None

    # flags
    solution_criteria: Optional[int] = None  # None on the HDF path — not stored there
    use_momentum: bool = False

    # per-barrel data
    barrel_stations: List[Tuple[Optional[float], Optional[float]]] = field(
        default_factory=list
    )
    barrel_names: List[str] = field(default_factory=list)

    @property
    def shape_name(self) -> Optional[str]:
        return SHAPE_NAMES.get(self.shape)

    @property
    def chart_desc(self) -> Optional[str]:
        return CHART_DESCS.get(self.chart) if self.chart is not None else None

    @property
    def scale_desc(self) -> Optional[str]:
        if self.chart is None or self.scale is None:
            return None
        return SCALE_DESCS.get((self.chart, self.scale))

    @property
    def solution_criteria_name(self) -> Optional[str]:
        if self.solution_criteria is None:
            return None
        return SOLUTION_CRITERIA.get(self.solution_criteria)

    @property
    def is_circular(self) -> bool:
        return self.shape == CIRCULAR_SHAPE

    @property
    def diameter(self) -> Optional[float]:
        """Barrel diameter for a circular culvert, else None.

        Circular culverts are dimensioned by ``rise`` alone; see ``span``.
        """
        return self.rise if self.is_circular else None

    @property
    def structure_id(self) -> str:
        """Readable key for the structure this group sits on."""
        if self.connection is not None:
            return self.connection
        return " / ".join(str(p) for p in (self.river, self.reach, self.rs))

    def __str__(self) -> str:  # pragma: no cover - convenience only
        # ASCII only: this gets printed to Windows consoles running cp1252,
        # where a fancy inch/prime glyph raises UnicodeEncodeError.
        size = (
            f"{self.rise} dia" if self.is_circular else f"{self.rise}x{self.span}"
        )
        return (
            f"{self.structure_id} :: {self.name} "
            f"({self.shape_name or self.shape}, {size}, {self.barrels} barrel(s))"
        )


# --------------------------------------------------------------------------
# scalar helpers
# --------------------------------------------------------------------------


def _num(raw: str) -> Optional[float]:
    """Parse one comma field as a float; a blank field is None."""
    raw = raw.strip()
    if raw == "":
        return None
    return float(raw)


def _int(raw: str) -> Optional[int]:
    v = _num(raw)
    return None if v is None else int(v)


def _at(fields: List[str], idx: int) -> str:
    """Field *idx* (0-based), or "" when the line is short.

    Trailing fields are genuinely optional in older files, so a short line is
    normal input rather than an error.
    """
    return fields[idx] if 0 <= idx < len(fields) else ""


def _f32(value) -> Optional[float]:
    """Round a float32 HDF value back to the decimals a human typed into RAS.

    The HDF stores culvert attributes as float32, so a length entered as 106.2
    reads back as 106.19999694824219.  float32 carries about 7 significant
    decimal digits, so formatting to 7 recovers the authored value exactly
    without inventing precision.
    """
    if value is None:
        return None
    f = float(value)
    if f != f:  # NaN — RAS's "unset" for US Distance
        return None
    return float(f"{f:.7g}")


def _decode(value) -> str:
    return value.decode(errors="replace").strip() if isinstance(value, bytes) else str(value).strip()


def match_keyword(line: str) -> Optional[str]:
    """Return the culvert keyword *line* starts with, or None.

    ``Culvert=`` is matched only as an exact prefix, so the sibling lines
    (``Culvert Bottom n=`` and friends) never register as a new group.
    """
    for kw in _ALL_KEYWORDS:
        if line.startswith(kw):
            return kw
    return None


# --------------------------------------------------------------------------
# ASCII
# --------------------------------------------------------------------------


def _read_station_line(lines: List[str], index: int, n_values: int):
    """Read *n_values* 8-char fixed-width station fields starting at *index*.

    Returns ``(values, lines_consumed)``.  Mirrors the wrapping rule used by
    every other fixed-width RAS block: 10 fields per line, wrapping only on
    field boundaries, so a value is never split across a line break.
    """
    values: List[Optional[float]] = []
    consumed = 0
    i = index
    while len(values) < n_values and i < len(lines):
        raw = lines[i].rstrip("\n")
        # A station line is pure fixed-width numbers.  Anything with an "=" is
        # the next key=value block, which means this group's station line is
        # missing (seen in hand-edited files) -- stop rather than misparse it.
        if "=" in raw:
            break
        fields = read_fixed_fields(raw, 8)
        fields = fields[: n_values - len(values)]
        if not any(f for f in fields):
            break
        try:
            parsed = [_num(f) for f in fields]
        except ValueError:
            break
        values.extend(parsed)
        consumed += 1
        i += 1
    return values, consumed


def _parse_group(
    lines: List[str],
    index: int,
    keyword: str,
    *,
    river: Optional[str],
    reach: Optional[str],
    rs: Optional[str],
    connection: Optional[str],
):
    """Parse the culvert group whose data line is at *index*.

    Returns ``(CulvertGroup, lines_consumed)``.
    """
    fields = lines[index].split("=", 1)[1].split(",")
    grouped = keyword in _GROUPED_KEYWORDS
    form = _GROUPED_KEYWORDS[keyword] if grouped else _SINGLE_FORM

    shape = _int(_at(fields, 0)) or 0
    rise = _num(_at(fields, 1))
    raw_span = _num(_at(fields, 2))

    # Circular culverts are dimensioned by rise (diameter) alone.  RAS writes a
    # BLANK span when a shape is switched to circular, then NORMALIZES that
    # blank to equal rise on any later save -- so the field is either empty or
    # RAS-derived, never independent information.  A span differing from rise
    # has never been observed on a circular culvert.  Report None either way so
    # callers cannot accidentally compare a derived value.
    span = None if shape == CIRCULAR_SHAPE else raw_span

    group = CulvertGroup(
        name=_at(fields, 12 if grouped else 13).strip(),
        form=form,
        keyword=keyword,
        shape=shape,
        rise=rise,
        span=span,
        length=_num(_at(fields, 3)),
        mann_top=_num(_at(fields, 4)),
        entrance_loss=_num(_at(fields, 5)),
        exit_loss=_num(_at(fields, 6)),
        chart=_int(_at(fields, 7)),
        scale=_int(_at(fields, 8)),
    )

    if keyword == "Connection Culv=":
        group.connection = connection
    else:
        group.river, group.reach, group.rs = river, reach, rs

    consumed = 1
    if grouped:
        # ..., US invert, DS invert, barrels, name, ...
        group.us_invert = _num(_at(fields, 9))
        group.ds_invert = _num(_at(fields, 10))
        group.barrels = _int(_at(fields, 11)) or 1
        name_idx = 12
        stations, used = _read_station_line(lines, index + 1, group.barrels * 2)
        group.barrel_stations = [
            (stations[i], stations[i + 1]) for i in range(0, len(stations) - 1, 2)
        ]
        consumed += used
    else:
        # ..., US invert, US station, DS invert, DS station, name, ...
        group.us_invert = _num(_at(fields, 9))
        group.ds_invert = _num(_at(fields, 11))
        group.barrels = 1
        group.barrel_stations = [(_num(_at(fields, 10)), _num(_at(fields, 12)))]
        name_idx = 13

    group.solution_criteria = _int(_at(fields, name_idx + 1))
    group.us_distance = _num(_at(fields, name_idx + 2))
    # ASCII stores -1 for true; the field is absent entirely in older files.
    group.use_momentum = _num(_at(fields, name_idx + 3)) not in (None, 0)

    # --- sibling lines + barrel names -----------------------------------
    prefix = _SIBLING_PREFIX[keyword]
    bottom_n = None
    i = index + consumed
    while i < len(lines):
        ln = lines[i]
        if match_keyword(ln) or ln.startswith(_BOUNDARIES):
            break
        if ln.startswith(prefix) and "=" in ln:
            key = ln.split("=", 1)[0][len(prefix) :].strip()
            raw = ln.split("=", 1)[1]
            if key == "Bottom n":
                bottom_n = _num(raw)
            elif key == "Bottom Depth":
                group.bottom_depth = _num(raw) or 0.0
            elif key == "Depth Blocked":
                group.depth_blocked = _num(raw) or 0.0
        elif ln.startswith(_BARREL_NAME_PREFIXES):
            parts = ln.split("=", 1)[1].split(",")
            if len(parts) > 1:
                group.barrel_names.append(parts[1].strip())
        i += 1

    # An absent "Bottom n" line means the bottom roughness equals the top.
    group.mann_bottom = bottom_n if bottom_n is not None else group.mann_top
    return group, consumed


def read_culverts_ascii(path: str) -> List[CulvertGroup]:
    """Read every culvert group from an ASCII ``.g##`` geometry file.

    Covers all five keywords.  Groups are returned in file order.
    """
    with open(path, "r", encoding="latin-1") as fh:
        lines = fh.read().splitlines()

    out: List[CulvertGroup] = []
    river = reach = rs = connection = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("River Reach="):
            parts = line.split("=", 1)[1].split(",")
            river = parts[0].strip()
            reach = parts[1].strip() if len(parts) > 1 else ""
            rs = None
        elif line.startswith("Type RM Length"):
            parts = line.split("=", 1)[1].split(",")
            rs = parts[1].strip() if len(parts) > 1 else None
        elif line.startswith("Connection="):
            connection = line.split("=", 1)[1].split(",")[0].strip()
        else:
            keyword = match_keyword(line)
            if keyword is not None:
                group, consumed = _parse_group(
                    lines,
                    i,
                    keyword,
                    river=river,
                    reach=reach,
                    rs=rs,
                    connection=connection,
                )
                out.append(group)
                i += consumed
                continue
        i += 1
    return out


# --------------------------------------------------------------------------
# HDF
# --------------------------------------------------------------------------


def read_culverts_hdf(path: str) -> List[CulvertGroup]:
    """Read culvert groups from a ``.g##.hdf`` geometry file.

    Adds RAS's own decoded enum labels but CANNOT supply
    ``solution_criteria`` — the HDF has no such column — so that field is left
    None.  Raises ``KeyError`` if the file holds no culvert groups.
    """
    import h5py  # local import: h5py is only needed on the HDF path

    with h5py.File(path, "r") as handle:
        structures = handle["Geometry/Structures"]
        attrs = structures["Attributes"][:]
        groups = structures["Culvert Groups/Attributes"][:]

        ident: Dict[int, tuple] = {}
        for row in range(len(attrs)):
            ident[row] = (
                _decode(attrs["Connection"][row]),
                _decode(attrs["River"][row]),
                _decode(attrs["Reach"][row]),
                _decode(attrs["RS"][row]),
            )

        out: List[CulvertGroup] = []
        for row in groups:
            sid = int(row["Structure ID"])
            connection, river, reach, rs = ident.get(sid, ("", "", "", ""))
            shape = int(row["Shape"])
            group = CulvertGroup(
                name=_decode(row["Name"]),
                form="connection" if connection else _SINGLE_FORM,
                keyword="<hdf>",
                shape=shape,
                rise=_f32(row["Rise"]),
                # RAS's HDF writer fills Span = Rise for circular culverts; that
                # is derived, not authored, so it is suppressed here exactly as
                # on the ASCII path.
                span=None if shape == CIRCULAR_SHAPE else _f32(row["Span"]),
                length=_f32(row["Length"]),
                barrels=int(row["Barrels"]),
                mann_top=_f32(row["Mann Top"]),
                mann_bottom=_f32(row["Mann Bottom"]),
                bottom_depth=_f32(row["Depth for Bottom Mann"]) or 0.0,
                depth_blocked=_f32(row["Depth Blocked"]) or 0.0,
                entrance_loss=_f32(row["Entrance Loss"]),
                exit_loss=_f32(row["Exit Loss"]),
                chart=int(row["Chart"]),
                scale=int(row["Scale"]),
                us_invert=_f32(row["US Invert"]),
                ds_invert=_f32(row["DS Invert"]),
                us_distance=_f32(row["US Distance"]),
                solution_criteria=None,  # not stored in the geometry HDF
                # The HDF stores this flag as 1 where the ASCII stores -1.
                use_momentum=int(row["Use Momentum"]) != 0,
            )
            if connection:
                group.connection = connection
            else:
                group.river, group.reach, group.rs = river, reach, rs
            out.append(group)
        return out


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------


def read_culverts(path: str, *, prefer_hdf: bool = False) -> List[CulvertGroup]:
    """Read culvert groups from a geometry file, ASCII or HDF.

    *path* may be either a ``.g##`` or a ``.g##.hdf``.  ASCII is preferred by
    default: it is the only geometry source for ``solution_criteria``, and RAS
    4.1 and older wrote no HDF at all.  Pass ``prefer_hdf=True`` to use the
    HDF's decoded enum labels when a sibling HDF exists, accepting the loss of
    ``solution_criteria``.
    """
    if path.lower().endswith(".hdf"):
        return read_culverts_hdf(path)

    if prefer_hdf and os.path.isfile(path + ".hdf"):
        try:
            return read_culverts_hdf(path + ".hdf")
        except (KeyError, OSError):
            pass  # no culvert table in the HDF — fall through to the ASCII
    return read_culverts_ascii(path)
