# hack_ras/geometry/conn_interp.py
"""
Placing station-referenced SA/2D connection features onto the connection's
GIS centerline.

Why this is NOT ``xs_interp``
-----------------------------
For a 1D cross section, RAS stationing and GIS cut-line length are
independent — a section stationed 0-800 ft may have a 400 ft cut line — so
``xs_interp`` maps features *proportionally*, and `dev_rules.md` requires
scripts to use it rather than treating a station as a distance.

**Connections are the opposite case, and the difference is enforced by
HEC-RAS itself.**  RAS requires a connection's station/elevation length to
match its GIS centerline length to within **1 ft or 0.5%, whichever is
smaller**, and refuses to run the model otherwise (it reports the mismatch as
an error).  GIS data was bolted onto 1D modeling after the fact, whereas the
whole 2D side of RAS was built with a tight GIS-to-model coupling.

So a connection station **is** an arc-length distance along
``Connection Line=``, and applying the fractional mapping here would be wrong.

The rule applies to weir-mode connections only
----------------------------------------------
Surveyed 2026-09-08 over every connection in Model_Hillside, Model_PCA,
Model_LAX and the test fixtures, grouped by ``Conn Routing Type=`` mode and
measured against RAS's ``min(1 ft, 0.5%)`` tolerance:

===================  ====  ==========  ===========================
Mode                 n     violations  worst drift
===================  ====  ==========  ===========================
Weir/Gate/Culverts    178           0  -0.032 ft
Bridge Opening         32          12  +419.728 ft
===================  ====  ==========  ===========================

For ``Weir/Gate/Culverts`` the rule is absolute — 178 for 178, worst case
0.032 ft — and a violation is fatal: truncating Model_Hillside levee L4's profile
450 ft short of its centerline made HEC-RAS refuse to run the model
(verified deliberately).  ``Bridge Opening`` connections are exempt, because
their station/elevation data is a bridge opening profile rather than a
spillway spanning the line; 12 of 32 exceeded the tolerance in geometries that
run fine — five in LAX_River_2D.g01/g12 (up to 21.9 ft) and GMF_DFA.g01/g02's
``CTHE`` by 419.7 ft.

The named violators are a **dated snapshot of live project models**, not fixed
facts: those geometries keep being edited, so any of them may comply (or a new
one may not) by the time you look.  What is stable is the split — the rule holds
for weir mode and does not apply to bridge mode — and that is what the code
keys on.  Re-run the survey rather than trusting the counts.  Each violating connection's ASCII parse was
cross-checked against its geometry HDF and matched exactly, so these are real
geometry facts, not parse artifacts.

:func:`station_tolerance` reproduces RAS's tolerance, :func:`stationing_ok`
tests it only where it applies, and :func:`check_stationing` raises.  Placement
functions never self-check: they clamp, because arc length is the mapping RAS
uses either way — an out-of-contract connection is a geometry problem for the
caller to report, not a reason to refuse a coordinate.
"""

from __future__ import annotations

from typing import List, Tuple

from .model import Connection
from .xs_interp import _clip_by_distance, _cumulative_lengths, _walk_to_dist

# HEC-RAS's own agreement rule between station/elevation length and GIS
# centerline length: 1 ft or 0.5%, whichever is SMALLER (so the percentage
# governs only connections shorter than 200 ft).
_STATION_TOL_FT = 1.0
_STATION_TOL_FRACTION = 0.005


class ConnectionStationMismatch(ValueError):
    """A connection's weir stationing disagrees with its centerline length.

    Raised by :func:`check_stationing`.  HEC-RAS rejects such a geometry, so
    station-to-XY placement on it cannot be trusted.
    """


# ---------------------------------------------------------------------------
# Lengths and the stationing contract
# ---------------------------------------------------------------------------

def centerline_length(conn: Connection) -> float:
    """Total 2D arc length of the connection centerline, in projected units.

    Raises ValueError if the connection has no ``Connection Line=`` polyline.
    """
    if len(conn.centerline) < 2:
        raise ValueError(
            f"Connection {conn.name!r} has no Connection Line polyline "
            f"({len(conn.centerline)} point(s)); cannot measure length."
        )
    return _cumulative_lengths(conn.centerline)[-1]


def profile_length(conn: Connection) -> float:
    """Station span of the weir profile, ``max_sta - min_sta``.

    Raises ValueError if the connection has no ``Conn Weir SE=`` profile.
    """
    if not conn.weir_profile:
        raise ValueError(
            f"Connection {conn.name!r} has no Conn Weir SE profile."
        )
    return conn.weir_profile[-1][0] - conn.weir_profile[0][0]


def station_tolerance(conn: Connection) -> float:
    """HEC-RAS's allowed station/GIS length disagreement for this connection.

    ``min(1 ft, 0.5% of the centerline length)`` — so 1 ft for anything longer
    than 200 ft, and proportionally tighter below that.
    """
    return min(_STATION_TOL_FT, _STATION_TOL_FRACTION * centerline_length(conn))


def station_drift(conn: Connection) -> float:
    """Signed disagreement, ``profile_length - centerline_length``, in feet.

    Positive means the stationing runs longer than the GIS line.  Compare
    against :func:`station_tolerance`; :func:`check_stationing` does both.
    """
    return profile_length(conn) - centerline_length(conn)


def stationing_enforced(conn: Connection) -> bool:
    """Whether RAS holds this connection's stationing to its GIS length.

    True for ``Weir/Gate/Culverts`` connections.  False for ``Bridge Opening``
    and for any routing type not yet seen — the rule has only been confirmed
    for weir mode, so an unrecognized mode is reported as unenforced rather
    than failed (see the module docstring).
    """
    return conn.is_weir_mode


def stationing_ok(conn: Connection) -> bool:
    """True if the connection satisfies RAS's station/GIS length rule.

    Trivially True where the rule does not apply
    (:func:`stationing_enforced`).  Use this to flag a connection whose station
    placement is suspect; :func:`check_stationing` raises instead.

    Raises ValueError when the connection is missing either the centerline or
    the weir profile, since there is nothing to compare.
    """
    if not stationing_enforced(conn):
        return True
    return abs(station_drift(conn)) <= station_tolerance(conn)


def check_stationing(conn: Connection) -> float:
    """Strict form of :func:`stationing_ok`: return the drift or raise.

    Raises :class:`ConnectionStationMismatch` when a weir-mode connection
    breaks RAS's rule — such a geometry does not run.  Placement functions do
    not call this; they clamp and leave reporting to the caller.

    Raises ValueError (not the mismatch error) when the connection is missing
    either the centerline or the weir profile.
    """
    drift = station_drift(conn)
    tol = station_tolerance(conn)
    if stationing_enforced(conn) and abs(drift) > tol:
        raise ConnectionStationMismatch(
            f"Connection {conn.name!r}: weir stationing spans "
            f"{profile_length(conn):.3f} ft but the GIS centerline is "
            f"{centerline_length(conn):.3f} ft ({drift:+.3f} ft, tolerance "
            f"{tol:.3f} ft). HEC-RAS's rule for a {conn.mode} connection is "
            f"1 ft or 0.5%, whichever is smaller, and it refuses to run the "
            f"model otherwise; check the station/elevation table."
        )
    return drift


# ---------------------------------------------------------------------------
# Station -> geometry
# ---------------------------------------------------------------------------

def station_to_xy(conn: Connection, station: float) -> Tuple[float, float]:
    """(X, Y) on the connection centerline at *station*.

    The station is walked directly as arc length from the centerline's first
    point (see the module docstring).  A station outside ``[0, length]`` is
    clamped to the ends rather than extrapolated.

    Raises ValueError if the connection has no centerline.
    """
    points = conn.centerline
    if len(points) < 2:
        raise ValueError(
            f"Connection {conn.name!r} has no Connection Line polyline; "
            "cannot map station to XY."
        )
    cum = _cumulative_lengths(points)
    return _walk_to_dist(points, cum, station - _profile_origin(conn))


def clip_polyline(
    conn: Connection,
    sta_start: float,
    sta_end: float,
) -> List[Tuple[float, float]]:
    """Sub-polyline of the centerline spanning ``[sta_start, sta_end]``.

    Entry and exit points are interpolated and interior vertices preserved, so
    the result follows every bend of the connection.  Stations outside the line
    are clamped; a range that collapses to nothing returns the same point twice
    (callers that must not emit a zero-length feature should compare
    :func:`clamp_station` of both ends first).

    Raises ValueError if the connection has no centerline.
    """
    points = conn.centerline
    if len(points) < 2:
        raise ValueError(
            f"Connection {conn.name!r} has no Connection Line polyline."
        )
    cum = _cumulative_lengths(points)
    origin = _profile_origin(conn)
    return _clip_by_distance(points, cum, sta_start - origin, sta_end - origin)


def clamp_station(conn: Connection, station: float) -> float:
    """*station* clamped to the connection's station range."""
    origin = _profile_origin(conn)
    return max(origin, min(station, origin + centerline_length(conn)))


def elev_at(conn: Connection, station: float) -> float:
    """Weir-crest (spillway) elevation interpolated at *station*.

    This is the elevation RAS routes flow over, and the top of a breach opening.
    Stations beyond either end return the nearest end's elevation.

    Raises ValueError if the connection has no ``Conn Weir SE=`` profile.
    """
    if not conn.weir_profile:
        raise ValueError(
            f"Connection {conn.name!r} has no Conn Weir SE profile; "
            "cannot interpolate a crest elevation."
        )
    return _interp_profile(conn.weir_profile, station)


def terrain_elev_at(conn: Connection, station: float) -> float:
    """Ground elevation under the centerline at *station*.

    From ``Connection Centerline Profile=``, which RAS usually leaves empty —
    check ``conn.terrain_profile`` before calling.

    Raises ValueError if the connection has no terrain profile.
    """
    if not conn.terrain_profile:
        raise ValueError(
            f"Connection {conn.name!r} has no Connection Centerline Profile; "
            "RAS writes 0 points unless the profile was pulled from terrain."
        )
    return _interp_profile(conn.terrain_profile, station)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _profile_origin(conn: Connection) -> float:
    """First weir-profile station — the station value at centerline point 0.

    Zero in every RAS-authored file seen, but reading it rather than assuming
    keeps a non-zero origin from silently shifting every placement.
    """
    return conn.weir_profile[0][0] if conn.weir_profile else 0.0


def _interp_profile(
    profile: List[Tuple[float, float]],
    station: float,
) -> float:
    """Linear interpolation of a (station, elevation) profile, clamped at the ends."""
    if station <= profile[0][0]:
        return profile[0][1]
    if station >= profile[-1][0]:
        return profile[-1][1]
    for i in range(1, len(profile)):
        s0, e0 = profile[i - 1]
        s1, e1 = profile[i]
        if s1 >= station:
            span = s1 - s0
            if span <= 0:
                return e1
            t = (station - s0) / span
            return e0 + t * (e1 - e0)
    return profile[-1][1]
