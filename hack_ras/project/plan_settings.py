# hack_ras/project/plan_settings.py
"""Unsteady plan settings: output intervals, computation interval, and the
simulation time window.

`read_plan_settings` reads one plan's settings; `set_plan_settings` is the
bulk, keyword-driven editor for them, taking the same flexible plan id-spec as
`plans.delete_plans` ('5-24', ['p05', 6], …). Both work on the `.p##` as raw
lines, so every line they are not asked to change stays byte-identical (BOM and
CRLF included) — see `hack_ras/utils/lines.py`.

These are the controls on the Unsteady Flow Analysis window's *Simulation Time
Window* and *Computation Settings* panels. The GUI label -> plan-file key
mapping is not guessable, so never key off a label:

    Computation Interval         Computation Interval=
    Mapping Output Interval      Mapping Interval=
    Hydrograph Output Interval   Output Interval=
    Detailed Output Interval     Instantaneous Interval=
    Simulation Time Window       Simulation Date=<date>,<time>,<date>,<time>

Two traps in that table: `Output Interval=` is the HYDROGRAPH interval, not the
mapping one, and the DETAILED interval is stored under the unrelated-sounding
`Instantaneous Interval=`. A plan also carries a `WQ Output Interval=` (water
quality, a different panel entirely); keys here are matched on the whole text
left of the `=`, so it is never caught by the `Output Interval` match.

The four interval dropdowns do NOT offer the same values (RAS 7.0 GUI,
user-confirmed 2026-09-11), so each is validated against its own list:

    computation   0.1SEC..0.5SEC, 1SEC..30SEC, 1MIN..30MIN, 1HOUR..12HOUR, 1DAY
    mapping       Max Profile, then the above plus 1WEEK, 1MON, 1YEAR
    hydrograph    1SEC..30SEC, 1MIN..30MIN, 1HOUR..12HOUR, 1DAY, 1WEEK/1MON/1YEAR
    detailed      Max Profile, then the same as hydrograph

Only the computation interval goes sub-second, and only it stops at 1 Day; only
mapping and detailed offer `Max Profile`, which is not an interval at all but a
literal string ("write the maximum profile only"). Tokens are HEC-DSS interval
names, so `1 Month` is stored `1MON`, not `1MONTH`. Every token here was read
back out of a GUI save of Model_Hillside/Current_Model p05 (2026-09-11), which
the user re-saved three times to fingerprint the ends of each list — none of
them is inferred.

Plan title and short identifier belong to this settings group but are NOT
reimplemented here. Changing them also has to rewrite the `.rasmap` display
names and the `.p##.hdf` title attributes, which is exactly what
`plans.retitle_plan` already does (RAS Mapper regenerates its Results layer
name from the HDF, so an ASCII-only retitle silently reverts). `title=` /
`short_id=` on `set_plan_settings` delegate there, so one call can change a
plan's identity and its settings.

Why the `.p##.hdf` is otherwise left alone: intervals and the time window are
run INPUTS that HEC-RAS writes into the HDF on the next compute, unlike the
title, which RAS Mapper reads back out of it. Editing them in the HDF would
only desynchronize it from the results it actually holds, and a plan whose
intervals or window changed has to be re-run regardless.

Steady plans carry all of these lines too — RAS writes them whatever the
solver — but only the unsteady solver reads them, and a steady plan's window is
typically `Simulation Date=,,,` (reads back as start=None / end=None).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

from hack_ras.project.plans import (
    PlanFileNotFound,
    _assert_no_active_run,
    _invalidate_model,
    _normalize_plan_id,
    _parse_ras_datetime,
    plan_path,
    read_plan_sidecar,
    retitle_plan,
)
from hack_ras.project.ras_project import RasProject
from hack_ras.resolve import expand_id_spec
from hack_ras.utils.lines import content_of, eol_of, read_lines, write_lines

logger = logging.getLogger(__name__)

# Keyword -> the plan-file key it writes. Interval keywords only; the time
# window and the title/short-ID pair are handled separately.
_INTERVAL_KEYS = {
    "computation_interval": "Computation Interval",
    "mapping_interval": "Mapping Interval",
    "hydrograph_interval": "Output Interval",
    "detailed_interval": "Instantaneous Interval",
}
_KEY_TO_ATTR = {v: k for k, v in _INTERVAL_KEYS.items()}

# The three that `output_intervals=` sets together (everything except the
# computation interval, which is a solver time step, not an output stride).
_OUTPUT_ATTRS = ("mapping_interval", "hydrograph_interval", "detailed_interval")

_WINDOW_KEY = "Simulation Date"

# The values each dropdown actually offers (RAS 7.0 GUI, user-confirmed
# 2026-09-11). The four lists are DIFFERENT, so membership is checked per
# field, not against one pooled set: only the computation interval goes
# sub-second, only it stops at 1 Day, and only the mapping and detailed
# intervals offer `Max Profile`. Writing an off-list value produces a plan the
# GUI silently rewrites, which is why membership is enforced rather than just
# the <number><unit> shape.
#
# Tokens are HEC-DSS interval names, so two do NOT spell out: `1 Month` is
# stored `1MON` and `Max Profile` is a literal string with a space, not an
# interval at all. Both are straight from a GUI save of
# Model_Hillside/Current_Model p05 (2026-09-11).
_MAX_PROFILE = "Max Profile"
_SUBSEC = ("0.1SEC", "0.2SEC", "0.3SEC", "0.4SEC", "0.5SEC")
_SEC = ("1SEC", "2SEC", "3SEC", "4SEC", "5SEC", "6SEC",
        "10SEC", "12SEC", "15SEC", "20SEC", "30SEC")
_MIN = ("1MIN", "2MIN", "3MIN", "4MIN", "5MIN", "6MIN",
        "10MIN", "12MIN", "15MIN", "20MIN", "30MIN")
_HOUR = ("1HOUR", "2HOUR", "3HOUR", "4HOUR", "6HOUR", "8HOUR", "12HOUR")
_DAY = ("1DAY",)
_LONG = ("1WEEK", "1MON", "1YEAR")      # all three GUI-confirmed

_ALLOWED = {
    "computation_interval": _SUBSEC + _SEC + _MIN + _HOUR + _DAY,
    "mapping_interval": (_MAX_PROFILE,) + _SUBSEC + _SEC + _MIN + _HOUR
                        + _DAY + _LONG,
    "hydrograph_interval": _SEC + _MIN + _HOUR + _DAY + _LONG,
    "detailed_interval": (_MAX_PROFILE,) + _SEC + _MIN + _HOUR + _DAY + _LONG,
}

_RAS_DATE_FMT = "%d%b%Y"


@dataclass
class PlanSettings:
    """One plan's simulation window, intervals, and identity.

    `start` / `end` are None when the plan's `Simulation Date=` is blank (the
    normal state for a steady plan). `window_raw` is that line's value exactly
    as stored, which is the only faithful record of RAS's end-of-day `2400`
    idiom — `end` normalizes it to the next day's 00:00.
    """
    plan_id: str
    title: str
    short_id: str
    start: datetime | None
    end: datetime | None
    window_raw: str
    computation_interval: str
    mapping_interval: str
    hydrograph_interval: str
    detailed_interval: str
    missing_keys: list[str] = field(default_factory=list)


# ---------------------------
# Value normalization (all raise ValueError before any file is touched)
# ---------------------------

def _norm_interval(value, attr: str) -> str:
    """'5 min' -> '5MIN', 'max profile' -> 'Max Profile'.

    Checked against `_ALLOWED[attr]`, because the four dropdowns offer
    different value sets — '0.1SEC' is a computation interval only, 'Max
    Profile' a mapping/detailed one only, and '1YEAR' anything but a
    computation interval.
    """
    token = re.sub(r"\s+", "", str(value)).upper()
    if token == "MAXPROFILE":
        token = _MAX_PROFILE
    allowed = _ALLOWED[attr]
    if token not in allowed:
        elsewhere = sorted(a for a, v in _ALLOWED.items()
                           if a != attr and token in v)
        hint = (f" (HEC-RAS offers it for {', '.join(elsewhere)}, not here)"
                if elsewhere else "")
        raise ValueError(
            f"{attr}={value!r} (read as {token!r}) is not a value HEC-RAS "
            f"offers for {attr}{hint}. Valid: {', '.join(allowed)}"
        )
    return token


def _norm_datetime(value, label: str) -> tuple[str, str]:
    """-> ('02JAN2025', '0101').

    Accepts a `datetime`, or a string in the plan file's own form
    ('02JAN2025,0101' or '02JAN2025 0101'). A string is written back as typed
    (after upper-casing the month and zero-padding the time), so RAS's
    end-of-day `2400` survives instead of becoming the next day's `0000`.
    """
    if isinstance(value, datetime):
        return value.strftime(_RAS_DATE_FMT).upper(), value.strftime("%H%M")
    parts = [p for p in re.split(r"[,\s]+", str(value).strip()) if p]
    if len(parts) != 2:
        raise ValueError(
            f"{label}={value!r}: expected a datetime, or a "
            f"'DDMMMYYYY,HHMM' string such as '02JAN2025,0101'."
        )
    date_tok, time_tok = parts[0].upper(), parts[1].zfill(4)
    try:
        _parse_ras_datetime(date_tok, time_tok)
    except ValueError as exc:
        raise ValueError(f"{label}={value!r} is not a valid RAS date/time: {exc}")
    return date_tok, time_tok


def _read_dt(date_tok: str, time_tok: str) -> datetime | None:
    """Parse a stored window half, tolerantly -> None if blank OR unparsable.

    Deliberately lenient: HEC-RAS stores what was typed into the time box
    without validating it as a clock time (a live Model_Hillside plan holds
    `2399`), so a reader that raised would be unable to report the very plan a
    caller is trying to inspect. `PlanSettings.window_raw` keeps the truth; the
    strictness lives on the write path (`_norm_datetime`).
    """
    try:
        return _parse_ras_datetime(date_tok, time_tok)
    except ValueError:
        return None


def _split_window(raw: str) -> list[str]:
    """'02JAN2025,0101,03JAN2025,2399' -> the 4 fields, padded to 4 if short."""
    parts = [p.strip() for p in raw.split(",")]
    parts += [""] * (4 - len(parts))
    return parts[:4]


# ---------------------------
# Read
# ---------------------------

def read_plan_settings(project: RasProject, plan_id: str) -> PlanSettings:
    """Read one plan's time window, intervals, title and short identifier.

    Reads the `.p##` only — no HDF, no .rasmap. A key the plan file does not
    carry comes back as '' and is listed in `missing_keys` rather than raising,
    so an unusual plan can still be inspected.

    Raises PlanFileNotFound if the `.p##` is missing.
    """
    pid = _normalize_plan_id(plan_id)
    path = plan_path(project, pid)
    if not os.path.isfile(path):
        raise PlanFileNotFound(f"Plan file not found: {path}")

    values: dict[str, str] = {}
    window_raw = ""
    for line in read_lines(path):
        key, sep, val = content_of(line).partition("=")
        if not sep:
            continue
        if key == _WINDOW_KEY:
            window_raw = val.strip()
        elif key in _KEY_TO_ATTR:
            values[_KEY_TO_ATTR[key]] = val.strip()

    d1, t1, d2, t2 = _split_window(window_raw)
    sidecar = read_plan_sidecar(path)
    missing = [_INTERVAL_KEYS[a] for a in _INTERVAL_KEYS if a not in values]
    if not window_raw:
        missing.append(_WINDOW_KEY)

    return PlanSettings(
        plan_id=pid,
        title=sidecar["title"],
        short_id=sidecar["short_id"],
        start=_read_dt(d1, t1),
        end=_read_dt(d2, t2),
        window_raw=window_raw,
        computation_interval=values.get("computation_interval", ""),
        mapping_interval=values.get("mapping_interval", ""),
        hydrograph_interval=values.get("hydrograph_interval", ""),
        detailed_interval=values.get("detailed_interval", ""),
        missing_keys=missing,
    )


# ---------------------------
# Write
# ---------------------------

def set_plan_settings(
    project: RasProject,
    spec,
    *,
    computation_interval=None,
    mapping_interval=None,
    hydrograph_interval=None,
    detailed_interval=None,
    output_intervals=None,
    start=None,
    end=None,
    title: str | None = None,
    short_id: str | None = None,
) -> dict:
    """Set intervals, the simulation time window, and/or the title on plans.

    `spec` selects the plans the same way `plans.delete_plans` does: '5-24',
    '5,7,19-21', ['p05', 6], or a bare 5 (see `resolve.expand_id_spec`).

    Intervals take any value from the HEC-RAS dropdowns as a string ('5MIN',
    '30SEC', '1HOUR', '3SEC'); case and internal spaces are ignored.
    `output_intervals=` sets the mapping, hydrograph and detailed intervals to
    one value in a single argument — the common "all output at N" request — and
    may not be combined with the individual three.

    `start` / `end` take a `datetime` or a 'DDMMMYYYY,HHMM' string, and are
    independent: passing only `end` rewrites the second half of
    `Simulation Date=` and leaves the first half's stored text untouched.

    `title` / `short_id` delegate to `plans.retitle_plan` (which also fixes the
    .rasmap and .p##.hdf), so they require `spec` to select exactly ONE plan —
    HEC-RAS requires plan titles to be unique, and a short identifier names the
    plan's RAS Mapper results folder.

    Everything is validated before any file is written: unknown interval
    values, unparsable dates, an end at or before the start, plans that are
    missing / orphaned / mid-run, and any requested key the plan file does not
    contain. A bad argument therefore changes NOTHING, on any plan.

    Returns `{'plans': [...], 'changed': {pid: {key: (old, new)}},
    'unchanged': [...], 'retitled': {pid: <retitle_plan report>}}`.

    Raises ValueError (bad value, no-op call, absent key, multi-plan retitle,
    orphan plan), PlanFileNotFound, or PlanRunActive.
    """
    pids = expand_id_spec(spec, kind="p")
    if not pids:
        raise ValueError(f"Plan spec {spec!r} selected no plans.")

    # --- resolve the interval keywords into one {plan-file key: value} map ---
    named = {
        "mapping_interval": mapping_interval,
        "hydrograph_interval": hydrograph_interval,
        "detailed_interval": detailed_interval,
    }
    if output_intervals is not None:
        clashes = [a for a in _OUTPUT_ATTRS if named[a] is not None]
        if clashes:
            raise ValueError(
                f"output_intervals= sets {', '.join(_OUTPUT_ATTRS)} together; "
                f"do not also pass {', '.join(clashes)}."
            )
        for attr in _OUTPUT_ATTRS:
            named[attr] = output_intervals
    named["computation_interval"] = computation_interval

    edits = {
        _INTERVAL_KEYS[attr]: _norm_interval(value, attr)
        for attr, value in named.items() if value is not None
    }

    # --- the time window ---
    new_start = _norm_datetime(start, "start") if start is not None else None
    new_end = _norm_datetime(end, "end") if end is not None else None

    # --- the retitle delegation ---
    if (title is not None or short_id is not None) and len(pids) != 1:
        raise ValueError(
            f"title/short_id change one plan at a time, but spec {spec!r} "
            f"selected {len(pids)}: {', '.join(pids)}."
        )
    if not edits and new_start is None and new_end is None \
            and title is None and short_id is None:
        raise ValueError(
            "set_plan_settings was called with nothing to change; pass at "
            "least one of the interval, start/end, or title keywords."
        )

    # --- validate every plan, and stage its new lines, before writing ---
    listed = project.model.plan_file_ids
    staged: dict[str, tuple[str, list, dict]] = {}
    for pid in pids:
        path = plan_path(project, pid)
        if not os.path.isfile(path):
            raise PlanFileNotFound(f"Plan file not found: {path}")
        if pid not in listed:
            raise ValueError(
                f"Plan '{pid}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to edit it."
            )
        _assert_no_active_run(project, pid)
        lines = read_lines(path)
        staged[pid] = (path, lines, _stage_plan(pid, lines, edits,
                                                new_start, new_end))

    # --- apply ---
    changed: dict[str, dict] = {}
    unchanged: list[str] = []
    for pid, (path, lines, diffs) in staged.items():
        if diffs:
            write_lines(path, lines)
            changed[pid] = diffs
            logger.info("%s: %s", pid, ", ".join(
                f"{k} {old!r} -> {new!r}" for k, (old, new) in diffs.items()))
        else:
            unchanged.append(pid)

    retitled = {}
    if title is not None or short_id is not None:
        pid = pids[0]
        current = read_plan_sidecar(plan_path(project, pid))
        retitled[pid] = retitle_plan(
            project, pid,
            current["title"] if title is None else title,
            short_id=current["short_id"].strip() if short_id is None
            else short_id,
        )

    _invalidate_model(project)
    return {
        "plans": pids,
        "changed": changed,
        "unchanged": unchanged,
        "retitled": retitled,
    }


def _stage_plan(pid: str, lines: list, edits: dict,
                new_start, new_end) -> dict:
    """Rewrite `lines` in place for one plan; return {key: (old, new)} for the
    lines that actually changed. Raises ValueError for a requested key the plan
    file does not contain, or a window that would end at/before it starts."""
    seen = set()
    diffs: dict[str, tuple[str, str]] = {}
    eol = eol_of(lines)

    for i, line in enumerate(lines):
        key, sep, val = content_of(line).partition("=")
        if not sep:
            continue
        if key in edits:
            seen.add(key)
            old, new = val.strip(), edits[key]
            if old != new:
                lines[i] = f"{key}={new}{eol}"
                diffs[key] = (old, new)
        elif key == _WINDOW_KEY and (new_start or new_end):
            seen.add(key)
            old = val.strip()
            fields = _split_window(old)
            if new_start:
                fields[0], fields[1] = new_start
            if new_end:
                fields[2], fields[3] = new_end
            _check_window(pid, fields)
            new = ",".join(fields)
            if old != new:
                lines[i] = f"{key}={new}{eol}"
                diffs[key] = (old, new)

    wanted = set(edits)
    if new_start or new_end:
        wanted.add(_WINDOW_KEY)
    absent = sorted(wanted - seen)
    if absent:
        raise ValueError(
            f"Plan '{pid}' has no {', '.join(repr(a) + '=' for a in absent)} "
            "line — refusing to invent one. Check the plan is an unsteady "
            "plan written by HEC-RAS."
        )
    return diffs


def _check_window(pid: str, fields: list) -> None:
    """The resulting window must run forwards. A half left blank (a steady
    plan's `Simulation Date=,,,`) is not checkable and is left alone."""
    start = _read_dt(fields[0], fields[1])
    end = _read_dt(fields[2], fields[3])
    if start is not None and end is not None and end <= start:
        raise ValueError(
            f"Plan '{pid}': simulation window would end at or before it "
            f"starts ({start:%d%b%Y %H%M} -> {end:%d%b%Y %H%M})."
        )
