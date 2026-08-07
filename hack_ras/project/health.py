# hack_ras/project/health.py
"""Read-only project status / health inspector.

`project_health(project)` returns a `ProjectHealth` snapshot — an inventory of
plans / geometries / flows (with titles and cross-references) plus a set of
consistency checks (orphan files, stale .prj entries, rasmap duplicate /
missing-file layers, computed results not yet in the rasmap, duplicate titles,
unused geometries / flows, active runs). `format_health(report)` renders it as a
readable text summary.

Purely read-only: nothing here writes to any file. It assembles data the library
already exposes (the .prj model, the plan/geom/flow text files, and the rasmap
queries in project/rasmap.py). Reading plan HDFs for "has results" needs h5py
(imported lazily); if h5py is unavailable those fields are left as None and the
unlisted-results check is skipped, everything else still works.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os

from hack_ras.project.plans import _read_plan_ref, plan_path
from hack_ras.project.ras_project import RasProject
from hack_ras.project.rasmap import rasmap_layer_refs, result_plan_ids
from hack_ras.utils.lines import content_of, read_lines

_ISSUE_FIELDS = (
    "orphan_files", "stale_prj_entries", "rasmap_duplicate_layers",
    "rasmap_missing_file_layers", "unlisted_results", "duplicate_titles",
    "unused_geometries", "unused_flows", "active_runs",
)


@dataclass
class PlanInfo:
    id: str
    title: str
    geom: str | None
    flow: str | None
    has_results: bool | None   # None: no .hdf, or h5py unavailable


@dataclass
class FileInfo:
    id: str
    title: str
    used_by: list[str]         # plan ids referencing this geometry / flow


@dataclass
class ProjectHealth:
    base_name: str
    current_plan: str | None
    plans: list[PlanInfo] = field(default_factory=list)
    geometries: list[FileInfo] = field(default_factory=list)
    flows: list[FileInfo] = field(default_factory=list)
    # consistency issues (each empty when healthy)
    orphan_files: list[str] = field(default_factory=list)
    stale_prj_entries: list[str] = field(default_factory=list)
    rasmap_duplicate_layers: list[str] = field(default_factory=list)
    rasmap_missing_file_layers: list[str] = field(default_factory=list)
    unlisted_results: list[str] = field(default_factory=list)
    duplicate_titles: list[str] = field(default_factory=list)
    unused_geometries: list[str] = field(default_factory=list)
    unused_flows: list[str] = field(default_factory=list)
    active_runs: list[str] = field(default_factory=list)

    @property
    def issues(self) -> dict:
        """{field: [...]} for every non-empty issue list."""
        return {k: getattr(self, k) for k in _ISSUE_FIELDS if getattr(self, k)}

    @property
    def ok(self) -> bool:
        """True when no consistency issue was found."""
        return not any(getattr(self, k) for k in _ISSUE_FIELDS)


def _title(path: str, key: str) -> str:
    return _read_plan_ref(path, key) or "" if os.path.isfile(path) else ""


def _current_plan(project: RasProject) -> str | None:
    for line in read_lines(project.prj_path):
        c = content_of(line)
        if c.startswith("Current Plan="):
            return c[len("Current Plan="):].strip()
    return None


def _dup_titles(kind: str, items: list) -> list[str]:
    by_title = defaultdict(list)
    for fid, title in items:
        if title:
            by_title[title].append(fid)
    return [f"{kind} title {t!r}: {', '.join(ids)}"
            for t, ids in by_title.items() if len(ids) > 1]


def project_health(project: RasProject) -> ProjectHealth:
    """Inspect the project and return a read-only ProjectHealth snapshot."""
    base = project.base_name
    folder = project.folder
    model = project.model

    try:
        import h5py
        have_h5py = True
    except ImportError:
        have_h5py = False

    # --- inventory: plans ---
    plans: list[PlanInfo] = []
    plan_geom: dict[str, str | None] = {}
    plan_flow: dict[str, str | None] = {}
    plan_titles: list[tuple[str, str]] = []
    has_results: dict[str, bool | None] = {}
    for pid in model.plan_file_ids:
        ppath = plan_path(project, pid)
        title = _title(ppath, "Plan Title")
        geom = _read_plan_ref(ppath, "Geom File") if os.path.isfile(ppath) else None
        flow = _read_plan_ref(ppath, "Flow File") if os.path.isfile(ppath) else None
        hdf = ppath + ".hdf"
        hr: bool | None = None
        if have_h5py and os.path.isfile(hdf):
            try:
                with h5py.File(hdf, "r") as h:
                    hr = "Results" in h
            except OSError:
                hr = None
        plan_geom[pid], plan_flow[pid], has_results[pid] = geom, flow, hr
        plan_titles.append((pid, title))
        plans.append(PlanInfo(pid, title, geom, flow, hr))

    def _used_by(kind_key: str, refs: dict) -> dict:
        used = defaultdict(list)
        for pid, val in refs.items():
            if val:
                used[val].append(pid)
        return used

    geom_used = _used_by("geom", plan_geom)
    flow_used = _used_by("flow", plan_flow)

    # --- inventory: geometries / flows ---
    geometries, geom_titles = [], []
    for gid in model.geom_file_ids:
        gpath = os.path.join(folder, f"{base}.{gid}")
        t = _title(gpath, "Geom Title")
        geom_titles.append((gid, t))
        geometries.append(FileInfo(gid, t, sorted(geom_used.get(gid, []))))
    # Steady (f##) and unsteady (u##) flows share one inventory: a plan's
    # 'Flow File=' reference points at either, so flow_used is already keyed
    # across both. Listed steady-first to match .prj order. Both file types
    # carry a 'Flow Title=' line.
    flows, flow_titles = [], []
    for uid in model.steady_file_ids + model.unsteady_file_ids:
        upath = os.path.join(folder, f"{base}.{uid}")
        t = _title(upath, "Flow Title")
        flow_titles.append((uid, t))
        flows.append(FileInfo(uid, t, sorted(flow_used.get(uid, []))))

    health = ProjectHealth(
        base_name=base, current_plan=_current_plan(project),
        plans=plans, geometries=geometries, flows=flows,
    )

    # --- issues: orphan files & stale .prj entries ---
    avail = project.available_ids()
    listed = {"plan": model.plan_file_ids, "geom": model.geom_file_ids,
              "unsteady": model.unsteady_file_ids,
              "steady": model.steady_file_ids}
    for kind in ("plan", "geom", "unsteady", "steady"):
        listed_set = set(listed[kind])
        for fid in avail.get(kind, []):
            if fid not in listed_set:
                health.orphan_files.append(f"{base}.{fid}")
        for fid in listed[kind]:
            if not os.path.isfile(os.path.join(folder, f"{base}.{fid}")):
                health.stale_prj_entries.append(f"{base}.{fid}")

    # --- issues: rasmap duplicate / missing-file layers ---
    rasmap_path = os.path.join(folder, f"{base}.rasmap")
    has_rasmap = os.path.isfile(rasmap_path)
    if has_rasmap:
        seen = defaultdict(int)
        for section, _type, basename in rasmap_layer_refs(rasmap_path):
            seen[(section, basename)] += 1
            if not os.path.isfile(os.path.join(folder, basename)):
                entry = f"{basename} ({section})"
                if entry not in health.rasmap_missing_file_layers:
                    health.rasmap_missing_file_layers.append(entry)
        for (section, basename), n in seen.items():
            if n > 1:
                health.rasmap_duplicate_layers.append(
                    f"{basename} ({section}) x{n}")

    # --- issues: computed results not represented in the rasmap ---
    if has_rasmap:
        rasmap_results = result_plan_ids(rasmap_path, base)
        health.unlisted_results = [
            pid for pid in model.plan_file_ids
            if has_results.get(pid) and pid not in rasmap_results
        ]

    # --- issues: duplicate titles ---
    health.duplicate_titles = (
        _dup_titles("plan", plan_titles)
        + _dup_titles("geometry", geom_titles)
        + _dup_titles("flow", flow_titles)
    )

    # --- issues: unused geometries / flows ---
    health.unused_geometries = [g.id for g in geometries if not g.used_by]
    health.unused_flows = [u.id for u in flows if not u.used_by]

    # --- issues: active runs ---
    for pid in model.plan_file_ids:
        if os.path.isfile(os.path.join(folder, f"{base}.{pid}.tmp.hdf")):
            health.active_runs.append(pid)

    return health


_ISSUE_LABELS = {
    "orphan_files": "Orphan files on disk (not in .prj)",
    "stale_prj_entries": "Stale .prj entries (file missing)",
    "rasmap_duplicate_layers": "Duplicate .rasmap layers",
    "rasmap_missing_file_layers": ".rasmap layers pointing at missing files",
    "unlisted_results": "Computed results not in .rasmap (RAS Mapper will append)",
    "duplicate_titles": "Duplicate titles (RAS requires unique)",
    "unused_geometries": "Geometries used by no plan",
    "unused_flows": "Flows used by no plan",
    "active_runs": "Plans mid-run (.p##.tmp.hdf present)",
}


def format_health(h: ProjectHealth) -> str:
    """Render a ProjectHealth as a readable multi-line text summary."""
    def res(hr):
        return {True: "yes", False: "no", None: "?"}[hr]

    lines = [f"Project: {h.base_name}   Current Plan: {h.current_plan or '-'}"]

    lines.append(f"\nPlans ({len(h.plans)}):")
    for p in h.plans:
        lines.append(f"  {p.id}  {p.title or '(missing)':<28} "
                     f"geom={p.geom or '-'} flow={p.flow or '-'} "
                     f"results={res(p.has_results)}")
    lines.append(f"\nGeometries ({len(h.geometries)}):")
    for g in h.geometries:
        used = ", ".join(g.used_by) if g.used_by else "(unused)"
        lines.append(f"  {g.id}  {g.title or '(missing)':<28} used by: {used}")
    lines.append(f"\nFlows ({len(h.flows)}):")
    for u in h.flows:
        used = ", ".join(u.used_by) if u.used_by else "(unused)"
        lines.append(f"  {u.id}  {u.title or '(missing)':<28} used by: {used}")

    issues = h.issues
    if not issues:
        lines.append("\nHealth: OK - no issues found.")
    else:
        n = sum(len(v) for v in issues.values())
        lines.append(f"\nHealth: {n} issue(s) across {len(issues)} check(s):")
        for key in _ISSUE_FIELDS:
            vals = getattr(h, key)
            if vals:
                lines.append(f"  {_ISSUE_LABELS[key]}:")
                for v in vals:
                    lines.append(f"    - {v}")
    return "\n".join(lines)
