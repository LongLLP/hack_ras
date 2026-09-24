# hack_ras/project/associations.py
"""Terrain / Manning's n / Infiltration layer associations of a geometry or plan.

This is RAS Mapper's **Manage Layer Associations** dialog (right-click the
Geometries or Results section): one row per geometry and per result, with a
column for each layer type. The `.rasmap` lists the layers but does NOT record
which geometry uses which. The associations live in the HDF, as attributes on
the `/Geometry` group:

| dialog column | attributes                                        | seen in    |
|---------------|---------------------------------------------------|------------|
| Terrain       | `Terrain Layername`, `Terrain Filename`           | 5.0.3, 7.0 |
| Manning's n   | `Land Cover Layername`, `Land Cover Filename`     | 7.0        |
| Infiltration  | `Infiltration Layername`, `Infiltration Filename` | 7.0        |

A `Geometry` row reads the `.g##.hdf`; a `Results` row reads the `.p##.hdf`,
whose `/Geometry` group is the copy of the geometry the plan ran with. So a
plan keeps the layers it was RUN with even after its geometry is re-associated.
GUI-confirmed on Hillside (2026-09-24): all four geometries and all 24 result
rows of the dialog match these attributes, including the existing-conditions
vs future-conditions infiltration split (p01-p11 `InfiltrationSCS`, p12-p24
`InfiltrationSCS_FutureConditions`).

Each 2D flow area group also carries copies of the same attributes. Across
every model in hack_ras_local the per-area layer names and filenames equal the
`/Geometry` ones; only the `... Date Last Modified` stamps differ. They are not
read here.

An absent attribute means "(None)" in the dialog, and older files (5.0.3)
never write the land-cover or infiltration pair. The two cases cannot be told
apart from the file, and both come back as `None`.

The dialog's other columns (% Impervious, Sediment Bed Material, Porosity And
Flow Drag, Spiral Intensity Source Factor) are deliberately NOT read: no model
on hand uses them, so their storage is unverified.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# dialog column (field name) -> HDF attribute prefix
_KINDS = (
    ("terrain", "Terrain"),
    ("mannings_n", "Land Cover"),
    ("infiltration", "Infiltration"),
)


@dataclass(frozen=True)
class LayerRef:
    """One associated layer.

    `name` is the RAS Mapper layer name the dialog shows. `filename` is the path
    as stored, relative to the project folder (`.\\Terrain\\Terrain.hdf`), and
    `path` is that resolved to an absolute path. Existence is not checked.
    """
    name: str
    filename: str
    path: str


@dataclass(frozen=True)
class LayerAssociations:
    """One row of the Manage Layer Associations dialog."""
    hdf_path: str
    terrain: Optional[LayerRef]
    mannings_n: Optional[LayerRef]
    infiltration: Optional[LayerRef]


def _decode(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    return str(value).strip()


def read_layer_associations(hdf_path: str) -> LayerAssociations:
    """Read the Terrain / Manning's n / Infiltration associations of a `.g##.hdf`
    or `.p##.hdf`.

    Raises FileNotFoundError if the file is missing, and ValueError if it has no
    `/Geometry` group. An unassociated layer is `None`.
    """
    if not os.path.isfile(hdf_path):
        raise FileNotFoundError(hdf_path)
    import h5py  # lazy: keeps the module importable without h5py installed

    folder = os.path.dirname(os.path.abspath(hdf_path))
    refs = {}
    with h5py.File(hdf_path, "r") as h:
        if "Geometry" not in h:
            raise ValueError(f"No /Geometry group in {hdf_path}")
        attrs = h["Geometry"].attrs
        for field, prefix in _KINDS:
            name = _decode(attrs.get(f"{prefix} Layername", b""))
            filename = _decode(attrs.get(f"{prefix} Filename", b""))
            if not (name or filename):
                refs[field] = None
                continue
            path = os.path.normpath(os.path.join(folder, filename)) if filename else ""
            refs[field] = LayerRef(name=name, filename=filename, path=path)
    return LayerAssociations(hdf_path=os.path.abspath(hdf_path), **refs)
