# hack_ras/results/steady_output.py
"""
Read per-cross-section WSE from a HEC-RAS steady-flow binary output file (``.O##``).

RAS 4.x writes no results HDF, so for a steady plan run in 4.1 the ``.O##`` is the
only place the computed water surface lives.  RAS 5.0.3 through 7.0 still write the
``.O##`` for every steady run, next to the ``.p##.hdf`` and in the same layout, so
this reader is also the cross-version check of last resort.

The format is undocumented.  It was decoded 2026-10-07 against the RAS 4.1 COM
controller (``RAS41.HECRASController``) and the plan HDFs, and every WSE read here
matches RAS's own value bit for bit — see ``ai_context.md`` for the evidence.  Only
what is needed for WSE is decoded:

* little-endian, laid out in 64-byte blocks
* bytes 0-15: int32 ``n_nodes``, ``n_profiles``, ``n_reaches`` (unverified),
  ``blocks_per_profile``
* bytes 64-127: the plan's Short Identifier AT RUN TIME
* byte 128: node table, ``n_nodes`` x 64 bytes:
  int32 record pointer, RS (8), reach (16), int32 node type, river (16),
  node name (16)
* profile names, 16 bytes each, directly after the node table
* a node's record starts at ``(pointer - 1) * 64``; its WSE is the float32 at
  +128, and each later profile repeats the whole data area
  ``64 * blocks_per_profile`` bytes further on

Two traps, both found the hard way:

* **+16 and +12 are decoys.**  They equal the WSE on most cross sections and
  differ by up to 4.4 ft on some — supercritical and mixed-regime reaches among
  them (``HEC_Critical_Creek_4.1/CRITCREK.O02``: 10 of 63).
* **The river field is 16 bytes, not 32.**  The next 16 are the geometry's
  ``Node Name=`` (16 characters max in the RAS GUI; ``"Atwood Avenue"``), blank
  in most models, which hides the split.  The multi-line node DESCRIPTION is not
  stored in the ``.O##``.

SI projects store FEET in the ``.O##`` (RAS 6.0 example, 2026-10-07) and are out of
scope: :func:`read_plan_steady_wse` refuses them rather than return feet labelled
as metres.  :func:`read_steady_output` cannot tell, because the units live in the
``.prj``, not in the ``.O##``.
"""
from __future__ import annotations

import logging
import os
import re
import struct

import numpy as np

from .model import SteadyProfileResults

_BLOCK = 64
_COUNTS = struct.Struct("<4i")
_NODE = struct.Struct("<i8s16si16s16s")   # 64 bytes
_SHORT_ID = slice(64, 128)
_TABLE_OFF = 128
_PROFILE_NAME_LEN = 16
_WSE_OFF = 128
_NODE_XS = 1        # other codes: 2 culvert, 3 bridge, 4 multiple opening, 6 lateral


def _text(raw: bytes) -> str:
    return raw.decode("latin-1").strip()


def _read(o_path: str) -> bytes:
    with open(o_path, "rb") as f:
        return f.read()


def _header(data: bytes, o_path: str):
    """``(n_nodes, n_profiles, blocks_per_profile)``, validated against the file size."""
    if len(data) < _TABLE_OFF:
        raise ValueError(f"Not a HEC-RAS steady output file (too short): {o_path}")
    n_nodes, n_prof, _n_reaches, bpp = _COUNTS.unpack_from(data, 0)
    names_end = _TABLE_OFF + _NODE.size * n_nodes + _PROFILE_NAME_LEN * n_prof
    if n_nodes <= 0 or n_prof <= 0 or bpp <= 0 or names_end > len(data):
        raise ValueError(
            f"Not a HEC-RAS steady output file (header nodes={n_nodes}, "
            f"profiles={n_prof}, blocks/profile={bpp}, size={len(data)}): {o_path}")
    return n_nodes, n_prof, bpp


def read_steady_output(o_path: str) -> SteadyProfileResults:
    """
    Read per-cross-section WSE for every profile from a ``.O##`` file.

    Returns the same :class:`SteadyProfileResults` as
    :func:`hack_ras.results.reader.read_steady_profile_wse`, keyed
    ``(river, reach, station)`` with interpolated stations keeping their ``*``
    (as ``GeometryParser`` spells them).  Structure nodes (bridges, culverts,
    lateral structures, multiple openings) carry no WSE and are left out.

    Values are in the project's units as RAS stores them, which for an SI
    project is FEET — use :func:`read_plan_steady_wse`, which checks.

    Raises
    ------
    FileNotFoundError
        If ``o_path`` does not exist.
    ValueError
        If the header or a record pointer does not fit the file.
    """
    data = _read(o_path)
    n_nodes, n_prof, bpp = _header(data, o_path)
    floats = np.frombuffer(data, dtype="<f4", count=len(data) // 4)
    names_off = _TABLE_OFF + _NODE.size * n_nodes
    profile_names = [
        _text(data[names_off + _PROFILE_NAME_LEN * i:
                   names_off + _PROFILE_NAME_LEN * (i + 1)])
        for i in range(n_prof)]

    stride = _BLOCK * bpp // 4
    wse = {}
    for i in range(n_nodes):
        ptr, rs, reach, node_type, river, _name = _NODE.unpack_from(
            data, _TABLE_OFF + _NODE.size * i)
        if node_type != _NODE_XS:
            continue
        first = ((ptr - 1) * _BLOCK + _WSE_OFF) // 4
        idx = first + stride * np.arange(n_prof)
        if ptr < 1 or idx[-1] >= len(floats):
            raise ValueError(
                f"Node {_text(rs)!r} record pointer {ptr} runs past the end of {o_path}")
        wse[(_text(river), _text(reach), _text(rs))] = floats[idx].astype(np.float64)

    return SteadyProfileResults(profile_names=profile_names, wse=wse)


def steady_output_short_id(o_path: str) -> str:
    """The plan Short Identifier a ``.O##`` was written under, at run time."""
    data = _read(o_path)
    _header(data, o_path)
    return _text(data[_SHORT_ID])


def steady_output_path(plan_path: str) -> str:
    """
    The ``.O##`` beside a ``.p##`` plan file — exactly ``<base>.O##``.

    An exact name, never a ``*.o##`` pattern: unsteady plans write
    ``<base>.IC.O##`` initial-conditions files with the same header layout.

    Raises
    ------
    ValueError
        If ``plan_path`` does not end in ``.p##``.
    FileNotFoundError
        If the plan has no steady output (not run, or not a steady plan).
    """
    base, ext = os.path.splitext(plan_path)
    m = re.fullmatch(r"\.p(\d\d)", ext, re.IGNORECASE)
    if m is None:
        raise ValueError(f"Not a plan file (.p##): {plan_path}")
    for letter in ("O", "o"):
        candidate = f"{base}.{letter}{m.group(1)}"
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"No steady output (.O{m.group(1)}) for plan: {plan_path}")


def _is_si_project(prj_path: str) -> bool:
    if not os.path.isfile(prj_path):
        return False
    with open(prj_path, encoding="latin-1") as f:
        return any(line.strip() == "SI Units" for line in f)


def read_plan_steady_wse(plan_path: str):
    """
    Steady WSE for a plan from whichever results it has: ``.p##.hdf`` first,
    else the ``.O##``.

    The HDF is preferred whenever it exists, with no cross-check against the
    ``.O##``: RAS 5.0+ writes both on every steady run, so their coexistence says
    nothing about staleness.  On the ``.O##`` route, a Short Identifier that no
    longer matches the plan file logs a warning — the plan was edited after the
    run, so the results may be stale.

    Returns
    -------
    (SteadyProfileResults, str)
        The results and the path they were read from.

    Raises
    ------
    FileNotFoundError
        If the plan has neither results file.
    ValueError
        If only the ``.O##`` exists and the project uses SI units (stored in feet;
        out of scope), or the ``.O##`` is malformed.
    """
    hdf_path = plan_path + ".hdf"
    if os.path.isfile(hdf_path):
        from .reader import read_steady_profile_wse     # h5py only on this route
        return read_steady_profile_wse(hdf_path), hdf_path

    o_path = steady_output_path(plan_path)
    prj_path = os.path.splitext(plan_path)[0] + ".prj"
    if _is_si_project(prj_path):
        raise ValueError(
            f"{os.path.basename(o_path)} belongs to an SI project; .O## files store "
            f"feet and SI is not supported.  Re-run in RAS 5.0+ for a .p##.hdf.")

    from ..project.plans import read_plan_sidecar
    plan_sid = read_plan_sidecar(plan_path)["short_id"].strip()
    run_sid = steady_output_short_id(o_path)
    if plan_sid and plan_sid != run_sid:
        logging.warning(
            "%s was written by plan Short Identifier %r, but %s now says %r; "
            "the plan was edited after the run, so results may be stale.",
            os.path.basename(o_path), run_sid, os.path.basename(plan_path), plan_sid)
    return read_steady_output(o_path), o_path
