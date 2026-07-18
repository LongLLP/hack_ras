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
