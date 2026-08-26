# hack_ras/project/hdf_titles.py
"""Title / short-ID attributes stored inside the `.p##.hdf` and `.g##.hdf`.

**Why this module exists (empirical, Hillside 2026-08-26).** The rest of the
package treats `.hdf` internals as cosmetic and leaves them to the next RAS run
(see the "Left alone" notes in `plans.py` / `geoms.py`). That policy is WRONG
for titles, and the failure is silent:

A `.rasmap` layer's display `Name=` is refreshed by RAS Mapper from the file the
layer's `Filename=` points at. For `<Plans>` that is the `.p##` TEXT file, so
retitling the text file is enough. But `<Results>` points at `.p##.hdf` and
`<Geometries>` points at `.g##.hdf` — so for those, RAS Mapper reads the title
out of the HDF and writes it back over whatever the `.rasmap` said. Observed
live: eleven plans were retitled in the `.p##` text files and the `.rasmap`, the
`.rasmap` verified clean, then RAS Mapper was opened and re-saved it with all
eleven `<Results>` layer names reverted to the old title. The `<Plans>` names
survived. A finished run is never re-run, so nothing would ever have corrected
it — the stale name is permanent until the HDF is edited.

The `Compute Messages (rtf)` / `(text)` datasets under `Results/Summary` also
embed the old title in their `Plan: '<title>'` banner. Those are the run's log —
a historical record of what actually executed — so they are deliberately NOT
rewritten.

**Which attributes exist is version-dependent**, so every write is
best-effort-if-present and the caller gets back the list of what was touched:

| group                        | attribute      | holds     | seen in |
|------------------------------|----------------|-----------|---------|
| `Plan Data/Plan Information` | `Plan Name`    | title     | 5.0.3, 7.0 |
| `Plan Data/Plan Information` | `Plan ShortID` | short ID  | 5.0.3, 7.0 |
| `Plan Data/Plan Information` | `Plan Title`   | title     | 7.0 only |
| `Results/Unsteady`           | `Plan Title`   | title     | unsteady runs |
| `Results/Unsteady`           | `Short ID`     | short ID  | unsteady runs |
| `Geometry`                   | `Title`        | title     | 5.0.3, 7.0 |

`Results/Steady` carries no title attributes at all (checked against the
Wisconsin Floodway 5.0.3 and 7.0 fixtures), so a steady plan's results tree is
named from `Plan Data/Plan Information` alone.

**String type fidelity.** RAS writes these as fixed-length ASCII sized exactly
to the string, but not with a uniform padding rule — `Plan Information` uses
`H5T_STR_NULLTERM` while `Results/Unsteady` uses `H5T_STR_SPACEPAD`. HDF5 gives
no way to resize an attribute in place, so `_set_str_attr` deletes and recreates
each one through the low-level API, copying the original's character set and
padding and re-sizing to the new string. Writing through the high-level
`attrs[k] = value` instead would silently normalize every one of them to h5py's
own convention.
"""
from __future__ import annotations

import os

# group path -> ((attribute, which)), which in {'title', 'short'}.
_PLAN_ATTRS = (
    ("Plan Data/Plan Information", (("Plan Name", "title"),
                                    ("Plan Title", "title"),
                                    ("Plan ShortID", "short"))),
    ("Results/Unsteady", (("Plan Title", "title"),
                          ("Short ID", "short"))),
)
_GEOM_ATTRS = (
    ("Geometry", (("Title", "title"),)),
)


def _set_str_attr(h5py, obj, name: str, value: str) -> None:
    """Replace a fixed-length string attribute, preserving cset and strpad.

    Reads the existing attribute's HDF5 string type, then deletes and recreates
    the attribute at the new length with the same character set and padding.
    """
    aid = h5py.h5a.open(obj.id, name.encode("latin-1"))
    tid = aid.get_type()
    cset, strpad = tid.get_cset(), tid.get_strpad()
    del aid

    import numpy as np

    raw = value.encode("latin-1")
    size = max(1, len(raw))          # HDF5 forbids a zero-length string type
    new_tid = h5py.h5t.C_S1.copy()
    new_tid.set_size(size)
    new_tid.set_cset(cset)
    new_tid.set_strpad(strpad)

    h5py.h5a.delete(obj.id, name.encode("latin-1"))
    sid = h5py.h5s.create(h5py.h5s.SCALAR)
    new_aid = h5py.h5a.create(obj.id, name.encode("latin-1"), new_tid, sid)
    # mtype MUST be passed explicitly. Left to infer one from the numpy dtype,
    # h5py picks a memory type that reserves a byte for a NUL terminator, and
    # the NULLTERM->NULLTERM conversion then truncates the last character —
    # 'Alpha' silently lands as 'Alph'. Handing HDF5 the same type on both
    # sides makes the write a straight copy. RAS itself stores these exactly
    # sized with no terminator, so the full string must survive.
    new_aid.write(np.array(raw, dtype=f"S{size}"), mtype=new_tid)


def _apply(hdf_path: str, spec, values: dict) -> list:
    """Write the (group, attribute) pairs in spec that are present in the file.

    Returns ['<group>/<attr>', ...] for the attributes actually written. A
    missing file, group, or attribute is skipped, not an error — which
    attributes exist depends on the RAS version and on whether the plan has
    results (see the module docstring).
    """
    if not os.path.isfile(hdf_path):
        return []
    import h5py  # lazy: keeps the module importable without h5py installed

    written: list = []
    with h5py.File(hdf_path, "r+") as h:
        for group, attrs in spec:
            if group not in h:
                continue
            obj = h[group]
            for attr, which in attrs:
                if attr not in obj.attrs:
                    continue
                _set_str_attr(h5py, obj, attr, values[which])
                written.append(f"{group}/{attr}")
    return written


def retitle_plan_hdf(hdf_path: str, title: str, short_id: str) -> list:
    """Update the plan title / short-ID attributes in a `.p##.hdf`.

    Best-effort: absent file, groups, or attributes are skipped. Run-log
    datasets under `Results/Summary` are left alone on purpose. Returns the
    list of attribute paths written.
    """
    return _apply(hdf_path, _PLAN_ATTRS, {"title": title, "short": short_id})


def retitle_geom_hdf(hdf_path: str, title: str) -> list:
    """Update `Geometry/Title` in a `.g##.hdf`. Returns the paths written."""
    return _apply(hdf_path, _GEOM_ATTRS, {"title": title})
