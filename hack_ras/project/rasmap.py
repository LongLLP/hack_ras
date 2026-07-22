# hack_ras/project/rasmap.py
"""Minimal .rasmap support: keep plan filename tokens in step on renumber.

Deliberately narrow, based on observed RAS Mapper behavior (GMF_DFA,
2026-07-17): layer display names refresh themselves from the files on load,
entries whose file is missing are flagged in the GUI and purgeable via
Tools > "remove missing layers", and hand-edited sections survive a GUI save
round-trip verbatim. So renumbering only needs to remap `Base.p##` filename
tokens (Filename= / GeometryHDF= attributes and any other occurrence) — no
layers are added, removed, renamed, or reordered here.
"""
from __future__ import annotations

import re

# A .rasmap layer opening tag and its Type / Filename attributes.
_LAYER_RE = re.compile(r"<Layer\b[^>]*?>", re.IGNORECASE | re.DOTALL)
_TYPE_RE = re.compile(r'Type="([^"]*)"', re.IGNORECASE)
_FILENAME_RE = re.compile(r'Filename="([^"]*)"', re.IGNORECASE)
# Leading ".\<folder>\" (either slash) of a project-relative rasmap path.
_FIRST_DIR_RE = re.compile(r"\./([^/]+)/")
# Layer type whose Filename points at a stored GIS result, not source data.
_RESULT_LAYER_TYPE = "rasresultsmap"


def source_data_folders(rasmap_path: str) -> set[str]:
    """Subfolder names the .rasmap references via any NON-results layer.

    RAS Mapper stores each layer's file with a project-relative path
    (``.\\<folder>\\<file>``). Layers of type ``RASResultsMap`` point at stored
    GIS outputs (WSE/Depth/etc. rasters); every other layer that names a
    subfolder points at *source data* — the 2D-mesh terrain (``TerrainLayer``),
    land-cover grids (``LandCover*Layer``), and map features
    (``*FeatureLayer``). This returns the set of folder names holding such source
    data, so a caller can avoid treating them as disposable result folders.

    A folder referenced *only* by ``RASResultsMap`` layers is a genuine result
    store and is not returned. A folder referenced by BOTH (e.g. a plan whose
    Short Identifier collides with the terrain folder's name, so RAS writes that
    plan's results into the terrain folder) IS returned — the non-results
    reference wins, because the folder still holds source data to protect.

    Folder names are taken verbatim from the .rasmap, matching the on-disk
    folders RAS created. Returns an empty set if no such references are found.
    """
    with open(rasmap_path, "r", encoding="latin-1", errors="ignore") as f:
        text = f.read()

    folders: set[str] = set()
    for tag in _LAYER_RE.finditer(text):
        seg = tag.group(0)
        type_m = _TYPE_RE.search(seg)
        fname_m = _FILENAME_RE.search(seg)
        if not (type_m and fname_m):
            continue
        if type_m.group(1).lower() == _RESULT_LAYER_TYPE:
            continue
        dir_m = _FIRST_DIR_RE.match(fname_m.group(1).replace("\\", "/"))
        if dir_m:
            folders.add(dir_m.group(1))
    return folders


def renumber_plans_in_rasmap(
    rasmap_path: str, base_name: str, idmap: dict
) -> int:
    """Remap every `<base_name>.p##` token in the .rasmap per idmap
    ({'p02': 'p06', ...}), in ONE pass so chained mappings cannot be applied
    twice. Tokens whose plan ID is not in idmap are untouched; everything
    else in the file is preserved byte-for-byte. Returns the number of
    tokens replaced.
    """
    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    pattern = re.compile(re.escape(base_name) + r"\.p(\d{2})(?!\d)")
    count = 0

    def _sub(match: re.Match) -> str:
        nonlocal count
        pid = f"p{match.group(1)}"
        if pid in idmap:
            count += 1
            return f"{base_name}.{idmap[pid]}"
        return match.group(0)

    new_text = pattern.sub(_sub, text)
    if count:
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(new_text)
    return count
