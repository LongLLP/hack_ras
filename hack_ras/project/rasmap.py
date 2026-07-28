# hack_ras/project/rasmap.py
""".rasmap support for plan file operations.

Based on observed RAS Mapper behavior (GMF_DFA, 2026-07-17): layer display
names refresh themselves from the files on load, entries whose file is missing
are flagged in the GUI and purgeable via Tools > "remove missing layers", and
hand-edited sections survive a GUI save round-trip verbatim.

- `renumber_plans_in_rasmap` remaps `Base.p##` filename tokens (Filename= /
  GeometryHDF= attributes and any other occurrence) — no layers added, removed,
  renamed, or reordered.
- `remove_plans_from_rasmap` splices out a deleted plan's `<Plans>` (RASPlan)
  and `<Results>` (RASResults) layer subtrees. Relying on RAS Mapper's
  "remove missing layers" is not enough: if a DIFFERENT operation later reuses
  the freed plan number (e.g. delete p16 then renumber a survivor onto p16),
  the stale layer now points at an existing file, so it is neither purged nor
  correctable — RAS Mapper refreshes its name to the new file's title, leaving
  a duplicate. Removing the layers at delete time keeps the number truly free.
- `sort_rasmap_layers` re-sorts the RASPlan / RASResults layers into ascending
  plan-number order, mirroring `sync.sort_prj_entries` (each layer is
  redistributed across the positions its kind already occupies; other layers
  stay put).

All three preserve the file byte-for-byte apart from the tokens / layers they
target, and keep the original encoding and line endings.
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

# One <Layer ...> / <Layer .../> opening or </Layer> closing tag. Group 1 is
# "/" for a self-closing opening tag, "" otherwise.
_LAYER_TOKEN_RE = re.compile(
    r"<Layer\b[^>]*?(/?)>|</Layer\s*>", re.IGNORECASE | re.DOTALL
)


def _norm_id(raw: str, letter: str = "p") -> str:
    """'16' / 'p16' / 'P16' -> 'p16' (letter='p'); 'u12' -> 'u12' (letter='u')."""
    s = str(raw).strip().lower().lstrip(letter)
    return f"{letter}{int(s):02d}"


def _section_inner(text: str, section: str) -> tuple[int, int] | None:
    """(start, end) offsets of the content between <section ...> and </section>,
    or None if the section is absent."""
    open_m = re.compile(r"<" + section + r"\b[^>]*?>", re.IGNORECASE).search(text)
    if not open_m:
        return None
    close_m = re.compile(r"</" + section + r"\s*>", re.IGNORECASE).search(
        text, open_m.end()
    )
    if not close_m:
        return None
    return open_m.end(), close_m.start()


def _top_level_layer_blocks(text: str, start: int, end: int) -> list[tuple]:
    """Direct-child <Layer> blocks in text[start:end], as
    (block_start, block_end, opening_tag). Nested <Layer> elements are part of
    their parent's block (balanced by <Layer>/</Layer> depth); self-closing
    tags are whole blocks on their own."""
    blocks: list[tuple] = []
    depth = 0
    open_start = None
    open_tag = None
    for m in _LAYER_TOKEN_RE.finditer(text, start, end):
        tok = m.group(0)
        if tok[:2] == "</":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    blocks.append((open_start, m.end(), open_tag))
                    open_start = open_tag = None
        elif m.group(1) == "/":            # self-closing opening tag
            if depth == 0:
                blocks.append((m.start(), m.end(), tok))
        else:                               # opening tag with children
            if depth == 0:
                open_start, open_tag, depth = m.start(), tok, 1
            else:
                depth += 1
    return blocks


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand [start, end) to whole lines: back to the start of start's line,
    forward past the newline ending end's line."""
    ls = text.rfind("\n", 0, start) + 1
    nl = text.find("\n", end)
    le = nl + 1 if nl != -1 else len(text)
    return ls, le


def _layer_basename(tag: str) -> str | None:
    """Trailing filename of a layer opening tag's Filename attribute
    (e.g. '.\\GMF_DFA.p16.hdf' -> 'GMF_DFA.p16.hdf'), or None if absent."""
    fn_m = _FILENAME_RE.search(tag)
    if not fn_m:
        return None
    return fn_m.group(1).replace("\\", "/").rsplit("/", 1)[-1]


# Section name -> (layer Type it holds, filename suffix keyed to a plan).
# <Plans> RASPlan layers name the plan text file (Base.p##); <Results>
# RASResults layers name the results HDF (Base.p##.hdf). Nested "Plan"
# sub-layers inside a Results block use the .hdf token, so keying RASPlan to
# the extension-less name never matches them.
_PLAN_SECTIONS = (
    ("Plans", "rasplan", ""),
    ("Results", "rasresults", ".hdf"),
)


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


def renumber_geoms_in_rasmap(
    rasmap_path: str, base_name: str, idmap: dict
) -> int:
    """Remap every `<base_name>.g##` token in the .rasmap per idmap
    ({'g03': 'g02', ...}), in ONE pass. This covers both the `<Geometries>`
    RASGeometry layer's `Filename` (and its nested sub-layers) and the
    `GeometryHDF="...g##.hdf"` attribute on every plan layer that uses the
    geometry. Tokens whose geometry ID is not in idmap are untouched;
    everything else is byte-for-byte. Returns the number of tokens replaced.

    (RASGeometry layers INSIDE a `<Results>` block name the plan HDF
    `Base.p##.hdf`, not `Base.g##`, so they are never matched here.)
    """
    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    pattern = re.compile(re.escape(base_name) + r"\.g(\d{2})(?!\d)")
    count = 0

    def _sub(match: re.Match) -> str:
        nonlocal count
        gid = f"g{match.group(1)}"
        if gid in idmap:
            count += 1
            return f"{base_name}.{idmap[gid]}"
        return match.group(0)

    new_text = pattern.sub(_sub, text)
    if count:
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(new_text)
    return count


def remove_plans_from_rasmap(
    rasmap_path: str, base_name: str, plan_ids
) -> dict:
    """Remove the RASPlan / RASResults layer subtrees for the given plan IDs.

    Deletes, from `<Plans>`, each `<Layer Type="RASPlan" Filename=".\\<base>.p##">`
    block (with its nested Encroachment children), and from `<Results>` each
    `<Layer Type="RASResults" Filename=".\\<base>.p##.hdf">` block (with its whole
    nested result subtree). All matching top-level layers are removed, so a
    duplicated (already self-healed) layer is cleaned up too. Everything else —
    other layers, `<EventConditions>`, non-plan sections — is preserved
    byte-for-byte.

    plan_ids is any iterable of plan IDs ('p16', 'P16', or '16'). Returns
    {'plans': [pids removed from <Plans>], 'results': [pids removed from
    <Results>]} (a pid appears once per layer removed).
    """
    pids = {_norm_id(p, "p") for p in plan_ids}
    removed: dict = {"plans": [], "results": []}
    if not pids:
        return removed

    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    spans: list[tuple[int, int]] = []
    for section, want_type, suffix in _PLAN_SECTIONS:
        inner = _section_inner(text, section)
        if not inner:
            continue
        key = "plans" if section == "Plans" else "results"
        for bstart, bend, tag in _top_level_layer_blocks(text, *inner):
            type_m = _TYPE_RE.search(tag)
            if not type_m or type_m.group(1).lower() != want_type:
                continue
            basename = _layer_basename(tag)
            if basename is None:
                continue
            for pid in pids:
                if basename == f"{base_name}.{pid}{suffix}":
                    spans.append(_line_span(text, bstart, bend))
                    removed[key].append(pid)
                    break

    if spans:
        for ls, le in sorted(spans, reverse=True):
            text = text[:ls] + text[le:]
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(text)
    return removed


def remove_flows_from_rasmap(
    rasmap_path: str, base_name: str, flow_ids
) -> list:
    """Remove the `<EventConditions>` RASEventConditions layers for the given
    unsteady flow IDs.

    Deletes each `<Layer Type="RASEventConditions" Filename=".\\<base>.u##.hdf">`
    from the `<EventConditions>` section. The RASEventConditions sub-layers that
    live INSIDE a `<Results>` block name the plan HDF (Base.p##.hdf), so keying
    on the Base.u##.hdf token never touches them. This is the flow-file analogue
    of `remove_plans_from_rasmap`, used by `delete_plan` when
    `delete_unused_flow` removes a now-orphaned flow file — unlike plan numbers,
    flow numbers are not renumbered, so the leftover layers would point at
    genuinely-missing files (purgeable by RAS Mapper), but removing them keeps
    the .rasmap self-consistent without a manual step.

    flow_ids is any iterable of unsteady IDs ('u12', 'U12', or '12'). Returns
    the list of IDs whose layer was removed (one entry per layer).
    """
    fids = {_norm_id(f, "u") for f in flow_ids}
    removed: list = []
    if not fids:
        return removed

    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    inner = _section_inner(text, "EventConditions")
    if not inner:
        return removed

    spans: list[tuple[int, int]] = []
    for bstart, bend, tag in _top_level_layer_blocks(text, *inner):
        type_m = _TYPE_RE.search(tag)
        if not type_m or type_m.group(1).lower() != "raseventconditions":
            continue
        basename = _layer_basename(tag)
        if basename is None:
            continue
        for fid in fids:
            if basename == f"{base_name}.{fid}.hdf":
                spans.append(_line_span(text, bstart, bend))
                removed.append(fid)
                break

    if spans:
        for ls, le in sorted(spans, reverse=True):
            text = text[:ls] + text[le:]
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(text)
    return removed


def remove_geoms_from_rasmap(
    rasmap_path: str, base_name: str, geom_ids
) -> list:
    """Remove the `<Geometries>` RASGeometry layers for the given geometry IDs.

    Deletes each `<Layer Type="RASGeometry" Filename=".\\<base>.g##.hdf">` from
    the top-level `<Geometries>` section. The RASGeometry sub-layers that live
    INSIDE a `<Results>` block name the PLAN HDF (Base.p##.hdf), so keying on
    the Base.g##.hdf token never touches them (they go with their result via
    `remove_plans_from_rasmap`). Flow/geometry analogue of
    `remove_plans_from_rasmap`, used by `delete_plan` when delete_unused_geom
    removes a now-unreferenced geometry file.

    geom_ids is any iterable of geometry IDs ('g02', 'G02', or '2'). Returns the
    list of IDs whose layer was removed (one entry per layer).
    """
    gids = {_norm_id(g, "g") for g in geom_ids}
    removed: list = []
    if not gids:
        return removed

    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    inner = _section_inner(text, "Geometries")
    if not inner:
        return removed

    spans: list[tuple[int, int]] = []
    for bstart, bend, tag in _top_level_layer_blocks(text, *inner):
        type_m = _TYPE_RE.search(tag)
        if not type_m or type_m.group(1).lower() != "rasgeometry":
            continue
        basename = _layer_basename(tag)
        if basename is None:
            continue
        for gid in gids:
            if basename == f"{base_name}.{gid}.hdf":
                spans.append(_line_span(text, bstart, bend))
                removed.append(gid)
                break

    if spans:
        for ls, le in sorted(spans, reverse=True):
            text = text[:ls] + text[le:]
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(text)
    return removed


def result_plan_ids(rasmap_path: str, base_name: str) -> set:
    """Set of plan IDs ('p##') that have a RASResults layer in `<Results>`.

    The inverse lookup used to tell which computed plan results are NOT yet
    represented in the .rasmap (see plans.plans_with_unlisted_results)."""
    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()
    inner = _section_inner(text, "Results")
    ids: set = set()
    if not inner:
        return ids
    for bstart, bend, tag in _top_level_layer_blocks(text, *inner):
        type_m = _TYPE_RE.search(tag)
        if not type_m or type_m.group(1).lower() != "rasresults":
            continue
        basename = _layer_basename(tag)
        m = basename and re.match(
            re.escape(base_name) + r"\.(p\d\d)\.hdf$", basename
        )
        if m:
            ids.add(f"p{m.group(1)[1:]}")
    return ids


def sort_rasmap_layers(
    rasmap_path: str, base_name: str, sections: tuple = ("Plans", "Results")
) -> dict:
    """Re-sort the RASPlan / RASResults layers into ascending plan-number order.

    Mirrors `sync.sort_prj_entries`: within each section the plan-keyed layers
    are redistributed across the positions they already occupy, sorted by plan
    number. Any other top-level layer in the section (e.g. a CalculatedLayer)
    keeps its position, and everything else in the file is byte-identical.

    sections selects which to sort ('Plans', 'Results'). Returns
    {'plans': [pids in final order], 'results': [pids in final order]} for the
    sections present.
    """
    with open(rasmap_path, "r", encoding="latin-1", newline="") as f:
        text = f.read()

    type_of = {"Plans": "rasplan", "Results": "rasresults"}
    result: dict = {}
    changed = False
    for section in sections:
        want_type = type_of.get(section)
        if want_type is None:
            raise ValueError(
                f"Unknown section {section!r}; expected one of {sorted(type_of)}"
            )
        inner = _section_inner(text, section)
        if not inner:
            continue

        targets = []  # (position_span, num, pid, chunk)
        for bstart, bend, tag in _top_level_layer_blocks(text, *inner):
            type_m = _TYPE_RE.search(tag)
            if not type_m or type_m.group(1).lower() != want_type:
                continue
            basename = _layer_basename(tag)
            num_m = basename and re.match(
                re.escape(base_name) + r"\.p(\d{2})", basename
            )
            if not num_m:
                continue
            ls, le = _line_span(text, bstart, bend)
            targets.append(((ls, le), int(num_m.group(1)),
                            f"p{num_m.group(1)}", text[ls:le]))

        key = section.lower()
        if not targets:
            result[key] = []
            continue

        positions = [t[0] for t in targets]
        ordered = sorted(targets, key=lambda t: t[1])
        # Write each sorted layer back into the same ordered positions, from the
        # last position to the first so earlier offsets stay valid.
        for (ls, le), t in sorted(
            zip(positions, ordered), key=lambda p: p[0][0], reverse=True
        ):
            if text[ls:le] != t[3]:
                text = text[:ls] + t[3] + text[le:]
                changed = True
        result[key] = [t[2] for t in ordered]

    if changed:
        with open(rasmap_path, "w", encoding="latin-1", newline="") as f:
            f.write(text)
    return result
