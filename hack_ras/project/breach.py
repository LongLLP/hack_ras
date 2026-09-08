# hack_ras/project/breach.py
"""
Reading levee/structure breach definitions out of a ``.p##`` plan file.

A breach lives in the **plan**, not the geometry, so the same connection
breaches differently from one plan to the next.  A plan with no breach has no
``Breach`` lines at all, which makes the presence of ``Breach Loc=`` the
enabled flag — there is no separate on/off key to read.

Block layout (one group per breach location, repeated)::

    Breach Loc=<river>,<reach>,<rs>,<is-connection>,<connection name>
    Breach Method= 0
    Breach Geom=<10 comma fields, see BREACH_GEOM_FIELDS>
    Breach Start=<8 comma fields, see decode_trigger>
    Breach Progression= 21
      <21 (time fraction, breach fraction) pairs, 8-char fields>
    Simplified Physical Breach Downcutting= 20
      <20 pairs>
    Simplified Physical Breach Widening= 20
      <20 pairs>
    Starting Notch Depth= 1            (Simplified Physical only)
    Initial Piping Diameter= 9.9       (Simplified Physical + Piping only)
    Mass Wasting Options= 0
    Breach Use User Defined Growth Ratio=-1
    ... DLBreach keys ...

Field decode
------------
Every ``Breach Geom`` and ``Breach Start`` field below was read off the RAS 7.0
GUI, not inferred: two Model_Hillside plans (Current_Model_extra p25 and p26,
2026-09-08) were authored with each control set to a distinct value — a User
Entered Data breach with asymmetric side slopes 1/1.1, and a Simplified Physical
one with 3.1/3.2 — the GUI was screenshotted, and the fields matched position by
position.  Both plans were then run, and RAS's own realised geometry came back
with exactly those slopes.  A third, independent fingerprint plan
(``tests/data/2D culvert bridge levee precip pipes/Model.p06``) reproduces the
same order, and the first five fields are corroborated by the results HDF (see
:func:`hack_ras.results.reader.read_breach_state`).

**Fields are stored even when the active method or trigger mode does not use
them.** A plan showing a trigger WS may in fact fire at a set time, and a
Simplified Physical breach still carries a stale piping coefficient.  The
``active_*`` properties return a value only in the mode that uses it; read the
raw attributes if you need what is on disk regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..geometry.blocks import xs_sta_elev
from ..utils.lines import read_lines

# Position -> meaning for ``Breach Geom=``, confirmed against the RAS GUI.
# Fields 2 and 3 are relabelled by the GUI depending on the method: "Final
# Bottom Width"/"Final Bottom Elevation" for User Entered Data, "Max Possible
# Bottom Width"/"Min Possible Bottom Elev" for Simplified Physical. They hold
# the same terminal geometry either way.
BREACH_GEOM_FIELDS = (
    "center_station",       # 1  Center Station
    "bottom_width",         # 2  Final / Max Possible Bottom Width
    "bottom_elev",          # 3  Final / Min Possible Bottom Elevation
    "left_slope",           # 4  Left Side Slope   (run/rise)
    "right_slope",          # 5  Right Side Slope  (run/rise)
    "piping",               # 6  Failure Mode: True = Piping, False = Overtopping
    "piping_coef",          # 7  Piping Coefficient
    "initial_piping_elev",  # 8  Initial Piping Elev
    "formation_time",       # 9  Breach Formation Time (hrs); blank = Simplified Physical
    "weir_coef",            # 10 Breach Weir Coef
)

# Breach Method= codes. 0 and 1 are GUI-confirmed; the DLBreach tab exists in
# the GUI but was greyed out in every plan seen, so its code is unknown and an
# unrecognized value is reported as None rather than guessed.
BREACH_METHODS = {
    0: "User Entered Data",
    1: "Simplified Physical",
}

TRIGGER_WS_ELEV = "WS Elev"
TRIGGER_WS_ELEV_DURATION = "WS Elev + Duration"
TRIGGER_SET_TIME = "Set Time"


@dataclass
class BreachTrigger:
    """Decoded ``Breach Start=`` line — when the breach fires.

    ``mode`` is one of the GUI's three "Trigger Failure at" choices:
    :data:`TRIGGER_WS_ELEV`, :data:`TRIGGER_WS_ELEV_DURATION`,
    :data:`TRIGGER_SET_TIME`.

    ``ws_elev`` is field 2, which the GUI labels differently per mode —
    "Starting WS" under WS Elev, "Immediate Initiation WS" under WS Elev +
    Duration — and leaves stored but unused under Set Time.  Use
    :attr:`starting_ws` / :attr:`immediate_initiation_ws` to get it only where
    it applies.
    """
    mode: str
    ws_elev: Optional[float] = None       # field 2
    date: str = ""                        # field 3, e.g. '01JAN2025'
    time: str = ""                        # field 4, e.g. '1213'
    threshold_ws: Optional[float] = None  # field 6, duration mode
    duration_hours: Optional[float] = None  # field 7, duration mode
    accumulate_duration: bool = False     # field 8, -1 = checked

    @property
    def starting_ws(self) -> Optional[float]:
        """Trigger WS, or None unless the mode is WS Elev."""
        return self.ws_elev if self.mode == TRIGGER_WS_ELEV else None

    @property
    def immediate_initiation_ws(self) -> Optional[float]:
        """Immediate-initiation WS, or None unless the mode is WS Elev + Duration."""
        return self.ws_elev if self.mode == TRIGGER_WS_ELEV_DURATION else None

    @property
    def set_time(self) -> str:
        """``'01JAN2025 1213'``, or '' unless the mode is Set Time."""
        if self.mode != TRIGGER_SET_TIME:
            return ""
        return f"{self.date} {self.time}".strip()

    def describe(self) -> str:
        """One-line human summary of the active trigger only."""
        if self.mode == TRIGGER_SET_TIME:
            return f"Set Time {self.set_time}"
        if self.mode == TRIGGER_WS_ELEV:
            return f"WS Elev {self.ws_elev}"
        acc = ", accumulated" if self.accumulate_duration else ""
        return (f"WS Elev + Duration: threshold {self.threshold_ws} for "
                f"{self.duration_hours} hr{acc}, immediate at {self.ws_elev}")


@dataclass
class BreachDefinition:
    """One breach location from a plan file.

    ``bottom_width`` / ``bottom_elev`` are the terminal breach geometry, and
    the side slopes are run/rise ratios (0 = vertical walls), so the widest the
    opening can get is :meth:`top_width`.
    """
    # --- Breach Loc= ---
    connection: str = ""
    river: str = ""
    reach: str = ""
    rs: str = ""
    is_connection: bool = True

    # --- Breach Method= / Breach Geom= ---
    method: Optional[int] = None
    center_station: Optional[float] = None
    bottom_width: Optional[float] = None
    bottom_elev: Optional[float] = None
    left_slope: float = 0.0
    right_slope: float = 0.0
    piping: bool = False
    piping_coef: Optional[float] = None
    initial_piping_elev: Optional[float] = None
    formation_time: Optional[float] = None
    weir_coef: Optional[float] = None

    # --- Breach Start= ---
    trigger: Optional[BreachTrigger] = None

    # --- curves and Simplified Physical extras ---
    progression: List[Tuple[float, float]] = field(default_factory=list)
    downcutting: List[Tuple[float, float]] = field(default_factory=list)
    widening: List[Tuple[float, float]] = field(default_factory=list)
    starting_notch_depth: Optional[float] = None
    initial_piping_diameter: Optional[float] = None
    mass_wasting: Optional[int] = None

    @property
    def method_name(self) -> Optional[str]:
        """``'User Entered Data'`` / ``'Simplified Physical'``, or None if unknown."""
        return BREACH_METHODS.get(self.method)

    @property
    def target(self) -> str:
        """Human label for what breaches: the connection, or river/reach/RS."""
        if self.is_connection:
            return self.connection
        return f"{self.river} / {self.reach} / RS {self.rs}"

    @property
    def active_formation_time(self) -> Optional[float]:
        """Formation time, or None under Simplified Physical (which ignores it).

        The GUI greys the control out and writes a blank field for Simplified
        Physical, but a plan switched from User Entered Data can retain a value.
        """
        if self.method == 1:
            return None
        return self.formation_time

    @property
    def active_piping_fields(self) -> dict:
        """Piping coefficient / elevation / diameter, empty under overtopping."""
        if not self.piping:
            return {}
        return {
            "piping_coef": self.piping_coef,
            "initial_piping_elev": self.initial_piping_elev,
            "initial_piping_diameter": self.initial_piping_diameter,
        }

    def breach_depth(self, crest_elev: float) -> Optional[float]:
        """Crest-to-invert height of the fully formed breach, or None.

        None when the plan has no bottom elevation.  Negative depths are
        possible on paper — a breach invert above the crest — and are returned
        as-is; :meth:`top_width` floors them at zero.
        """
        if self.bottom_elev is None:
            return None
        return crest_elev - self.bottom_elev

    def top_width(self, crest_elev: float) -> Optional[float]:
        """Widest the breach opening can get, at *crest_elev*.

        ``bottom_width + (left_slope + right_slope) * depth`` — the top of a
        trapezoid whose sides run outward at the given run/rise ratios.  With
        zero side slopes (vertical walls) this equals ``bottom_width``.

        Returns None when the plan gives no bottom width.  Pass the weir crest
        elevation at :attr:`center_station`, from
        :func:`hack_ras.geometry.conn_interp.elev_at`.
        """
        if self.bottom_width is None:
            return None
        depth = self.breach_depth(crest_elev)
        if depth is None:
            return self.bottom_width
        return self.bottom_width + (self.left_slope + self.right_slope) * max(depth, 0.0)


# ---------------------------------------------------------------------------
# Field-level decoding
# ---------------------------------------------------------------------------

def _float_or_none(text: str) -> Optional[float]:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_true(text: str) -> bool:
    return text.strip().lower() == "true"


def _fields(line: str, count: int) -> List[str]:
    """Comma fields of a ``Key=a,b,c`` line, right-padded to *count* entries."""
    parts = line.split("=", 1)[1].split(",")
    parts += [""] * (count - len(parts))
    return parts


def decode_trigger(line: str) -> BreachTrigger:
    """Decode a ``Breach Start=`` line into a :class:`BreachTrigger`.

    ``Breach Start=F1,F2,F3,F4,F5,F6,F7,F8``: F1 True selects WS Elev, F5 True
    selects WS Elev + Duration, both False means Set Time.  Confirmed against
    the GUI in all three modes.
    """
    f = _fields(line, 8)
    if _is_true(f[4]):
        mode = TRIGGER_WS_ELEV_DURATION
    elif _is_true(f[0]):
        mode = TRIGGER_WS_ELEV
    else:
        mode = TRIGGER_SET_TIME
    return BreachTrigger(
        mode=mode,
        ws_elev=_float_or_none(f[1]),
        date=f[2].strip(),
        time=f[3].strip(),
        threshold_ws=_float_or_none(f[5]),
        duration_hours=_float_or_none(f[6]),
        accumulate_duration=_float_or_none(f[7]) not in (None, 0.0),
    )


def decode_geom(line: str, breach: BreachDefinition) -> None:
    """Apply a ``Breach Geom=`` line to *breach* (see :data:`BREACH_GEOM_FIELDS`)."""
    f = _fields(line, len(BREACH_GEOM_FIELDS))
    breach.center_station = _float_or_none(f[0])
    breach.bottom_width = _float_or_none(f[1])
    breach.bottom_elev = _float_or_none(f[2])
    breach.left_slope = _float_or_none(f[3]) or 0.0
    breach.right_slope = _float_or_none(f[4]) or 0.0
    breach.piping = _is_true(f[5])
    breach.piping_coef = _float_or_none(f[6])
    breach.initial_piping_elev = _float_or_none(f[7])
    breach.formation_time = _float_or_none(f[8])
    breach.weir_coef = _float_or_none(f[9])


def decode_loc(line: str) -> BreachDefinition:
    """Start a :class:`BreachDefinition` from a ``Breach Loc=`` line.

    ``Breach Loc=<river>,<reach>,<rs>,<is-connection>,<connection>`` — the
    first three fields are blank (16/16/8 spaces) for an SA/2D connection
    breach, and field 4 says which kind it is.  Names are padded in the file
    and stripped here, so they compare directly against an HDF ``S16`` name.
    """
    f = _fields(line, 5)
    return BreachDefinition(
        river=f[0].strip(),
        reach=f[1].strip(),
        rs=f[2].strip(),
        is_connection=_is_true(f[3]),
        connection=f[4].strip(),
    )


# ---------------------------------------------------------------------------
# Plan-file reader
# ---------------------------------------------------------------------------

# Pair-table blocks inside a breach group: 'Key= N' then N 8-char pairs.
_PAIR_BLOCKS = {
    "Breach Progression=": "progression",
    "Simplified Physical Breach Downcutting=": "downcutting",
    "Simplified Physical Breach Widening=": "widening",
}

# Single-value keys inside a breach group.
_SCALAR_KEYS = {
    "Starting Notch Depth=": "starting_notch_depth",
    "Initial Piping Diameter=": "initial_piping_diameter",
}


def read_breach_definitions(plan_path: str) -> List[BreachDefinition]:
    """Read every breach location defined in a ``.p##`` plan file.

    Returns them in file order — empty for a plan with no breach.  A plan may
    define several: two-breach plans repeat the whole group, each with its own
    method, geometry, trigger and curves (confirmed on Model_Hillside
    Current_Model_extra p25, which breaches levees L4 and L7 at once with
    different progression and downcutting tables), so the curves are per-breach,
    not plan-global.

    Read through :func:`hack_ras.utils.lines.read_lines`, which strips a UTF-8
    BOM — without it a BOM'd plan's line-1 keys do not match.
    """
    lines = read_lines(plan_path)
    breaches: List[BreachDefinition] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r\n")

        if line.startswith("Breach Loc="):
            breaches.append(decode_loc(line))
            i += 1
            continue

        if not breaches:
            i += 1
            continue

        current = breaches[-1]

        if line.startswith("Breach Method="):
            value = _float_or_none(line.split("=", 1)[1])
            current.method = None if value is None else int(value)
            i += 1
            continue
        if line.startswith("Breach Geom="):
            decode_geom(line, current)
            i += 1
            continue
        if line.startswith("Breach Start="):
            current.trigger = decode_trigger(line)
            i += 1
            continue
        if line.startswith("Mass Wasting Options="):
            value = _float_or_none(line.split("=", 1)[1])
            current.mass_wasting = None if value is None else int(value)
            i += 1
            continue

        for key, attr in _SCALAR_KEYS.items():
            if line.startswith(key):
                setattr(current, attr, _float_or_none(line.split("=", 1)[1]))
                i += 1
                break
        else:
            for key, attr in _PAIR_BLOCKS.items():
                if line.startswith(key):
                    pairs, consumed = xs_sta_elev.parse_sta_elev(lines, i)
                    setattr(current, attr, pairs)
                    i += consumed
                    break
            else:
                i += 1

    return breaches


def read_plan_breach_summary(plan_path: str) -> str:
    """Multi-line human summary of a plan's breaches; '' when there are none.

    Intended for logs and QC output, not parsing.
    """
    breaches = read_breach_definitions(plan_path)
    if not breaches:
        return ""
    out = []
    for idx, b in enumerate(breaches, start=1):
        mode = "Piping" if b.piping else "Overtopping"
        out.append(
            # ASCII only: this string is printed, and a Windows console using
            # cp1252 mangles non-ASCII on the way out.
            f"breach {idx}: {b.target} - {b.method_name or f'method {b.method}'}, "
            f"{mode}, station {b.center_station}, bottom {b.bottom_width} ft "
            f"@ {b.bottom_elev}, slopes {b.left_slope}/{b.right_slope}, "
            f"trigger {b.trigger.describe() if b.trigger else 'unknown'}"
        )
    return "\n".join(out)
