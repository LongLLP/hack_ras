# hack_ras/project/flows.py
"""Flow file operations for BOTH flow kinds: renumber (single or bulk), insert
a numbering gap, compact, reorder, clone, delete.

The third file-type subsystem alongside plans.py and geoms.py, and the closer
analogue of geoms.py: a flow file is a SHARED DEPENDENCY (many plans point at
one via `Flow File=`), so renumbering one rewrites that reference in every
referencing plan — the cross-reference that makes this a subsystem rather than
a rename. Like geometry and unlike plans, there is no "current flow" key in the
.prj, so nothing global to repoint.

TWO INDEPENDENT NAMESPACES. Steady (.f##) and unsteady (.u##) numbering are
separate — f01 and u01 legitimately coexist in one project. Every ID this
module accepts must therefore carry its kind prefix ('u12', 'f03'); a bare
number raises ValueError instead of guessing a namespace. That is a deliberate
asymmetry with plans.py/geoms.py, where a bare number is unambiguous. Moving
across kinds (u01 -> f01) is refused too: that would be a file-format
conversion, not a rename.

What the two kinds do and don't share:
- `Flow File=` in a .p## is the plan's flow reference and holds an f## OR a
  u## — the SAME key for both kinds.
- in the .prj they are different keys: `Unsteady File=u##` vs `Flow File=f##`
  (historical: the first HEC-RAS had no unsteady). Because IDs here are always
  prefixed, the .prj rewrite matches on the parsed ID and never has to branch
  on the key.
- family files: `.u##` plus its `.u##.hdf` preprocessor sidecar; steady is a
  single `.f##` (no .hdf sidecar exists — verified across every local model).
  Both candidates are listed and filtered by os.path.isfile, so disk is the
  authority either way. There is no `.x##`-equivalent for flows.
- the `.rasmap` has an `<EventConditions>` RASEventConditions layer keyed
  `Base.u##.hdf`; steady flow has NO rasmap presence at all. The
  RASEventConditions layers nested inside a `<Results>` block name
  `Base.p##.hdf`, so keying on the `Base.u##` token never touches them — in a
  real 6-plan model, 4 of 5 EC layers were results-nested.

NOT touched, deliberately:
- `Restart Filename=` lines INSIDE a .u file are PLAN-keyed, not flow-keyed
  (they name a `Base.p##.<stamp>.rst`). Renumbering a flow renames the file
  around them and leaves the line alone; rewriting those is plans.py's job.
- `.u##.hdf` internals and the `Flow Filename` attr in each `.p##.hdf` — a
  renumbered results HDF keeps stale provenance until RAS recomputes it, and
  hack_ras must not edit a binary to fix that.
- `.b##` / `.O##` run artifacts embed flow TITLES, not numbers.

A `.p##.tmp.hdf` on a plan that uses the flow means HEC-RAS is mid-run on it —
operations refuse with FlowRunActive rather than pull files out from under a
running simulation.
"""
from __future__ import annotations

import logging
import os

from hack_ras.project.plans import (
    _prj_flow_key,
    _read_plan_ref,
    plan_path,
)
from hack_ras.project.ras_project import RasProject
from hack_ras.project.rasmap import (
    remove_flows_from_rasmap,
    renumber_flows_in_rasmap,
)
from hack_ras.resolve import expand_id_spec
from hack_ras.utils.lines import content_of, eol_of, read_lines, write_lines

logger = logging.getLogger(__name__)

_KIND_LETTER = {"unsteady": "u", "steady": "f"}
_LETTER_KIND = {v: k for k, v in _KIND_LETTER.items()}


class FlowFileNotFound(FileNotFoundError):
    """A referenced flow file (.u## or .f##) was not found on disk."""


class FlowIdInUse(FileExistsError):
    """The target flow ID is already taken (listed in the .prj or on disk)."""


class FlowInUse(RuntimeError):
    """The flow is still referenced by a plan's `Flow File=`."""


class DuplicateFlowTitle(ValueError):
    """The new flow title collides with an existing flow's title.

    HEC-RAS requires every flow title within a project to be unique. The check
    is scoped to the same kind, matching how RAS presents steady and unsteady
    flows in separate file lists.
    """


class FlowRunActive(RuntimeError):
    """A plan using this flow has a `.p##.tmp.hdf` — HEC-RAS is running it."""


# ---------------------------
# ID helpers
# ---------------------------

def _normalize_flow_id(raw: str) -> str:
    """'u2' / 'U02' -> 'u02'; 'f3' -> 'f03'. The kind prefix is REQUIRED.

    Raises ValueError for a bare number ('02'), an unknown prefix, or a number
    outside 1-99. Steady and unsteady numbering are independent namespaces, so
    there is no safe default kind to assume.
    """
    s = str(raw).strip().lower()
    if not s or s[0] not in _LETTER_KIND:
        raise ValueError(
            f"Flow ID must carry its kind prefix — 'u12' (unsteady) or 'f03' "
            f"(steady), got {raw!r}. Steady and unsteady numbering are "
            "independent namespaces, so a bare number is ambiguous."
        )
    letter, digits = s[0], s[1:]
    if not digits.isdigit():
        raise ValueError(f"Invalid flow ID: {raw!r}")
    n = int(digits)
    if not 1 <= n <= 99:
        raise ValueError(f"Flow number out of range 1-99: {raw!r}")
    return f"{letter}{n:02d}"


def _flow_num(fid: str) -> int:
    return int(fid[1:])


def _kind_of(fid: str) -> str:
    """'u02' -> 'unsteady'; 'f01' -> 'steady'."""
    return _LETTER_KIND[fid[0]]


def _letter_of_kind(kind: str) -> str:
    if kind not in _KIND_LETTER:
        raise ValueError(
            f"Unknown flow kind {kind!r}; expected 'unsteady' or 'steady'."
        )
    return _KIND_LETTER[kind]


def flow_path(project: RasProject, flow_id: str) -> str:
    """Absolute path of a flow file next to the .prj (existence not checked)."""
    return os.path.join(project.folder,
                        f"{project.base_name}.{_normalize_flow_id(flow_id)}")


def _invalidate_model(project: RasProject) -> None:
    """Drop the cached ProjectModel so the next access re-parses the .prj."""
    project.__dict__.pop("model", None)


def _listed_ids(project: RasProject, letter: str) -> list[str]:
    """The .prj's flow IDs of one kind, in the order listed."""
    if letter == "u":
        return list(project.model.unsteady_file_ids)
    return list(project.model.steady_file_ids)


def _flow_id_in_use(project: RasProject, fid: str) -> bool:
    """True if fid is listed in the .prj or has a file (or .hdf) on disk."""
    if fid in _listed_ids(project, fid[0]):
        return True
    path = flow_path(project, fid)
    return os.path.exists(path) or os.path.exists(path + ".hdf")


def _read_flow_title(path: str) -> str:
    for line in read_lines(path):
        if line.startswith("Flow Title="):
            return content_of(line)[len("Flow Title="):].strip()
    return ""


def _expand_flow_spec(spec) -> list[str]:
    """Expand a mixed-kind id spec, e.g. 'u09-u11,u13,f02' or ['u09', 'f2'].

    EVERY number must carry a kind prefix, including both endpoints of a range
    ('u09-u11', not 'u09-11'), and a range may not cross kinds. One spec may
    mix kinds across tokens. Returns a sorted list of unique prefixed IDs
    (steady first, then unsteady).
    """
    if isinstance(spec, str):
        spec = spec.split(",")
    elif isinstance(spec, int):
        raise ValueError(
            f"Flow id spec must carry kind prefixes ('u12', 'f03'), got "
            f"{spec!r} — a bare number is ambiguous."
        )
    tokens = [str(t).strip() for t in spec if str(t).strip()]
    grouped: dict[str, list[str]] = {}
    for token in tokens:
        parts = token.split("-")
        letters = []
        for part in parts:
            p = part.strip().lower()
            if not p or p[0] not in _LETTER_KIND:
                raise ValueError(
                    f"Flow id spec token {token!r} must carry its kind prefix "
                    "('u09-u11', 'f02') — a bare number is ambiguous."
                )
            letters.append(p[0])
        if len(set(letters)) > 1:
            raise ValueError(
                f"Flow id spec token {token!r} mixes kinds — a range cannot "
                "cross the steady/unsteady namespaces."
            )
        grouped.setdefault(letters[0], []).append(token)
    out: list[str] = []
    for letter, toks in grouped.items():
        out.extend(expand_id_spec(toks, kind=letter))
    return sorted(set(out))


# ---------------------------
# Flow-keyed files & cross-references
# ---------------------------

def _plans_using_flow(project: RasProject, fid: str) -> list[str]:
    """Plan IDs (listed in the .prj, file present) whose Flow File= is fid."""
    hits = []
    for pid in project.model.plan_file_ids:
        ppath = plan_path(project, pid)
        if os.path.isfile(ppath) and _read_plan_ref(ppath, "Flow File") == fid:
            hits.append(pid)
    return hits


def _assert_no_flow_run_active(project: RasProject, fid: str) -> None:
    for pid in _plans_using_flow(project, fid):
        tmp = f"{project.base_name}.{pid}.tmp.hdf"
        if os.path.exists(os.path.join(project.folder, tmp)):
            raise FlowRunActive(
                f"{tmp} exists — HEC-RAS appears to be running plan '{pid}', "
                f"which uses flow '{fid}'. Finish or stop the run first."
            )


def _flow_family_names(project: RasProject, fid: str) -> list[str]:
    """Existing filenames (relative to the project folder) keyed to fid: the
    flow file itself and its .hdf preprocessor sidecar.

    Both candidates are listed for both kinds and filtered by os.path.isfile —
    no steady .f##.hdf has ever been observed, but disk is the authority here
    exactly as it is in plans._family_names.
    """
    base = project.base_name
    candidates = [f"{base}.{fid}", f"{base}.{fid}.hdf"]
    return [n for n in candidates
            if os.path.isfile(os.path.join(project.folder, n))]


def _renamed_flow_family_name(name: str, base: str, old: str, new: str) -> str:
    """Counterpart of one family filename under the new flow ID."""
    stem = f"{base}.{old}"
    if name.startswith(stem):                   # .u##/.f##, .u##.hdf
        return f"{base}.{new}" + name[len(stem):]
    raise ValueError(f"Not a flow-family filename for {old}: {name!r}")


def _rewrite_flow_refs_in_plans(project: RasProject, idmap: dict) -> list[str]:
    """Rewrite `Flow File=` references in every listed plan, one pass with the
    complete mapping (chain-safe). Returns 'p##: old -> new' strings.

    A plan's `Flow File=` holds an f## or a u##, and the IDs in idmap are
    prefixed, so one lookup serves both kinds. A malformed plan with no
    `Flow File=` line at all (observed in the wild) is skipped.
    """
    report = []
    for pid in project.model.plan_file_ids:
        ppath = plan_path(project, pid)
        if not os.path.isfile(ppath):
            continue
        lines = read_lines(ppath)
        for i, line in enumerate(lines):
            c = content_of(line)
            if c.startswith("Flow File="):
                fid = c[len("Flow File="):].strip()
                if fid in idmap:
                    lines[i] = f"Flow File={idmap[fid]}" + line[len(c):]
                    write_lines(ppath, lines)
                    report.append(f"{pid}: {fid} -> {idmap[fid]}")
                break  # exactly one Flow File= per plan
    for entry in report:
        logger.info("plan flow reference updated: %s", entry)
    return report


# ---------------------------
# Public operations
# ---------------------------

def renumber_flows(project: RasProject, mapping: dict) -> dict:
    """Renumber several flow files at once: {'u01': 'u02', 'u02': 'u01', ...}.

    Every ID must carry its kind prefix, and no entry may cross kinds
    (u01 -> f01 is refused: that is a format conversion, not a rename).
    Everything is validated before any file is touched: every source must exist
    and be listed in the .prj under its own key, no two sources may share a
    target, and a target may only be occupied if its occupant is itself being
    moved by this mapping. Chains and cycles are handled automatically (a
    '<name>.renumtmp' hop breaks cycles), so a straight swap is just a 2-cycle.

    Renames the flow family (.u##/.f## and a .u##.hdf sidecar if present), then
    applies the complete mapping in ONE pass to the .prj (`Unsteady File=` /
    `Flow File=`, selected by the ID's own prefix), to `Flow File=` in EVERY
    plan that uses a renumbered flow, and to `Base.u##` tokens in the .rasmap.
    One pass matters: sequential per-entry application would corrupt chained
    mappings (u02->u06 while u06->u12).

    Returns {'files': [(old, new), ...], 'plan_refs': [...],
    'rasmap_tokens': int}.
    """
    idmap: dict = {}
    for old_raw, new_raw in mapping.items():
        old, new = _normalize_flow_id(old_raw), _normalize_flow_id(new_raw)
        if old[0] != new[0]:
            raise ValueError(
                f"Cannot renumber across flow kinds: {old} -> {new}. Steady "
                "(.f##) and unsteady (.u##) are different file formats — "
                "converting between them is not a renumbering."
            )
        if old == new:
            raise ValueError(f"Old and new flow IDs are the same: {old}")
        if old in idmap:
            raise ValueError(f"Duplicate source flow ID: {old}")
        idmap[old] = new
    if len(set(idmap.values())) != len(idmap):
        raise ValueError(f"Duplicate target flow IDs in mapping: {idmap}")

    for old in idmap:
        old_path = flow_path(project, old)
        if not os.path.isfile(old_path):
            raise FlowFileNotFound(f"Flow file not found: {old_path}")
        if old not in _listed_ids(project, old[0]):
            raise ValueError(
                f"Flow '{old}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to renumber it."
            )
        _assert_no_flow_run_active(project, old)
    for new in idmap.values():
        if new in idmap:
            continue  # occupied now, but its occupant is being moved too
        if _flow_id_in_use(project, new):
            raise FlowIdInUse(
                f"Flow ID '{new}' is already in use. (If it is a stale .prj "
                "entry with no file on disk, run sync_prj first.)"
            )

    base = project.base_name
    folder = project.folder
    pairs = []
    for old, new in idmap.items():
        for name in _flow_family_names(project, old):
            pairs.append((name, _renamed_flow_family_name(name, base, old, new)))
    sources = {src for src, _ in pairs}
    for src, dst in pairs:
        if dst not in sources and os.path.exists(os.path.join(folder, dst)):
            raise FlowIdInUse(
                f"Cannot rename {src} -> {dst}: target file already exists "
                "and is not part of this renumbering."
            )

    # Execute: rename whatever has a free target; break cycles via a temp name.
    pending = list(pairs)
    deferred = []
    while pending:
        ready = [(s, d) for s, d in pending
                 if not os.path.exists(os.path.join(folder, d))]
        if ready:
            for src, dst in ready:
                os.rename(os.path.join(folder, src), os.path.join(folder, dst))
            pending = [p for p in pending if p not in ready]
        else:
            src, dst = pending.pop(0)
            tmp = src + ".renumtmp"
            os.rename(os.path.join(folder, src), os.path.join(folder, tmp))
            deferred.append((tmp, dst))
    for tmp, dst in deferred:
        os.rename(os.path.join(folder, tmp), os.path.join(folder, dst))

    # .prj — one pass. The ID's prefix decides which key it belongs to, so a
    # u-mapping can never match an 'Flow File=f##' (steady) line, or vice versa.
    prj_lines = read_lines(project.prj_path)
    eol = eol_of(prj_lines)
    for i, line in enumerate(prj_lines):
        c = content_of(line)
        for key in ("Unsteady File=", "Flow File="):
            if c.startswith(key):
                fid = c[len(key):].strip()
                if fid in idmap and _prj_flow_key(fid) == key:
                    prj_lines[i] = f"{key}{idmap[fid]}{eol}"
                break
    write_lines(project.prj_path, prj_lines)

    plan_refs = _rewrite_flow_refs_in_plans(project, idmap)

    rasmap_path = os.path.join(folder, f"{base}.rasmap")
    rasmap_tokens = 0
    if os.path.isfile(rasmap_path):
        rasmap_tokens = renumber_flows_in_rasmap(rasmap_path, base, idmap)

    _invalidate_model(project)
    logger.info(
        "renumbered %d flow(s): %d file(s), %d plan ref(s), "
        "%d rasmap token(s)",
        len(idmap), len(pairs), len(plan_refs), rasmap_tokens,
    )
    return {"files": pairs, "plan_refs": plan_refs,
            "rasmap_tokens": rasmap_tokens}


def renumber_flow(project: RasProject, old_id: str, new_id: str) -> None:
    """Rename flow old_id to new_id — the single-entry case of
    renumber_flows(); see there for everything that gets renamed/updated."""
    renumber_flows(project, {old_id: new_id})


def insert_flow_gap(project: RasProject, at_id: str, count: int) -> dict:
    """Shift every listed flow of at_id's KIND numbered >= at_id up by count,
    freeing at_id .. at_id+count-1 for new flow files.

    The kind comes from at_id's own prefix ('u05' shifts unsteady only), so the
    other namespace is never disturbed. Returns {old_id: new_id} for what moved.
    """
    at_flow = _normalize_flow_id(at_id)
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")

    letter = at_flow[0]
    at = _flow_num(at_flow)
    mapping = {}
    for fid in _listed_ids(project, letter):
        if _flow_num(fid) < at:
            continue
        n = _flow_num(fid) + count
        if n > 99:
            raise ValueError(
                f"Shifting '{fid}' by {count} exceeds {letter}99 — cannot "
                "insert gap."
            )
        mapping[fid] = f"{letter}{n:02d}"

    if mapping:
        renumber_flows(project, mapping)
    return mapping


def compact_flows(
    project: RasProject,
    kinds: tuple = ("unsteady", "steady"),
) -> dict:
    """Renumber each requested kind's listed flows to a contiguous 01..N by
    ascending number, filling gaps (u01,u03,u06 -> u01,u02,u03).

    The two namespaces are compacted independently — pass kinds=("unsteady",)
    to leave steady numbering alone (or the reverse). Both kinds' mappings are
    applied in ONE renumber_flows call, so validation still happens before any
    file is touched. Returns the combined {old_id: new_id} mapping of what moved
    (empty if everything requested is already contiguous).
    """
    mapping = {}
    for kind in kinds:
        letter = _letter_of_kind(kind)
        for i, fid in enumerate(
                sorted(_listed_ids(project, letter), key=_flow_num), start=1):
            target = f"{letter}{i:02d}"
            if fid != target:
                mapping[fid] = target
    if mapping:
        renumber_flows(project, mapping)
    return mapping


def reorder_flows(project: RasProject, order) -> dict:
    """Renumber one kind's listed flows into the given order as 01..N.

    `order` is the COMPLETE list of that kind's current flow IDs, written in the
    order you want them to end up: ['u02', 'u01'] swaps the two. The kind is
    taken from the IDs themselves and every ID must agree — the namespaces are
    independent, so reordering both means two calls.

    The complete list is required on purpose: naming only the flows you want to
    move would make the outcome depend on flows you never mentioned. Every
    listed flow of that kind must appear exactly once — a missing, duplicated,
    unknown, or mixed-kind ID raises ValueError before any file is touched.
    Because positions come from the list, a kind with gaps gets compacted too.

    Returns the {old_id: new_id} mapping of what moved (empty if `order` is
    already the current numbering).
    """
    ids = [_normalize_flow_id(f) for f in order]
    if not ids:
        raise ValueError("order is empty — nothing to reorder.")
    letters = {f[0] for f in ids}
    if len(letters) > 1:
        raise ValueError(
            f"order mixes flow kinds ({sorted(letters)}) — steady and unsteady "
            "numbering are independent, so reorder them one kind at a time."
        )
    letter = ids[0][0]

    dupes = sorted({f for f in ids if ids.count(f) > 1}, key=_flow_num)
    if dupes:
        raise ValueError(f"Duplicate flow IDs in order: {dupes}")

    listed = set(_listed_ids(project, letter))
    unknown = [f for f in ids if f not in listed]
    if unknown:
        raise ValueError(
            f"Flow(s) not listed in {project.base_name}.prj: {unknown}"
        )
    missing = sorted(listed - set(ids), key=_flow_num)
    if missing:
        raise ValueError(
            f"order must list every {_LETTER_KIND[letter]} flow in the "
            f"project — missing: {missing}. (Add them in the position you "
            "want them to keep.)"
        )

    mapping = {}
    for i, fid in enumerate(ids, start=1):
        target = f"{letter}{i:02d}"
        if fid != target:
            mapping[fid] = target
    if mapping:
        renumber_flows(project, mapping)
    return mapping


def clone_flow(
    project: RasProject,
    source_id: str,
    new_title: str,
    *,
    new_id: str | None = None,
) -> str:
    """Create a new flow file as a copy of source_id with a new title.

    Copies only the flow text file (RAS regenerates a .u##.hdf on the next
    run); the `Flow Title=` line is replaced with new_title, and the .prj entry
    is inserted under the right key (`Unsteady File=` / `Flow File=`) keeping
    that kind's entries in ascending order. new_id defaults to the next free
    number IN THE SAME KIND and must not cross kinds.

    Note an unsteady clone inherits the source's `Restart Filename=` line if it
    has one — the copy will consume the same plan's restart output.

    Returns the new flow ID. Raises DuplicateFlowTitle if new_title matches
    another flow of the same kind (RAS lists the two kinds separately, so a
    cross-kind title collision is not checked), FlowIdInUse if new_id is taken.
    """
    src = _normalize_flow_id(source_id)
    src_path = flow_path(project, src)
    if not os.path.isfile(src_path):
        raise FlowFileNotFound(f"Flow file not found: {src_path}")
    letter = src[0]
    listed = _listed_ids(project, letter)
    if src not in listed:
        raise ValueError(
            f"Flow '{src}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to clone it."
        )

    if new_id is None:
        used = {_flow_num(f) for f in listed}
        available = project.available_ids().get(_LETTER_KIND[letter], [])
        used.update(_flow_num(_normalize_flow_id(f)) for f in available)
        n = max(used) + 1 if used else 1
        if n > 99:
            raise ValueError(
                f"No free {_LETTER_KIND[letter]} flow number left "
                f"({letter}99 is in use)."
            )
        new = f"{letter}{n:02d}"
    else:
        new = _normalize_flow_id(new_id)
        if new[0] != letter:
            raise ValueError(
                f"new_id {new} is a different flow kind than source {src} — "
                "a clone cannot cross the steady/unsteady namespaces."
            )
        if _flow_id_in_use(project, new):
            raise FlowIdInUse(f"Flow ID '{new}' is already in use.")

    for fid in listed:
        p = flow_path(project, fid)
        if os.path.isfile(p) and _read_flow_title(p) == new_title:
            raise DuplicateFlowTitle(
                f"Flow title '{new_title}' is already used by '{fid}' — "
                "HEC-RAS requires unique flow titles."
            )

    lines = read_lines(src_path)
    eol = eol_of(lines)
    for i, line in enumerate(lines):
        if content_of(line).startswith("Flow Title="):
            lines[i] = f"Flow Title={new_title}{eol}"
            break
    write_lines(flow_path(project, new), lines)

    key = _prj_flow_key(new)
    prj_lines = read_lines(project.prj_path)
    prj_eol = eol_of(prj_lines)
    entry = f"{key}{new}{prj_eol}"
    kind_idx = [
        (i, content_of(l)[len(key):].strip())
        for i, l in enumerate(prj_lines)
        if content_of(l).startswith(key)
    ]
    insert_at = None
    for i, fid in kind_idx:
        if _flow_num(fid) > _flow_num(new):
            insert_at = i
            break
    if insert_at is None:
        insert_at = kind_idx[-1][0] + 1 if kind_idx else len(prj_lines)
    prj_lines.insert(insert_at, entry)
    write_lines(project.prj_path, prj_lines)
    _invalidate_model(project)
    logger.info("cloned flow %s -> %s (%r)", src, new, new_title)
    return new


def delete_flow(
    project: RasProject,
    flow_id: str,
    *,
    force: bool = False,
    clean_rasmap: bool = True,
) -> dict:
    """Delete a flow file and everything keyed to its number: the .u##/.f##
    file, a .u##.hdf sidecar if present, and its .prj entry.

    Refuses (FlowInUse) if any listed plan still references the flow via
    `Flow File=`, unless force=True — in which case it deletes anyway and warns
    that those plans now point at a missing flow (they will not open/run until
    repointed).

    With clean_rasmap (default True), an unsteady flow's `<EventConditions>`
    RASEventConditions layer is removed from the .rasmap (via
    remove_flows_from_rasmap). Steady flow has no rasmap presence, so this is a
    no-op for an f##.

    Returns {'deleted': [filenames], 'prj_removed': [entries],
    'referencing_plans': [...], 'warnings': [...],
    'rasmap_removed': [flow ids]}.
    """
    fid = _normalize_flow_id(flow_id)
    fpath = flow_path(project, fid)
    if not os.path.isfile(fpath):
        raise FlowFileNotFound(f"Flow file not found: {fpath}")
    if fid not in _listed_ids(project, fid[0]):
        raise ValueError(
            f"Flow '{fid}' exists on disk but is not listed in "
            f"{project.base_name}.prj (orphan) — refusing to delete it."
        )
    _assert_no_flow_run_active(project, fid)

    base = project.base_name
    folder = project.folder
    referencing = _plans_using_flow(project, fid)
    warnings = []
    if referencing and not force:
        raise FlowInUse(
            f"Flow '{fid}' is still referenced by plan(s) {referencing} — "
            "refusing to delete. Pass force=True to delete anyway (those "
            "plans will then point at a missing flow)."
        )
    if referencing and force:
        msg = (f"flow '{fid}' deleted while still referenced by plan(s) "
               f"{referencing} — those plans now point at a missing flow.")
        warnings.append(msg)
        logger.warning(msg)

    deleted = []
    for name in (f"{base}.{fid}", f"{base}.{fid}.hdf"):
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            os.remove(path)
            deleted.append(name)

    key = _prj_flow_key(fid)
    prj_lines = read_lines(project.prj_path)
    kept = []
    prj_removed = []
    for line in prj_lines:
        if content_of(line) == f"{key}{fid}":
            prj_removed.append(content_of(line))
            continue
        kept.append(line)
    write_lines(project.prj_path, kept)

    rasmap_removed = []
    if clean_rasmap and fid[0] == "u":
        rasmap_path = os.path.join(folder, f"{base}.rasmap")
        if os.path.isfile(rasmap_path):
            rasmap_removed = remove_flows_from_rasmap(rasmap_path, base, [fid])

    _invalidate_model(project)
    logger.info(
        "deleted flow %s: %d file(s), %d prj entrie(s), %d rasmap layer(s)",
        fid, len(deleted), len(prj_removed), len(rasmap_removed),
    )
    return {"deleted": deleted, "prj_removed": prj_removed,
            "referencing_plans": referencing, "warnings": warnings,
            "rasmap_removed": rasmap_removed}


def delete_flows(
    project: RasProject,
    spec,
    *,
    force: bool = False,
    clean_rasmap: bool = True,
) -> dict:
    """Delete several flow files given a flexible id spec, e.g. 'u09-u11,u13'
    or ['u09', 'f2'] (see _expand_flow_spec). Every token must carry its kind
    prefix; one spec may mix kinds.

    All ids are validated up front (each must exist, be listed in the .prj, and
    have no plan of theirs mid-run) and, unless force=True, the whole call is
    refused with FlowInUse if ANY target is still referenced by a plan — so a
    bad spec deletes nothing. Then each is removed via delete_flow.

    Returns a consolidated report:
        {'deleted_flows': [fid, ...], 'deleted': [filenames],
         'prj_removed': [entries], 'referencing_plans': {fid: [pid, ...]},
         'warnings': [...], 'rasmap_removed': [fid, ...]}
    """
    fids = _expand_flow_spec(spec)

    referenced = {}
    for fid in fids:
        if not os.path.isfile(flow_path(project, fid)):
            raise FlowFileNotFound(f"Flow file not found: {flow_path(project, fid)}")
        if fid not in _listed_ids(project, fid[0]):
            raise ValueError(
                f"Flow '{fid}' exists on disk but is not listed in "
                f"{project.base_name}.prj (orphan) — refusing to delete it."
            )
        _assert_no_flow_run_active(project, fid)
        refs = _plans_using_flow(project, fid)
        if refs:
            referenced[fid] = refs
    if referenced and not force:
        raise FlowInUse(
            f"Flow(s) {sorted(referenced)} still referenced by plan(s) "
            f"{referenced} — refusing to delete. Pass force=True to delete "
            "anyway (those plans will then point at a missing flow)."
        )

    report = {
        "deleted_flows": [], "deleted": [], "prj_removed": [],
        "referencing_plans": {}, "warnings": [], "rasmap_removed": [],
    }
    for fid in fids:
        r = delete_flow(project, fid, force=force, clean_rasmap=clean_rasmap)
        report["deleted_flows"].append(fid)
        report["deleted"].extend(r["deleted"])
        report["prj_removed"].extend(r["prj_removed"])
        if r["referencing_plans"]:
            report["referencing_plans"][fid] = r["referencing_plans"]
        report["warnings"].extend(r["warnings"])
        report["rasmap_removed"].extend(r["rasmap_removed"])
    return report
