# Project AI Context – hack_ras

## What This Project Does
Python tools for parsing and manipulating HEC-RAS model files, and for reading HEC-RAS
binary results (HDF5). HEC-RAS is hydraulic engineering software that stores model data
in plain-text files with fixed formats and numeric suffixes (`.g01`, `.p02`, etc.) and
writes simulation results to HDF5 files (`.p##.hdf`).

## Package Structure
| Package | Purpose |
|---------|---------|
| `hack_ras/` (top level) | `RasProject` — the recommended entry point for any project |
| `hack_ras/project/` | Parse `.prj` project files; `ProjectModel` dataclass; `plans.py` — plan file operations (renumber, insert numbering gap, compact, reorder, clone, delete); `geoms.py` — the geometry-file analogue; `flows.py` — the flow-file analogue, covering BOTH steady (`.f##`) and unsteady (`.u##`) |
| `hack_ras/geometry/` | Parse and transform `.g##` geometry files; `shift.py` translates XS GIS cut lines along their alignment; `xs_interp.py` maps RAS station values to GIS cut-line XY coordinates |
| `hack_ras/results/` | Read plan HDF5 files — cell geometry, WSE, volume tables, pipe networks |
| `hack_ras/gis/` | GIS operations — profile line sampling, station computation, line-in-polygon measurement, 2D mesh cell attribute export |
| `hack_ras/utils/` | Shared utilities (logging, line helpers) |
| `hack_ras/resolve.py` | File discovery and ID resolution (lower-level module) |

## HEC-RAS File Title Uniqueness

HEC-RAS requires every file within a project to have a unique human-readable
title.  Duplicate titles cause the project to malfunction — HEC-RAS cannot
reliably distinguish between files that share the same name.  This applies to:

| File type | Title field |
|-----------|-------------|
| Geometry (`.g##`) | `Geom Title=` |
| Plan (`.p##`) | `Plan Title=` |
| Unsteady flow (`.u##`) | `Flow Title=` |
| Steady flow (`.f##`) | `Flow Title=` |

When creating a new file derived from an existing one (e.g. a shifted geometry
`g17` copied from `g16`), **always supply a new title**.  Scripts that write
new files must enforce this — `shift_xs_gis.py` treats `geom_name_out` as a
required config key and exits with an error if it is missing.

## Key Design Principles
- One package per HEC-RAS file type
- No admin dependencies — must run in Anaconda/Spyder without elevated privileges
- Emphasis on reproducibility and auditability
- Fail gracefully — do not crash on partial or malformed data; raise explicit exceptions instead
- **Lossless roundtrip**: `GeometryFile` stores original raw lines; structured fields are
  parsed on top of the raw lines, not instead of them. In `utils/lines.py` the same
  guarantee holds byte-for-byte through a read/write cycle, including a leading UTF-8
  BOM: `read_lines` strips one so line-1 key matches work, and `write_lines`
  re-attaches it when the destination already had one. Files created fresh get no BOM.
  See the `utils/lines.py` docstring for why, and `tests/test_bom_handling.py`.
  A plan's BOM is NOT stable across HEC-RAS itself: `Model_Hillside/Current_Model`
  p05 carried one, and a GUI edit of its Computation Settings rewrote the file
  WITHOUT it while its untouched siblings p06-p24 kept theirs (observed
  2026-09-11). So a BOM is a property of the last writer, not of the project —
  never treat its presence or absence as a fingerprint of who wrote a file, and
  never hard-code an expectation of one; read via `read_lines`.
  `GeometryParser` reads with `utf-8-sig` instead, so a BOM never reaches
  `raw_lines` (where it used to hide `Geom Title=` and make `GeometryWriter` raise
  `UnicodeEncodeError`); nothing re-attaches it, because every `GeometryWriter` call
  site writes a NEW file — the shifter, `merge`, and the Geometry_Mesh_Health snapper all take
  an output path and never overwrite their source
- **Typed exceptions over None**: resolution and lookup functions raise typed exceptions
  (`ValueError`, `GeometryFileNotFound`, etc.) rather than returning `None`
- **`.prj` is authoritative**: the project file is the definitive list of which files belong
  to a project. Files that exist on disk but are not referenced by the `.prj` (orphans) are
  not part of the project and are silently excluded from all discovery.

## File Naming Conventions
HEC-RAS uses a base name plus a typed numeric suffix:
| Suffix | File type | Keyed to |
|--------|-----------|----------|
| `.prj` | Project file (key=value pairs, links to all others) | — |
| `.g##` | Geometry file (rivers, reaches, cross-sections, GIS cut lines) | — |
| `.g##.hdf` | Geometry preprocessor output (HDF5), regenerated per run | geometry |
| `.p##` | Plan file (text sidecar — plan title, geometry + flow reference) | — |
| `.p##.hdf` | Plan results file (HDF5 — cell geometry, WSE, pipe network results) | plan |
| `.p##.tmp.hdf` | In-progress results — RENAMED to `.p##.hdf` by RAS at run end; its presence means a run is ACTIVE on that plan | plan |
| `.p##.<DDMMMYYYY HHMM>.rst` | Restart file WRITTEN by plan `##` ("Write IC File at Sim End"); the stamp uses RAS's 2400 convention (see below) | plan |
| `.b##` | UNET run/boundary input, regenerated each run; text; embeds plan/project TITLES but not the plan number — the suffix is the only plan link | plan |
| `.bco##` | Unsteady computation log (text; 0 bytes until flushed at run end) | plan |
| `.ic.o##` | Binary initial-conditions output; embeds the plan title, not the number | plan |
| `.O##` | STEADY output file (uppercase O) — the steady counterpart of `.b##`/`.bco##` | plan |
| `.r##` | STEADY run file | plan |
| `.x##` | Preprocessor run file — keyed to GEOMETRY `g##`, NOT the plan (user-confirmed; line 3 carries the title of the last plan run with that geometry, which misleads) | geometry |
| `.u##` | Unsteady flow file | — |
| `.u##.hdf` | Flow preprocessor output (HDF5), regenerated | flow |
| `.f##` | Steady flow file | — |
| `.dss` | Shared DSS output — ALL plans write into the one file | — |
| `.rasmap` (+`.backup`) | RAS Mapper layer config (XML); `.backup` = the previous save state | — |

`##` is a two-digit number (`01`, `02`, …). A project may have multiple geometry or plan files.
Multiple plans may share the same geometry (same `g##` ID), which is important for grouping.
The plan-keyed rows are exactly the family that `renumber_plans` / `delete_plan` handle.
A plan is steady OR unsteady, so only one artifact set exists for it (`.b##`/`.bco##`/
`.ic.o##` or `.O##`/`.r##`). `_family_names` lists BOTH sets as candidates and filters
by `os.path.isfile` — it never infers the type from the `.p##` file. Disk is the
authority: a malformed plan (no `Flow File=` line — observed in the wild) would defeat a
classifier and silently strand real files, whereas an existence check cannot misjudge.

## HEC-RAS Runtime & GUI Behavior (empirical — GMF_DFA live runs, 2026-07-17)

**Restart (.rst) semantics.** A plan's simulation may start at ANY time regardless of
the restart file's timestamp — a rst snapshotted at 02JAN 2400 legitimately
initializes plans starting 01JAN 0000 (user-proven). Filenames use the 2400
convention: a run ending 02JAN 0000 writes `...01JAN2026 2400.rst`. Consumption is
via `Restart Filename=<verbatim filename>` in the `.u` file (`Use Restart=-1`);
restart files can also have arbitrary names (`banana.rst`) with no plan number.

**Hydrograph timing in .u files.** `Use Fixed Start Time=True` anchors a
`Flow Hydrograph=` to its `Fixed Start Date/Time=` line (sim entering mid-hydrograph
is normal — RAS interpolates at sim time); `False` aligns ordinate 1 to the
simulation start. RAS holds the last ordinate when a hydrograph ends before the
window does.

**Stored-but-unused fields are a general RAS pattern.** Plan and flow files keep ALL
alternative-mode values in the file with flags selecting the active one. Never read a
value without checking its mode flag. Two decoded examples:
- `Breach Start=F1,F2,F3,F4,F5,F6,F7,F8` — F1 True → "WS Elev" mode (F2 = trigger
  WS); F5 True → "WS Elev + Duration" (F2 = Immediate Initiation WS, F6 = Threshold
  WS, F7 = Duration Above Threshold in hours, F8 = Accumulate Duration checkbox,
  -1 checked / 0 unchecked); both False → "Set Time" using F3,F4 (date, HHMM).
  Inactive fields (including F2 in Set Time mode) are stored but ignored; RAS also
  writes blank inactive fields (`Breach Start=True,756.8,,,False,,,0`).
- `Use Fixed Start Time=False` keeps its `Fixed Start Date/Time=` line populated.

**GUI resave normalization.** Opening and saving a hand-edited `.u` file in the RAS
GUI rewrites only whitespace padding (e.g. trailing spaces on Non-Newtonian lines) —
RAS fully accepts hand-renamed/edited files; expect a few spurious whitespace diffs
in ExamDiff after a GUI touch.

**RAS Mapper / .rasmap.** Mapper does NOT rewrite the .rasmap on open (saves on
close/save; `.rasmap.backup` = previous save state). Layer display names refresh
from the actual files' titles at load, so stale names in the XML are cosmetic.
That refresh cuts BOTH ways and is not purely benign: a name you correct only in
the XML is overwritten from the file on the next open. See the Retitling
section — which file it reads differs per section, and for `<Results>` /
`<Geometries>` it is the `.hdf`.
Layers whose referenced file is missing render italic + red-asterisk and are purged
by Tools > "remove missing layers"; result layers re-create themselves when a plan
runs. Hand-edited `<Plans>`/`<Results>` sections survive a GUI save round-trip
verbatim — which is why `renumber_plans_in_rasmap` only remaps filename tokens.
(`project/rasmap.py` also exposes the read-only `source_data_folders` query —
see Plan File Operations.)

## Project Entry Point — `RasProject`

`from hack_ras import RasProject` is the recommended way to work with a HEC-RAS project.
Pass the absolute path to the `.prj` file; `ValueError` is raised if the file is missing
or is not a HEC-RAS project (e.g. an ESRI shapefile projection file with the same extension).

The top-level package also **re-exports the `project/` operations modules** —
`from hack_ras import RasProject, plans, geoms, sync, rasmap, health` — so a script
needs one import line instead of one per subpackage. The MODULES are re-exported, not
their functions: `plans` and `geoms` have deliberately parallel APIs (renumber / insert
gap / compact / clone / delete), so the module name at the call site is what says which
file type is being operated on, and flattening the functions into the top level would
throw that cue away. `hack_ras.plans` and `hack_ras.project.plans` are the same module
object (pinned by `tests/test_package_exports.py`); the canonical home is unchanged, and
nothing new is imported eagerly (none of the five pulls in h5py/geopandas at module
level). When adding a module to `project/`, decide whether it belongs in `__all__` —
that list is the second place to maintain, and the cost of this convenience.

```python
project = RasProject(r"C:\path\to\NKC_Hillside_Levee.prj")
project.folder        # directory containing the .prj
project.base_name     # "NKC_Hillside_Levee"
project.title         # project title string from the .prj
project.model         # ProjectModel — parsed .prj content (list fields below)
project.plan_hdfs()               # all .p##.hdf files listed in the .prj that exist on disk
project.plan_hdfs(['p14','p15'])   # filtered subset
project.plan_hdfs(['01', 'p03', '14-16'])  # flexible spec (see expand_id_spec)
project.crs_prj()     # ESRI .prj CRS file (via RAS Mapper or folder search)
project.crs_wkt()     # ...and its contents, as WKT ready for geopandas/pyproj
project.rasmap_path   # <folder>/<base_name>.rasmap (existence NOT checked)
project.rasmap        # bound .rasmap accessor — see below
project.family()      # {'geom': [...], 'plan': [...], ...} — filesystem-based
project.available_ids()           # same, as ID strings
```

`plan_hdfs()` uses `ProjectModel.plan_file_ids` (parsed from the `.prj`) as the
authoritative plan list, then checks HDF existence. Orphaned HDF files on disk that
are not listed in the `.prj` are excluded automatically.

## Parsing Strategy — Project (`.prj`)

The `.prj` file uses repeated keys for multi-valued entries:
```
Geom File=g01
Geom File=g02
Plan File=p01
Plan File=p14
...
```
`ProjectModel` stores these as **lists**:
- `geom_file_ids: list[str]` — all geometry IDs referenced by the project
- `plan_file_ids: list[str]` — all plan IDs, in the order listed in the `.prj`
- `unsteady_file_ids: list[str]` — all unsteady flow IDs (`.prj` `Unsteady File=u##`)
- `steady_file_ids: list[str]` — all steady flow IDs (`.prj` `Flow File=f##`)

Note the asymmetric key names: in the `.prj`, `Flow File=` means STEADY specifically
(historical — the first HEC-RAS had no unsteady). Inside a `.p##` plan file the same
`Flow File=` key is that plan's flow reference and may hold an `f##` OR a `u##` id.
Same key, different file, different meaning — do not reuse one parser for both.

Scalar fields (`title`, `y_axis_title`, etc.) work as before.
`resolve_filenames(basename_map)` maps all IDs of each type to filenames, returning
`{'geom': [...], 'plan': [...], 'unsteady': [...], 'steady': [...]}`.

## Flexible file-id selection — `resolve.expand_id_spec(spec, kind='p')`

Config `plan_files` lists (and any future geom/unsteady/steady selection) go
through `expand_id_spec`, which normalises a mixed list of tokens into sorted,
unique two-digit ids. A token may be a bare number (`01`, `3`), a prefixed id
(`p03`, `P7`), or an inclusive range (`01-9`, `14-16`, `p14-p16`). A comma
separates TOKENS, not specs, so a whole selection can be one string
(`'5,7,19-21'`), a list entry may itself carry commas, and spaces around them
are ignored — `'16-17,21-23'` and `['16-17', '21-23']` are the same spec. That
splitting used to be a `spec.split(",")` prelude repeated in `delete_plans`,
`delete_geoms` and `set_plan_settings`; it moved in here (2026-09-11) so every
caller gets it, including the YAML-driven `Scripts/` tools, where
`plan_files: "5-24,30"` previously raised. Purely additive — a comma used to be
a hard error, so no working spec changed meaning.
`flows._expand_flow_spec` still splits commas itself, and must: it reads each
token's kind prefix and groups by kind BEFORE delegating, because one flow spec
may mix `u` and `f` (`'u09,f02'`).
`kind` is the type-prefix letter (`'p'`/`'g'`/`'u'`/`'f'`). The function is **pure** (no disk
access) — callers validate the returned ids against real files. Selection is
**strict**: `RasProject.plan_hdfs` requires every expanded id (including every
number inside a range) to exist, raising `PlanHdfNotFound` otherwise — ranges are
not silently filtered to what happens to be present. `ValueError` is raised on a
malformed token, an id outside 1–99, or a reversed range. `export_xs_gis.py` uses
the same helper; the four `extract_*` scripts get it for free via `plan_hdfs`.

## Plan File Operations (`hack_ras/project/plans.py`, `project/sync.py`, `project/rasmap.py`)

In-place plan management for a project. All functions take a `RasProject`, edit
files losslessly (raw-line edits; untouched lines byte-identical, CRLF preserved),
and treat the `.prj` as authoritative — orphan plan files on disk are rejected.
Shared raw-line I/O helpers live in `hack_ras/utils/lines.py`
(`read_lines` / `write_lines` / `eol_of` / `content_of`).

```python
from hack_ras import RasProject, plans, geoms, sync, rasmap, health  # module re-exports
from hack_ras.project.plans import (   # or import the names directly
    renumber_plan, renumber_plans, insert_plan_gap, reorder_plans,
    clone_plan, delete_plan, plan_short_ids, plans_with_unlisted_results,
    read_plan_sidecar)
from hack_ras.project.sync import sort_prj_entries, sync_prj
from hack_ras.project.rasmap import (              # .rasmap-specific ops
    remove_plans_from_rasmap, remove_flows_from_rasmap,  # (delete_plan calls
    remove_geoms_from_rasmap, result_plan_ids,           #  the remove_* fns)
    sort_rasmap_layers)

project = RasProject(r"...\Model.prj")
plan_short_ids(project)                  # {plan_id: 'Short Identifier='} in prj order;
                                         #   skips plans whose .p## file is missing
read_plan_sidecar(plan_path)             # {'title','short_id','geom_id','flow_id'} from
                                         #   one .p## — BOM-safe; use instead of
                                         #   hand-rolling a 'Geom File=' loop per script
sync_prj(project)                        # drop prj entries whose files are missing; fix Current Plan
renumber_plans(project, {"p20": "p02",   # bulk renumber: chains/cycles auto-ordered
                         "p02": "p06"})  #   ('<name>.renumtmp' hop breaks cycles)
renumber_plan(project, "p25", "p30")     # single-plan case of the same machinery
insert_plan_gap(project, "p25", 5)       # shifts all plans >= p25 up by 5; returns {old: new}
compact_plans(project)                    # renumber survivors to contiguous p01..pN
reorder_plans(project, ["p01", "p02",    # renumber into this order as p01..pN;
                        "p05", "p06",    #   the COMPLETE current-ID list is required
                        "p03", "p04"])   #   (ValueError up front otherwise)
delete_plans(project, "16-17,21-26,30-35")  # bulk delete by id-spec (fail-fast)
sort_prj_entries(project)                # optional: re-sort prj Plan/Geom/Unsteady/Flow File=
                                         #   lines ascending; kinds=("plan",) etc. to limit
                                         #   (kind 'steady' == the prj's 'Flow File=f##' lines)
delete_plan(project, "p08",              # deletes plan + outputs; optional unused-file cleanup
            delete_unused_geom=True, delete_unused_flow=True)
retitle_plan(project, "p58", "FC 002year 260824")   # rename in place; short id follows title
retitle_plan(project, "p58", "FC 002year 260824",  # ...or give the short id its own value
             short_id="FC002_260824")
clone_plan(project, "p24", "L4 1214",    # copy with new Plan Title / Short Identifier (padding kept)
           line_edits={"Breach Start=": "Breach Start=False,,01JAN2025,1214,False,,,0"},
           new_id="p25")                 # new_id optional — defaults to next free number
project.rasmap.sort()                    # optional: re-sort .rasmap RASPlan/RASResults
                                         #   layers into ascending plan-number order
                                         #   (bound accessor — see below; the free
                                         #   sort_rasmap_layers(path, base) still works)
```

Renumbering/deleting covers the whole plan-keyed file family — `.p##`, `.p##.hdf`,
run artifacts `.b##` / `.bco##` / `.ic.o##` (unsteady) or `.O##` / `.r##` (steady),
and `Base.p##.<stamp>.rst` restart
files — plus cross-file references: `Restart Filename=` lines in the `.u` files and
the `.rasmap` (`project/rasmap.py`). `renumber_plans` remaps `Base.p##` tokens
there; `delete_plan` removes the deleted plan's RASPlan/RASResults layer subtrees
(see below). All reference updates are applied in ONE pass with the complete
mapping; sequential per-plan application would corrupt chained mappings (p02→p06
while p06→p12). `.x##` run files are keyed to GEOMETRY, not plans, and are never
touched. Restart references that carry no plan number (e.g. `banana.rst`) are left
alone.

- A `.p##.tmp.hdf` means HEC-RAS is mid-run on that plan — operations raise
  `PlanRunActive` instead of touching files.
- `sync_prj` is removal-only (it never adopts orphans); it is the fix for
  `PlanIdInUse` caused by prj-listed-but-missing plan IDs.
- `delete_plan` warns (logging + report) when a surviving `.u` file references the
  deleted plan's restart output; with the optional flags it also removes the plan's
  geometry / flow file (plus `.hdf` sidecars and the geometry's `.x##`) when no
  other listed plan references it. It now also cleans the `.rasmap` by default
  (`clean_rasmap=True`): `remove_plans_from_rasmap` splices out the plan's RASPlan
  (`<Plans>`) and RASResults (`<Results>`) layer subtrees. This replaces the old
  "leave it to RAS Mapper's remove-missing-layers" design, which was unsafe under
  number reuse — deleting a plan then renumbering a survivor ONTO its number left
  the stale layer pointing at an existing file, so it was neither purged nor
  correctable and RAS Mapper refreshed its name to the new file's title, leaving a
  visible duplicate (observed live, GMF_DFA 2026-07-28 — this is what motivated the
  fix). RASResultsMap rasters and CalculatedLayers built on the plan still live in
  result subfolders, not as plan-keyed `.rasmap` layers, so they are still left to
  RAS Mapper. Pass `clean_rasmap=False` to restore the old leave-it-alone behavior.
  When `delete_unused_flow` / `delete_unused_geom` also removes a now-orphaned
  flow / geometry file, `clean_rasmap` additionally drops that flow's
  RASEventConditions layer (`remove_flows_from_rasmap`, keyed on `Base.u##.hdf`)
  / that geometry's `<Geometries>` RASGeometry layer (`remove_geoms_from_rasmap`,
  keyed on `Base.g##.hdf`). Both are section-scoped and token-keyed, so the
  RASEventConditions / RASGeometry sub-layers INSIDE a RASResults block (which
  name `Base.p##.hdf`) are never touched — those go with their result. For FLOW
  numbers this is pure tidy-up (hack_ras has no flow renumber — see TODO item D —
  so the leftover layer would point at a genuinely-missing file). For GEOMETRY
  numbers it is also a reuse-safety fix, because `renumber_geoms` CAN free and
  refill a geometry number, which is the same zombie-layer trap as plans.
  The `rasmap_removed` report field is `{'plans': [...], 'results': [...],
  'event_conditions': [...], 'geometries': [...]}`.
- `plans_with_unlisted_results(project)` flags plan IDs whose `.p##.hdf` has a
  top-level `Results` group but which have NO RASResults layer in the `.rasmap`
  (uses `result_plan_ids(rasmap_path, base)`; h5py imported lazily). These are
  the results RAS Mapper auto-generates and APPENDS out of numeric order the next
  time the project is opened — the common state after running plans headlessly.
  Use it to know before opening (e.g. run `sort_rasmap_layers` only AFTER opening
  RAS Mapper once, so it has materialized every result). Read-only.
- `sort_rasmap_layers(rasmap_path, base_name, sections=("Plans","Results"))` is the
  `.rasmap` analogue of `sort_prj_entries`: it re-sorts the RASPlan/RASResults
  layers into ascending plan-number order (each redistributed across the positions
  its kind already occupies; other layers — e.g. CalculatedLayer siblings — and
  `<EventConditions>`, which is keyed to `.u##`, stay put). Standalone/optional; not
  called by delete or renumber. Note `<Results>` is otherwise stored by RAS in
  run order, not numeric order.
- `clone_plan` enforces plan-title uniqueness (`DuplicatePlanTitle`) and inserts the
  new `Plan File=` entry in ascending numeric position in the `.prj`. After writing
  it sanity-checks breach triggers: an ACTIVE Set Time trigger dated outside the
  plan's `Simulation Date=` window logs a WARNING (never an error — placeholder
  triggers are a legitimate workflow). Trigger field layout: `Breach Start=`
  F1 True → "WS Elev" mode, F5 True → "WS Elev + Duration", both False → "Set Time"
  (F3/F4); inactive fields are stored but unused.
- `line_edits` maps a line prefix to the full replacement line; each prefix must match
  exactly one line (`ValueError` otherwise — a multi-breach plan needs a different tool).
- Typed exceptions: `PlanFileNotFound`, `PlanIdInUse`, `DuplicatePlanTitle`,
  `PlanRunActive`.
- `insert_plan_gap` delegates to `renumber_plans` — every target ID is validated
  (collisions, orphan files on disk, p99 overflow) before any file is touched.
- `compact_plans(project)` renumbers the listed plans to a contiguous p01..pN by
  ascending number (fills gaps; the plan-side twin of `geoms.compact_geoms`) — the
  "...and renumber the rest sequentially" half of the common delete-then-compact
  request. `reorder_plans(project, order)` is the same thing with the positions
  taken from `order` instead of from the current numbers: it builds the
  `{old: new}` mapping and delegates to `renumber_plans`, so it inherits all of
  its validation. `order` must be the COMPLETE list of currently-listed plan IDs
  (loose forms like `'3'`/`'P3'` accepted) — a missing, duplicated, or unknown ID
  is a `ValueError` raised before any file is touched. That requirement is
  deliberate: naming only the plans to move would make the outcome depend on
  plans the caller never mentioned. Because positions come from the list, a
  project with gaps gets compacted as a side effect. It is the answer to
  "insert p05 and p06 after p02" — write the order you want, not the moves. `delete_plans(project, spec, delete_unused_geom=, delete_unused_flow=)`
  bulk-deletes by a flexible id-spec (`'16-17,21-26,30-35'` string, or a list —
  via `resolve.expand_id_spec`); it validates every id up front (exists, listed,
  not mid-run) so a bad spec deletes NOTHING, then loops `delete_plan` and returns
  one consolidated report (`deleted_plans`, `deleted`, `prj_removed`, `warnings`,
  net `current_plan`, merged `rasmap_removed`). A geometry/flow shared by several
  deleted plans is still removed once, when its last user goes.
- Read-only queries used by GIS post-processing: `plan_short_ids(project)` (plans.py)
  maps each prj-listed plan to its `Short Identifier=` — the label RAS Mapper uses to
  name that plan's stored-results subfolder; `source_data_folders(rasmap_path)`
  (rasmap.py) returns the subfolders a `.rasmap` references via non-`RASResultsMap`
  layers (terrain, land-cover, feature layers) — i.e. the source-data folders to
  protect (never delete) when collecting result GIS. A folder referenced by BOTH a
  results map and a source layer (a plan whose Short ID collides with, say, the
  terrain folder) stays protected. Consumed by
  `Scripts/DataMgmt_Results_Collection/copy_results_gis.py`.

## Plan Settings — Intervals / Time Window / Title (`hack_ras/project/plan_settings.py`)

The keyword editor for an unsteady plan's *Simulation Time Window* and
*Computation Settings* panels. `plans.py` owns a plan's IDENTITY and NUMBER
(renumber/clone/delete/retitle); this owns its SETTINGS. Same conventions:
takes a `RasProject`, edits the `.p##` as raw lines (untouched lines stay
byte-identical, BOM and CRLF included), `.prj`-listed plans only, refuses a
plan mid-run.

```python
from hack_ras import RasProject, plan_settings          # module re-export
from hack_ras.project.plan_settings import read_plan_settings, set_plan_settings

s = read_plan_settings(project, "p05")   # -> PlanSettings dataclass
s.computation_interval, s.mapping_interval               # '3SEC', '1HOUR'
s.hydrograph_interval, s.detailed_interval               # '10MIN', '30SEC'
s.start, s.end            # datetime | None (None when Simulation Date= is blank)
s.window_raw              # '02JAN2025,0101,03JAN2025,2399' — as stored
s.title, s.short_id, s.missing_keys

# the common request: all three output intervals, many plans, one call
set_plan_settings(project, "5-24", output_intervals="5MIN")

set_plan_settings(project, "5-24,30", computation_interval="3SEC",
                  mapping_interval="1HOUR", hydrograph_interval="10MIN",
                  detailed_interval="30SEC")
set_plan_settings(project, "p05", start=datetime(2025, 1, 2, 1, 1),
                  end="03JAN2025,2400")        # datetime or RAS string
set_plan_settings(project, "p05", title="002year 5min", short_id="002yr5")
```

GUI label -> plan-file key. The mapping is NOT guessable and two entries are
actively misleading, so never key off a label:

| GUI (Unsteady Flow Analysis)  | plan-file key              | keyword                |
|-------------------------------|----------------------------|------------------------|
| Computation Interval          | `Computation Interval=`     | `computation_interval` |
| Mapping Output Interval       | `Mapping Interval=`         | `mapping_interval`     |
| Hydrograph Output Interval    | `Output Interval=`          | `hydrograph_interval`  |
| Detailed Output Interval      | `Instantaneous Interval=`   | `detailed_interval`    |
| Simulation Time Window        | `Simulation Date=d,t,d,t`   | `start` / `end`        |

`Output Interval=` is the HYDROGRAPH interval, not the mapping one, and the
DETAILED interval hides under `Instantaneous Interval=`. A plan also carries an
unrelated `WQ Output Interval=` (water quality); keys are matched on the whole
text left of the `=`, so that one is never caught by the `Output Interval`
match — a `startswith('Output Interval')` scan in a hand-rolled script would be
fine, but `'Output Interval' in line` would not.

- `spec` is the flexible plan id-spec (`'5-24'`, `'5,7,19-21'`, `['p05', 6]`,
  `5`) — `resolve.expand_id_spec`, which splits the commas.
- `output_intervals=` sets mapping + hydrograph + detailed to one value in one
  argument (the common "all output at N minutes" request). It cannot be
  combined with the three individual keywords — `ValueError`.
- Interval values are checked against `_ALLOWED[field]`, not just the
  `<number><unit>` shape — an off-list value produces a plan the GUI silently
  rewrites. Case and internal spaces are ignored (`' 5 min '` -> `'5MIN'`,
  `'max profile'` -> `'Max Profile'`).
- **The four dropdowns do NOT offer the same values** (RAS 7.0 GUI,
  user-confirmed 2026-09-11), which is why validation is per field:

  | field | offers |
  |-------|--------|
  | computation | `0.1`..`0.5SEC`, `1..6/10/12/15/20/30SEC`, `1..6/10/12/15/20/30MIN`, `1/2/3/4/6/8/12HOUR`, `1DAY` |
  | mapping | `Max Profile` + all of the above + `1WEEK`, `1MON`, `1YEAR` |
  | hydrograph | as mapping, but NO `Max Profile` and NO sub-second |
  | detailed | `Max Profile` + as hydrograph |

  Only the computation interval goes sub-second and only it stops at `1DAY`;
  only mapping and detailed offer `Max Profile`. A value offered somewhere but
  not on the field being set gets an error naming the fields that do take it.
- **Two tokens do not spell out.** The values are HEC-DSS interval names, so
  `1 Month` is stored `1MON`, NOT `1MONTH` — and `Max Profile` is a literal
  string with a space and mixed case ("write the maximum profile only"), not an
  interval token, so it does not survive the uppercase-and-strip normalization
  that every other value does. Every token in `_ALLOWED` was read back out of a
  GUI save of `Model_Hillside/Current_Model` p05 (2026-09-11) — the user
  re-saved it three times to fingerprint the ends of each list (`0.1SEC`,
  `Max Profile`, `1MON`, `1YEAR`, `1WEEK`), so none of them is inferred.
- `start` / `end` take a `datetime` or the file's own `'DDMMMYYYY,HHMM'` string
  (a space instead of the comma and a lowercase month are accepted), and are
  INDEPENDENT: passing only `end` rewrites the second half of
  `Simulation Date=` and leaves the first half's stored text alone. A string is
  written back as typed, which is the only way to express RAS's end-of-day
  `2400` — a `datetime` cannot, since it normalizes to the next day's `0000`.
  The resulting window must run forwards (`ValueError` otherwise); a half left
  blank is not checkable and is not checked.
- `title` / `short_id` delegate to `plans.retitle_plan`, so they also fix the
  `.rasmap` display names and the `.p##.hdf` title attributes (an ASCII-only
  retitle silently reverts — RAS Mapper regenerates its Results layer name from
  the HDF). Because plan titles must be unique and a short ID names the plan's
  RAS Mapper results folder, they require `spec` to select exactly ONE plan.
  Passing one of the pair preserves the other rather than re-deriving it, so
  `title=` alone does NOT reset a short ID that deliberately differs.
- Everything is validated before anything is written — off-list intervals,
  unparsable dates, a backwards window, missing / orphan / mid-run plans, and
  any requested key the plan file does not contain (it is never invented). A
  bad argument therefore changes NOTHING, on any plan.
- Report: `{'plans': [...], 'changed': {pid: {key: (old, new)}},
  'unchanged': [...], 'retitled': {pid: <retitle_plan report>}}`. A plan already
  holding the requested values lands in `unchanged` and its file is not
  rewritten at all.
- **The `.p##.hdf` is deliberately NOT touched** (unlike `retitle_plan`, which
  must). Intervals and the time window are run INPUTS that HEC-RAS writes into
  the HDF on the next compute, so editing them there would only desynchronize it
  from the results it actually holds — and a plan whose intervals or window
  changed has to be re-run regardless. Corollary for results code: after a
  settings change the `.p##.hdf` still reports the OLD `Base Output Interval`
  and time stamps until the plan is re-run.
- `read_plan_settings` is deliberately lenient where `set_plan_settings` is
  strict: an unparsable stored time reads back as `start=None`/`end=None` with
  `window_raw` keeping the truth, rather than raising and making the plan
  impossible to inspect. This is not hypothetical — HEC-RAS stores what was
  typed into the time box without validating it as a clock time
  (`Model_Hillside/Current_Model` p05 held `Simulation Date=...,2399` after a
  GUI edit, 2026-09-11; minute 99 is not a time, and `strptime` rejects it).
  `set_plan_settings` refuses to WRITE such a value, which is right: HEC-RAS
  itself will not RUN a plan whose end time is `2399` (user-confirmed
  2026-09-11), so the GUI stores it but the solver rejects it. `2400` IS legal
  and is RAS's own end-of-day idiom — the write path accepts it.
- Steady plans carry all four interval lines too — RAS writes them whatever the
  solver — but only the unsteady solver reads them, and a steady plan's window
  is typically `Simulation Date=,,,` (reads back as `start=None`/`end=None`,
  and `missing_keys == []` because the LINE is present). Verified on
  `tests/data/Wisconsin Floodway/SterpCreek.p01`.
- Scope: these four groups only. Other plan settings (the rest of Computation
  Settings, the 2D solver tolerances, output options) are not implemented —
  extend `_INTERVAL_KEYS` / add a keyword here rather than starting a new
  module.

## Geometry File Operations (`hack_ras/project/geoms.py`)

The geometry-file analogue of `plans.py`, added when the need arose to compact /
reorder geometry numbering and delete geometries directly (not just as the
`delete_plan(delete_unused_geom=True)` side-path). A geometry is a **shared
dependency** — many plans point at one via `Geom File=g##` — so renumbering a
geometry rewrites that reference in **every plan file**, which is the piece that
makes it more than a rename.

```python
from hack_ras.project.geoms import (
    renumber_geom, renumber_geoms, insert_geom_gap, compact_geoms, reorder_geoms,
    clone_geom, delete_geom)

renumber_geoms(project, {"g03": "g02", "g05": "g03"})  # bulk, chain/cycle-safe
renumber_geom(project, "g05", "g02")                   # single-entry case
insert_geom_gap(project, "g02", 1)                     # shift g>=02 up by 1
compact_geoms(project)                                 # g01,g03,g05 -> g01,g02,g03
reorder_geoms(project, ["g01", "g03", "g02"])           # complete list, as g01..gN
retitle_geom(project, "g01", "New Geom Title")          # rename g01 in place (no new file)
clone_geom(project, "g01", "New Geom Title", new_id="g07")  # copy .g## + new title
delete_geom(project, "g04")                            # refuses if a plan uses it
delete_geom(project, "g04", force=True)                # deletes anyway (+warns)
delete_geoms(project, "g04-g06", force=True)           # bulk delete by id-spec
```

Renumbering covers the geometry family (`.g##`, `.g##.hdf`, `.x##` — the `.x##`
preprocessor run file is geometry-keyed) plus, in ONE pass with the complete
mapping: the `.prj` `Geom File=` entries, the `Geom File=g##` line in **every
plan that uses a renumbered geometry**, and `Base.g##` tokens in the `.rasmap`
(`renumber_geoms_in_rasmap` — the `<Geometries>` layer's `Filename` and every
plan layer's `GeometryHDF=`; RASGeometry sub-layers inside `<Results>` name
`Base.p##.hdf`, so they are never matched). Chains/cycles use the same
`.renumtmp` hop as plan renumbering. Left alone (cosmetic, same policy as plans):
`.g##.hdf` internals, the `Geometry Filename` attr in each `.p##.hdf`, and the
stale plan title on `.x##` line 3. ONE carve-out, added with `retitle_geom`:
`Geometry/Title` in the `.g##.hdf` IS written, because RAS Mapper sources the
`<Geometries>` layer name from it — see the Retitling section. There is **no "Current Geometry"** key in the
`.prj` (geometry is chosen per-plan), so nothing global to repoint.

- `delete_geom` removes the family + `.prj Geom File=` entry, and (default
  `clean_rasmap=True`) the `<Geometries>` RASGeometry layer via
  `remove_geoms_from_rasmap`. It **refuses (`GeomInUse`)** if any listed plan
  still references the geometry, unless `force=True` — which deletes anyway and
  warns that those plans now point at a missing geometry (a forced delete leaves
  their `GeometryHDF=` in the rasmap dangling, by design; the plans still exist).
- `reorder_geoms(project, order)` is the geometry twin of `plans.reorder_plans`
  (same complete-list requirement, same ValueError-before-any-write behavior,
  single namespace so no kind argument). It is the most far-reaching of the three
  reorders: a geometry is shared, so each move rewrites `Geom File=` in every
  referencing plan plus the `<Geometries>` layer and each plan layer's
  `GeometryHDF=`. Added session 20 after `reorder_plans`/`reorder_flows`, when the
  user spotted geometry was the one subsystem missing it.
- `compact_geoms` builds the fill-the-gaps mapping and delegates to
  `renumber_geoms`; `insert_geom_gap` is the inverse (mirrors `insert_plan_gap`).
- `delete_geoms(project, spec, force=)` bulk-deletes by id-spec (mirrors
  `delete_plans`): validates every id up front and, unless force, refuses the
  whole call with `GeomInUse` if ANY target is still referenced — so nothing is
  deleted on a bad spec. Consolidated report: `deleted_geoms`, `deleted`,
  `prj_removed`, `referencing_plans` (per gid), `warnings`, `rasmap_removed`.
- `clone_geom` copies only the `.g##` text with a new (unique) `Geom Title=`
  (`DuplicateGeomTitle` otherwise) and inserts the `Geom File=` entry ascending;
  RAS regenerates the `.g##.hdf` on the next run (mirrors `clone_plan`).
- Typed exceptions: `GeomFileNotFound`, `GeomIdInUse`, `GeomInUse`,
  `DuplicateGeomTitle`, `GeomRunActive` (a plan using the geometry is mid-run —
  a `.p##.tmp.hdf` exists). Orphan geometries (on disk but not in the `.prj`) are
  rejected, mirroring the plan ops.

## Flow File Operations (`hack_ras/project/flows.py`)

The third file-type subsystem, and the closer analogue of `geoms.py` than of
`plans.py`: a flow file is a **shared dependency** (many plans point at one via
`Flow File=`), so renumbering one rewrites that reference in **every referencing
plan**; and like geometry there is no "current flow" key in the `.prj`, so nothing
global to repoint. Covers BOTH kinds — a module that handled only `.u##` would
re-open the flow==unsteady assumption that session 19 removed.

```python
from hack_ras import RasProject, flows

flows.renumber_flows(project, {"u01": "u02", "u02": "u01"})  # bulk; a swap is a 2-cycle
flows.renumber_flow(project, "u12", "u09")                   # single-entry case
flows.insert_flow_gap(project, "u05", 3)      # kind comes from at_id's own prefix
flows.compact_flows(project)                 # both namespaces, independently
flows.compact_flows(project, kinds=("unsteady",))     # ...or just one
flows.reorder_flows(project, ["u02", "u01", "u04"])   # complete list of ONE kind
flows.retitle_flow(project, "u02", "New Flow Title")   # rename u02 in place
flows.clone_flow(project, "u02", "New Flow Title", new_id="u07")
flows.delete_flow(project, "u12", force=False, clean_rasmap=True)
flows.delete_flows(project, "u09-u11,u13,f02")        # prefixes required
```

**TWO INDEPENDENT NAMESPACES — the one real departure from `plans`/`geoms`.**
Steady and unsteady numbering are separate (`f01` and `u01` legitimately coexist),
so every ID this module accepts must carry its kind prefix; a bare `'01'` raises
`ValueError` rather than guessing, including inside id-specs (`'u09-u11'`, never
`'09-11'`, and both endpoints of a range must carry it). That is a deliberate
asymmetry with the other two modules, where a bare number is unambiguous.
Cross-kind moves (`u01` -> `f01`) are refused as well — a format conversion, not
a rename — as are cross-kind ranges and a mixed-kind `reorder_flows` order.

What the kinds share and don't:
- `Flow File=` in a `.p##` is the plan's flow reference and holds an `f##` **or** a
  `u##` — the SAME key for both kinds. In the `.prj` they are DIFFERENT keys
  (`Unsteady File=u##` vs `Flow File=f##`). Because IDs are always prefixed, the
  `.prj` rewrite matches on the parsed ID and never branches on the key.
- family files: `.u##` + its `.u##.hdf` preprocessor sidecar; steady is a lone
  `.f##` (**no `.f##.hdf` exists** — verified across every local model, and
  confirmed on the Wisconsin fixture). Both candidates are listed and filtered by
  `os.path.isfile`, so disk stays the authority. No `.x##`-equivalent for flows.
- `.rasmap`: unsteady has an `<EventConditions>` RASEventConditions layer keyed
  `Base.u##.hdf` (remapped by the new `renumber_flows_in_rasmap`, removed by the
  existing `remove_flows_from_rasmap`); **steady has no rasmap presence at all**.
  The RASEventConditions layers nested inside a `<Results>` block name
  `Base.p##.hdf`, so token-keying never touches them — and this is load-bearing,
  not theoretical: 4 of 5 EC layers in the live Pattison model and 3 of 6 in the
  2D-culvert fixture are results-nested.

- **`Restart Filename=` is plan-keyed, not flow-keyed.** Those lines live INSIDE a
  `.u` file but name a `Base.p##.<stamp>.rst`, so a flow renumber renames the file
  around them and leaves the line alone (rewriting them is `plans.py`'s job). Both
  subsystems write to `.u` files for unrelated reasons, so this has its own
  regression test.
- Delete semantics mirror `delete_geom`: refuse with `FlowInUse` if any listed plan
  still references the flow, unless `force=True` (deletes anyway + warns that those
  plans now point at a missing flow). `clean_rasmap=True` drops the EC layer for a
  `u##` and is a no-op for an `f##`. `delete_flows` validates every id up front, so
  a bad spec — including a range spanning a gap — deletes nothing.
- `clone_flow` copies only the text file with a new unique `Flow Title=`
  (`DuplicateFlowTitle`, scoped to the same kind since RAS lists the two kinds
  separately) and inserts the `.prj` entry ascending under the right key; RAS
  regenerates the `.u##.hdf`. An unsteady clone inherits the source's
  `Restart Filename=` line if it has one.
- Left alone, same policy as plans/geoms: `.u##.hdf` internals, the `Flow Filename`
  attr in each `.p##.hdf` (stale provenance until RAS recomputes), and `.b##`/`.O##`
  (they embed flow TITLES, not numbers). `retitle_flow` adds no carve-out — a
  `.u##.hdf` holds no title to write — and the `Flow Title` attr each `.p##.hdf`
  records is left stale on purpose (confirmed inert; see the Retitling section).
- Typed exceptions: `FlowFileNotFound`, `FlowIdInUse`, `FlowInUse`,
  `DuplicateFlowTitle`, `FlowRunActive` (a plan using the flow is mid-run — a
  `.p##.tmp.hdf` exists). Orphans (on disk, not in the `.prj`) are rejected.

Tests: `tests/test_flow_ops.py` (47, synthetic mixed-kind project — f01 shared by
two steady plans, u01 by two unsteady, plus an unused f03/u04 and a deliberate gap
in each namespace) and `tests/test_flow_ops_fixture.py` (8, real models — the
2D-culvert model for unsteady incl. its genuine stale `u03` prj entry + stale EC
layer, and Wisconsin Floodway for steady, where `f01` is shared by both plans and
there is no `.rasmap` at all).

## Retitling — `retitle_plan` / `retitle_geom` / `retitle_flow`

The rename-the-name twin of the three `renumber_*` families, added 2026-08-26.
`renumber_*` changes only the `##`; before this there was NO way to change a
title except `clone_* + delete_*`, which for a shared geometry or flow also
renumbers the dependency. Each lives in its own subsystem module and reuses that
module's existing `Duplicate*Title` guard (previously reachable only from the
clone functions), its orphan rejection, and its mid-run check.

```python
plans.retitle_plan(project, "p58", "FC 002year 260824")                  # short id follows title
plans.retitle_plan(project, "p58", "FC 002year 260824", short_id="X")    # ...or set it apart
geoms.retitle_geom(project, "g01", "New Geom Title")
flows.retitle_flow(project, "u02", "New Flow Title")
```

**Only a plan has two names.** `Plan Title=` plus `Short Identifier=` (a
fixed-width padded field, kept at its original width). `short_id` defaults to
`new_title`, matching how HEC-RAS seeds it. A geometry (`Geom Title=`) and a flow
(`Flow Title=`) have one name each and take no `short_id`. Flow title uniqueness
is checked within the flow's own kind, since steady and unsteady are independent
namespaces.

### A title lives in up to four places, and the `.rasmap` is NOT the authority

**Empirical, Hillside 2026-08-26 — this is the whole reason retitle is more than
a one-line edit.** A `.rasmap` layer's display `Name=` is refreshed by RAS Mapper
from whatever file the layer's `Filename=` points at. That target differs by
section:

| section | `Filename=` points at | name shows | name comes from | rasmap-only edit sticks? |
|---|---|---|---|---|
| `<Plans>` RASPlan | `Base.p##` (TEXT) | plan TITLE | `Plan Title=` | yes |
| `<Results>` RASResults | `Base.p##.hdf` | SHORT ID | HDF attrs | **no — reverted** |
| `<Geometries>` RASGeometry | `Base.g##.hdf` | geom title | HDF `Geometry/Title` | **no — reverted** |
| `<EventConditions>` | `Base.u##.hdf` | flow title | `Flow Title=` (HDF has none) | yes |

**The title-vs-short-ID split is confirmed, not inferred** (Hillside 2026-08-26):
p62 was retitled to `'Renamed plan title'` with `short_id='Renamed short ID'` —
the first plan in that model where the two differ — and RAS Mapper showed
`Renamed plan title` in the `<Plans>` tree and `Renamed short ID` in the
`<Results>` tree, with all result data intact. This is why `retitle_plan` writes
the two sections separately via `only_sections=`. `retitle_flow` was confirmed in
the same pass: `u09` -> `RENAMED_FLOW` appeared in the unsteady flow editor, in
the referencing plans, and in the `<EventConditions>` tree.

Observed directly: eleven plans were retitled in the `.p##` text files AND the
`.rasmap`, the `.rasmap` was verified clean, then RAS Mapper was opened once and
re-saved it with all eleven `<Results>` names reverted to the old title — while
the `<Plans>` names survived. Hence `retitle_plan` / `retitle_geom` write the HDF
too (`update_hdf=True`), via `project/hdf_titles.py`. Re-verified after the fix:
RAS Mapper was opened again and left every name alone.

**The scope of the exception to the "cosmetic" policy.** "RAS rewrites `.hdf`
internals on the next run" stays TRUE and is not being overturned — it fails only
for a plan that has ALREADY been computed, because that is precisely the plan you
will not re-run (these eleven were ~5 min of compute each). The useful test is
not numbers-vs-titles, it is whether anything READS the stale field back:

- **Inert** — nothing reads it, so a wrong value is just a wrong note on a
  finished record. `Plan Data/Plan Information/Geometry Filename` in
  `p58.hdf` still reads `NKC_Hillside_Levee.g10` after the g10->g08 compaction,
  and RAS Mapper was opened with it in that state and displayed everything
  correctly (Hillside 2026-08-26). Leave these alone.
- **Contagious** — RAS Mapper reads it and writes it back into the `.rasmap`,
  destroying a correct value there. The title attributes in the table below.
  These must be written.

A field that gets copied into another project file is not cosmetic; it is a
source. That is the whole carve-out.

Which HDF attributes exist is version- and results-dependent, so every write is
best-effort-if-present and the report lists what was touched:

| group | attribute | holds | seen in |
|---|---|---|---|
| `Plan Data/Plan Information` | `Plan Name` | title | 5.0.3, 7.0 |
| `Plan Data/Plan Information` | `Plan ShortID` | short ID | 5.0.3, 7.0 |
| `Plan Data/Plan Information` | `Plan Title` | title | 7.0 only |
| `Results/Unsteady` | `Plan Title` | title | unsteady runs |
| `Results/Unsteady` | `Short ID` | short ID | unsteady runs |
| `Geometry` | `Title` | title | 5.0.3, 7.0 |

`Results/Steady` carries no title attributes (checked on the Wisconsin Floodway
5.0.3 and 7.0 fixtures), and a `.u##.hdf` carries none either — a flow's name
lives only in its text file and the `.rasmap`, which is why `retitle_flow` has no
`update_hdf`. The `Results/Summary/Compute Messages (rtf)` / `(text)` datasets
embed the old title in their `Plan: '<title>'` banner and are deliberately left
alone: they are the run's log, a record of what actually executed.

**Two traps in the HDF write** (`hdf_titles._set_str_attr`):
1. *Padding is not uniform.* RAS writes these as fixed-length ASCII sized exactly
   to the string, but `Plan Information` uses `H5T_STR_NULLTERM` while
   `Results/Unsteady` uses `H5T_STR_SPACEPAD`. HDF5 cannot resize an attribute in
   place, so each is deleted and recreated through the low-level API copying the
   original's cset/strpad. Assigning via `attrs[k] = value` would normalize them
   all to h5py's own convention.
2. *`mtype` must be passed explicitly to `h5a.write`.* Left to infer one from the
   numpy dtype, h5py picks a memory type that reserves a byte for a NUL
   terminator, and the conversion silently truncates the last character —
   `'Alpha'` lands as `'Alph'`. Caught by `test_hdf_string_not_truncated`.

**RAS Mapper names the sub-layers inside a `<Results>` block generically**, and
never from a stored title: the geometry node is literally `"Geometry"` and the
plan node literally `"Plan"` (both observed live). The RASPlan one is the trap —
it shares its `Type` with the `<Plans>` layer and is separated only by the `.hdf`
suffix, which is exactly why `_PLAN_SECTIONS` keys `<Plans>` to the
extension-LESS `Base.p##`. Guarded by
`test_nested_results_plan_sublayer_never_matched`.

`retitle_in_rasmap(path, base, kind, renames, only_sections=)` in `rasmap.py`
does the display-name half: it edits only the `Name` attribute of a matched
layer's opening tag (byte-for-byte elsewhere), XML-escapes the value, and invents
no `Name` where a layer has none. It is keyed on the same section/type/suffix
table the removal functions use, so the RASGeometry and RASEventConditions
sub-layers nested inside a `<Results>` block — which name `Base.p##.hdf` — are
never matched. `only_sections` exists because a plan's two layers do not show the
same string: `<Plans>` shows the TITLE, `<Results>` shows the SHORT ID.

### RESOLVED: a geometry / flow retitle does NOT fan out into the plan HDFs

Every `.p##.hdf` keeps its own copy of the names of the geometry and flow it ran
with, alongside its own:

```
Plan Data/Plan Information | Geometry Title    = 'FC gravity flow - 260824'
Plan Data/Plan Information | Flow Title        = '002year'
Plan Data/Plan Information | Geometry Filename = 'NKC_Hillside_Levee.g10'
```

`retitle_geom` / `retitle_flow` deliberately leave these alone. Tested live
(Hillside 2026-08-26): g08 was retitled to `RENAME_TEST` while p58-p68 held
finished results computed under the old title. Result:

- The `<Geometries>` tree shows `RENAME_TEST` — the `.g##.hdf` write lands.
- The geometry node inside a `<Results>` block is labeled with the **literal
  generic string "Geometry"**, not with any stored title (confirmed in RAS
  Mapper's own "RAS Geometry Properties" dialog, whose `Title:` field reads
  `Geometry`). It has no `Name` attribute in the `.rasmap` because RAS Mapper
  never derives one — so there is nothing for a stale value to overwrite.
- That dialog's Summary panel reports the plan HDF's stored provenance verbatim:
  `Geometry Title = FC gravity flow - 260824` next to
  `Plan Title = FC 500year breach L7 260824` / `Plan Short ID = ...`. The plan's
  own names show the NEW values (end-to-end confirmation that `retitle_plan`'s
  HDF write is what RAS Mapper reads); the geometry name shows the OLD one.
- Nothing breaks: the mesh loads, the result geometry HDF opens normally.

So this copy is **inert but visible** — read-only in a properties dialog, never
copied back into the `.rasmap`. And leaving it stale is not a compromise, it is
correct: the result WAS computed against a geometry titled
`FC gravity flow - 260824`. Rewriting it would falsify the run record, which is
the same reason `Results/Summary/Compute Messages` is left alone. Two provenance
fields, one rule, now with a live test behind it.

The distinction this closes out: `<Results>` layers are contagious for the
plan's OWN name (RAS Mapper derives the layer name from it and writes it back)
and inert for every name the plan merely recorded.

Same treatment for the same reason: the `.b##` run artifacts embed the plan
title (`NKC_Hillside_Levee.b58` still contains `n only`). Compute inputs from a
finished run, never displayed — left alone.

Tests: `tests/test_retitle.py` (30, synthetic project with real h5py-built
`.p##.hdf` / `.g##.hdf` so the padding and truncation behavior is exercised for
real; the fixture `.rasmap` carries the nested Results sub-layers and varied
attribute order copied from a live file). Baseline 488 -> 518.

## `.rasmap` Bound Accessor — `RasProject.rasmap` (`project/rasmap.py`)

Everything in `project/rasmap.py` is a stateless free function taking
`(rasmap_path, base_name, ...)`, which made every call site rebuild the path and
repeat the base name. `RasProject` now exposes `rasmap_path` and `rasmap` — the
latter a thin `RasMap` bound to those two values that forwards to the same
functions:

```python
project.rasmap.exists()            # many projects have no .rasmap at all
project.rasmap.sort()                          # sort_rasmap_layers
project.rasmap.sort(sections=("Plans",))
project.rasmap.renumber_plans({"p02": "p06"})  # renumber_*_in_rasmap
project.rasmap.renumber_geoms({"g03": "g02"})
project.rasmap.renumber_flows({"u01": "u02"})
project.rasmap.remove_plans(["p16", "p17"])    # remove_*_from_rasmap
project.rasmap.remove_geoms(["g04"])
project.rasmap.remove_flows(["u12"])
project.rasmap.layer_refs()                    # read-only queries
project.rasmap.result_plan_ids()
project.rasmap.source_data_folders()
```

**Deliberately thin, and this is the design constraint, not a shortcut.** It binds
two arguments and forwards — it does NOT parse the `.rasmap` XML into a model. RAS
Mapper owns that file; the narrow token/section editing in this module is exactly
what makes hand-editing it safe, and a parsed model would invite whole-file
rewrites. Because it holds no parsed state and caches no file content, it cannot
go stale against the file; the accessor itself is a `cached_property` only because
`(path, base_name)` are fixed for the project's lifetime. The free functions remain
the implementation and stay public — `delete_plan` / `renumber_plans` / `geoms` /
`flows` call them internally and were not changed.

`exists()` is a method (pathlib precedent), and the mutating methods raise from the
underlying `open()` if the file is absent — guard with `exists()` when a project
may have no `.rasmap`. `renumber_flows` was added to the accessor beyond the
original TODO list, which predated `flows.py`. Tests: 7 in `tests/test_rasmap.py`,
each pairing the accessor call with the equivalent free-function call so the sugar
cannot drift from what it wraps.

## Project Health / Status Inspector (`hack_ras/project/health.py`)

Read-only snapshot of a project — the thing to run to verify state after an
operation (it retired the ad-hoc, escaping-fragile inspection scripts) and to
answer "what's in this model / what's inconsistent?".

```python
from hack_ras.project.health import project_health, format_health
h = project_health(project)          # -> ProjectHealth dataclass
print(format_health(h))              # readable multi-line text summary
h.ok                                 # True when no consistency issue found
h.issues                             # {field: [...]} for every non-empty issue
```

`ProjectHealth` carries an **inventory** — `current_plan`; `plans`
(`PlanInfo`: id, title, geom, flow, has_results); `geometries` / `flows`
(`FileInfo`: id, title, `used_by` plan-ids) — and these **issue lists** (all
empty ⇒ `ok`): `orphan_files` (on disk, not in .prj), `stale_prj_entries`
(listed, file missing), `rasmap_duplicate_layers` (same token twice in a
section — the zombie case), `rasmap_missing_file_layers`, `unlisted_results`
(computed `.p##.hdf` with no `<Results>` layer — RAS Mapper will append them out
of order), `duplicate_titles` (RAS requires unique per kind), `unused_geometries`
/ `unused_flows`, `active_runs` (`.p##.tmp.hdf` present). Nothing here writes.
`has_results` / `unlisted_results` read the plan HDFs via h5py (imported lazily;
absent h5py ⇒ those are `None` / skipped, the rest still works). Backed by the
read-only `rasmap.rasmap_layer_refs()` and `rasmap.result_plan_ids()` queries.

The companion dry-run/preview (show a mutating op's change-set before applying)
is the paired, still-open TODO item — see docs/TODO.md.

## Parsing Strategy — Geometry
- **Block-driven**: `GeometryParser` dispatches to specialized handlers per block type
  (`River Reach=`, `Type RM Length=`, `XS GIS Cut Line=`, `#Sta/Elev=`, `#XS Ineff=`, etc.)
- **Lossless roundtrip**: `GeometryFile` stores original raw lines; `GeometryWriter`
  writes them back unchanged.
- **Strict errors**: resolution functions raise typed exceptions rather than `None`.

### Parsed cross-section blocks

All block data in the geometry file uses **8-character fixed-width fields** (except
`XS GIS Cut Line=` which uses 16-character fields).  Use `read_fixed_fields(line, 8)`
from `hack_ras/geometry/blocks/base.py` to split data lines.

| Block header | Handler | `CrossSection` field | Notes |
|---|---|---|---|
| `XS GIS Cut Line=` | `blocks/xs_gis.py` | `cutline: XSGISCutLine` | 16-char fields; X,Y pairs |
| `#Sta/Elev=` | `blocks/xs_sta_elev.py` | `sta_elev: List[Tuple[float,float]]` | 8-char fields; (station, elevation) pairs |
| `#XS Ineff=` | `blocks/xs_ineff.py` | `ineff: IneffFlowAreas` | 8-char fields; see IFA section below |
| `#Mann=` | `blocks/xs_mann.py` | `manning_def: ManningDef` | Two formats — see below |
| `Bank Sta=` | `blocks/xs_bank_sta.py` | `bank_stations: Tuple[float,float]` | left bank, right bank stations |
| `Levee=` | `blocks/xs_levee.py` | `levee: Levee` | single line; left/right sta+elev, `None` per absent side |
| `#Block Obstruct=` | `blocks/xs_block_obstruct.py` | `blocked_obstructions: BlockedObstructions` | 8-char triplets `[start,end,elev]`; `normal` (flag 0) / `multiple_block` (flag -1); no `Permanent` follower |

### Parsed non-cross-section blocks

| Block header | Handler | `GeometryFile` field | Notes |
|---|---|---|---|
| `Storage Area 2D Points=` | `blocks/storage_area_2d.py` | `storage_areas_2d: List[StorageArea2D]` | 16-char fields; 2D-mesh cell seeds; `N = 0` is header-only |
| `Connection=` family | `blocks/connection.py` | `connections: Dict[str, Connection]` | Centerline (16-char) + weir/terrain profiles (8-char). See **SA/2D Connections** — stationing is arc length, NOT the fractional XS rule |

### Manning's n formats

All formats store data as `(station, n_value, position_code)` triplets in 8-char
fixed-width fields.  The header is `#Mann= N , method , 0`.

The method integer reflects the state of the **"Horizontal Variation in n-values"
checkbox** in the HEC-RAS GUI:

| method | GUI checkbox | Meaning |
|--------|-------------|---------|
| `0` | OFF | Standard LOB/Channel/ROB.  Always N=3; stations are the XS left edge, left bank station, and right bank station. |
| `-1` | ON | Horizontal variation, modern convention.  N entries at arbitrary stations. |
| `1` | ON | Horizontal variation, legacy convention (older files only).  Same semantics as `-1`. |

Example — method=0 (standard, checkbox OFF):
```
#Mann= 3 , 0 , 0
         0     0.11        0    623.5    0.065        0   664.71     0.11        0
```

Example — method=-1 (horizontal variation, checkbox ON):
```
#Mann= 5 ,-1 , 0
         0      100        0   482.77     0.11        0   850.38    0.065        0
    872.77     0.11        0   941.38      100        0
```

Position codes are informational and discarded on parse.  Stored as
`ManningDef(method=<int>, entries=[(station, n_value), …])`.

**Read**: accept method 0, -1, and 1 (all use identical triplet structure).
**Write**: preserve method=0 verbatim; always write method=-1 for any newly
constructed horizontal variation output (never write method=1).

### Ineffective flow areas (IFAs)

Parsed by `blocks/xs_ineff.py`.  The `#XS Ineff=` header gives the count and type flag;
`Permanent Ineff=` (always the next block) provides per-area T/F flags.

**Type flag:**
- `0` → `"normal"`: always exactly 2 areas — a left-bank zone and a right-bank zone.
- `-1` → `"multiple_block"`: 1–10 arbitrary blocked zones.

**Data format** — each area occupies 3 consecutive 8-char fields: `start_sta`, `end_sta`, `elevation`.
A blank field and an explicit `0` are equivalent (both parse to `0.0` for stations, `None` for elevation).

**Sentinel values for normal type:**
- `start_sta = 0.0` → the area begins at the leftmost XS station (`sta_elev[0][0]`).
- `end_sta = 0.0` → the area ends at the rightmost XS station (`sta_elev[-1][0]`).
- Both `start_sta` and `end_sta` equal `0.0` → the IFA entry is **blank** (undefined);
  skip it entirely. Either the left or the right area can be blank independently.

**Elevation** — `None` means infinite height (the ineffective zone is always active
regardless of water surface elevation).

```python
@dataclass
class IneffArea:
    start_sta: float           # 0.0 = left sentinel (see above)
    end_sta: float             # 0.0 = right sentinel (see above)
    elevation: Optional[float] # None = infinite height
    permanent: bool            # from Permanent Ineff= block

@dataclass
class IneffFlowAreas:
    ifa_type: str              # "normal" or "multiple_block"
    areas: List[IneffArea]
```

### Station-to-XY interpolation — `geometry/xs_interp.py`

HEC-RAS cross-section stationing and GIS cut-line coordinates are **independent**.
A cross-section may be stationed from 0 to 800 ft while its GIS cut line is only
400 ft long in projected map coordinates.  The HEC-RAS GUI reconciles this by
mapping station-based features (IFAs, blocked obstructions, Manning's n breaks)
proportionally along the cut line:

```
fraction = (station − min_sta) / (max_sta − min_sta)
dist_along_cutline = fraction × cutline_arc_length
```

Two public functions implement this mapping:

| Function | Returns | Raises |
|---|---|---|
| `station_to_xy(xs, station)` | `(x, y)` at the given RAS station | `ValueError` if `xs.cutline` or `xs.sta_elev` is `None` |
| `clip_xs_polyline(xs, sta_start, sta_end)` | `List[(x,y)]` sub-polyline from `sta_start` to `sta_end` | same |

Both functions take a `CrossSection` directly.  The cut-line vertices between
the interpolated entry and exit points are preserved, so the result correctly
follows all bends in the GIS cut line regardless of how many station/elevation
pairs the cross-section has.

Use these functions (not ad-hoc arc-length arithmetic) whenever a script needs
to place any station-referenced XS feature in GIS space.

## HEC-RAS Version Detection (`hack_ras/version.py`)

HEC-RAS **rewrites the HDF5 schema between major versions** (e.g. 5.0.3 → 7.0 moved
the XS name arrays into a compound `Cross Sections/Attributes`, dropped
`Cross Section Variables`, and relocated `Results/Summary`). The text files
(`.g##/.f##/.u##/.p##`) change far less, so version-gating is an **HDF concern only** —
text parsers do not need it.

**Authoritative version = the HDF root attribute `File Version`** (e.g.
`"HEC-RAS 7.0 April 2026"`). Do **not** use the plan text `Program Version=` line (not
updated consistently — a model re-run in 7.0 can still read `5.03`) or the HDF
`File Type` attr (a 7.0 geometry HDF was observed mislabelled `"HEC-RAS Results"`).

```python
from hack_ras.version import RasVersion
ver = RasVersion.from_hdf(hdf_path)      # -> RasVersion(7, 0) or None if absent/unparseable
if ver and ver >= RasVersion(6, 0):
    ...   # compound layout
```

**Pattern for version-sensitive HDF reads — structural probing, not exact-version match.**
Select the layout by which known dataset is actually present (see
`read_xs_name_index` in `results/reader.py` as the reference): probe the 5.x flat
datasets, then the 6.0+ compound dataset. A new version number that keeps an existing
schema then needs no code change. If **no** known layout matches, assume the latest
known layout and log a loud warning with the detected `File Version`; if the assumed
datasets are also absent, let the `KeyError` propagate so an unknown schema fails loudly
rather than returning wrong data. The parsed version is for warning/error context and for
the rare case where the same path means different things across versions.

Adopt this incrementally: convert an HDF reader when it is next touched, not in a sweep.
`read_steady_profile_wse` / `read_xs_name_index` are the first converted example
(verified against both the 5.0.3 and 7.0 Starkweather DCRA models).

## HEC-RAS HDF5 Structure (`.p##.hdf`)

All datasets are in the plan HDF file. The `.p##` text sidecar alongside it holds
the plan title and geometry ID (`Geom File=g##`).

### 2D Flow Area Registry
Path: `Geometry/2D Flow Areas/` (top-level datasets, not inside any area subgroup)

| Dataset | Shape | dtype | Notes |
|---------|-------|-------|-------|
| `Attributes` | (N_areas,) | structured | One row per area: `Name` (S16), `Cell Count` (int32 — active/non-perimeter cells only), plus Manning's n, tolerances, spacing, etc. |
| `Cell Info` | (N_areas, 2) | int32 | `[start, count]` per area in the merged `Cell Points` array (active cells only; perimeter dummy cells are excluded) |
| `Cell Points` | (total_active, 2) | float64 | Concatenated cell-centre XY for all areas, ordered by area then local cell index |

Key facts:
- `Cell Count` in `Attributes` and the count column in `Cell Info` reflect **active** cells
  only. The per-area geometry subgroup (`Cells Center Coordinate`) has a larger N that
  includes perimeter dummy cells, so `Cell Count < N_subgroup`.
- Cell indices stored in SA 2D Conn datasets (`Headwater Cells`, `Tailwater Cells`) are
  **local** to the connected 2D area, not offsets into `Cell Points`.
- Each area also has a subgroup `Geometry/2D Flow Areas/{area_name}/` with its own
  `Cell Maximum Index` attribute (highest non-perimeter cell index in that area).

### 2D Flow Area Geometry
Base path: `Geometry/2D Flow Areas/{area_name}/`

| Dataset | Shape | dtype | Notes |
|---------|-------|-------|-------|
| `Cells Center Coordinate` | (N, 2) | float64 | XY of each cell centre |
| `FacePoints Coordinate` | (M, 2) | float64 | Shared vertex pool |
| `Cells FacePoint Indexes` | (N, max_faces) | int32 | Per-cell index into FacePoints; −1 = unused slot |
| `Perimeter` | (K, 2) | float64 | Outer boundary polygon vertices |
| `Cells Minimum Elevation` | (N,) | float64 | Minimum terrain elevation; **NaN for perimeter dummy cells** |
| `Cells Center Manning's n` | (N,) | float32 | The n value the solver used at each cell centre; **populated for perimeter dummy cells too** |
| `Cells Surface Area` | (N,) | float32 | Horizontal plan area of the cell as RAS sees it. Matches the perimeter-accurate `polygons[i].area` everywhere (to float32). Numerically zero (~1e-12, sometimes negative) for perimeter dummy cells. Surfaced as `AreaGeometry.plan_areas` |
| `FacePoints Is Perimeter` | (M,) | int32 | `-1` = face point lies on the mesh perimeter, `0` = interior. Note the flag is **−1, not 1** — summing it gives a negative count |
| `Faces Perimeter Info` / `Faces Perimeter Values` | (F,2) int32 / (P,2) float64 | Extra vertices along a face where the mesh perimeter bends between its two face points |
| `Cells Volume Elevation Info` | (N, 2) | int32 | Per-cell `[start_idx, count]` into Values array; count=0 for perimeter dummies |
| `Cells Volume Elevation Values` | (total_pairs, 2) | float32 | Packed `[elevation, volume]` pairs for all cells |

Key facts:
- **Perimeter dummy cells** have NaN `Cells Minimum Elevation` and `count=0` in the Info
  array. They must be excluded from profile output and spatial queries.
- `Cells Minimum Elevation` is bit-for-bit identical to the first elevation entry in each
  cell's volume table — confirmed empirically. The two datasets are redundant by design.
- Cell polygons are reconstructed from `FacePoints Coordinate[Cells FacePoint Indexes[i]]`
  (strip negative padding indices before building the `Polygon`).
- **Boundary cells need `Faces Perimeter Values`.** A face on the mesh perimeter can
  bend between its two face points, and the bend vertices are stored in
  `Faces Perimeter Values`, NOT in `Cells FacePoint Indexes`. Face points alone cut the
  corner (or bulge past it) on such a cell. Walk `Cells Face and Orientation Values`
  instead and splice each face's perimeter run in; `reader._perimeter_polygons` does
  this and `read_area_geometry` uses it.
- **The perimeter-accurate outline is what RAS computes with** (measured 2026-08-24 over
  8 meshes / 4 plans / 7486 cells: Hillside p12, p51, p62 and the test fixture p02):
  - `polygons[i].area / Cells Surface Area[i]` = 1.0 to at worst **5.4e-08** relative,
    i.e. float32 round-off on that dataset, for every cell, interior and boundary alike.
  - The cell polygons tile `Perimeter` with **zero** symmetric difference
    (`unary_union(cells) ^ Polygon(Perimeter)` = 0.0 sq ft).
  - Face points alone: off by up to **25%** on a single boundary cell, and leaving
    81.6k sq ft (`Interior`) / 1.18M sq ft (`RockCr`) of the mesh untiled.

  So the detailed perimeter is not cosmetic — it is the geometry the volume tables are
  integrated over and the continuity equation uses. There is no use case for the
  face-point-only outline; it survives only as a pre-7.0 fallback.
- **Verified against RAS 7.0 only.** Every 2D plan HDF on disk (Hillside, PCA, Pattison,
  DCRA, the fixture) is 7.0 and carries all five perimeter/face datasets. No pre-7.0 2D
  model was available, so `_facepoint_polygons` (the fallback taken when any of
  `_PERIM_POLY_KEYS` is absent) is **untested on real data**.

### Volume-Elevation Table Usage
```
start, count = info[cell_idx]          # from Cells Volume Elevation Info
elev = values[start:start+count, 0]    # elevations for this cell
vol  = values[start:start+count, 1]    # volumes for this cell
interpolated = np.interp(wse, elev, vol)
```
- If WSE ≤ elev[0]: volume = 0.0 (dry)
- If WSE > elev[-1]: **linear extrapolation** — `vol[-1] + (wse - elev[-1]) * cell_plan_area`
  HEC-RAS treats the cell as a flat-bottomed tank once WSE exceeds the highest terrain
  point; volume grows linearly at the cell's horizontal plan area (ft² or m²).
  Do **not** clamp to `vol[-1]` — that underestimates storage in deeply flooded cells.
- `cell_plan_area` comes from **`AreaGeometry.plan_areas[cell_idx]`** (i.e. RAS's own
  `Cells Surface Area`), not from `polygons[cell_idx].area`. The two agree to float32
  round-off on a 7.0 HDF, but `plan_areas` needs no reconstruction and stays correct on
  the pre-7.0 fallback path. `Scripts/Results_Profile_Lines_Volume/extract_volume.py` uses it.

#### Cell min/max are EFFECTIVE ground, not the source DEM
`CellVolumeTable.top_elevations` vectorises the `elev[-1]` lookup for every cell (nan where a
cell's table is empty) — the high-side counterpart to `AreaGeometry.min_elevations`.

Both are the terrain **the model computes with**: the RAS Mapper terrain layer the geometry
names, with its modifications applied. Terrain modifications are the designed mechanism for
overriding the raster where a hydraulically significant feature is known to be missing from
it — burned piers, channels, culvert inlet/outlet inverts — so the modified surface is the
real one and the source `.tif` is a means to an end. This cuts both ways: a burned channel or
culvert invert lowers `min_elevations`, and that lowered value is what RAS routes on.

GUI-confirmed: LAX_River_2D p19 cell 12432 shows a maximum elevation of **649.658** in RAS
Mapper's own volume-elevation table, and `top_elevations[12432]` returns 649.6576. The cell is
under `RR6_CPKC_Overflo`, where `Pier 31` / `Pier 32` are burned in at 649.70.

**The practical consequence: you cannot reproduce these by sampling the parent `.tif`.** Over
376 cells of that model clear of every modification the two agree (mean **+0.01 ft** from the
DEM maximum inside the cell polygon, p99 of |error| 0.88 ft, worst 2.85 ft). Over 24 cells
carrying a modification or a structure the table runs higher and only ever higher — mean
**+4.40 ft**, worst **+26.73 ft** at a Gillette St pier burned near 669 ft where the bare DEM
tops out near 642 ft. `min_elevations` shifts the same way (worst 2.92 ft). Sample the terrain
clone, or use these values; do not cross-check one against the other's source.

It is terrain, not structure geometry — the table follows burned ground, not a bridge's
chords. `Brdg2_GRST`'s footprint cell 12660 tops out at 655.50 while its `2DBR Cells`
`High Chord` is 682.0. Measured 2026-09-09; a 9-cell spot check had suggested the bare-DEM
match held everywhere, which a 400-cell sample did not support.

### Output Blocks
The `Results/Unsteady/Output/Output Blocks/` group contains three named output blocks:

| Block | Contents |
|-------|----------|
| `Base Output` | Summary Output + Unsteady Time Series — the primary block to read from |
| `DSS Hydrograph Output` | Unsteady Time Series only (no Summary Output) |
| `DSS Profile Output` | Unsteady Time Series only (no Summary Output) |

All reader code should target `Base Output`. The other two blocks duplicate the time-series
data for DSS export purposes and can be ignored.

Base path used throughout: `Results/Unsteady/Output/Output Blocks/Base Output/`

### Water Surface Elevation Results

| Path (relative to base) | Shape | Notes |
|--------------------------|-------|-------|
| `Summary Output/2D Flow Areas/{area}/Maximum Water Surface` | (2, N) | Row 0 = max WSE (sub-step accuracy, may exceed any time-series value); row 1 = time of maximum as **decimal days from midnight** of the simulation start date |
| `Unsteady Time Series/2D Flow Areas/{area}/Water Surface` | (T, N) | WSE at every output time step |
| `Unsteady Time Series/SA 2D Area Conn/Time Date Stamp` | (T,) | Timestamp strings: `b'01JAN2025 00:30:00'` (upper-case, bytes) |

WSE type options (used throughout `results.reader`):
- `"Maximum"` — reads Summary Output row 0 (sub-step accuracy, may exceed any single time step)
- `"Maximum from Time Series"` — `nanmax` across the full time series
- `"<timestamp>"` — match against Time Date Stamp array (case-insensitive)

### Summary Output — 2D Flow Areas (per area)
Path: `Summary Output/2D Flow Areas/{area}/`

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Maximum Water Surface` | (2, N_cells) | Row 0 = max WSE; row 1 = time of max in decimal days (sub-step accuracy) |
| `Minimum Water Surface` | (2, N_cells) | Row 0 = min WSE; row 1 = time |
| `Maximum Face Velocity` | (2, N_faces) | Row 0 = max velocity; row 1 = time in decimal days |
| `Minimum Face Velocity` | (2, N_faces) | Row 0 = min (most-negative) velocity; row 1 = time |
| `Cell Maximum Water Surface Error` | (2, N_cells) | Row 0 = max solver error (ft); row 1 = time in decimal days |
| `Cell Cumulative Iteration` | (N_cells,) float32 | Total times each cell hit max iterations |
| `Cell Last Iteration` | (N_cells,) int32 | Times each cell was last to converge |
| `Starting Differences WSE` | (3, N_cells) | Rows: prior profile WSE, first time step WSE, difference |
| `Starting Differences Velocity` | (3, N_faces) | Same but for velocity |

Group-level **attributes** (not datasets) hold per-area volume accounting summary:
`Vol Accounting Ending Volume`, `Vol Accounting Error`, `Vol Accounting Error Percentage`,
`Vol Accounting External Inflow/Outflow`, `Vol Accounting Internal Inflow/Outflow` (all float32, acre-ft).

### Summary Output — Pipe Networks
Path: `Summary Output/Pipe Networks/{network}/` (only in Base Output)

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Maximum Water Surface` | (2, N_nodes) | Max node water surface; row 1 = time in decimal days |
| `Maximum Face Flow` | (2, N_faces) | Max face flow; row 1 = time |
| `Maximum Face Velocity` | (2, N_faces) | Max face velocity; row 1 = time |
| `Maximum Link US/DS Flow` | (2, N_pipes) | Max pipe flow (US or DS end); row 1 = time |
| `Maximum Link US/DS Velocity` | (2, N_pipes) | Max velocity; row 1 = time |
| `Maximum Link US/DS Water Surface` | (2, N_pipes) | Max WSE at pipe end; row 1 = time |
| `Minimum Face Flow / Velocity / Water Surface` | (2, …) | Same pattern as Maximum equivalents |

### Structures (Connection Geometry)
Path: `Geometry/Structures/Attributes` — structured array, one row per connection/structure.

Relevant fields for SA 2D Area Conn lookups:

| Field | dtype | Notes |
|-------|-------|-------|
| `Connection` | S16 | Connection name (truncated to 16 chars) |
| `US SA/2D` | S16 | Name of the **HW-side** 2D flow area (or storage area) |
| `DS SA/2D` | S16 | Name of the **TW-side** 2D flow area (or storage area) |
| `SNN ID` | int32 | Integer node ID; matches the `Node Pointer` HDF attribute on the connection's results group |
| `US Type` / `DS Type` | S16 | `b'2D'` for a 2D mesh side |

To find which 2D area an SA 2D Conn's HW/TW cells belong to:
1. Read `Node Pointer` attr from the connection's results group (e.g. `Results/…/SA 2D Area Conn/{name}`)
2. Find the row in `Geometry/Structures/Attributes` where `SNN ID == Node Pointer`
3. `US SA/2D` → HW area name; `DS SA/2D` → TW area name

Use `read_sa2d_areas(hdf_path, connection)` from `hack_ras.results.reader`.

**Step 1 does not work for a `Bridge Opening` connection** — its results group carries no
`Node Pointer` attribute. Match the connection NAME against the `Connection` field instead
(strip the `'<area> '` group-name prefix first), and mind that `Connection` is S16 so long names
truncate. `read_sa2d_areas()` does this automatically. See **SA/2D Connection Result Layouts**.

The `Mode` field (`S18`) is what distinguishes the two: `b'Bridge Opening'` vs
`b'Weir/Gate/Culverts'`. `read_connection_centerline()` exposes it as
`ConnectionCenterline.mode`, and the ASCII side spells the same split as
`Conn Routing Type= 32` vs `1`.

### 2D Bridge Geometry (`2DBR *` tables)
A `Bridge Opening` connection's mesh footprint lives in flat `Geometry/Structures/2DBR *`
tables, every row tagged with `Structure ID` (the row index into
`Geometry/Structures/Attributes`) and `Mesh ID`. Filter by `Structure ID` to get one bridge.

| Dataset | Extra fields | Notes |
|---------|--------------|-------|
| `2DBR Cells` | `Cell Index`, `High Chord`, `Low Chord` | Cells inside the bridge footprint — the deck band. Chord elevations are per cell |
| `2DBR US Cells` | `Cell Index`, `Station Start`, `Station End` | Cells on the headwater side, WITH stationing along the alignment |
| `2DBR DS Cells` | `Cell Index`, `Station Start`, `Station End` | Same, tailwater side |
| `2DBR Faces` | `Face Index` | Every face belonging to the structure |
| `2DBR BU Faces` / `2DBR BD Faces` | `Face Index`, `FP Start Index`, `FP End Index` | Bridge-up / bridge-down faces |

Use `read_bridge_cells(hdf_path, connection)` → `BridgeCells`, which filters all three
cell tables to one structure and exposes `cells` / `high_chord` / `low_chord` plus
`stations('us'|'ds')` → `{cell: (start, center, end)}`.

These are the only place a bridge-mode connection carries per-cell stations; its results group
does not (see **SA/2D Connection Result Layouts**). `2DBR US Cells` / `2DBR DS Cells` hold the
same cell lists the results group's `Headwater Cells` / `Tailwater Cells` give, which is exactly
how `read_sa2d_connection` stations a bridge-mode `Sa2dCell`.

The profiles along the alignment live in `Profile Data`, indexed by the nine profile slots in
`Table Info` — read them all with `read_structure_profiles()`. For a bridge, the
`Centerline Profile` dips **through the opening**, so its minimum is the channel bed, not an
embankment crest — a trap when computing overtopping elevations. Weir-mode connections have no
such dip; their profile minimum is the road low point.

### Plan Metadata
Path: `Plan Data/Plan Information` — HDF5 group with scalar **attributes** (not datasets).

| Attribute | Example value | Notes |
|-----------|---------------|-------|
| `Simulation Start Time` | `b'01Jan2025 00:00:00'` | Reference midnight for Summary Output decimal-days times |
| `Simulation End Time` | `b'02Jan2025 00:00:00'` | |
| `Time Window` | `b'01Jan2025 00:00:00 to 02Jan2025 00:00:00'` | Human-readable window |
| `Plan Title` | `b'FC 050year'` | Same as `.p##` sidecar `Plan Title=` line |
| `Plan Name` / `Plan ShortID` | same as title | |
| `Geometry Title` | `b'FC gravity flow'` | |
| `Base Output Interval` | `b'30MIN'` | Output time-step interval |
| `Computation Time Step Base` | `b'1SEC'` | Computational sub-step |
| `Plan Filename` | `b'GMF_DFA.p01'` | Basename of the .p## the run used (RAS 7.0; verified live) |
| `Flow Filename` / `Geometry Filename` | `b'GMF_DFA.u01'` / `b'GMF_DFA.g01'` | Same, for the flow/geometry files |
| `Project Filename` | absolute path | Full path of the .prj at run time |

The four `*Filename` attributes mean a renamed/renumbered results HDF carries
stale internal filenames (fixed-length `np.bytes_`). Whether RAS/RAS Mapper cares
is untested — RAS Mapper loaded renamed projects fine in the GMF_DFA job — so
`renumber_plans` deliberately leaves HDF internals alone; revisit with h5py attr
updates only if evidence appears that something reads them.

Use `read_simulation_start_time(hdf_path)` from `hack_ras.results.reader` to get a
`datetime` object parsed from `Simulation Start Time`.

### Unsteady Time Series — 2D Flow Areas
Base path: `Results/.../Unsteady Time Series/2D Flow Areas/{area}/`

Each 2D area group contains:

| Path | Shape | Notes |
|------|-------|-------|
| `Water Surface` | (T, N_cells) | WSE at every output time step |
| `Face Velocity` | (T, N_faces) | Face-normal velocity at every output time step |
| `Boundary Conditions/Cell Cumulative Excess Depth` | (T, N_cells) | Cumulative excess precipitation (in) |
| `Boundary Conditions/Cell Cumulative Infiltration Depth` | (T, N_cells) | Cumulative infiltration (in) |
| `Boundary Conditions/Cell Cumulative Precipitation Depth` | (T, N_cells) | Cumulative gross precipitation (in) |
| `Computations/Inner Iteration Number` | (T, 1) | Sum of inner-loop iterations per time step |
| `Computations/Outer Iteration Number` | (T, 1) | Number of outer-loop iterations |
| `Computations/Outer Status` | (T, 1) | Convergence code: 1=ConvMax, 2=ConvRMS, 3=Stall, 4=Iter, 5=Small, −1=Max, −2=Div |
| `Computations/Max Water Surface Cell` | (T, 1) | Cell index of highest WSE at each time step |
| `Computations/Volume` | (T, 1) | Total wet volume (acre-ft) |

### Unsteady Time Series — 2D Hyd Conn (culverts and inline structures)
Base path: `Results/.../Unsteady Time Series/2D Flow Areas/{area}/2D Hyd Conn/{connection}/`

2D Hyd Conn features are culverts and inline road structures **within** a 2D flow area (not levees
between areas). Each has a `Node Pointer` attribute linking to `Geometry/Structures/Attributes`.

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Headwater Cells` | (N_hw,) int32 | HW cells — local to the area |
| `Tailwater Cells` | (N_tw,) int32 | TW cells — local to the area |
| `Culvert Groups/{Culvert #N}` | (T, 3) | Culvert Flow (cfs), Stage HW (ft), Stage TW (ft) |
| `Structure Variables` | (T, 5) | Total Flow, Weir Flow, Stage HW, Stage TW, Total Culvert Flow |
| `Weir Variables` | (T, 9) | Weir Flow, Sta US, Sta DS, Top Width, Max Depth, Avg Depth, Flow Area, Coef |
| `HW TW Cells/Water Surface HW Cells` | (T, N_hw) | WSE time series per unique HW cell |
| `HW TW Cells/Water Surface TW Cells` | (T, N_tw) | WSE time series per unique TW cell |
| `HW TW Segments/HW TW Station` | (N_segs+1,) float32 | Face-point stations |
| `HW TW Segments/Headwater Cells` | (N_segs,) \|S10 | Cell index string per segment |
| `HW TW Segments/Tailwater Cells` | (N_segs,) \|S10 | Same, TW side |
| `Geometric Info/Gates and Culverts/{Culvert #N}/Culvert CL Cell HW` | (N,) int32 | Cells at culvert centerline — HW side |
| `Geometric Info/Gates and Culverts/{Culvert #N}/Culvert CL Cell TW` | (N,) int32 | Cells at culvert centerline — TW side |

### Unsteady Time Series — 2D Bridges
Base path: `Results/.../Unsteady Time Series/2D Bridges/{area} {bridge_name}/`

2D Bridges are road bridges modelled inside a 2D flow area — a connection whose
`Mode` is `Bridge Opening`. The group key is **area-prefixed**, exactly like an interior
connection under `SA 2D Area Conn` (`b'Perimeter 1 Brdg2_GRST'`, `b'Watershed Bridge'`),
NOT the bare bridge name.

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Headwater Cells` | (N_hw,) int32 | Upstream face cells |
| `Tailwater Cells` | (N_tw,) int32 | Downstream face cells |
| `Cell WS US` | (T, N_hw) | WSE on the upstream side of each bridge cell |
| `Cell WS DS` | (T, N_tw) | WSE on the downstream side |
| `Face Flow` | (T, N_faces) | Flow through each bridge face |
| `Structure Variables` | (T, 6) | Flow (cfs), Stage HW, Stage TW, Head loss, Drag Factor, Error HW |

`Cell WS US` / `Cell WS DS` map **positionally** onto `Headwater Cells` / `Tailwater Cells`:
column *i* is the cell at index *i*. Verified against Summary Output per cell on
LAX_River_2D p19 `Brdg2_GRST` — 20 of 20 columns agree to 6e-5 ft.

**RAS writes these results three times, byte-identically** (checked dataset by dataset on
Model.p02 `Bridge` and LAX_River_2D p19 `Brdg2_GRST`):

| Path | Key |
|------|-----|
| `Unsteady Time Series/2D Bridges/{area} {name}` | area-prefixed |
| `Unsteady Time Series/2D Flow Areas/{area}/Bridges/{name}` | bare name |
| `Unsteady Time Series/SA 2D Area Conn/{area} {name}` | area-prefixed |

So a bridge is reachable through `list_sa2d_connections()` / `read_sa2d_connection()` like
any other connection — pick whichever path is convenient, there is no "primary" copy.

### SA 2D Area Conn Results
Base path: `Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series/SA 2D Area Conn/{connection}/`

SA 2D Area Conn features (levees, lateral structures) have **no Summary Output group** — only
time-series data at output-interval resolution (e.g. 30 min).

**Group attribute:** `Node Pointer` (int) — links this results group to the geometry row
in `Geometry/Structures/Attributes` via `SNN ID`.

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Headwater Cells` | (N_hw,) int32 | Unique cell indices — **local to the HW 2D area** |
| `Tailwater Cells` | (N_tw,) int32 | Unique cell indices — **local to the TW 2D area** |
| `HW TW Cells/Water Surface HW Cells` | (T, N_hw) float32 | WSE time series per unique HW cell |
| `HW TW Cells/Water Surface TW Cells` | (T, N_tw) float32 | WSE time series per unique TW cell |
| `HW TW Segments/HW TW Station` | (N_segs+1,) float32 | Face-point stations along the structure |
| `HW TW Segments/Headwater Cells` | (N_segs,) \|S10 | Cell index string per segment, e.g. `b'1008'` |
| `HW TW Segments/Tailwater Cells` | (N_segs,) \|S10 | Same, TW side |
| `Structure Variables` | (T, 4) | Total Flow, Weir Flow, Stage HW, Stage TW |
| `Weir Variables` | (T, 9) | Weir Flow, Sta US, Sta DS, Top Width, Max Depth, Avg Depth, Flow Area, Coef |
| `Geometric Info/Headwater Face Points` | (N,) int32 | Face-point indices along the HW side of the levee |
| `Geometric Info/Headwater Face Points Stations` | (N,) float32 | Stations for HW face points (ft from start) |
| `Geometric Info/Tailwater Face Points` | (N,) int32 | Face-point indices along the TW side |
| `Geometric Info/Tailwater Face Points Stations` | (N,) float32 | Stations for TW face points |

**Time arrays** — at the `SA 2D Area Conn/` level (parent of individual connection groups):

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Time` | (T,) float64 | Decimal days from simulation start (same reference as Summary Output) |
| `Time Date Stamp` | (T,) \|S19 | `b'01JAN2025 00:30:00'` — upper-case, no milliseconds |
| `Time Date Stamp (ms)` | (T,) \|S22 | `b'01JAN2025 00:30:00:000'` — with milliseconds |
| `Time Step` | (T,) float32 | Actual adaptive time step used, in seconds |

**Time of max — correct approach:** Do NOT use `nanargmax` on the time series (output-interval
resolution only, e.g. 30 min). Instead, look up the HW/TW cell indices in the Summary Output
of the connected 2D area, which has sub-step accuracy:
```
Summary Output/2D Flow Areas/{hw_area}/Maximum Water Surface[1, cell_idx]  # decimal days
```
Convert to datetime: `simulation_start + timedelta(days=decimal_days)`. Use
`read_sa2d_areas()` to get area names, `read_summary_max()` for the lookup, and
`read_simulation_start_time()` for the reference datetime — all in `hack_ras.results.reader`.

Station assignment: segment j spans `station[j]` to `station[j+1]`; its midpoint =
`(station[j] + station[j+1]) / 2`. Each unique cell's representative station (`Sa2dCell.station`) =
mean of midpoints across all segments where that cell appears. `Sa2dCell.station_start` =
minimum face-point station bounding those segments; `Sa2dCell.station_end` = maximum.
Use `list_sa2d_connections()` and `read_sa2d_connection()` from `hack_ras.results.reader`
to get a typed `Sa2dConnection` object with `hw_cells` / `tw_cells` lists sorted by station.

### SA/2D Connection Result Layouts — the two modes are NOT the same shape
The table above is the `Weir/Gate/Culverts` layout. A `Bridge Opening` connection writes a
different, flatter set of datasets under the **same** `SA 2D Area Conn/{name}/` parent, and
sharing a parent is the whole trap — the group is there, `list_sa2d_connections()` lists it,
and only the dataset read fails.

| | `Weir/Gate/Culverts` | `Bridge Opening` |
|---|---|---|
| HW/TW cell indices | `Headwater Cells` / `Tailwater Cells` | same |
| per-cell WSE | `HW TW Cells/Water Surface HW Cells` / `…TW Cells` | `Cell WS US` / `Cell WS DS` |
| weir stationing | `HW TW Segments/HW TW Station` + per-segment cell labels | **absent** |
| face points | `Geometric Info/…Face Points[ Stations]` | **absent** |
| flow | `Weir Variables`, `HW TW Segments/Flow` | `Face Flow` |
| `Structure Variables` | (T, 4) Total Flow, Weir Flow, Stage HW, Stage TW | (T, 6) Flow, Stage HW, Stage TW, Head loss, Drag Factor, Error HW |
| `Node Pointer` group attr | present | **absent** |

Consequences, all verified on LAX_River_2D p19 (24 weir-mode + 15 bridge-mode connections)
and on the `2D culvert bridge levee precip pipes` fixture (`Watershed Bridge`):

- `read_sa2d_connection()` reads **both**. The bridge results group has no stationing, so it
  takes stations from the geometry's `2DBR US Cells` / `2DBR DS Cells`, which station the very
  same cells (`station` = midpoint of the cell's station range), and the cells sort by station
  as in weir mode. Stations fall back to `nan` only when the geometry is absent. Note this is
  bridge-alignment stationing, not weir stationing — a bridge opening profile does not span
  its centerline (see **Stationing — arc length**), so `geometry/conn_interp.py`'s weir-crest
  helpers still do not apply to a bridge.
- `read_sa2d_areas()` reads **both**, by two different routes. Weir mode matches the
  `Node Pointer` group attribute against `SNN ID`; a bridge group has no `Node Pointer`, so it
  falls back to matching the connection NAME against the `Connection` field, after stripping the
  `'<area> '` prefix. Because `Connection` is an S16 field, two longer names can truncate to the
  same 16 characters — with no `Node Pointer` left to disambiguate, the fallback raises
  `ValueError` rather than guessing. Checked on all 42 connection groups across LAX_River_2D p19
  and the fixture: every group name unprefixes to an exact `Connection` match, including the two
  that are exactly 16 chars (`Brdg7_GRST_Overf`, `RR6_CPKC_Overflo`).
- Per-cell deck elevations and the raw station tables are in `Geometry/Structures/2DBR *` —
  see **2D Bridge Geometry** and `read_bridge_cells()`.
- **Flow and stage for either mode come from `read_structure_timeseries()`** (no breach is
  required; it was called `read_breach_timeseries` until 2026-09-09). Weir mode returns
  `Total Flow` / `Weir Flow` / `Total Culvert Flow` / `Stage HW` / `Stage TW` plus nine weir
  columns; bridge mode returns `Flow` / `Stage HW` / `Stage TW` / `Head loss` / `Drag Factor` /
  `Error HW` and no weir. For a culvert connection's flow split PER GROUP instead of summed,
  use `read_culvert_group_results()`.

### Pipe Network Geometry & Results
```
Geometry/Pipe Networks/{network}/Node Indices      # global→local mapping
Geometry/Pipe Networks/{network}/Conduit Indices
Geometry/Pipe Nodes/Attributes                     # structured array: Name, System Name
Geometry/Pipe Conduits/Attributes                  # structured array: Name, US Node, DS Node

Results/…/Unsteady Time Series/Pipe Networks/{network}/Cell Water Surface   (T, N_cells)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Cell Courant         (T, N_cells)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Face Flow            (T, N_faces)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Face Velocity        (T, N_faces)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Face Water Surface   (T, N_faces)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Depth         (T, N_nodes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Water Surface  (T, N_nodes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Top + Side Inlet Flow  (T, N_nodes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Pipe Flow DS   (T, N_pipes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Pipe Flow US   (T, N_pipes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Vel DS / Vel US  (T, N_pipes)
```

#### Along-conduit profiles — the RAS Mapper pipe profile plot

The `Pipes/*` datasets are **lumped per conduit** (one US and one DS value each).
The profile RAS Mapper draws along a conduit is at **face** resolution, and its
x-axis comes from geometry:

```
Geometry/Pipe Networks/{network}/Faces Conduit ID and Stations   # struct, (N_faces,)
    ConduitID  int32    global conduit index (see the index-space trap below)
    ConduitStation float32   distance along the conduit from the US node
    CellUS / CellDS int32
    Elevation  float32   conduit invert at that face
Geometry/Pipe Networks/{network}/Cells Node and Conduit IDs      # (N_cells, 2), -1 = n/a
```

Filter the face table on `ConduitID`, sort by `ConduitStation`, and use those row
positions as column indices into `Face Water Surface` / `Face Velocity` /
`Face Flow`. `read_conduit_profile()` does exactly this.

**INDEX-SPACE TRAP — the IDs inside `Geometry/Pipe Networks/{net}/` are GLOBAL.**
`ConduitID`, and the node/conduit IDs in `Cells Node and Conduit IDs`, index the
*global* `Geometry/Pipe Conduits/Attributes` and `Geometry/Pipe Nodes/Attributes`
tables — **not** the network-local results positions that `PipeNetwork.nodes` /
`PipeNetwork.conduit_index` hold. `Node Indices` / `Conduit Indices` are the
local→global map. Verified on Hillside p10 by comparing node-cell polygon
centroids against `Geometry/Pipe Nodes/Points`: global interpretation gave a
median offset of **1.16 ft**, local interpretation **1111 ft**. The two spaces are
easy to conflate because `Conduit Indices` is often the identity permutation
(it is on Hillside and on the test fixture) — but `Node Indices` is a genuine
permutation in both, which proves they are different spaces. Never index the
global attribute tables with a results position.

**Station direction: `ConduitStation` increases US → DS.** Verified on Hillside
p10 across all 215 conduits by comparing the sign of the face-invert trend against
the sign of `US Elevation - DS Elevation`: 195 agree, **0 disagree**, 4 too flat to
judge. The check includes **10 adverse-slope conduits** (DS invert above US), which
rules out a trivial "invert always falls" artifact.

**RAS Mapper plots the reverse.** Its profile x-axis runs downstream-on-the-left.
Checked against four GUI screenshots on plan p10 (`002yr pumping`): C44503, 26C45,
and the chained pair 26C57442 (US) + 26C46 (DS), where the GUI placed the DS
conduit first and the axis extent equalled the summed conduit lengths. End
velocities matched the reversed HDF order in every case (e.g. 26C57442: HDF
0.52 ft/s at the DS end / 2.28 at the US end, plotted left-to-right as 0.6 → 2.3).
Use `ConduitProfile.station_from_ds` to reproduce that axis. Caveat: all 215
Hillside conduit polylines are drawn US→DS, so this model cannot separate
"RAS Mapper always plots DS→US" from "RAS Mapper plots reverse-polyline-order".

**Face coverage is partial at the ends.** Faces sit at internal cell boundaries, so
the first is typically half a cell in from the US node and the last stops short of
the DS node (C44503: 69 faces spanning 29.9–2061.1 ft of a 2076.0 ft conduit).
Some conduits additionally carry a boundary face at station 0.0 or at the full
length, stored *out of order at the end of the array* — always sort by station,
never assume array order. Use `us_invert` / `ds_invert` to close a profile onto
its nodes.

**`Minimum *` is not a strict lower bound.** `Minimum Face Water Surface` sits
ABOVE the smallest value in `Unsteady Time Series` on dry / near-dry faces — by up
to 0.278 ft (5 of 93 faces) on the test fixture and 3.185 ft (58 of 2850 faces) on
Hillside p10, because the two are referenced to different dry-bed elevations.
`Maximum` has no such problem (0 violations on both models). `Minimum <= Maximum`
always holds. Treat `Minimum` as indicative only.

**EG and critical WS are not stored.** RAS Mapper derives the energy grade as
`WS + V²/2g` (`ConduitProfile.energy_grade`) and computes critical depth from Q
and the conduit section. Only WS is in the file; when the conduit is surcharged
that stored WS *is* the HGL.

### Pump Stations
```
Geometry/Pump Stations/Attributes            # struct, one row per station
Geometry/Pump Stations/Points                # (N, 2) station locations
Geometry/Pump Stations/Pump Groups/Attributes            # Pump Station ID -> group
Geometry/Pump Stations/Pump Groups/Pumps/Attributes      # Pump Group ID -> pump
Geometry/Pump Stations/Pump Groups/Efficiency Curves Info    # (G, 2) [start, count]
Geometry/Pump Stations/Pump Groups/Efficiency Curves Values  # (N, 2) head/flow pairs

Results/…/Unsteady Time Series/Pumping Stations/{station}/Structure Variables  (T, V)
```

Useful `Attributes` fields: `Name`, `Inlet Pipe Node`, `Outlet Pipe Node`,
`Inlet SA/2D`, `Outlet SA/2D`, `Highest Pump Line Elevation`, `Pump Groups`.
Pump rows carry `WS On` / `WS Off` trigger elevations.

**The whole group is ABSENT in a gravity-flow geometry** — `list_pump_stations()`
returns `[]`, which is normal, not an error.

**Never index `Structure Variables` positionally.** The column count and order
vary per station: on Hillside, `26thAve PumpSta` writes 11 columns and
`RockCr PumpSta` writes 5. Every column is named by the dataset's
`Variable_Unit` attribute — `(V, 2)` byte pairs of `(name, unit)`, e.g.
`(b'Flow', b'cfs')`, `(b'Stage HW', b'ft')`, `(b'Pump 1', b'cfs')`,
`(b'Pump 1', b'Pumps on')`. `read_pump_station` maps by that attribute.

**Per-pump results are PER GROUP, not per pump.** A group of three pumps writes
one flow column plus a `Pumps on` count running 0..3. Hillside's `26thAve
PumpSta` has 4 groups of 1 pump; `RockCr PumpSta` has 1 group of 3.

Pipe-node references are written as `'Base [J314]'` — network name, then node in
brackets. `read_pump_station` splits these into `inlet_network` / `inlet_node`;
a station tied to a 2D area instead leaves them `None` and fills `inlet_area`.

**The pump curve is PER PUMP, not per group.** `Efficiency Curves Values` is
`(N, 2)` — column 0 = static head ascending, column 1 = flow descending — and
`Efficiency Curves Info` gives each group's `[start, count]`, indexed by the
GLOBAL pump-group row (select a station's groups via `Pump Station ID`). A group
of three pumps sharing one curve delivers THREE TIMES the tabulated flow with all
three running: verified against results, where `group flow / curve(head)` equals
the `pumps_on` count exactly (1.00, 2.00, 3.00 with no scatter) on every group of
a real model. Summing curves without the multiplier understates a multi-pump
station threefold. `PumpCurve.group_capacity()` applies `n_pumps`;
`PumpCurve.capacity()` is one pump. Despite the name, these are the pump curves —
there is no separate head/flow dataset.

**Head can run outside the tabulated curve.** Observed on a real station: head
(`stage_tw - stage_hw`) reached 19.33 ft against a curve tabulated only to
18.17 ft. `capacity()` CLAMPS to the end of the curve rather than extrapolating —
an extrapolated pump curve is meaningless — and `in_range()` / the `n_clamped`
count from `pump_station_capacity()` report when that happened, so a silently
clamped capacity never passes for a real one.

**Compare capacity against INFLOW, not the station's own outflow.** RAS delivers
exactly `pumps_on x curve(head)`, so station outflow equals full capacity whenever
every pump runs and can never exceed it — testing outflow against capacity is
nearly a tautology. The meaningful question is whether the water ARRIVING exceeded
what the pumps could move, i.e. `NodeTimeSeries.flow_in + inlet_flow` at the
station's inlet node.

### Pipe node rim elevations — two sources, and `Depth` follows the override
`Geometry/Pipe Nodes/Attributes` carries `Invert Elevation`, `Depth`,
`Terrain Elevation`, `Terrain Elevation Override` (NaN where unset) and
`Node Type`. Two defensible rim elevations exist and they are NOT interchangeable:

* **terrain** — `Terrain Elevation`, sampled from the DEM.
* **override** — `Terrain Elevation Override` where the modeller set one, falling
  back to the terrain elevation elsewhere.

They agree at every node with no override, so on many geometries the choice looks
academic. Where one IS set they can differ substantially, and **the override is
what RAS itself uses**: `Invert Elevation + Depth` reproduces the override, not
the terrain elevation. Measured on a real model, one outfall node carried terrain
728.67 against an override of 719.00 — a 9.67 ft difference. Because an override
is typically LOWER than the DEM value, defaulting to terrain would silently
under-count rim exceedances. `read_node_rims(hdf, source='override'|'terrain')`
defaults to `'override'` for that reason and returns both plus the raw fields.

Node types (`Junction`, `Start`, `External`, `Closed`, `Culvert Opening`) are
returned unfiltered so callers filter afterwards; the reader does not decide what
counts as a node.

**Node maxima are not in Summary Output.** `Summary Output/Pipe Networks/{net}/
Maximum Water Surface` is per CELL, not per node — `read_node_max_wse` reduces the
`Nodes/Water Surface` time series instead, and returns the time index of each
node's peak alongside the value.

### Two geometries of one model can be DIFFERENT pipe networks
A model often carries paired geometries for the same system — commonly a
free-outfall variant and one where the outfall is replaced by a pump station.
The pumped variant typically **drops the outfall node and its conduit**, so the
two geometries have different node and conduit counts even though they describe
the same trunk system. Assume nothing about them lining up.

- **Compare pipe networks by NAME, never by row position.** Node and conduit
  ordering and counts are per-geometry.
- Conduits shared between the two usually keep an identical length, so a shared
  station axis over the shared reach is exact.
- **But the face count of a conduit at a TERMINAL node depends on whether a
  downstream conduit exists.** A conduit whose DS node continues downstream gets
  a boundary face that the same conduit does not get when its DS node is
  terminal. Measured on a real paired model: of two trunk routes, one matched
  face-for-face and the other differed in exactly one conduit — the last one into
  the terminal node, which reached ~60 ft further along its own length in the
  variant where the outfall continued. Plot each plan against its own station
  column, or trim to the common faces.
- `read_path_profile` raises rather than reading silently when a path's conduits
  are absent from the plan's geometry — which is what feeding it a path traced on
  the other geometry produces.

### Resolving pipe-network forks by flow is PLAN-DEPENDENT
Choosing a downstream branch by peak flow gives different answers in different
plans, because the flow split itself changes between events. Measured across
eight plans of one real model covering six fork nodes: **one fork flipped** its
preferred branch between events (the losing branch gained ~8 cfs while the winner
lost ~6), and a second came within 0.9 cfs of flipping.

So: **trace a `ConduitPath` ONCE and reuse it for every plan being compared.**
Re-tracing per plan can hand two plans different station axes with no error
raised — a silent corruption of any profile comparison.

`trace_path` raises `AmbiguousRoute` by default rather than guessing.
`peak_flow_resolver` is opt-in and takes the plan HDF explicitly, so the plan
dependence is visible at the call site, and it records each decision in
`ConduitPath.forks`. `via=[...]` pins a route from geometry alone with no results
involved — prefer it whenever the branch choice is already known.

Note that `trace_path` first discards branches that cannot reach the destination,
so most forks resolve themselves and only genuinely braided reaches ever raise.

### Computation Block (high-frequency solver diagnostics)
Path: `Results/Computation Block/`

Stores per-computational-time-step solver diagnostics (86 401 rows for a 1-second
time-step, 24-hour simulation). These are for debugging convergence, not for results output.

```
Results/Computation Block/2D Flow Areas/{area}/Inner Iteration Number   (86401, 1)
Results/Computation Block/2D Flow Areas/{area}/Outer Iteration Number   (86401, 1)
Results/Computation Block/2D Flow Areas/{area}/Outer Max Water Surface Correction  (86401, 1)
Results/Computation Block/2D Global/2D Iteration Error   (86401,)
Results/Computation Block/2D Global/2D Iterations        (86401, 3)  # [N_iter, area_ptr, cell_idx]
Results/Computation Block/Global/Time                    (86401,)    # decimal days
Results/Computation Block/Global/Time Date Stamp (ms)    (86401,)    # b'01JAN2025 00:00:01:000'
```

### Results/Summary (run-level metadata) — LOCATION MOVED IN RAS 7.0

**RAS 7.0 (April 2026) relocated these attributes** (verified live, GMF_DFA
2026-07-17): `Results/Summary` group attrs are now EMPTY and the run summary
lives at **`Results/Unsteady/Summary`** (plus run-identity attrs on
`Results/Unsteady` itself: Plan Title, Program Version, Short ID, Simulation
Time Window, Type of Run). Files written by 6.x keep the old location — any
reader must check both. The attribute names are unchanged:

| Attribute | Example | Notes |
|-----------|---------|-------|
| `Solution` | `'Unsteady Finished Successfully'` | Check first to confirm a run completed |
| `Computation Time Total` | `'00:05:45'` | Wall-clock runtime (HH:MM:SS) |
| `Maximum WSEL Error` | `0.0` | Max residual at end of run |
| `Run Time Window` | `'20MAY2026 14:52:34 to …'` | When RAS executed the plan |
| `Time Solution Went Unstable` | `nan` | NaN if stable throughout |
| `Computation Time DSS` / `Maximum number of cores` / `Time Stamp Solution Went Unstable` | | Also present at the 7.0 location |

Volume accounting totals are nested in `Results/Summary/Volume Accounting/` and its
sub-groups (`Volume Accounting 2D/{area}/`, `Volume Accounting Pipe Networks/{network}/`):
`Cum Inflow`, `Cum Outflow`, `Error`, `Error Percent`, `Vol Ending` — all float32, acre-ft.

## Results Package API (`hack_ras/results/`)

### `model.py` — dataclasses

| Class | Fields | Notes |
|-------|--------|-------|
| `PlanMetadata` | `geom_id: str`, `plan_title: str` | Parsed from `.p##` text sidecar |
| `AreaGeometry` | `cell_centers (N,2)`, `min_elevations (N,)`, `polygons list`, `plan_areas (N,)`, `boundary Polygon`, `cell_gdf GeoDataFrame` | Non-dummy cells only in `cell_gdf`; `polygons[i]` is `None` if the cell has fewer than 3 faces. `polygons` follows the 2DFA perimeter (see 2D Flow Area Geometry). `plan_areas` = `Cells Surface Area` — use it for `interpolate_cell_volume`, not `polygons[i].area` |
| `CellVolumeTable` | `info (N_cells,2) int32`, `values (total_pairs,2) float32`; property `top_elevations (N_cells,)` | `info[i] = [start, count]`; `values[:,0]` = elevation, `values[:,1]` = volume. `top_elevations` = each cell's `elev[-1]`, nan for an empty table — the highest EFFECTIVE ground elevation (terrain modifications included), GUI-confirmed; see **Cell min/max are EFFECTIVE ground** |
| `Sa2dCell` | `cell_idx: int`, `station: float`, `wse (T,) float64`, `station_start: float`, `station_end: float` | `station` = mean of segment midpoint stations (center); `station_start`/`station_end` = min/max face-point stations bounding the cell's segments; default `nan`. In BRIDGE mode these come from `2DBR US/DS Cells` instead — midpoint of the cell's station range |
| `BridgeCells` | `connection`, `footprint`, `us`, `ds` (RAS structured arrays); properties `cells`, `high_chord`, `low_chord`; method `stations('us'\|'ds')` | One `Bridge Opening` connection's slice of the flat `2DBR *` tables. `stations()` → `{cell: (start, center, end)}` |
| `CulvertGroupResults` | `name`, `timestamps (T,)`, `columns`, `values dict[str,(T,)]`; properties `flow`, `stage_hw`, `stage_tw` | ONE culvert group's time series. `name` is RAS's key (`'Culvert #1'`), byte-identical to the geometry side's `Name`, so it joins onto `read_culverts()` |
| `Sa2dConnection` | `name: str`, `timestamps (T,) str`, `hw_cells list[Sa2dCell]`, `tw_cells list[Sa2dCell]` | Both cell lists sorted by station ascending |
| `BreachState` | `connection`, `fired: bool`, `hdf_path_kind`, `center_station`, `breach_at`, `breach_at_days`, `bottom_width`, `bottom_elev`, `left_slope`, `right_slope`, `top_width`, `max_flow`, `max_velocity`, `max_flow_area`, `time_of_max_top_width`, `columns` | The widest state REACHED, not the plan's terminal geometry. `fired=False` = defined but never triggered. `top_width` is None unless a crest elevation was supplied |
| `ConnectionCenterline` | `name`, `points (N,2)`, `profile (M,2)`, `us_area`, `ds_area`, `mode`, `snn_id`, `parts` | HDF twin of the ASCII `Connection Line=` / `Conn Weir SE=` blocks |
| `PipeNode` | `name: str`, `system_name: str` | From `Geometry/Pipe Nodes/Attributes` |
| `PipeConduit` | `name: str`, `us_node: str`, `ds_node: str` | From `Geometry/Pipe Conduits/Attributes` |
| `PipeNetwork` | `name`, `nodes dict[str,int]`, `conduits dict[str,PipeConduit]`, `conduit_index dict[str,int]`, `upstream_of dict`, `downstream_of dict` | `nodes[name]` → results column index |
| `NodeTimeSeries` | `timestamps`, `depth`, `wse`, `inlet_flow`, `flow_in`, `flow_out` — all `(T,) float64` | `flow_in` = sum of `Pipe Flow DS` for conduits draining into node |
| `ConduitTimeSeries` | `timestamps`, `flow_us`, `flow_ds`, `vel_us`, `vel_ds` — all `(T,) float64` | US/DS ends of the conduit |
| `Pump` | `name`, `ws_on`, `ws_off` | One physical pump's trigger elevations |
| `PumpGroup` | `name`, `flow (T,)`, `pumps_on (T,)`, `pumps list[Pump]`, `n_pumps` | RAS reports flow and an on-count PER GROUP, not per pump |
| `PumpStation` | `name`, `timestamps`, `flow`, `stage_hw`, `stage_tw`, `groups`, `inlet_network`/`inlet_node`, `outlet_network`/`outlet_node`, `inlet_area`/`outlet_area`, `highest_pump_line_elev`; properties `n_pumps`, `pumps_on` | Node fields parsed from RAS's `'Base [J314]'` form; `None` when the station is tied to a 2D area instead |
| `PumpCurve` | `group`, `head (P,)`, `flow (P,)`, `n_pumps`; methods `capacity(head)`, `group_capacity(head)`, `in_range(head)` | Flow is PER PUMP; `capacity` clamps outside the tabulated range rather than extrapolating |
| `NodeRims` | `names`, `node_types`, `invert`, `depth`, `terrain`, `override`, `rim`, `source`; `has_override`, `as_dict()` | `rim` follows the selected source; `invert + depth` reproduces the OVERRIDE rim |
| `NodeMaxWse` | `network`, `names`, `wse (N,)`, `time_index (N,)`, `timestamps`; `as_dict()`, `time_of_max(node)` | Max over the run per node; Summary Output's equivalent is per CELL and cannot be used |
| `VolumeAccounting` | `kind`, `name`, `vol_starting`, `vol_ending`, `cum_inflow`, `cum_outflow`, `error`, `error_percent`, `precip_excess`, `precip_excess_depth`, `units` | RAS's own mass balance, stored as group ATTRIBUTES. `vol_ending` is END-of-run, not the peak |
| `ConduitPath` | `network`, `conduits`, `nodes`, `segments`, `bridges`, `forks`; properties `start`, `end` | ROUTE ONLY, no results — trace once and reuse across every plan compared |
| `PathProfile` | `path`, `when`, `station`, `invert`, `crown`, `wse`, `velocity`, `flow`, `conduit_of`, `node_at`, `total_length`; properties `depth`, `is_surcharged`, `surcharge_margin`, `energy_grade` | One continuous profile chained along a ConduitPath, station accumulated across conduits and bridged gaps |
| `ConduitProfile` | `station`, `invert`, `wse`, `velocity`, `flow`, `face_indices` — all `(F,)`; plus `us_node`/`ds_node`, `us_invert`/`ds_invert`, `length`, `rise`, `span`, `shape`, `si_units` | Along-conduit profile at FACE resolution — the RAS Mapper profile plot. Properties: `depth`, `crown`, `is_surcharged`, `energy_grade` (`wse + V²/2g`), `station_from_ds` (RAS Mapper x-axis). `station` ascends US→DS |

### `reader.py` — public functions

#### Discovery
| Function | Returns | Notes |
|----------|---------|-------|
| `list_areas(hdf_path)` | `list[str]` | Names of 2D flow areas; empty list if none |
| `list_sa2d_connections(hdf_path)` | `list[str]` | Names of SA 2D Area Conn groups; empty if none. Raw group names — an interior connection appears area-prefixed |
| `list_connections(hdf_path)` | `list[str]` | Every structure name in `Geometry/Structures`; works on a `.g##.hdf` too |
| `list_breach_connections(hdf_path)` | `list[str]` | Connections with breach output, area prefix stripped and de-duplicated; empty if no breach formed |
| `list_pipe_networks(hdf_path)` | `list[str]` | Names of pipe networks; empty if none |

#### Plan / geometry
| Function | Returns | Notes |
|----------|---------|-------|
| `read_plan_metadata(hdf_path)` | `PlanMetadata` | Parses `.p##` text sidecar; raises `FileNotFoundError` if missing |
| `read_area_geometry(hdf_path, area)` | `AreaGeometry` | Reads cell centres, perimeter-accurate cell polygons, `Cells Surface Area`, boundary, min elevation; excludes perimeter dummy cells from `cell_gdf`. Falls back to face-point-only polygons (with a warning) if the plan HDF lacks any of `_PERIM_POLY_KEYS` |
| `read_face_geometry(hdf_path, area)` | `FaceGeometry` | Per-face Manning's n (the value RAS conveys with), cell/face-point indices, unit normals, lengths, and the dual "diamond" polygons. Accepts a `.p##.hdf` or a `.g##.hdf`; raises `KeyError` on a pre-7.0 HDF with no face property datasets. There is deliberately **no** cell-centre Manning's n reader — see the mesh export section |
| `read_cell_volume_table(hdf_path, area)` | `CellVolumeTable` | Raw info + values arrays; use `interpolate_cell_volume` to query, or `.top_elevations` for each cell's highest effective ground elevation |
| `interpolate_cell_volume(table, cell_idx, wse, cell_plan_area)` | `float` | Returns 0.0 if dry; linearly extrapolates above table max using `cell_plan_area` |

#### WSE results
| Function | Returns | Notes |
|----------|---------|-------|
| `read_wse(hdf_path, area, wse_type)` | `np.ndarray (N,) float64` | `wse_type` = `"Maximum"`, `"Maximum from Time Series"`, or a timestamp string |
| `read_timestamps(hdf_path)` | `np.ndarray (T,) str` | All output-interval time stamps from the HDF |
| `read_simulation_start_time(hdf_path)` | `datetime` | Parses `Plan Data/Plan Information` attr `Simulation Start Time`; format `%d%b%Y %H:%M:%S` |
| `read_summary_max(hdf_path, area, cell_indices)` | `dict[int, tuple[float, float]]` | Returns `{cell_idx: (max_wse, time_days)}` where `time_days` is decimal days at sub-step accuracy |

#### SA 2D Area Conn
| Function | Returns | Notes |
|----------|---------|-------|
| `read_sa2d_connection(hdf_path, connection)` | `Sa2dConnection` | HW and TW cell WSE time series + stations; cells sorted by station. Reads BOTH mode layouts — a `Bridge Opening` connection has no stationing, so its cells carry `nan` stations and stay in HDF order (see **SA/2D Connection Result Layouts**) |
| `read_structure_timeseries(hdf_path, connection)` | `dict` | `timestamps`, `kind`, `structure`, `breaching`, `weir`. Finds both HDF layouts; column names come from `Variable_Unit`. See **Breach Results** for the two traps. **No breach required** — this is the route to ANY connection's flow/stage series. Renamed from `read_breach_timeseries` 2026-09-09; that name is gone |
| `read_culvert_group_results(hdf_path, connection)` | `dict[str, CulvertGroupResults]` | Per-culvert-group flow and stage, which `Structure Variables` reports only summed as `Total Culvert Flow`. Empty dict (not an error) for a levee or bridge |
| `read_breach_state(hdf_path, connection, crest_elev=None)` | `BreachState` | The breach the run actually opened; raises `KeyError` if the plan wrote no breach output |
| `read_plan_breach_data(hdf_path)` | `list[dict]` | `Plan Data/Breach Data`: `name`, `kind`, `bottom_width`, `side_slopes`. Mirrors `Breach Geom` fields 2/4/5 |
| `read_connection_centerline(hdf_path, connection)` | `ConnectionCenterline` | Centerline + profile from `Geometry/Structures`; use to cross-check an ASCII parse. Reads only the `Centerline Profile` slot — use `read_structure_profiles` for the other eight |
| `read_structure_profiles(hdf_path, connection)` | `dict[str, (N,2)]` | ALL nine `Table Info` profile slots, empty ones omitted: `Centerline`, `US/DS XS`, `US/DS BR`, `US/DS BR Weir` (deck high chord), `US/DS BR Lid` (low chord). Slots need not share a station range |
| `read_bridge_cells(hdf_path, connection)` | `BridgeCells` | A bridge's mesh footprint + per-cell chords + HW/TW stationing, from `2DBR *`. Works on a `.g##.hdf`. `KeyError` on a weir-mode connection |
| `read_sa2d_areas(hdf_path, connection)` | `tuple[str, str]` | `(hw_area, tw_area)` — looks up `US SA/2D` / `DS SA/2D` via `SNN ID == Node Pointer`, falling back to a `Connection`-name match for a `Bridge Opening` group, which has no `Node Pointer`. Raises `ValueError` if that name is ambiguous under S16 truncation |

#### 1D steady-flow cross-section results
| Function | Returns | Notes |
|----------|---------|-------|
| `read_steady_profile_wse(hdf_path)` | `SteadyProfileResults` | WSE only; the original narrow reader, still used by `export_xs_gis.py` |
| `read_steady_xs_results(hdf_path)` | `SteadyXsResults` | **Every** `(n_profiles, n_xs)` dataset under `Steady Profiles/Cross Sections` + its `Additional Variables` subgroup, keyed by HDF dataset name |

`SteadyXsResults` carries `profile_names`, `keys` (`(river, reach, station)` in
results order), and `values` (`name -> (n_profiles, n_xs)` float64, with RAS's
~3.4e38 undefined sentinel converted to `nan`).  Methods: `has(name)`,
`variable_names()`, `get(variable, river, reach, station, profile)`,
`mean_velocity(river, reach, station, profile)`, `reaches_of(river)`, and
`find_keys(river, station, reach=None)` — which **infers the reach** for a
river/station pair that carries no reach name (numeric station compare, so an
Excel `27962` matches `'27962'`; a trailing `*` on interpolated XS is handled).
**HEC-RAS permits the same station on two reaches of one river**, so more than
one hit is possible and means the pair is genuinely ambiguous — pass `reach` to
resolve it, and treat >1 hit as an error rather than taking the first.  River and
reach names are matched via `utils.names.normalize_name` (see below) because RAS
pads reach names in the file — `'Upper Reach  B'` carries two spaces, which a
hand-typed name must not have to reproduce.  A blank/whitespace-only `reach`
counts as not supplied.

Which variables exist is **strongly version-dependent**, so always `has()` first:
5.0.3 writes four Additional Variables (`Area Flow Total`,
`Area including Ineffective Total`, `Conveyance Total`, `Top Width Total`); 7.0
writes ~50, including `Velocity Total`.  `mean_velocity` encapsulates that split —
it uses `Velocity Total` when present and otherwise derives `Flow / Area Flow
Total`.  The two routes were verified to agree to 7 significant figures on the
same model run in both versions (SterpCreek p01 vs p02), and the derived route
reproduced a hand-built FEMA floodway data table exactly (14/14 rows, Starkweather
p03 `100-year`).

**`Cross Section Variables` (5.x) is unusable — never read it.** Its declared
shape `(n_profiles, 34, n_xs)` does not describe its layout: the data is actually
a per-XS record stream with a **40-float stride** (34 named variables + 6 zero
pad), so `n_profiles × 34 × n_xs` floats cannot even hold `n_profiles × 40 × n_xs`
— the tail is truncated.  Every column read out of it (WSEL, Q, `Vel Total`, …)
is misaligned and does not match the RAS GUI.  `read_steady_xs_results`'s shape
filter drops it automatically; `Water Surface`, `Flow`, and the Additional
Variables are standalone, correctly aligned datasets and are what to use instead.
This is the empirical basis for the alignment warning that was already on
`read_steady_profile_wse`.

Also note `Geometry Info/Cross Section Only` (`(n_xs,)`, `'River Reach Station'`
strings) — same order as `/Geometry/Cross Sections`, handy for sanity-checking
alignment.

#### Pipe networks
| Function | Returns | Notes |
|----------|---------|-------|
| `read_pipe_network(hdf_path, network)` | `PipeNetwork` | Geometry, index maps, adjacency dicts for one network |
| `read_node_timeseries(hdf_path, network, node_name)` | `NodeTimeSeries` | Depth, WSE, inlet flow, computed flow_in / flow_out |
| `read_conduit_timeseries(hdf_path, network, conduit_name)` | `ConduitTimeSeries` | Flow and velocity at US and DS ends |
| `list_pump_stations(hdf_path)` | `list[str]` | Empty list when the geometry has no pump stations (normal for gravity flow) |
| `read_pump_station(hdf_path, station)` | `PumpStation` | Total flow, HW/TW stage, per-group flow and on-count, pump trigger elevations, inlet/outlet connectivity. Columns mapped via the `Variable_Unit` attribute, never positionally |
| `pump_stations_for_node(hdf_path, node, network=None)` | `list[str]` | Which station serves a given manhole |
| `read_node_points(hdf_path)` | `dict[str, (x, y)]` | Pipe node coordinates |
| `trace_path(network, start, end, via=None, resolver=None)` | `ConduitPath` | Downstream route. Branches that cannot reach `end` are discarded first; a genuine fork raises `AmbiguousRoute` unless `via` or a `resolver` is given |
| `peak_flow_resolver(hdf_path, network)` | callable | Opt-in fork resolver preferring the greater peak \|flow\|. **PLAN-DEPENDENT** — see the empirical section above |
| `join_paths(paths, node_points)` | `ConduitPath` | Join runs across a physical break (open channel, detention pond); station later advances by the true node-to-node distance |
| `read_pump_curves(hdf_path, station)` | `dict[str, PumpCurve]` | Per-group head/flow curves. Flow is PER PUMP — see the Pump Stations section |
| `pump_station_capacity(curves, head)` | `(np.ndarray, int)` | Total capacity with every pump running, plus a count of heads clamped outside a curve. Compare against INFLOW, not station outflow |
| `read_node_rims(hdf_path, source='override')` | `NodeRims` | Rim elevations and node types. `'override'` (default, what RAS uses) or `'terrain'` |
| `read_node_max_wse(hdf_path, network)` | `NodeMaxWse` | Max WSE per node over the run, vectorised; `network` accepts a `PipeNetwork` or a name |
| `list_volume_accounting(hdf_path)` | `dict[str, list[str]]` | `{kind: [names]}`; empty when the plan has none |
| `read_volume_accounting(hdf_path, name, kind='2D')` | `VolumeAccounting` | RAS's mass-balance totals for one area / pipe network / reach |
| `read_path_profile(hdf_path, network, path, when='Maximum')` | `PathProfile` | Chain conduit profiles onto one station axis. Raises if a path conduit is absent from this plan's geometry |
| `read_conduit_profile(hdf_path, network, conduit_name, when='Maximum')` | `ConduitProfile` | Along-conduit profile at every face. `network` accepts a `PipeNetwork` **or** a bare network name. `when` = `'Maximum'`, `'Minimum'`, or a time-stamp string. Max/Min are PER-FACE ENVELOPES, not snapshots — pass a stamp for a physically consistent profile |

## Line-in-polygon measurement (`hack_ras/gis/clip.py`)

`PolygonProbe(polygon, tol=1.0).measure(line) -> LineInPolygon` — how much of a
line falls inside a polygon, **plus whether that number can be trusted**. Written
for FEMA floodway-data-table widths (length of a cross-section line inside the
mapped floodway) but has no HEC-RAS specifics.

The polygon must be in a **projected** CRS — lengths come out in its coordinate
units, so a geographic CRS silently yields degrees (use `RasProject.crs_wkt()` and
`gdf.to_crs()` first). Buffers are built once in the constructor, so measuring
many lines against one dissolved polygon is cheap.

**The coincident-boundary trap** — the reason this is a module and not a one-line
shapely call. Mapped polygons are routinely digitized *to* a cross section (a
floodway terminates at the last mapped section). When the polygon edge lands a
fraction of a foot off the line, the line stops *crossing* the polygon and starts
running *alongside* it; the intersection then returns only the overlap slivers,
and the answer swings by tens of feet for a sub-foot offset. **Nothing about the
intersection reveals this** — it is a single, clean, plausible segment. Observed
live (Starkweather PRT RS 6260.361, 2026-08-03): 53.34 ft measured where the true
width was 116.76 ft, from a ~1 ft offset; a second instance at RS 27962 failed the
other direction (1 ft the other way → 0.00 ft).

Detection: a line that CROSSES stays within `tol` of the boundary for only about
`4 * tol` (entering and leaving); one running ALONGSIDE racks up far more.
Measured on a real 21-section table at `tol=1.0` ft, clean crossings came in at
4.0–6.1 ft and the two bad sections at 24.7 / 119.1 ft, so `COINCIDENT_FACTOR`
(10) sits in a wide gap. `LineInPolygon` fields: `length` (the width to report),
`along_boundary`, `widened_length` (length against `polygon.buffer(tol)` — the
number to quote when reporting the problem), `clean_crossing` (`4*tol`), `tol`,
`coincident`, and the `empty` property. Consumer: `Scripts/Results_FWDT_Output`.

## GIS Profile Line Workflow (`hack_ras/gis/`)
`compute_profile_stations(line, area_data)` takes a shapely `LineString` and a dict of
`AreaGeometry` objects. It returns a sorted list of `ProfilePoint` objects:
- **`cell` points** — profile line intersects a cell; `cell_idx` is set
- **`boundary` points** — profile crosses the area perimeter; `cell_idx` is None
- **`endpoint` points** — profile start/end; `cell_idx` is None if outside all cells

`assign_wse(pts, area_wse, area_min_elev)` populates WSE on the list via direct lookup
(cell points) and linear interpolation (boundary/endpoint points).

For **volume extraction**, only `cell` points are used (boundary/endpoint points have no
cell index and therefore no volume table to query). Profile lines that extend beyond the
mesh boundary are handled gracefully — the out-of-mesh portion is silently ignored.

## 2D Mesh Face Manning's n Export (`hack_ras/gis/mesh.py`)

**Manning's n lives on faces, not cell centres.** HEC-RAS builds a hydraulic property
table per mesh *face*, and the n in it is the value the computations use — HEC,
["Creating Hydraulic Property Tables for 2D Flow Areas"](https://www.hec.usace.army.mil/confluence/rasdocs/r2dum/6.4/development-of-a-2d-or-combined-1d-2d-model/creating-hydraulic-property-tables-for-2d-flow-areas).
It is column 3 of `Faces Area Elevation Values`, which the HDF labels itself:
`attrs["Column"]` = `['Z', 'Area', 'Wetted Perimeter', "Manning's n"]`, units
`['ft', 'ft^2', 'ft', 's/m^(1/3)']`.

Other hydraulic properties — volume, surface area, minimum elevation — **are**
cell-centred and apply to the cell volume. Those are unaffected and still live on
`read_area_geometry` / `read_cell_volume_table`. Roughness is simply not one of them.

```python
from hack_ras.results.reader import read_face_geometry
from hack_ras.gis.mesh import face_mannings_gdf, export_face_mannings_shp

fg  = read_face_geometry(hdf_path, "RockCr")   # FaceGeometry: arrays + dual polygons
gdf = face_mannings_gdf(hdf_path)              # all areas, no CRS attached
export_face_mannings_shp(hdf_path, out_shp, areas=["RockCr"], crs=wkt)
```

Both accept a `.p##.hdf` or a `.g##.hdf` — the face datasets live in either, so two
geometries can be diffed without running plans.

### There is deliberately no cell-centre Manning's n export

`Cells Center Manning's n` exists in the HDF and RAS Mapper will draw it, but RAS does
not convey with it. A cell-centre reader and exporter were written on 2026-08-24 and
**removed the same day** — they made the wrong value the path of least resistance, and
had already produced one wrong conclusion.

The evidence, from `NKC_Hillside_Levee` g07 vs g09. Shrinking one n-override polygon
("Drainage Ditch", 78 → 73 vertices) left `Cells Center Manning's n` **bit-identical on
all 4107 cells** and every cell volume table unchanged, while changing **27 of 2361
`RockCr` faces** (0 in `Interior`): 0.035→0.080 ×196 sub-rows, 0.035→0.040 ×175,
0.035→0.060 ×92, 0.080→0.035 ×5. The computed Ditch WSE profile moved by up to **1.3 ft**.
RAS flagged the recompute in `Property Tables LC Hash` on both areas. A cell-centre export
showed nothing at all.

Of those 27 faces, 4 were streamwise (both cells on the profile chain, adjacent in station
order) and 14 were channel↔overbank laterals; the 4 streamwise ones carried the biggest n
jumps and track the profile response station for station.

If the raw cell-centre values are ever needed to demonstrate the discrepancy to a
reviewer, they need no library support:

```python
with h5py.File(hdf_path, "r") as hdf:
    n = hdf[f"Geometry/2D Flow Areas/{area}/Cells Center Manning's n"][:]
```

### One value per face

RAS indexes face n by elevation, so it *can* vary with stage when Vertical Variation in
Manning's n is switched on. It does not vary in practice here: measured 2026-08-24 over
every 2D model on disk — Hillside (all geometries plus backups), PCA, Pattison, and the
test fixture — **0 of 308,301 faces** vary. `read_face_geometry` takes the lowest-elevation
row and logs a warning if a face ever varies, rather than silently reducing a curve to one
number. Stage-varying face n is out of scope; the warning is the tripwire.

### Dual ("diamond") polygons, not polylines

A polyline layer at mesh scale is thousands of hairlines that cannot be filled or
symbolised by value. Each face is drawn as the ring
`[cell_L centre, face point A, cell_R centre, face point B]` — the face is the **diagonal**,
not an edge.

Alternating centre/face-point is load-bearing: ordering the ring `[cL, A, B, cR]` instead
makes a self-intersecting bowtie (measured on Hillside g07: 5331 of 5431 polygons invalid,
total area collapsing to 26% of the mesh).

The diamond is the face's own control volume, and the polygons tile the mesh. Measured on
Hillside g07: 0 invalid rings, 0 zero-area, **zero overlap** (union == sum of areas), and
total within **0.0043%** (`Interior`) / **0.0068%** (`RockCr`) of the summed
`Cells Surface Area`. The residual is perimeter faces that bend — the diagonal cuts across
the bend rather than following it. Not clipped; the effect is a rounding error and clipping
would cost more than it buys.

A Voronoi/Thiessen tessellation of face midpoints was **considered and rejected**: its
polygons straddle mesh cells, it needs clipping to the perimeter, and it encodes proximity
rather than the connectivity RAS actually solves on.

### Fields and layout

- `area`, `face_idx`, `mannings_n`, `cell_l`, `cell_r`, `face_len`, `norm_x`, `norm_y`,
  `geometry`. All ≤10 chars — the shapefile driver silently truncates longer names, and two
  fields truncating to the same stem collide. `mannings_n` is exactly at the limit; do not
  lengthen it.
- `cell_l` / `cell_r` are local cell indices, the same indexing `read_area_geometry` and the
  WSE/volume output use, so the layer joins to those.
- `norm_x` / `norm_y` come from `Faces NormalUnitVector and Length` and are the direction
  flow through the face travels — enough to sort faces into streamwise vs lateral in GIS
  against any reach direction, without the library needing to know about a centreline.
- **One file for all areas, with an `area` field.** Face indices are local to each area, so
  two areas both start at face 0; keeping them together makes that visible and lets one
  symbology cover the model.
- Perimeter faces name a **ghost cell** on one side rather than a negative index (RAS puts
  that ghost's centre at or near the face midpoint), so their diamond collapses onto the
  real half-cell. Correct, and it is why the tiling still closes.
- CRS defaults to `resolve.read_crs_wkt(dirname(hdf_path))`. If no projection file is found
  it logs a warning and writes no `.prj` — the coordinates are model coordinates regardless.

Verified end-to-end on Hillside `p47` (g07) and `p58` (g09): 7792 face polygons per plan
(5431 `Interior` + 2361 `RockCr`), CRS resolved to NAD83 / Missouri West via the `.rasmap`.
`RockCr` face n shifts between the two plans — 0.035 (151→126), 0.04 (575→584), 0.06
(973→978), 0.08 (591→602) — which is the g07/g09 edit, and is exactly what a cell-centre
layer could not see.

`Scripts/Geometry_Mesh_nvals/` drives it.

## Mapping 2D Results (`hack_ras/gis/wse_surface.py`)

Two rules, chosen with `mode`:

* **`horizontal`** — each cell flat at its own computed WSE. Reproduces RAS Mapper's
  `Horizontal` render mode **exactly** (measurements below), is the volume-faithful
  rule, and is the cheap one: no face, structure or neighbour is consulted.
* **`interpolated`** (the default) — the surface ramps between cell centres wherever a
  continuous water surface actually exists, never across a structure and never across a
  face that is not drowned from both sides. For a steep, coarse mesh where flat cells
  read as blocky.

Both draw the same triangles from the same cell fans; only the vertex values differ.
Read the module docstring for the full rationale; this section is the API and the traps.

```python
from hack_ras.gis.wse_surface import (
    area_bounds, build_wse_surface, export_wse_depth,
    export_wse_depth_per_source, vrt_sources, clone_source_vrt,
    difference_rasters, check_cell_volumes, same_mesh,
)

# RAS Mapper's own layout and RAS Mapper's own rule — one raster per terrain
# source at its native resolution, plus a .vrt. No `bounds`: each output covers
# its source's FULL extent, so every plan shares a grid per source.
res = export_wse_depth_per_source(plan_hdf, terrain_vrt, out_dir,
                                  wse_type="Maximum", mode="horizontal")

# One grid instead. Pass EVERY plan being compared — the union, so a bigger mesh
# is never clipped.
bounds = area_bounds([plan_a, plan_b])              # mesh perimeter, geometry-stable
same_mesh(plan_a, plan_b)                           # cell for cell? see below
res = export_wse_depth(plan_hdf, terrain_vrt, out_dir,
                       wse_type="Maximum", bounds=bounds, mode="interpolated")

df  = check_cell_volumes(plan_hdf, "RockCr",
                         res["wse"]["RockCr"], res["mapped_volumes"]["RockCr"])
difference_rasters(a_depth, b_depth, out, treat_dry_as_zero=True)   # depth change
difference_rasters(a_wse,   b_wse,   out, treat_dry_as_zero=False)  # WSE change
```

| Function | Returns | Notes |
|----------|---------|-------|
| `build_wse_surface(hdf, area, wse, dry_tol=0.01, mode="interpolated")` | `WseSurface` | Fans each wet cell from its centre to its own outline. `mode="horizontal"` gives every vertex the cell's own WSE and skips the face work entirely. `KeyError` on a pre-7.0 HDF — there is no approximate fallback, because an outline that does not follow the mesh boundary throws triangles outside the mesh |
| `barrier_faces(hdf, area)` | `set[int]` | Faces carrying a structure, from `Structures/Default Weir Connectivity` |
| `rasterize_surface(surface, transform, row_off, col_off, h, w, out=, cells_out=)` | `(values, cells)` | Barycentric scan-conversion, one triangle at a time. `transform` is the **output grid's**, so the offsets are output-grid pixels — not the terrain's |
| `export_wse_depth(...)` | dict | WSE + depth GeoTIFFs on **one** grid, tiled; `wse_path`, `depth_path`, `surfaces`, `wse`, `mapped_volumes`, `bounds`, `shape`, `mode`. Refuses a terrain whose CRS differs from the model's — `allow_crs_mismatch=True` overrides |
| `export_wse_depth_per_source(...)` | dict | One WSE + depth raster **per terrain source** at its native resolution, plus a `.vrt`. `wse_path`/`depth_path` are the `.vrt`s; `wse_paths`/`depth_paths` are `{source: tif}`; also `shapes`, and the rest as above. No `bounds` — each output is its source's full extent. `mode` defaults to `"horizontal"` |
| `vrt_sources(vrt)` | `list[str]` | A terrain `.vrt`'s sources, absolute, in RAS Mapper priority order (highest first) — from the sibling `.hdf`'s `Priority` attrs when present, else document order, warning if they disagree. **Read the priority trap below before using a `.vrt` as one raster** |
| `clone_source_vrt(terrain_vrt, out_vrt, {src: replacement})` | path | Clone a terrain `.vrt` with each source swapped for another raster on the same full grid. Keeps `SrcRect`/`DstRect` bit for bit; strips terrain stats and histograms |
| `area_bounds(hdf_paths, areas=None)` | `(x0,y0,x1,y1)` | Union of the mesh perimeter boxes. Takes one path or several — **pass every plan being compared**, or a differing mesh is silently clipped |
| `same_mesh(hdf_a, hdf_b, areas=None, atol=1e-6)` | `bool` | Do two plans compute on the same mesh, cell for cell? Compares area names, cell counts and five arrays within a tolerance |
| `difference_rasters(a, b, out, treat_dry_as_zero=True)` | path | `b - a` over the ground both cover. Needs a shared pixel lattice (`grid_overlap`), **not** equal extents — the output is the intersection, cropped. Raises on different resolutions, sub-pixel offsets, or disjoint inputs |
| `grid_overlap(open_a, open_b, tol_px=1e-6)` | `(Window, Window)` or `None` | The window of each raster covering the ground both describe. `None` when they cannot be compared without resampling. Takes **open datasets**, not paths |
| `check_cell_volumes(hdf, area, wse, mapped)` | `DataFrame` | Mapped volume per cell vs `interpolate_cell_volume` |

### A terrain `.vrt` read as one raster returns the WRONG source

GDAL composites a VRT in document order, so the **last** source wins where sources
overlap. RAS Mapper lists sources **highest priority first** and stitches them
internally (`Terrain/Stitch TIN *` in the terrain `.hdf`) rather than holing the lower
tiles, so GDAL's answer is backwards.

Measured on `NKC_Hillside_Levee` `Terrain/02_Surveyed_Channel` 2026-09-14. The `.hdf`
gives `c02_RAS_clipped` `Priority` 0 and `s04_StatePlane_clipped` `Priority` 1, and the
`.vrt` lists them in that order — so reading the `.vrt` hands back **s04 LiDAR
throughout the surveyed channel**, up to **2.5 ft** above the c02 surveyed bed over the
249,405 ft² the survey covers. `s04` is fully valid under the whole c02 footprint
(3,527,884 of 3,527,884 px), so nothing about the file hints at the problem.

RAS Mapper's own depth export does not have this flaw: it writes one depth tile per
source computed against that source, and holes the lower-priority tile. Verified on the
same model — over the 52,493 px where its c02 and s04 depth tiles overlap,
`c02 + depth_c02` equals `s04 + depth_s04` exactly, and its s04 tile is NoData over 79%
of the c02 footprint. **Use `export_wse_depth_per_source` on any multi-source terrain.**

**Nothing has to be configured for this, and it is not per-model.** `vrt_sources` resolves
priority itself: it reads the sibling terrain `.hdf`'s `Priority` attributes when they are
there, uses the `.vrt`'s document order when they are not, and **logs a warning if the two
disagree** rather than silently picking one. So a model that broke the convention would be
mapped correctly *and* say so.

Surveyed across every terrain `.vrt` in this workspace 2026-09-14 — **38 found, 36 with
`Priority`** — document order and `Priority` order agreed **36 of 36**: single-source, the
Hillside 2-source pair, Muncie's `TerrainWithChannel` (`ChannelOnly` then `muncie_clip`),
and the Baxter example's 5 tiles. The two without `Priority` are old HEC example projects
and one of them is genuinely multi-source (`BaldEagleCrkMulti2D/Terrain50`: `dtm_20ft`
then `baldeagledem`), which is why document order has to stay a working fallback.

### `horizontal` reproduces RAS Mapper exactly

`Depth (Max)` **and** `WSE (Max)` exported from RAS Mapper into
`Current_Model\<Short Identifier>\` for **all 12 plans that have exports**, against
`export_wse_depth_per_source(..., mode="horizontal")`, 2026-09-14. Both terrain sources,
every pixel, no tolerance and no resampling on either side:

| | Depth (Max) | WSE (Max) |
|---|---|---|
| px compared | 82,968,192 | 82,968,192 |
| mean `ours − RAS` | 0.00000 ft | 0.00000 ft |
| rms | 0.000000 ft | 0.000000 ft |
| **max abs** | **0.00000 ft** | **0.00000 ft** |

Per plan the px-both count runs 6.21M (`050year`) to 7.78M (`FC 500year breach L4`) and
every one of the 24 comparisons is 0.00000 ft max. **Identical, not merely close.**

`WSE (Max)` and `Depth (Max)` come out of RAS Mapper on **the same mask** — the wet px
count agrees exactly for all 12 plans — so RAS masks its WSE raster by depth as well, and
so does this. A WSE raster that runs past the water's edge is nobody's intent.

Two things account for the pixels RAS writes and this does not (30–38k per plan, ~0.5%):

* **`depth_tol`**, which **defaults to 0.0** and so matches RAS's extent. RAS writes any
  positive depth — its smallest on p09 is 0.000977 ft, float32 granularity rather than a
  threshold. Raising it to 0.01 drops 25,669 px on p09, **100% of them 0.0099 ft deep or
  less**: a display threshold, better applied in GIS where it costs no re-map.
* **The holing rule**, deliberately. A lower-priority source is holed wherever a
  higher-priority one has data; RAS leaves a partial fringe (4,826 px on p09) that its
  own `.vrt` then covers with the higher-priority tile anyway.

Do not build the hole by letting GDAL decimate the higher-priority mask into each tile —
a boundless `out_shape` read of the 1 ft c02 source into 3.28 ft tiles over-masked
30,891 px whose centres lie nowhere near c02, dropping 0.58% of the water. The mask is
held in memory and indexed by computed row/column instead.

### Matching grids: one lattice, not one extent

`grid_overlap` decides whether two rasters can be differenced, and it asks only the two
questions that matter: **same pixel size**, and **origins a whole number of pixels apart**.
Given both, the overlapping pixels correspond one for one and subtracting them invents
nothing. It returns a window into each — both rasters in full when they are the same grid,
so it subsumes an equality test — and `difference_rasters` writes the intersection.

Neither test is bit-exact, and that matters. RAS Mapper writes the Hillside s04 tile at
pixel 3.2808333333333586 ft and `export_wse_depth_per_source` at 3.2808333333333555 — the
same 1 m cell by different arithmetic. 3e-15 ft, accumulating to 3e-11 ft across the
10,888 rows, in a 3.28 ft pixel. A bit-exact guard refused it, so a RAS-Mapper-vs-hack_ras
difference could not be computed at all. `tol_px` sits five orders above that round-off
and six below the one whole pixel a real misalignment moves a corner.

**Extents need not match**, and requiring them was the second false negative. Two plans
mapped in separate runs on one terrain land on the same lattice but cover the union of
whatever meshes were in each run: on the test fixture, p02 (g02) and p07 (g05) come out
3101x1559 and 3101x1897, origins exactly 338 pixels apart. The grid comes from the
terrain, so a mesh difference moves the window, never the lattice.

What still raises: different resolutions (1 ft vs 3.28 ft — different samples of the same
ground, no window aligns them), a sub-pixel offset (every pixel straddles four of the
other), and disjoint inputs. All three need resampling, which would put invented values
into a depth map where nothing distinguishes them from real water.

**Across layouts, difference WSE and not Depth.** NoData means *dry* in a single-grid
export but *another tile covers this* in one tile of a per-source export, and
`treat_dry_as_zero` cannot tell them apart. Same layout on both sides and it cancels;
across layouts it does not. Measured 2026-09-15, the merged 1 ft export against the
`.vrt` run's c02 tile: as Depth, min -16.85 ft, mean -1.975 ft, 11.28M px "changed", all
of it the c02 tile declining to describe ground s04 describes; as WSE, **243,228 px,
0 changed, max 0.00 ft** — the two terrains agree exactly.

Going the other way, **RAS Mapper drops one pixel this module writes**, the same one in
all 12 plans: c02 row 3381 col 6551 (2771850.4, 1085707.3), where the c02 terrain is valid
at 745.69 — 7.4 ft below the LiDAR, so deep channel — and 12.7 ft submerged, and RAS's c02
depth tile is NoData. 10 of the surrounding 5x5 are written by RAS against 11 valid c02
terrain pixels. One pixel in 243,219, and this module's value is the defensible one; noted
so nobody re-investigates it.

### The interpolation rules (`mode="interpolated"`)

Triangles are fanned **per cell**, centre to outline, so no edge ever crosses a mesh
face. Cell centres carry `read_wse` unaltered. A face point takes the mean of the
WSE of the cells around it that are *communicating* with the cell being drawn; two
cells sharing a face are **not** communicating when the face carries a structure,
when either cell is dry, or when the face is not submerged from **both** sides.

**Test the lower WSE against the face minimum, not the higher.** Asking whether water
crosses the face at all is far too permissive on a steep coarse mesh. `NKC_Hillside_Levee`
p15 `RockCr` cell 933: WSE 836.39, cell min elevation 830.66, a downslope neighbour so
much lower that averaging put **813.65** at a shared face point — 22.7 ft below the cell's
own water level. The plane then sat below ground over all 206,255 pixels and a cell holding
131,000 ft³ mapped as bone dry. **476 of 1071 `RockCr` cells failed that way.** Water does
spill across such a face; the two pools still have separate surfaces. Switching to the
lower WSE moved `RockCr`'s per-cell median volume error from **−97.6% to −12.3%** and
tripled the split face point count.

A cell with no both-side-drowned face is isolated, every vertex takes its own WSE, and it
renders flat — horizontal mode, which is the volume-faithful answer. The interpolation
switches itself on only where a continuous water surface exists.

### Structure chains are area-local — scope them

`Default Weir Connectivity` stores face point indices in `RS/FP` as decimal strings, one
chain per structure and side, and **the indices are local to the 2D area named by the
structure's `US SA/2D` (HW) or `DS SA/2D` (TW)**. `SID` indexes `Structures/Attributes`
directly (verified: g03 SID 0–15, 16 rows). Matching every chain against every area
produced real false positives on Hillside — `Interior` 85 barrier faces instead of 56,
`RockCr` 83 instead of 68. The scoped counts are exactly right: `Interior` = 63 L1–L7 TW
chain points − 7 chains = 56; `RockCr` = the same 56 from the HW side plus 12 from the nine
internal C1–C9 chains = 68.

**Internal connections are the reason this module exists.** On `NKC_Hillside_Levee`,
`C1 Walker Road` … `C9 Clay Edwards` have `US SA/2D == DS SA/2D == RockCr` — road
embankments crossing the interior of a single mesh. Any interpolation that does not know
about them smears their head drop across cells whose equivalent side is 400–690 ft
(`RockCr` median cell 160,000 ft², max 471,000 ft²; `Interior` median 40,000 ft²).
`L1`–`L7` are `RockCr`↔`Interior` and need no handling — the areas are triangulated
separately and mosaicked.

### Accuracy — and why `check_cell_volumes` reads low

Measured on p15 at the maximum envelope, 2026-09-14, against terrain integrated directly
at 1 ft under a flat water surface at each cell's own WSE (150 cells per area):

| reference: terrain integrated | `Interior` | `RockCr` |
|---|---|---|
| this map, total | **+0.06%** | **−0.35%** |
| this map, per-cell median | −0.00% | −0.00% |
| RAS volume-elevation table, total | +2.39% | **+9.19%** |
| RAS table, per-cell median | +3.84% | +11.39% |

The map is right to a fraction of a percent. The 2–6% shortfall `check_cell_volumes`
reports is the **reference** being high: RAS's volume-elevation curve is piecewise linear
over ~47 tabulated points per cell, and linear interpolation of a convex V(Z) overestimates
between them. Not a defect — the solver routes on that table, so the table *is* the model's
storage — but a map drawn on terrain can never quite hold the volume the solver conserved.
Per-cell tails are wide (p1 −66%/−93%, p99 +62%) and are the interpolation working: where
the surface genuinely slopes, volume moves between neighbours and nets out.

### Measured against RAS Mapper's own render modes

RAS 7.0 offers four renderings, not three — `Sloping (Cell Corners)`,
`Sloping (Cell Corners + Face Centers)` with two independent sub-options
(`Use Depth-Weighted Faces ("Precip Mode")` and `Shallow Water reduces to
Horizontal`), and `Horizontal`. **RAS Mapper crashes computing a WSE raster with
Precip Mode on** (observed 2026-09-14, NKC_Hillside_Levee `FC 100year`); that mode
is therefore untested here.

The user exported `WSE (Max)` for the other three from p15 into
`Current_Model\FC 100year\`. They land on the **full terrain grid** (23212 x 35722,
1 ft, origin 2756852.5518 / 1106680.3564) and ours is an integer-pixel window of the
same grid, so every comparison below is pixel for pixel with no resampling.

Difference over pixels wet in both, `ours - RAS`, in ft:

| RAS Mapper mode | wet px | RAS only | ours only | mean | rms | min | max | \|d\|>1 ft |
|---|---|---|---|---|---|---|---|---|
| Sloping (Cell Corners) | 77.0M | 15.8M | 8.7M | −0.544 | 1.880 | −32.13 | +10.26 | 13.9% |
| Sloping (Corners + Face Centers) | 72.2M | 10.1M | 7.8M | −0.188 | 1.038 | −25.35 | +10.34 | 8.9% |
| … + Shallow reduces to Horizontal | 73.7M | 8.0M | 4.2M | −0.166 | 0.903 | −25.35 | +10.34 | 7.2% |
| Horizontal | 70.1M | 2.3M | 2.1M | **+0.016** | **0.301** | −9.58 | +7.39 | 2.1% |

Ours is 69.9M wet px. It tracks `Horizontal` closely and departs from the sloping
modes by tens of feet over millions of pixels — by design: the surface reduces to
horizontal wherever no face is drowned from both sides, which on a rain-on-grid
model is most cells.

#### Volume against the solver's own storage

Integrating `max(WSE − terrain, 0)` per cell and comparing to
`interpolate_cell_volume` — the solver's conserved storage, independent of any
rendering choice, so it favours nobody:

| | `Interior` total | `RockCr` total |
|---|---|---|
| RAS volume-elevation table | 60,983,399 ft³ | 47,838,238 ft³ |
| Sloping (Cell Corners) | +4.90% | **+117.22%** |
| Sloping (Corners + Face Centers) | +0.69% | +27.17% |
| … + Shallow reduces to Horizontal | +1.18% | +24.92% |
| Horizontal | −2.19% | −7.91% |
| **this module** | **−2.19%** | **−5.43%** |

Those were computed against the terrain `.vrt` read as a single raster, which is now known
to return LiDAR in the surveyed channel (see the priority trap above). Re-run per source on
2026-09-14, `mode="horizontal"` on p15 gives `Interior` **−2.19%** — unchanged, the channel
is not in it — and `RockCr` **−7.41%** rather than −7.91%: the half percent is the c02
surveyed bed finally being subtracted where it belongs.

`Sloping (Cell Corners)` maps **more than twice** the water RAS stored in `RockCr`,
92% of its cells off by more than 25%. The negative figures for `Horizontal` and this
module are the table's own high bias, measured above.

#### The excess is a function of cell relief

Not of the structures — the head drops across `C1`-`C9` are only 0.5-0.8 ft. It is
steep, coarse cells, where a tilted plane intersects the valley walls far above the
pond the cell actually holds. `RockCr` cells binned by relief
(`top_elevations − min_elevations`):

| `RockCr` cells | n | Sloping (Cell Corners) | this module |
|---|---|---|---|
| relief < 10 ft | 10 | −16.7% | −3.2% |
| relief 10-30 ft | 212 | **+55.2%** | −1.8% |
| relief > 30 ft | 849 | **+138.8%** | −6.6% |

Median `RockCr` cell relief is **40.4 ft** across cells 400-690 ft wide, so the
high-relief bin is the mesh. Worst single cell, 777: 77.8 ft of relief, 7.82 ft of
water at the deepest point, RAS table 18,698 ft³, `Sloping (Cell Corners)`
**1,520,698 ft³** — 81x over; this module 15,620 ft³. Over a 2800 ft window around it
the sloping mode maps 7.33M ft³ against our 1.59M and horizontal's 1.51M, filling the
ravine walls (see `Mapped_Results\steep_cell_zoom.png`).

This is the same face-averaging failure documented above for cell 933, seen from the
other side: averaging WSE across a face that is not drowned from both sides pushes the
plane too low on one side (drying a wet cell) and too high on the other (flooding a
hillside). The both-side-submergence test removes both.

`Mapped_Results\embankment_transects.png` shows the local structure behaviour: across
`C6 N Holmes`, `Sloping (Cell Corners)` rides about 1.5 ft above every other rendering
and above the HDF's own headwater cell WSE.

### Traps

- **Terrain modifications are not in the `.tif` or `.vrt`.** They live in the terrain HDF's
  `Modifications` group and RAS applies them on the fly. `02_Surveyed_Channel.hdf` carries
  exactly one, `Ditch_fix_RS_9580` — a `SetIfLower` channel, 3.5 ft top width, 1:1 slopes,
  along a **14.1 ft** polyline at (2766081, 1088562), profile 760.78→760.75, max reach 10 ft.
  About 100 ft², so the bare `.vrt` is fine *for this model*. Elsewhere, export the terrain
  from RAS Mapper with modifications applied. See **Cell min/max are EFFECTIVE ground**.
- **A live modification can be invisible in the RAS Mapper GUI.** The user could not find
  `Ditch_fix_RS_9580` anywhere in the layer tree, but RAS is computing with it: `RockCr`
  cell 1035 has `Cells Minimum Elevation` 760.746 against a bare `.vrt` minimum of 761.797
  in the same cell, landing within 0.006 ft of the modification's own profile bottom of
  760.752 (measured 2026-09-14, p15). So **the GUI is not a reliable way to rule terrain
  modifications out** — read the terrain HDF's `Modifications` group. Treated as a RAS
  Mapper display bug and left alone at the user's direction; the operational conclusion
  stands regardless of the cause.
- **Hillside is rain-on-grid, so the maximum envelope wets every cell** — minimum depth at a
  cell centre is 0.30 ft (`Interior`) / 0.72 ft (`RockCr`). A max-WSE map covering the whole
  mesh is correct, not a bug. Threshold for display in GIS; map a timestamp for a real wet edge.
- **`rasterize_surface`'s offsets are output-grid, not terrain-grid.** `export_wse_depth`
  passes the window transform, which already carries the terrain offset; adding it again
  silently writes zero pixels.
- **Concave cells spill a little.** Fanning assumes the centre sees the whole outline. The
  centre is inside its polygon on every mesh checked, which bounds it: summed fan area
  exceeds `Cells Surface Area` by +0.0015% (`Interior`) / +0.013% (`RockCr`), per-cell median
  0.0000%, 10 of 3686 cells over 0.1%, worst 3.2%. Hillside has 65 + 104 concave cells, so
  concavity is common and harmless. Not worth clipping triangles for.

### g01 and g03 are the same mesh

Cell centres, face points, cell and face minimum elevations and surface areas all agree
element for element to float round-off (max 4e-9 ft) between `EC gravity flow` (g01) and
`FC gravity flow` (g03) — same 2844 `Interior` + 1263 `RockCr` cells, same 16 structures.
They differ in infiltration (`InfiltrationSCS` vs `InfiltrationSCS_FutureConditions`), land
cover and flow. So an EC vs FC comparison needs no resampling and cell *i* is the same cell
in both.

### Writing rasters RAS Mapper can read

**Observed 2026-09-14, and only partly explained.** The fixture terrain was shrunk from
26.9 MB to 2.6 MB by clipping it with rasterio — a lossless sub-window, pixels verified
bit-identical against the original — and the result "turned into a mess in RAS Mapper".
The model was rebuilt with a RAS-Mapper-authored terrain instead, which is what the fixture
now carries.

Three things differed between the clipped raster and one RAS Mapper writes, and **which of
them mattered is not established**:

| | rasterio clip | RAS Mapper's own |
|---|---|---|
| compression | DEFLATE, **PREDICTOR=3**, zlevel 9 | DEFLATE, **no predictor** |
| overviews | none | **[2, 4, 8, 16]** |
| companion `Terrain.hdf` | **stale** — still indexed the pre-clip 6928x2632 extent | regenerated to match |

The stale `Terrain.hdf` is on its own sufficient to explain it: that file holds RAS's
per-tile `Mask` / `Min-Max` / `Perimeter` pyramids (308 tiles at level 0 for the old
extent), and RAS Mapper reads it rather than the `.tif` directly. Missing overviews would
also degrade the zoomed-out display. The floating-point predictor is standard GeoTIFF and
GDAL round-trips it exactly, but RAS Mapper does not use GDAL.

**The rule that follows regardless of the cause: do not hand RAS Mapper a terrain built by
anything other than RAS Mapper.** A `.tif` is only half of a RAS terrain; the `.hdf`
alongside it is an index that must be regenerated with it. Rewriting the raster and leaving
the `.hdf` is the trap, and it is silent — every pixel reads back correct from GDAL.

**SETTLED — `export_wse_depth`'s `predictor=3` output loads fine in RAS Mapper**
(user-tested 2026-09-14 on `p15_Maximum_Depth.tif`). No change was made, and the default
stays: dropping the predictor would have taken that raster from **50.3 MB to 100.0 MB**.
RAS Mapper reads ordinary rasters and shapefiles as happily as ArcGIS Pro or QGIS. Terrain
is the exception, because it is not an ordinary raster to RAS — it is imported through a
dedicated process that writes RAS's own `.tif` + `.hdf` + `.vrt` triple. **Hot-swapping any
part of that triple is what broke, not the compression.**

**And shrinking the terrain further is a dead end** (measured 2026-09-14, so nobody retries
it). RAS's 6.90 MB is not a deflate-level choice: the same values at `zlevel=1` with
overviews come to 4.71 MB and at `zlevel=9` to 3.01 MB, so RAS carries ~2 MB of its own
encoding overhead. And it re-encodes on import (a 26.9 MB source became 6.90 MB), so **no
compression choice on the input survives** — a lossless 1.86 MB re-compression was imported
through the GUI and came back essentially the same size. Below the terrain's own rounding
there is no entropy left either (next section). The only remaining lever is a coarser
rounding, and that is a modeling decision, not a storage one.

#### Terrain rounding is a user setting, and it is a modeling parameter

RAS Mapper's **New Terrain Layer** dialog has a `Rounding (Precision)` dropdown —
1/100, 1/1000, 1/8, 1/16, 1/32, 1/64, 1/128, 1/1024 — applied when RAS writes its own
raster. RAS **defaults to 1/32 ft**, but that is only a default and **not one of the
terrains on disk uses it** — every one was set deliberately. Measure, never assume.

Measured across the models on disk (2026-09-14), every value exactly on the stated grid:

| terrain | rounding | distinct values |
|---|---|---|
| test fixture `Terrain.Terrain` | **1/16 ft** | 1,636 |
| Hillside `02_Surveyed_Channel` — *both* its input rasters | **1/64 ft** | 1,788 / 19,618 |
| Hillside `01_LiDAR_rounding` | 1/128 ft | 39,226 |

One terrain rounds all of its inputs to the same grid, so the setting reads back consistently
across a multi-raster terrain.

**It is not just a storage choice — too coarse degrades the solution.** On the test fixture
the user tried **1/8 ft and got WSE errors**, and settled on 1/16; Hillside runs at 1/64. So
a terrain's precision is a parameter to record with the model, not an implementation detail,
and coarsening it to save disk is not free.

**RAS does not store the setting anywhere.** The terrain `.hdf` carries per-tile
`Maximum Cell-Value` / `Minimum Cell-Value` and nothing about rounding, so the only way to
recover it is to test the values against dyadic grids (`all(v / 2**-k) is integral`).

**Measure it with a FULL read.** A decimated read (`rasterio.read(out_shape=...)` smaller
than the raster) is served from the internal overviews, whose averaged pixels sit off-grid
and report a far finer quantum than the data really has — that mistake made the Hillside
LiDAR raster look like 1/2048 when it is 1/64.

Terrain precision propagates straight into cell minimum elevations and volume tables — see
**Cell min/max are EFFECTIVE ground** — which is why a re-import at a different rounding
changes results and needs a re-run.

### When to use `interpolated` rather than `horizontal`

**On a flat, finely-meshed area, use `horizontal` and do not interpolate.** That is the
conclusion from the Hillside `Interior` measurements, and it held against the module's own
author-bias, so state it plainly to anyone who asks. The choice used to be "this module vs
RAS Mapper"; since `mode="horizontal"` reproduces `Horizontal` exactly it is now just a
`mode`, and there is no longer any reason to leave the model for it.

Measured on p15, `Interior` (the leveed interior drainage area) against `RockCr`:

| | `Interior` | `RockCr` |
|---|---|---|
| median cell relief | **4.1 ft** (p25 2.9, p75 6.9) | 40.4 ft |
| median cell size | 200 ft | 400 ft |
| WSE step between hydraulically joined neighbours | **median 0.028 ft** (p90 0.48, p99 1.27) | median 0.566 ft (p90 3.63) |

The stair-step horizontal mode produces *is* that WSE step, so on `Interior` it is a third of
an inch and invisible. Over the 53.6M pixels wet in both, this module and `Horizontal` differ
by **mean +0.0010 ft, rms 0.095 ft**, with only 3.4% of pixels past 0.25 ft — below any
threshold a decision turns on.

And `Horizontal` is the **better** volume performer per cell there, which is not a surprise:
horizontal is what the volume-elevation curve assumes, so any interpolation moves volume
between cells and buys nothing where the surface is already flat.

| `Interior`, vs RAS's own storage | total | per-cell median | p5 / p95 | cells off >25% |
|---|---|---|---|---|
| `Horizontal` | −2.19% | −3.01% | −17.9% / −0.4% | **3.3%** |
| this module | −2.19% | −2.60% | −60.6% / +12.4% | 17.9% |

One more argument, specific to a **maximum** map: it is a per-cell envelope, not a surface
that ever existed. On `Interior`, **9.1%** of joined adjacent pairs peak more than 30 minutes
apart, 5.6% more than 2 hours, 2.9% more than 6 hours (max 11.98 h), so interpolating between
two cell maxima blends two different instants. `RockCr` is far more synchronous (0.4% past
30 min), so this argument cuts specifically toward horizontal on the flat interior.

**Where `interpolated` earns its place** is the opposite regime — steep, coarse cells, where
`Sloping (Cell Corners)` maps +117% of the water RAS stored and `Horizontal` is visibly
blocky. It delivers horizontal's accuracy without the cell-boundary steps.

**Watch the fringe either way.** The two renderings disagree on wet/dry over about 3% of the
`Interior` wet area (1.74M px wet in horizontal and dry here, 1.39M the other way). Irrelevant
for area-wide mapping; it can matter if the grid feeds structure-level work such as
first-floor estimation, where one building at the waterline could flip.

**Scope: 2D flow areas only.** There is no 1D cross-section mapping here — for 1D use the
steady XS readers and `geometry/xs_interp.py`.

### Comparing two plans — same mesh, or not

**Same mesh is the normal case and nothing special happens.** `same_mesh` compares area
names, cell counts, and `Cells Center Coordinate` / `Cells Minimum Elevation` /
`Cells Surface Area` / `FacePoints Coordinate` / `Faces Minimum Elevation` element for
element. Within a **tolerance, not exactly**: two geometries written out separately differ
in the last bits even when the mesh was never touched — Hillside g01 vs g03 disagree by at
most **4e-9 ft** across all five arrays and 4107 cells.

**Cross-mesh works because the grid comes from the terrain, not the mesh.** Two plans
exported against the same terrain with the same `bounds` land on an identical grid whatever
their meshes look like, so `difference_rasters` subtracts them exactly with nothing
resampled. Two things to get right:

- **Union the bounds across every plan** (`area_bounds([a, b])`). Taking one plan's bounds
  clips wherever the other reaches further. This was a latent bug in the runner even on a
  single mesh.
- **A per-cell comparison between the plans stops being meaningful** — they are not the
  same cells. `check_cell_volumes` is still valid per plan.

**"No mesh" is treated as "dry"** — the user's decision, 2026-09-14. Where one plan's mesh
covers ground the other's does not, `treat_dry_as_zero=True` reports the newly-wet depth as
a real increase, on the reasoning that a mesh is typically extended *because* water was seen
creeping into new ground. Near the water's edge part of any cross-mesh difference is the
change in cell size rather than a change in water; that is a caveat to state, not something
the code can separate. A companion raster flagging where the two footprints differ was
considered and dropped — **RAS Mapper already offers that**.

The differing-mesh test fixture is `Model.p07` (g05): `Interior` refined 59 to 196 cells over
the same footprint, `Watershed` refined *and extended* 59 to 386 cells over an extra
1,431,334 ft**2. One fixture, both cases.

### The terrain CRS guard

`export_wse_depth` refuses a terrain whose CRS differs from the model's. The reference is the
plan HDF's own root `Projection` attribute — no `.prj` hunting, no `.rasmap` parse.

The comparison must be **semantic** (`pyproj.CRS.equals`), never string equality: RAS writes
the ESRI dialect (`NAD_1983_StatePlane_Missouri_West_FIPS_2403`) and GDAL the EPSG-style name
(`NAD83 / Missouri West`) for the same system. Verified no false positive against Hillside's
own terrain `.vrt` and a RAS Mapper result export.

It exists because the failure is otherwise **silent**: a terrain in the wrong CRS usually
still overlaps the mesh, so the "does not overlap" check never fires and every depth is wrong
by the offset. The motivating case is real — NAD83(HARN) vs NAD83(2011) on the PCA model,
about 3.5 ft apart. `allow_crs_mismatch=True` overrides; a missing CRS on either side warns
and skips, since that is absent metadata rather than evidence of a mismatch.

There is deliberately **no** guard comparing the terrain path against the geometry's own
`Terrain Filename` attribute — pointing the tool at a different terrain (a RAS Mapper export
with modifications baked in, a clipped subset) is a legitimate workflow.

### Runner

`Scripts/Results_WSE_Depth_Maps/map_results.py` + a YAML config. Everything is tiled and
nothing holds the full grid.

Each plan writes into `out_dir\<its Short Identifier>\` — the same folder name RAS Mapper
uses for its own exports, so `Mapped_Results\500year\` sits beside `Current_Model\500year\`
and the two compare file for file. **Nothing is ever written into the RAS Mapper export
folders themselves.** A plan with no Short Identifier, or one holding a character a folder
name cannot, falls back to the plan id rather than failing the run.

**`map_results.py` only maps.** It carried a `compare:` key until 2026-09-15; that was
removed because it could only difference two plans mapped in the *same run*, which is
the one case that rarely matters. Differencing now lives in `diff_results.py` beside it
— see the next section.

| config key | default | |
|---|---|---|
| `mode` | `horizontal` | `horizontal` or `interpolated` |
| `per_source` | `true` | one raster per terrain source + a `.vrt`, vs one grid |
| `depth_tol` | `0.0` | matches RAS's wet extent exactly; raising it trims a display fringe |

Timing on the full Hillside mesh, 2026-09-14: **`per_source` 115 Mpx in ~5 s per plan**,
against ~22 s for 485 Mpx on the single 1 ft grid. The 1 m LiDAR is 11x fewer pixels when it
is not upsampled to sit beside the 1 ft channel survey, and none of that upsampling carried
information.

### Differencing (`Scripts/Results_WSE_Depth_Maps/diff_results.py`)

Separate tool, separate config. It reads **finished rasters** — no project, plan HDF or
terrain — so either side can be a RAS Mapper export or a `map_results.py` export, in any
combination, and a run costs seconds. Each comparison is `[sim1, sim2]` and writes
`sim2 - sim1`. Endpoints are `tag:folder` against named roots, or a path; a folder is
globbed for its Depth/WSE raster, which absorbs `Depth (Max).vrt` vs
`p09_Maximum_Depth.vrt` naming without configuration.

Tiles are paired **by grid**, not by name, because the two producers agree on no filename
and on all the geometry. Three things it will not do, each deliberate: resample, collapse
sources, or read a `.vrt` for statistics.

**Dry cells.** `treat_dry_as_zero=True` (depth) counts dry as zero depth, so a pixel wet in
only one run carries the depth that appeared (+) or went away (−). WSE cannot do that — a
dry pixel has no elevation — so WSE differences are written only where both are wet.

**The summary line applies source priority; the per-source lines do not.** Reading the
`.vrt` for statistics reported a ras-vs-mapped max of +0.00 ft while the c02 tile held a
+12.77 ft pixel (GDAL composites last-source-wins; RAS Mapper lists highest priority
first). But the moment a summary collapses several tiles it must drop pixels a
higher-priority source already covers, or ground is counted twice and partly from the
worse source: that is the difference between reporting a minimum of −15.74 ft and of
−0.94 ft. Its mean is area-weighted, since 1 ft and 3.28 ft tiles are not equal samples.

### Incremental depth is terrain-independent

Measured on Hillside 2026-09-15, mapping all 12 plans twice — once on
`02_Surveyed_Channel.vrt` per source, once on a single ArcGIS-merged 1 ft surface (LiDAR
bilinear-resampled to 1 ft, mosaicked under the c02 channel) — and differencing each way:

| | channel (1 ft) | LiDAR area |
|---|---|---|
| terrain, merged − source | rms 0.0045 ft, max 0.008 | rms 0.1435 ft, max 11.67, unbiased |
| **absolute** depth | rms 0.0045 ft | rms 0.0348 ft, p5/p95 ±0.03 |
| **incremental** depth | rms 0.0000 ft | rms 0.0024–0.0031 ft, p5=p50=p95=0.000 |

All nine incremental comparisons reported **identical** min/max/mean under both terrains,
because the terrain cancels: `(WSE₂ − T) − (WSE₁ − T) = WSE₂ − WSE₁`. So a depth-change
raster does not depend on which surface it was measured against, anywhere both scenarios
are wet; only wet/dry edges retain any sensitivity. Absolute depth does depend on it, but
modestly — and note the terrain differs by rms 0.14 ft while depth differs by 0.035 ft,
because bilinear smoothing departs most at steep breaks, which are mostly dry.

The `.vrt` was closer to RAS's volume in **24 of 24** plan/area checks, mean margin
0.169 pp — small, but a perfectly consistent sign is the resample cost appearing exactly
where theory predicts. Native resolution beats interpolated-up resolution, slightly.

**Two terrains are usually not differenceable.** The merged surface landed on the c02
lattice (integer 8447/17592 ft offset) but the `.vrt` composite sits 0.695/0.484 ft off
it — the fractional `DstRect` RAS Mapper gives c02 inside the `.vrt`. Sub-pixel, so no
resampling-free difference exists; compare two terrains by sampling, not by subtraction.

## Geometry XS GIS Shift (`hack_ras/geometry/shift.py`)

Translates cross-section GIS cut-line polylines along their own alignment while
preserving total arc length.  Driven by a pandas DataFrame (typically loaded from
an Excel file) with columns `River`, `Reach`, `River Station`, `Translation`.

```python
import pandas as pd
from hack_ras.geometry.parser import GeometryParser
from hack_ras.geometry.writer import GeometryWriter
from hack_ras.geometry.shift import build_translation_dict, shift_xs_cutlines

df    = pd.read_excel(r"path\to\XS to shift.xlsx")
trans = build_translation_dict(df)          # {(norm_river, norm_reach, norm_rs): float}
geom  = GeometryParser().parse_file(r"path\to\Model.g16")
out   = shift_xs_cutlines(geom, trans, new_title="Edited in Python")
GeometryWriter().write(out, r"path\to\Model.g17")
```

| Function | Purpose |
|----------|---------|
| `build_translation_dict(df)` | Validate + normalise DataFrame → lookup dict; warns on duplicates |
| `shift_xs_cutlines(geom, translations, new_title)` | Return new `GeometryFile` with shifted raw lines and updated in-memory cutlines |
| `shift_polyline(points, dist, tol)` | Core algorithm — slide a polyline by `dist` along itself |

`shift_xs_cutlines` streams through `raw_lines`, matches on normalised
`(river, reach, rs)` keys, and is non-destructive (the original `GeometryFile`
is not modified).  River/reach/RS matching is case- and whitespace-insensitive.
All other geometry content is passed through byte-for-byte.

The companion CLI script is at
`Scripts/Geometry_XS_GIS_Shift/shift_xs_gis.py` (YAML-configured,
`python shift_xs_gis.py config.yaml`).

Config keys: `prj_path`, `geom_in` (e.g. `g16`), `geom_out` (e.g. `g17`),
`excel_path`, `geom_name_out` (required — see HEC-RAS title uniqueness note
below).  The script resolves full geometry paths from the project file via
`resolve_id`, writes the new geometry, and appends `Geom File=<geom_out>` to
the `.prj` so HEC-RAS recognises the new file without a manual edit.

## Culvert Groups (`hack_ras/geometry/culverts.py`)

Read-only reader for culvert groups, ASCII or HDF, returning a flat
`List[CulvertGroup]`:

```python
from hack_ras.geometry.culverts import (
    read_culverts, read_culverts_ascii, read_culverts_hdf)

groups = read_culverts(geom_path)                    # ASCII (default)
groups = read_culverts(geom_path, prefer_hdf=True)   # HDF when a sibling exists
```

**ASCII is primary, not a fallback.** Two independent reasons:

1. The geometry HDF has **no solution-criteria column** (it is not under
   `Geometry/Structures` at all), so an HDF-only read silently loses that field.
   `read_culverts_hdf` reports `solution_criteria=None` rather than implying the
   default.
2. **RAS 5.0.3 wrote no culvert table into the geometry HDF whatsoever**, even
   for a geometry whose ASCII has culverts — `Culvert Groups` is a 7.0-era
   addition (`tests/data/Wisconsin Floodway/SterpCreek.g01` is 5.0.3 and has
   none; `g02`, the same model re-saved by 7.0, does). 4.1 and older wrote no
   HDF at all. `prefer_hdf=True` therefore falls through to the ASCII on
   `KeyError`.

What the HDF adds is RAS's own decoded enum labels and nothing else.

### Five keywords, one layout

`grep "^Culvert="` finds a MINORITY of culverts. RAS rewrites the keyword
automatically when a group's barrel count crosses 1:

| keyword | context | `form` |
|---|---|---|
| `Multiple Barrel Culv=` | 1D bridge/culvert node, >1 barrel | `multi_barrel` |
| `Culvert=` | 1D bridge/culvert node, single barrel | `single_barrel` |
| `Connection Culv=` | SA/2D connection | `connection` |
| `LW Culv=` | lateral structure | `lateral` |
| `IW Culv=` | inline structure | `inline` |

The four "grouped" forms share ONE comma-separated layout:

```
<kw>=shape,rise,span,length,n_top,ent_loss,exit_loss,chart,scale,
     US_inv,DS_inv,barrels,name,solution_criteria,us_distance[,use_momentum]
```

then a station line — two 8-char fixed-width fields per barrel (US sta, DS sta),
10 fields per line, wrapping only on field boundaries.

`Culvert=` is the lone outlier: no barrel count and no station line; its single
barrel's stations sit inline at positions 11 and 13, shifting name to 14,
solution criteria 15, us_distance 16, use_momentum 17.

The trailing `use_momentum` field is **optional** — absent in older files
(15-field grouped / 16-field single) — so field count is never assumed.

Sibling lines are keyword-prefixed (`Culvert `, `Conn Culv `, `LW Culv `,
`IW Culv `) and each has a defined meaning when absent:

| sibling | HDF column | absent means |
|---|---|---|
| `Bottom n` | `Mann Bottom` | equals `Mann Top` |
| `Bottom Depth` | `Depth for Bottom Mann` | 0.0 |
| `Depth Blocked` | `Depth Blocked` | 0.0 |

Barrel *name* rows are keyword-specific (`BC Culvert Barrel=` 1D,
`Conn Culvert Barrel=` 2D; lateral/inline have none).

Positions 1-13 and 15 were confirmed **uniquely** against RAS-written HDF over
220 culvert groups spanning four keywords; the two flag fields were confirmed by
a purpose-built RAS 7.0 GUI experiment (fixtures `SterpCreek.g03`,
`Model.g04`). The reader agrees with the HDF on 17 fields across 284
ASCII/HDF pairs.

### Enums — verified values only

`SOLUTION_CRITERIA`: `0` Computed Flow Control, `1` Inlet Control,
`2` Outlet Control (each set in the GUI and re-read).

`use_momentum`: **ASCII stores `-1` for true, the HDF stores `1`.** The GUI
checkbox reads "Use Momentum (GIS/2D only)" and the option is used **only in 2D**
— RAS still writes the flag on 1D culverts, where it is inert.

`SHAPE_NAMES` / `CHART_DESCS` / `SCALE_DESCS` are **deliberately partial**: only
codes actually observed in RAS output are mapped (4 shapes, 5 charts), and
anything else resolves to `None` rather than a guessed label. HEC-RAS defines
more shapes and ~60 FHWA charts. Integer codes are always exposed.

### Empirical RAS behavior the reader encodes

- **Circular span is never trustworthy.** A box→circular switch writes span
  BLANK, and any *later* save normalizes that blank to equal rise — so the field
  is either empty or RAS-derived, never independent. A circular span differing
  from rise has never been observed. `span` is reported as `None` whenever the
  shape is circular (on both paths — the HDF writer fills `Span = Rise`); use
  `rise` / the `diameter` property. Do not compare `span` for circular culverts.
- **Changing shape silently resets `Chart #`** (box chart 8 → circular chart 1),
  so chart/scale are partly RAS-derived and weak as QC signals.
- **Barrel name rows reorder unpredictably on save** when barrels share
  identical stations. The `barrels` count is authoritative; the reader never
  infers count from the name rows.
- HDF culvert attributes are **float32**, so an authored 106.2 reads back as
  106.19999694824219. The reader formats to 7 significant digits — float32's
  real precision — recovering the typed value without inventing any.

Not wired into `GeometryParser`: culverts hang off structures rather than cross
sections, so this is a self-contained linear pass over the raw lines. That keeps
the lossless roundtrip and `CrossSection` untouched, at the cost of a second
read of the file.

### Per-group culvert RESULTS — `read_culvert_group_results`

The geometry reader above is read-only and has no writer; for what a culvert
group actually *did*, `results.reader.read_culvert_group_results(hdf, conn)`
returns `{group_name: CulvertGroupResults}` from
`<connection group>/Culvert Groups/<name>` (`(T, 3)`: `Culvert Flow`,
`Stage HW`, `Stage TW`).

`Structure Variables` carries only the summed `Total Culvert Flow`, so the split
is the only way to see which barrel group carried the water — on LAX_River_2D
p19 `Culv2` the three groups peak at 44.3 / 97.1 / 96.9 cfs. The group keys are
**byte-identical** to the geometry side's
`Culvert Groups/Attributes['Name']` (`'Culvert #1'`, …), so the two join with no
name munging. Four LAX connections have more than one group (`Culv2` three;
`Culv14` / `Culv17` / `Culv22` two).

## SA/2D Connections (`hack_ras/geometry/blocks/connection.py`, `geometry/conn_interp.py`)

`GeometryParser` populates `GeometryFile.connections`, a `{name: Connection}` dict, from
the `Connection=` block family. One `Connection` carries:

| Field | Source line | Notes |
|-------|-------------|-------|
| `name` | `Connection=` | Padded to 16 chars in the file, stripped here — compares directly against an HDF `S16` name |
| `label_xy` | `Connection=` fields 2-3 | The anchor RAS draws the NAME at. **Not** a geometry point |
| `centerline` | `Connection Line= N` | N `(x, y)` in projected units; same 16-char layout as `XS GIS Cut Line=` |
| `weir_profile` | `Conn Weir SE= N` | N `(station, elevation)`; the spillway/levee crest RAS routes flow over. `N = 0` happens (bridge-mode connections) |
| `terrain_profile` | `Connection Centerline Profile= N` | Ground under the centerline. RAS writes 0 points on ~94% of connections |
| `up_sa` / `dn_sa` | `Connection Up/Dn SA=` | HW / TW area names; **equal** for a connection interior to one 2D area |
| `weir_coef`, `weir_width` | `Conn Weir Coef=`, `Conn Weir WD=` | |
| `routing_type` | `Conn Routing Type=` | Drives `.mode` / `.is_weir_mode` |
| `last_edited` | `Connection Last Edited Time=` | RAS stamps this on a GUI edit — useful for spotting an accidental change |

`Conn Routing Type=` maps to the connection Mode the GUI shows and the geometry HDF
stores in `Structures/Attributes['Mode']` (`CONN_ROUTING_MODES`): **1 = Weir/Gate/Culverts**,
**32 = Bridge Opening**. Verified by matching every ASCII connection to its HDF row
across Model_Hillside, Model_PCA, Model_LAX and the test fixtures (178 type-1,
32 type-32, no exceptions). Other RAS routing
types exist but have not been seen, so an unknown value gives `mode is None`.

### Stationing — arc length, and RAS enforces it
A connection's weir stationing **is** distance along its centerline, the opposite of the
cross-section rule. HEC-RAS holds the two lengths to within **1 ft or 0.5%, whichever is
smaller**, and refuses to run the model otherwise. See **Mapping RAS Stations to GIS
Coordinates** in `dev_rules.md` for the full survey and the bridge-mode exemption. Use
`conn_interp`: `station_to_xy`, `clip_polyline`, `clamp_station`, `elev_at`,
`terrain_elev_at`, `centerline_length`, `profile_length`, `station_drift`,
`station_tolerance`, `stationing_enforced`, `stationing_ok`, `check_stationing`
(raises `ConnectionStationMismatch`).

## Breach Definitions (`hack_ras/project/breach.py`)

`read_breach_definitions(plan_path)` returns a `list[BreachDefinition]` in file order.
A plan with no breach has **no `Breach` lines at all**, so the presence of `Breach Loc=`
is the enabled flag — there is no separate on/off key. A plan may define several
breaches; each repeats the whole group with its own method, geometry, trigger and
curves, so the progression / downcutting / widening tables are **per breach**, not global.

`Breach Loc=<river>,<reach>,<rs>,<is-connection>,<connection>` — first three fields blank
for an SA/2D connection breach, field 4 says which kind.

`Breach Geom=` field order, read off the RAS 7.0 GUI (not inferred), corroborated by an
independent fingerprint plan and by `Plan Data/Breach Data` in the HDF:

| # | Field | Notes |
|---|-------|-------|
| 1 | Center Station | Equals the HDF's `Centerline Breach` attribute |
| 2 | Bottom width | GUI label depends on method: "Final Bottom Width" (User Entered) / "Max Possible Bottom Width" (Simplified Physical) |
| 3 | Bottom elevation | "Final Bottom Elevation" / "Min Possible Bottom Elev" |
| 4 | Left side slope | run/rise; 0 = vertical walls |
| 5 | Right side slope | |
| 6 | Failure Mode | `True` = Piping, `False` = Overtopping |
| 7 | Piping Coefficient | |
| 8 | Initial Piping Elev | |
| 9 | Breach Formation Time (hrs) | **Blank under Simplified Physical** (GUI greys it out) |
| 10 | Breach Weir Coef | |

`Breach Method=`: **0 = User Entered Data, 1 = Simplified Physical** (GUI-confirmed).
The DLBreach tab was greyed out in every plan seen, so its code is unknown and
`method_name` returns None rather than guessing.

Per-breach extras: `Breach Progression= N` + N pairs, `Simplified Physical Breach
Downcutting/Widening= N` + N pairs (all 8-char `#Sta/Elev=` layout), `Starting Notch
Depth=` and `Initial Piping Diameter=` (Simplified Physical only; the latter only with
Piping), `Mass Wasting Options=`.

`Breach Start=F1..F8` → `BreachTrigger`. The GUI's three "Trigger Failure at" modes:
F1 True = **WS Elev**, F5 True = **WS Elev + Duration**, both False = **Set Time**.
F2 is dual-labelled — "Starting WS" under WS Elev, "Immediate Initiation WS" under
WS Elev + Duration; F3/F4 are the Set Time date/time; F6 Threshold WS, F7 Duration
Above Threshold (hrs), F8 Accumulate Duration (-1 = checked).

**Every field is stored even when the active mode or method ignores it.** A plan showing
a trigger WS may in fact fire at a set time, and a Simplified Physical breach retains a
stale piping coefficient. The `active_*` properties and `starting_ws` /
`immediate_initiation_ws` / `set_time` return a value only where it applies.

`BreachDefinition.top_width(crest_elev)` gives the widest the opening can get:
`bottom_width + (left+right slope) * max(crest - bottom_elev, 0)`. Confirmed against
RAS's own realised geometry on Model_Hillside Current_Model_extra p25 and p26, whose
asymmetric slopes (1/1.1 and 3.1/3.2) came back exactly, top widths matching to four
decimals. Pair it with `conn_interp.elev_at(conn, center_station)`.

## Breach Results (`hack_ras/results/reader.py`)

`read_breach_state(hdf_path, connection, crest_elev=None)` → `BreachState`: the breach
the run actually opened. `fired` separates "defined but never triggered" (a legitimate
outcome) from "no breach output". The realised geometry is the **widest state reached**,
not the plan's terminal geometry — a run ending mid-formation stops short, and the side
slopes grow with it (observed: a plan whose final slopes are 2/3 was still at 0.61/0.92
at the end of the simulation), so `top_width` peaks at whichever step maximises it,
which need not be the widest-bottom step. Without `crest_elev` the top width is None.

Also: `list_breach_connections(hdf_path)`, `read_plan_breach_data(hdf_path)` (reads
`Plan Data/Breach Data`; `Names` are kind-prefixed, `b'Connection|<name>'`), and
`read_connection_centerline(hdf_path, connection)` → `ConnectionCenterline` (the HDF
twin of the ASCII blocks, for cross-checks; agrees with the ASCII parse to the digit on
all 221 connections across Model_Hillside, Model_PCA, Model_LAX and the fixtures).

### Two traps in `Breaching Variables`
1. **Column count is not fixed.** An overtopping breach has 9 columns; a **piping**
   breach has 10, the extra one being `Top-Elevation` (which RAS then leaves
   unpopulated). Confirmed on four plans. Column names therefore come from the
   dataset's own `Variable_Unit` attribute, never a hardcoded list — the same fix
   also corrects `Structure Variables`, which has 5 columns on a culvert connection
   and 4 on a levee.
2. **Pre-breach rows are blanked inconsistently** — NaN under `SA 2D Area Conn`, **zero**
   under `2D Hyd Conn`. Test `Bottom-Width > 0`, not `isfinite`; an `isfinite` test
   reports a never-fired breach as fired and gives a minimum invert of 0.

### Where a connection's results live — the group is not always named after it
| Connection joins | Path | Group name |
|------------------|------|------------|
| two different areas (or area + storage area) | `Unsteady Time Series/SA 2D Area Conn/<conn>` | the connection name |
| one area, both sides the same mesh | `Unsteady Time Series/2D Flow Areas/<area>/2D Hyd Conn/<conn>` **and** `SA 2D Area Conn/<area> <conn>` | area-prefixed in the second |

For an interior connection RAS writes the data **twice**, and the two copies are
byte-identical (same shape, values, and `Breach at` / `Centerline Breach` attributes), so
either is authoritative. `read_structure_timeseries` tries all three spellings — a bare
lookup on the connection name silently misses every interior connection —
and `list_breach_connections` strips the area prefix so the two spellings collapse to one
entry.

## Current Work
*(Last updated: 2026-08-03, session 18)*
- `results/`, `gis/`, `project/`, and `geometry/shift` packages are complete and in production use
- `RasProject` is the stable top-level entry point; user scripts reference a `.prj` path
- `#Sta/Elev=`, `#XS Ineff=`, `#Mann=`, and `Bank Sta=` blocks are now parsed.
  `CrossSection.sta_elev`, `CrossSection.ineff`, `CrossSection.manning`, and
  `CrossSection.bank_stations` are all populated.
- `CrossSection._raw_line_start` and `CrossSection._raw_line_end` track each XS's
  position in `GeometryFile.raw_lines` (set by the parser; not semantic fields).
- `geometry/merge.py` provides `Transform`, `MergeConfig`, `merge_sta_elev()`,
  `merge_manning()`, `merge_ineff()`, `build_merged_cutline()`, and
  `write_merged_geometry()` for stitching cross-sections from two geometry files.
  `write_merged_geometry()` returns `None` — see session 5 below for why the old
  warnings-list return was removed.
- `geometry/xs_cutline_blend.py` provides `try_blend_extension()` for using the
  non-selected geometry's cut line to extend the output cut line rather than straight-line
  projection, when the two cut lines run in the same general alignment.
- `geometry/xs_interp.py` is the canonical tool for mapping RAS station values to GIS
  cut-line XY coordinates; use it for any future station-referenced feature export.
- XS Editor GUI app lives at `../RAS_xsedit/xsedit.py` (sibling to this repo); built with
  PySide6 + pyqtgraph; uses the `xsedit` conda environment. The `hack_ras` test suite
  uses the `Hillside_Levee` conda environment.
- `tests/test_geometry_merge.py` covers `write_merged_geometry` using Sterp Creek fixtures
  in the sibling `RAS_xsedit` repo. See `RAS_xsedit/tests/README.md` for how to add cases.
- **`SterpCreek.g03` regenerated and verified (2026-07-06)** — the user exported a new
  g03 from the GUI using a new 5-XS scenario (`RAS_xsedit/tests/xsedit_config.json`:
  gap segment, B-sourced IFAs, cut-line blend, an extension + truncations) and
  verified it XS-by-XS in HEC-RAS (`RAS_xsedit/tests/Test_notes.txt`).
  `_sterp_configs()` in `test_geometry_merge.py` now loads that JSON directly at
  runtime instead of hardcoding configs, so config/fixture/test can't drift apart
  silently.  Full suite green: **107 passed, 0 failed, 0 skipped**.
- Test coverage for `project/catalog.py` and `utils/` modules not yet written

### Session 21 changes (2026-08-24): 2D mesh Manning's n export — faces only

Request: get Manning's n out of a plan HDF as a polygon shapefile — RAS Mapper can only
show a resampled raster.

Started cell-centred, **ended face-only**. The cell-centre layer was built first, then
removed the same day once HEC's "Creating Hydraulic Property Tables for 2D Flow Areas"
and a live diff both confirmed RAS conveys with the *face* value, not the cell centre.
The removal is the substantive outcome; see the mesh export section for the evidence.

- `results/reader.py` — new `read_face_geometry(hdf_path, area)` returning `FaceGeometry`.
  `read_cell_mannings()` was added and then **removed** — it made the wrong value the
  path of least resistance. The dataset is still two h5py lines away if ever needed.
- `results/model.py` — new `FaceGeometry` dataclass.
- `gis/mesh.py` — new module. `face_mannings_gdf()` / `export_face_mannings_shp()`.
  `cell_mannings_gdf()` / `export_cell_mannings_shp()` were added and then removed, along
  with the `nface_min` / `nface_max` cell fields, whose only purpose was flagging
  centre-vs-face disagreement — moot once the centre layer was gone.
- `tests/test_face_mannings.py` — 29 tests on the `Model.p02.hdf` fixture, including the
  dual-polygon tiling property and the HDF's own `Column` attribute proving column 3 is n.
- `tests/test_area_geometry.py` — gained `TestCellGdfDropsPerimeterDummies`, relocated
  from the deleted `test_cell_mannings.py`. That was the only place asserting `cell_gdf`
  filters ghost cells, and the filter outlived the layer that used it.
- `Scripts/Mesh_cell_nvals/` renamed to `Scripts/Mesh_nvals/`, faces only, no `export:` key.
- **Cell polygons made perimeter-accurate** (same session, after the user asked whether
  RAS actually computes with the detailed perimeter). Validating the export against
  `Cells Surface Area` had shown boundary cells reconstructing 0.79–1.25×; the question
  was whether RAS's own computational area follows the face points or the perimeter.
  It follows the perimeter, decisively — see the two bullets under 2D Flow Area Geometry
  for the measurements. `read_area_geometry` now walks
  `Cells Face and Orientation Values` and splices in `Faces Perimeter Values`
  (`reader._perimeter_polygons`), with `_facepoint_polygons` kept as a pre-7.0 fallback.
  Interior cells are unchanged; only boundary cells move. Cost: 2.1× the polygon build
  (50 ms vs 24 ms for 2844 cells; ~1.7 s per 100k cells).
- `AreaGeometry.plan_areas` added (from `Cells Surface Area`) and made the recommended
  `cell_plan_area` source for `interpolate_cell_volume`;
  `Scripts/Profile_Lines_Volume/extract_volume.py` switched over from
  `polygons[i].area`. This shifts boundary-cell volumes slightly — accepted knowingly
  as strictly more correct.
- `tests/test_area_geometry.py` — 5 tests pinning polygon area against
  `Cells Surface Area` and the zero-symmetric-difference tiling. Mutation-checked:
  forcing the fallback path fails 3 of the 5. Baseline 459 → **477 passing**.

### Session 20 changes (2026-08-10): module re-exports, `reorder_plans`, `flows` subsystem

All driven by one real request on `Model_Pattison/03 assess abutments RS 7.0` ("insert
p05 and p06 after p02, and swap u01 and u02") and the questions it surfaced. Additive
throughout — no behavior change to existing functions.

- **`hack_ras/__init__.py` re-exports the `project/` op modules** (`plans`, `geoms`,
  `flows`, `sync`, `rasmap`, `health`) — see the Project Entry Point section for the
  rationale and the module-vs-function decision. `tests/test_package_exports.py` (+2)
  pins that the re-exports are the same module objects and that `__all__` matches.
- **`plans.reorder_plans(project, order)`** — renumber into a given order as p01..pN,
  with the complete-list requirement (see the bullet in Plan File Operations).
  ~30 lines delegating to `renumber_plans`; +7 tests in `test_plan_ops.py`.
- **`project/flows.py` — TODO item D, now CLOSED.** The third file-type subsystem,
  covering both flow kinds; see the Flow File Operations section above for the API
  and the design rules. All four open design decisions were confirmed with the user
  before building: prefix-required IDs (no bare numbers), cross-kind moves refused,
  `kinds=("unsteady","steady")` on `compact_flows` so one kind can be compacted
  without the other, and `reorder_flows` included. Plus `renumber_flows_in_rasmap`
  in `rasmap.py`. +55 tests.
- **`geoms.reorder_geoms(project, order)`** — added last, when the user noticed
  geometry was the only subsystem without a reorder (plans got one first, flows got
  one by decision 4, geoms was never in either request's scope). Identical in shape
  to `reorder_plans`; +7 tests. All three subsystems now expose the same
  insert-gap / compact / reorder trio.
- **`RasProject.rasmap_path` + `RasProject.rasmap` — TODO item C, now CLOSED.** The
  bound `.rasmap` accessor; see its section above. `RasMap` lives in
  `project/rasmap.py` next to the functions it wraps and takes `(path, base_name)`,
  so it has no `RasProject` dependency and creates no import cycle (`rasmap.py`
  imports nothing from the package). +7 tests. Baseline 362 -> **440**.

The Pattison job shaped all three. Its two asks were a plan REORDER (a 4-way
permutation, `{p05:p03, p06:p04, p03:p05, p04:p06}` — the motive for `reorder_plans`)
and a FLOW SWAP (u01 <-> u02), which had to be hand-rolled at the time and is now
just `flows.renumber_flows(project, {"u01": "u02", "u02": "u01"})`. Two things learned
doing it by hand that the module now encodes: a pure SWAP needs **no `.prj` edit** (the
set of `Unsteady File=` ids is unchanged — only the plan refs and the rasmap token
move), and this model's steady/unsteady mix put `.O05/.O06/.r05/.r06` on disk alongside
`.b05/.bco05` for plans that are now unsteady (stale artifacts from when they were
steady), which the `os.path.isfile` filter in `_family_names` handles correctly — the
session-19 design paying off. The user completed the job manually before `flows.py`
landed, so Pattison now reads p01/p02 steady on f01, p03/p04 1D-unsteady on u01
(Design_Flow_1D), p05/p06 2D on u02 (Design_Flow_2D), `.prj` sorted.

### Session 19 changes (2026-08-07): steady flow made first-class in project ops

Driven by a real request — delete steady plans p01/p02 from `Model_Pattison` (a mixed
steady/unsteady model). `delete_plans` would have run, but left `.O01/.O02/.r01/.r02`
orphaned. Root cause across three sites: the package assumed **flow == unsteady**.

- **Plan-keyed steady artifacts.** `_family_names` knew only `.b##`/`.bco##`/`.ic.o##`;
  `.O##`/`.r##` are now candidates too, filtered by `os.path.isfile` as before. NO plan-
  type detection — see the file-suffix table note for why disk beats a classifier.
  The load-bearing half is `_renamed_family_name` (stems gained `O` and `r`): it is
  shared with `renumber_plans`, so before this, renumbering a steady plan STRANDED its
  outputs at the old number, where a plan later renumbered into that slot silently
  inherited them. That was a wrong-results bug, not untidiness.
- **`Flow File=` is the .prj's steady key.** RAS registers steady flow as
  `Flow File=f##` and unsteady as `Unsteady File=u##`; it never writes `Steady File=`,
  which `sync.py` had been looking for since it was written. Consequence: steady
  entries fell through `_FILE_KEYS` as unrecognised lines, so `sync_prj` could not
  remove a `.prj` entry whose `.f##` was missing and always reported `steady: []`.
  Fixed in `_FILE_KEYS` and `sort_prj_entries`. `parser.py`/`model.py` gained
  `steady_file_ids` (+ `resolve_filenames`), and `health.py` now inventories steady
  flows and checks them for stale/orphan/unused/duplicate-title like every other kind.
  `delete_unused_flow` picks the key via the new `_prj_flow_key(flow_id)` helper.
- **The naming trap** (user-explained, worth keeping): `Flow File=` means different
  things in different files. In a `.p##` it is that plan's flow reference and holds
  either `f##` or `u##`. In the `.prj` it means specifically STEADY. The generic word
  "Flow" is historical — the first HEC-RAS had no unsteady computations. That
  ambiguity is very likely what led someone to invent the tidier-looking but
  nonexistent `Steady File=`.
- **Behavior changes to be aware of**: `sync_prj` now actually removes dangling
  `Flow File=` entries it used to preserve (it still never deletes files, and its test
  remains "is the file on disk?", NOT "does a plan use it?" — an unused-but-present
  flow file is legal and survives); `sort_prj_entries` reorders them by default; and
  `health` reports steady problems it previously ignored.
- **Empirical, from validating the fix against a hand-built target** (Model_Pattison
  00/01/02): a renamed `.p##.hdf` keeps STALE INTERNAL PROVENANCE. Its
  `Plan Data/Plan Information` attrs `Plan Filename` and `Geometry Filename` still name
  the pre-renumber files (p05.hdf renamed to p03.hdf still says `Pattison_Bridge.p05` /
  `.g05`). hack_ras cannot fix this without editing a binary, which it must not do; RAS
  clears it by regenerating the HDF on the next compute. Harmless for RAS Mapper (the
  .rasmap references are correct and were verified identical to the hand-built target),
  but do not trust a renumbered results HDF's embedded provenance until it is re-run.
  Note `strings` does NOT surface these — they live in HDF object headers; read them
  with h5py.
- New `tests/test_steady_flow.py` (17 tests) uses the real `Wisconsin Floodway`
  (SterpCreek) steady fixture on a temp copy for the artifact/delete/renumber cases,
  small synthetic projects for parser/sync/health. Verified non-vacuous: 15 of the 17
  fail against the pre-fix code. The 2 that pass either way are deliberate guards that
  the delete does NOT over-reach. Full suite: **362 passed** (345 baseline + 17).

### Session 18 changes (2026-08-03): steady XS results reader + FWDT_Output script

Driven by a new consumer: `Scripts/FWDT_Output/fwdt_output.py`, which fills a FEMA
floodway data table.  `read_steady_profile_wse` only exposed WSE, so
`read_steady_xs_results` was added (see the "1D steady-flow cross-section results"
section above) — it loads every `(n_profiles, n_xs)` dataset under
`Steady Profiles/Cross Sections` and `Additional Variables`, which makes it
version-proof by construction (5.0.3 → 4 additional variables, 7.0 → ~50) rather
than naming datasets that may not exist.  New `SteadyXsResults` dataclass with
`has` / `get` / `mean_velocity` / `find_keys` (the reach-inference helper).

The old "`Cross Section Variables` WSEL is misaligned" warning was tracked to its
actual cause this session: the block is a per-XS record stream with a **40-float
stride** (34 named vars + 6 pad) behind a declared `(n_prof, 34, n_xs)` shape, so
it is not merely permuted — it is truncated and unrecoverable.  Documented above;
the new reader's shape filter excludes it. Velocity therefore comes from
`Velocity Total` (6.0+) or `Flow / Area Flow Total` (5.x); the two agree to 7
significant figures on SterpCreek p01 (5.0.3) vs p02 (7.0).

`tests/test_steady_xs_results.py` (23 tests) — both real SterpCreek fixtures for
the two geometry layouts and the cross-version velocity check, a synthetic file
for the sentinel→nan masking and the shape filter, and a synthetic
same-station-on-two-reaches file (no real fixture has one) for the ambiguity and
loose reach-name matching.  Baseline 297 → **320**.

**Same session, second pass — reach disambiguation, boundary QC, and three
library extractions.** HEC-RAS permits the same station on two reaches of one
river, so `find_keys` gained an optional `reach` (plus `reaches_of`) and the script
now leaves an ambiguous row BLANK instead of taking the first hit. Then a review
of "what in the script belongs in the library" moved three things out:

1. **`resolve.read_crs_wkt(folder, specified=None)`** + `RasProject.crs_wkt()` —
   `find_crs_prj` returned only the *path*, so all THREE scripts
   (`FWDT_Output`, `XS_GIS_Export`, `Mesh_Health`) hand-opened and read it. The
   `try/except CrsProjectionFileNotFound` deliberately stayed in each script: the
   policies genuinely differ (FWDT aborts — widths would be in degrees;
   `export_xs_gis` warns and writes shapefiles with no CRS; `snap_cell_centers`
   warns and treats coordinates as already-model).
2. **`gis/clip.py`** — see the "Line-in-polygon measurement" section above. The
   `xs_interp.py` precedent applies: subtle geometry belongs in the library, not
   re-derived per script.
3. Nothing moved for plan resolution — the script was simply switched to
   `RasProject.plan_hdfs([plan_id])`, which is already `.prj`-authoritative and
   raises `PlanHdfNotFound`, replacing a hand-rolled
   `resolve_id` + `"…" + ".hdf"` + `os.path.exists`. (`export_xs_gis` legitimately
   still uses `resolve_id` — it needs the plan TEXT file to read `Geom File=`.)

Deliberately left in the script, per the session-14 precedent that pure
filesystem/format plumbing is not the library's charter: the Excel column
mapping / backup / row iteration, and `watercourse_map` (a FEMA DFIRM
attribute-naming bridge — if a second FEMA script appears it becomes a `fema`
module, not `hack_ras` core). Baseline 320 → **345** (+10 `tests/test_gis_clip.py`,
+5 `read_crs_wkt`/`crs_wkt` in `tests/test_find_crs_prj.py`).

Validation of the consumer: the script reproduced the user's hand-built table for
Starkweather p03 `100-year` exactly on all three HDF columns (area, velocity, WSE)
for 14/14 rows, and the shapefile-derived mapped width to within 0.12 ft on 13/14
(the 14th, RS 31432, was hand-entered by the user because that HEC-RAS
cross-section is not georeferenced).

### Session 15 changes (2026-07-27): Storage Area 2D Points block + Mesh_Health cell-seed snapper

First 2D-mesh (non-XS) geometry block parsed. `blocks/storage_area_2d.py`
reads `Storage Area 2D Points= N` — a storage area's 2D **cell-seed points**
(cell centers) in the SAME 16-char fixed-width layout as `XS GIS Cut Line=`
(2 XY pairs / 64-char line, field-boundary wrap). `parse_2d_points(lines, idx)`
returns `(points, consumed)`; N=0 (a 1D storage area) is header-only.
`format_2d_points_lines(points)` reuses the shared `_fmt(v,16)` formatter, so
rewriting an unchanged block is byte-identical (verified on GMF_DFA.g01's
11,970-point block) — an edit that moves a few seeds diffs only those lines.
The parser now dispatches `Storage Area=` (name) and `Storage Area 2D Points=`
into `GeometryFile.storage_areas_2d: List[StorageArea2D]` (name, points, and
`_header_line`/`_data_start`/`_data_end` raw-line span). XS parsing and the
lossless roundtrip are untouched (these lines previously fell through). New
`tests/test_storage_area_2d.py` (6 tests) uses the two-area
`2D culvert bridge levee precip pipes/Model.g02` (Interior=32, Watershed=29)
and the zero-point `Baxter/Baxter.g02`. Baseline 241 → **247**.

Consumer: `Scripts/Mesh_Health/snap_cell_centers.py` (+ `config.yaml`) —
YAML-driven tool that snaps hand-digitized 2D cell seeds in the vicinity of a
user shapefile (polygon/polyline/point) onto the lattice inferred from the
surrounding seeds. Lattice = per-axis modal spacing (auto or configured) +
phase from a wrap-safe **median** of `coord mod spacing` over a reference ring
(median, not mean — one refined-zone outlier otherwise biased the phase;
learned empirically: 61/62 ref cells were on the 200-ft lattice, the median
nailed 7.107/25.850 while the mean gave 7.006). Seeds already within 0.01 ft of
a node are left byte-untouched; a move beyond `max_move_ft` (default 0.75·spacing)
or a collision (two seeds → one node, or onto an occupied node) leaves that seed
in place with a warning. Output: new geom + `Geom File=` appended to .prj
(default, requires new `Geom Title`), or in-place overwrite with a one-time
`.bak`. Verified on a copy of Model_PCA/02_GMF_DFA_v7.0 g01 (Mesh_Health.shp):
28 in-region seeds → 20 moved (max 33.98 ft) onto the exact 200-ft lattice, 8
already aligned, 0 collisions, all out-of-region seeds byte-identical. User
must re-open the geometry in HEC-RAS to regenerate the computed mesh (.g0x.hdf).
The tool is intended to expand (more 2D mesh-health operations).

### Session 17 changes (2026-07-28): delete_plan cleans the .rasmap; sort_rasmap_layers

Driven by a real GMF_DFA job: the user deleted 14 plans (16-17, 21-26, 30-35)
and renumbered the survivors to a contiguous p01-p21. Some renumber TARGETS
(p16, p17, p21) were also just-deleted NUMBERS, so RAS Mapper's
"remove missing layers" left duplicate/garbled `<Plans>` and `<Results>` layers
— the exact number-reuse footgun the old docs only warned about. Fixed at the
source: `delete_plan` now removes the plan's `.rasmap` layers so a freed number
is truly vacant before any renumber can adopt a zombie.

- `project/rasmap.py` gained `remove_plans_from_rasmap(rasmap_path, base, pids)`
  (splices the RASPlan `<Plans>` block and the RASResults `<Results>` subtree via
  balanced `<Layer>`/`</Layer>` depth matching; keyed by Type + the exact
  `Base.p##` / `Base.p##.hdf` filename token so nested "Plan" sub-layers and other
  sections are untouched; removes all matches so an already-self-healed duplicate is
  cleaned too), `remove_flows_from_rasmap(rasmap_path, base, flow_ids)` (same for
  RASEventConditions layers in `<EventConditions>`, keyed on `Base.u##.hdf`), and
  `sort_rasmap_layers(rasmap_path, base, sections)` (numeric re-sort of the
  plan-keyed layers into the positions their kind already occupies, mirroring
  `sort_prj_entries`; leaves CalculatedLayer siblings and `<EventConditions>` put).
  All preserve encoding + CRLF and touch nothing else.
- `delete_plan` gained `clean_rasmap=True` (default on) and a `rasmap_removed`
  report field `{plans, results, event_conditions}`. It removes the plan's
  RASPlan/RASResults layers, and — when `delete_unused_flow` removes an orphaned
  flow file — that flow's RASEventConditions layer too. `renumber_plans` is
  unchanged (still token-remap only — with delete cleaning up first, no zombie
  remains for it to collide with).
- Tests: +11 (6 in `test_rasmap.py` for the three new fns on a RAS-shaped synthetic
  rasmap incl. EventConditions + a nested Event-Conditions sub-layer; delete cleans
  rasmap / leave-alone / delete-then-reuse-no-zombie / unused-flow-cleans-EC in
  `test_plan_ops.py`; rasmap assertions in `test_plan_ops_fixture.py`). Baseline
  249 -> **260**.
- Follow-ups (same session, after a live-open surprise): opening RAS Mapper on the
  finished GMF_DFA showed the Results tree disordered at the end. Root cause (NOT
  `.rasmap.backup` — that was just the prior save-state): three computed plans
  (Increment02 + Increment02/03 Breach) had result HDFs but NO RASResults layer in
  the original rasmap, so `sort_rasmap_layers` (which only reorders EXISTING layers)
  couldn't place them; RAS Mapper auto-generated + appended them on open. Fix: with
  all 21 result layers now present, re-running `sort_rasmap_layers` ordered the
  complete set (stable thereafter — RAS Mapper only appends MISSING results). This
  motivated two additions: (a) `plans_with_unlisted_results(project)` +
  `result_plan_ids()` — flag computed-but-unlisted results before opening RAS Mapper
  (common after headless runs); (b) `remove_geoms_from_rasmap()` wired into
  `delete_plan`'s `delete_unused_geom` path (symmetric with the flow/EventConditions
  cleanup) — since hack_ras has NO geometry renumber/delete, this delete-unused
  side-path was the only place a geometry file left a stale `<Geometries>` layer.
  +4 tests. Baseline 260 -> **264**.
- New geometry-file subsystem `project/geoms.py` (user-requested after confirming
  hack_ras had NO geometry renumber/delete — only the delete_plan side-path):
  `renumber_geoms`/`renumber_geom` (bulk + single, chain/cycle-safe, rewrites
  `Geom File=` in the .prj AND in every referencing plan, remaps `.g##` rasmap
  tokens via new `renumber_geoms_in_rasmap`), `insert_geom_gap`, `compact_geoms`
  (fill-the-gaps), `clone_geom` (DuplicateGeomTitle), `delete_geom`
  (refuse-if-referenced + `force=True`, clean_rasmap default). Family = `.g##` /
  `.g##.hdf` / `.x##`. No "Current Geometry" in the .prj. +20 tests
  (`test_geom_ops.py` synthetic 3-geom/4-plan fixture + a `renumber_geoms_in_rasmap`
  rasmap test); validated on a copy of the real 2D-culvert fixture (compact
  g02,g03->g01,g02 rewrote plan Geom File= + rasmap Geometries/GeometryHDF, left
  the plan-keyed nested Results RASGeometry untouched). Baseline 264 -> **284**.
- Ergonomics for the "user asks Claude to run these conversationally" workflow:
  `compact_plans` (plan-side twin of `compact_geoms` — was missing), and bulk
  `delete_plans` / `delete_geoms` taking a flexible id-spec ('16-17,21-26,30-35'
  string or list, via `resolve.expand_id_spec`, which splits commas). Both bulk
  deletes validate every id up front (fail-fast: a bad spec deletes nothing;
  delete_geoms additionally refuses the whole call if any target is referenced,
  unless force) and return one consolidated report. +10 tests. Baseline 284 ->
  **294**. Two paired follow-ups the user deferred to the TODO: a read-only
  project status/health inspector, and dry-run/preview on the mutating ops (see
  docs/TODO.md).
- Built TODO item A (read-only status/health inspector, `project/health.py`:
  `project_health` -> `ProjectHealth` + `format_health`; new read-only rasmap
  queries `rasmap_layer_refs` / `result_plan_ids`). Confirmed with the user that
  A stands alone (no dependency on B; B only ever reuses A's rendering) and that
  their always-backup habit makes B optional. +3 tests (`test_health.py`).
  Baseline 294 -> **297**. B (dry-run/preview) remains the one open TODO.
- Ran the full real job twice at the user's request. Session-first run (delete +
  renumber only) validated end-to-end on the live model and a copy of
  `02_GMF_DFA_v7.0 - backup1`: Plans p01-p21 / Results both contiguous, ZERO
  duplicates. Second run (user restored the model to 35 plans) added a NEW task —
  delete the Increment04-09 unsteady flow files: done via
  `delete_plan(..., delete_unused_flow=True)` on the p21-p26 + p30-p35 deletions,
  which orphaned and removed exactly u12-u17 (u06/u08, shared with survivors, kept).
  Then the EC-cleanup + `sort_rasmap_layers` were applied to the live file, leaving
  Plans/Results numerically sorted (no dups) and EventConditions u01-u11 with zero
  missing-file layers. (Plan/flow mapping for this model: u09/u10/u11 = Increment01/
  02/03, u12-u17 = Increment04-09; each Increment shares one u-file between its plain
  and Breach plan.)

### Session 16 changes (2026-07-27): mesh-snapper mesh-refresh, collisions, grid source, prj fix

Library: added `format_2d_points_header(n)` to `blocks/storage_area_2d.py` (reproduces
RAS's `Storage Area 2D Points= {n} ` spacing byte-for-byte) for when the seed count
changes; +2 tests -> baseline **249**. Everything else was in
`Scripts/Mesh_Health/snap_cell_centers.py`:

- **Mesh-refresh trigger (verified live).** Editing the seeds alone does not make RAS
  regenerate — it compares the text `Storage Area 2D PointsPerimeterTime=` stamp to the
  geometry HDF's `Data Date` / `Geometry Time`. The script now bumps that stamp to now.
  Do NOT delete `.g0x.hdf` to force regen: pipe networks and other features live ONLY in
  the HDF (the HDF also stores the input seeds as `Geometry/2D Flow Areas/Cell Points`).
- **`on_collision: leave|drop`.** A surplus hand-digitized seed whose nearest node is
  already occupied is left in place (`leave`, default) or removed keeping the nearest
  (`drop`, reduces N + rewrites the header + writes `<qc>_dropped.shp`).
- **`grid_source: local|master|explicit`** + a discrepancy warning. SPACING always comes
  from the sparse ring or config — inferring spacing from the whole dense area returns
  rounding noise (~0.1 ft), which was a real bug caught in dry-run. `master` takes the
  phase from all area seeds; `explicit` uses `spacing` + `anchor`.
- **PRJ registration bug fixed.** The old append rewrote the whole .prj in text mode,
  downgrading CRLF->LF (RAS then refused to load) and appended at EOF. Now uses
  `utils.lines` (latin-1, endings preserved) and inserts in ascending order among the
  `Geom File=` lines. Backups increment (`.bak`, `.bak2`, ...) so iterative overwrite
  runs never clobber an earlier one.

### Session 14 changes (2026-07-22): read-only Short-ID / rasmap queries for GIS post-processing

Added two read-only helpers so a standalone results-GIS collection script
(`Scripts/Results_GIS/copy_results_gis.py`) doesn't re-implement HEC-RAS file
parsing: `plan_short_ids(project)` in `project/plans.py` (prj-authoritative
`{plan_id: Short Identifier}`, reuses `plan_path` + `_read_plan_ref`, skips
plans whose `.p##` is missing) and `source_data_folders(rasmap_path)` in
`project/rasmap.py` (subfolders referenced by any non-`RASResultsMap` layer —
terrain, land-cover, features — with the collision rule that a folder referenced
by BOTH a results map and a source layer stays protected). The copy/rename/vrt-
patch/delete mechanics stayed in the script (pure filesystem, not the library's
charter). New `tests/test_rasmap.py` (4 tests: real 2D-culvert `Model.rasmap`
plus a synthetic rasmap for the RASResultsMap-exclusion and collision cases) and
one test in `test_plan_ops_fixture.py` (`plan_short_ids` on the real model,
asserting the stale-`p03` slot is skipped). Baseline: **241 passed / 0 failed /
0 skipped**.

### Session 13 changes (2026-07-17): plan-ops suite — sync, bulk renumber, delete, rasmap, breach check

Implemented all six items from `docs/TODO.md` (user-approved same day; see the
updated "Plan File Operations" section above for the API): `sync_prj`
(project/sync.py), `renumber_plans` bulk core with plan-keyed artifact families /
one-pass restart-ref + rasmap updates / cycle-safe ordering (renumber_plan and
insert_plan_gap now delegate to it), `renumber_plans_in_rasmap`
(project/rasmap.py), `delete_plan` with optional unused-geom/flow cleanup, the
advisory breach-trigger check in `clone_plan`, and `PlanRunActive` guards on
`.p##.tmp.hdf`. Raw-line helpers promoted from plans.py privates to
`hack_ras/utils/lines.py`. 19 new tests in test_plan_ops.py (RichProjectBase
synthetic fixture with artifacts, rst files, restart refs, rasmap).
Validated end-to-end on a copy of the full 25-plan GMF_DFA model
(Model_PCA/03_hack_ras_test): 14 plans deleted (g02/g03 + x02/x03 auto-removed
with their last users), 10-plan chained renumber (53 files, 3 restart refs,
140 rasmap tokens), 3 breach clones with expected placeholder-trigger warnings.
Renumber replaces `Plan File=` entries in place; the optional
`sort_prj_entries(project, kinds=...)` in project/sync.py (added same session
after the user saw p01, p06, ..., p05, p14 in the plan-open dialog, then
generalized to geom/unsteady/steady at the user's request) re-sorts each kind's
entries ascending, moving only those lines. Stale rasmap layers after
delete-then-reuse of a plan number are out of scope per the user — RAS Mapper
was observed self-healing them.

The user then rebuilt the '2D culvert bridge levee precip pipes' fixture in the
RAS 7.0 GUI as a full runnable mini model — final shape: 3 plans (p02 with a
real levee breach, WS Elev trigger; p04 writing the restart u04 consumes; p05),
2 geometries, all run artifacts, PLUS deliberately-kept stale .prj entries
(Plan File=p03 / Unsteady File=u03, files deleted in the GUI) — see the
dev_rules.md fixture table; the stale entries are load-bearing.
`tests/test_plan_ops_fixture.py` runs the whole sequence on a temp copy:
stale-slot renumber refusal -> sync_prj on the genuine GUI leftover -> 3-link
renumber chain (breach plan + rst files follow) -> sort/sync no-ops -> clone of
the real breach plan (WS mode: no warning; Set Time outside window via
line_edits: warning) -> delete with unused-flow cleanup -> restart-orphan
warning. This verifies our assumptions about RAS-authored files rather than
synthetic mocks. The ESRI CRS projection file moved to Terrain/ in the rebuild
— test_ras_project / test_find_crs_prj constants updated.
Baseline: **152 passed / 0 failed / 0 skipped**.

### Session 12 changes (2026-07-07): Manning method 0 preserved for all-A edits

`merge_manning` always wrote method -1 for any rebuilt XS — by design for
simplicity, but a user who merely truncates/extends/gaps a method-0 (LOB/Ch/ROB)
cross-section doesn't expect the n-value METHOD to flip to horizontal variation.
New `_method0_manning()` in merge.py runs before `merge_manning` and keeps
method 0 when: A's input is method 0 (3 entries), every segment source is 'A'
or a gap (None), and the output has bank stations.  Entries are A's three
n-values positionally (LOB/Ch/ROB), keyed to the merged left edge and the
output's own snapped bank stations — so truncating *through* a bank keeps
method 0 with that bank (and its n-entry) snapped to the surviving edge,
per the user's explicit rule.  Anything else falls back to method -1 as before.
Bank-station computation moved above Manning in `_build_merged_xs_lines` (banks
only need `merged_se`) so the pass-through can use the snapped banks.
`write_mann` header now pads the method to 2 chars (`, 0 ,` like RAS's own
spacing; no change for `-1`).  Three new tests (trim, trim-through-bank, gap).

Fixture regenerated and HEC-RAS-verified by the user same day (config JSON
gained a truncate-through-bank case); suite back to fully green at 126.

**Second fix, same session — method -1 first-station n-value.** The user's
HEC-RAS review caught that RS 42788's flat extension to -50 produced a
method -1 block whose first entry sat at 0, not -50 — HEC-RAS refuses to run
a method -1 block with no n-value on the XS's first station.  Cause:
`_n_at_station` is a step-function lookup and returns None for stations left
of the source's first n-entry, so an extension segment contributed no entry.
Fix: `merge_manning` now checks its final entry list against `merged_se[0][0]`
and, when the first station lacks an entry, prepends one carrying the earliest
existing n-value (extends the edge roughness leftward, mirroring the flat
geometry extension).  Deliberately NOT fixed by clamping `_n_at_station` —
that would also stamp values at interior segment starts that legitimately
inherit the previous segment's n.  New test pins RS 42788's shape.

Fixture regenerated and HEC-RAS-verified by the user same day (RS 42788's
`#Mann=` gained the `-50 0.07 0` triplet; Test_notes updated, including the
new RS 42268 severe-truncation/bank-cut case).  Baseline:
**127 passed / 0 failed / 0 skipped**.

### Session 11 changes (2026-07-07): merge.py honors A-only configs; GUI warnings

The session-9 deferred limitation (a config referencing Geometry B for an XS that
exists only in A silently exported a verbatim copy of A) is addressed from both sides:

- **`write_merged_geometry` no longer discards every config on an A-only XS.** The
  old `xs_b is None` short-circuit is replaced by an unsatisfiability rule: an A-only
  XS with a non-trivial config referencing **only A data** (a trim/extension via
  breakpoints) now goes through the real merge pipeline like any paired XS; only a
  config that actually **requests B data** (`'B'` in segment_sources, or
  cutline/ineff source = 'B') keeps the raw Geometry A pass-through — never B
  segments silently emptied, never A silently substituted per-segment.
  `_build_merged_xs_lines`, `merge_manning`, and `merge_ineff` now accept
  `xs_b=None` (guarded; reachable only for all-A configs). RS 42893's output is
  unchanged (all-B config → still pass-through), so the known-good `SterpCreek.g03`
  byte-comparison stayed green with no re-verification needed. Two new tests in
  `test_geometry_merge.py` pin both halves. Baseline: **123 passed / 0 failed / 0 skipped**.
- **The GUI (`RAS_xsedit/xsedit.py`) now warns** instead of staying silent: a
  persistent amber label in the Navigation box flags A-only XS while B is loaded, and
  export scans all states for unsatisfiable B references — a Continue/Cancel dialog
  lists offenders (≤10; generic wording above that), Cancel navigates to the first
  one. See `RAS_xsedit/CLAUDE.md` for GUI details (also added this session:
  Save Config / Save Config As… / Ctrl+S direct-save flow).

### Session 10 changes (2026-07-06): plan file operations — `project/plans.py`

New module `hack_ras/project/plans.py` (see "Plan File Operations" section above for
API): `renumber_plan`, `insert_plan_gap`, `clone_plan`, driven by the user's recurring
need to insert new breach-timing plans into an existing numbered sequence. First
production use: the 46A Hillside model — p25–p31 (L7 breaches) shifted to p30–p36,
new L4 plans p25–p29 (breach 1214–1210) cloned from p24, new L7 plans p37–p40
(breach 1203–1200) cloned from p36; renamed files verified byte-identical, clones
verified to differ only in Plan Title / Short Identifier / Breach Start.
14 tests added in `tests/test_plan_ops.py` (synthetic tmp-dir mini project — the
operations mutate files, so a shared checked-in fixture doesn't fit).
Baseline: **121 passed / 0 failed / 0 skipped**.

### Session 9 changes (2026-07-06): shift.py cutline wrap bug fixed; shared `write_cutline()`

The session-8 bug (below) is fixed. `blocks/xs_gis.py` now has `write_cutline()`,
used by both `shift.py` (whose broken `_format_xs_gis_lines` is deleted) and
`merge.py` (whose `_write_cutline_block` is deleted — it wrote only 9 significant
figures, losing precision HEC-RAS itself keeps).

**HEC-RAS native cut line format — confirmed against an organic fixture.** The user
built `tests/data/XSCutLines stress test/XSCut_stress_test.g01` entirely in the RAS
GUI (input values recorded in `XS_Cutline_input.csv`, some with more decimals than a
field can hold; three XS with 11/9/12-point cut lines at 7-digit, 5-digit, and
2-digit coordinate magnitudes):

- 16-char fixed-width fields, right-justified, 4 fields (2 XY pairs) per 64-char
  line; wrapping always lands on a field boundary.
- Values with enough digits fill their field completely, leaving NO whitespace
  between adjacent fields — the block can only be read as fixed columns, never
  whitespace-split.
- Each value carries as many digits as fit the 16-char field (up to ~15 significant
  figures), trailing zeros stripped.  RAS *truncates* digits that don't fit
  (`…36345064855` → `…36345064`, not `…065`); `_fmt` rounds instead, which can only
  differ on computed values carrying more precision than any RAS-written file —
  every value parsed from a file round-trips byte-for-byte
  (`test_write_cutline_reproduces_organic_bytes` asserts this for all three blocks).

Four regression tests added in `tests/test_xs_shift.py` (parse packed fields,
byte-reproduce organic blocks, never split fields on 8-point write + re-parse,
shift-and-roundtrip all three stress XS).

**Shifted fixtures verified in RAS.** `XSCut_stress_test.g02` (shift right: RS 3000
+100 ft, RS 2000 +105 ft, RS 1000 +10 ft — all past the first GIS vertex) and `.g03`
(same distances left) were generated with the fixed shifter and the user confirmed
the shifted cut lines display correctly in HEC-RAS.  Note the sign convention: cut
lines are digitized left bank → right bank, and a positive `shift_polyline` distance
slides the line toward its own end point, i.e. toward the RIGHT bank (facing
downstream) — which can be screen-LEFT in a north-up plan view.

**Stretched-stationing fixture + 8-char station rounding (same day).** The user built
`tests/data/Massive XS stations/Massive.g01` in the RAS GUI (input recorded in
`RS_input_to_RAS.xlsx`): RS 1000 is a normal 219-point XS (−350..777.71); RS 500 is
the same XS with LOB/Channel/ROB scaled ×1000 in RAS (−350..1127361, organically
packed 8-char `#Sta/Elev=` fields like `870.9768549.99`).  Key finding: the user
entered bank stations 451530.795 / 474140.825 and RAS saved both as `451530.8` /
`474140.8` in BOTH `#Sta/Elev=` and `Bank Sta=` — **RAS never writes a station
needing more than 8 chars; it ROUNDS to fit the field, matching `_fmt`'s behavior
exactly** (contrast: 16-char cut line fields are truncated, not rounded).  Since
RAS-authored files can therefore never contain overflowing stations, overflow only
arises from our own pipeline (transforms/interpolation).
`test_bank_sta_matches_block_station_stretched_xs` was retargeted from the deleted
SterpCreek.g04 to this fixture (A = B = Massive.g01, config on RS 500 with
h_offset=0.33 to force 9-char stations, truncated right end); its skip guard and
sibling-repo dependency are gone.

**Known-good g03 regenerated; test now JSON-driven (same day).** The user rebuilt the
Sterp Creek scenario (5 configured XS; source B g01 was also edited — new title
'old XS w new IFAs', RS 42893 removed from B to create a B-missing case), exported a
new `SterpCreek.g03` via the GUI, and verified it in HEC-RAS
(`RAS_xsedit/tests/Test_notes.txt`).  Independent checks confirmed: regenerating from
the JSON via `write_merged_geometry` reproduces the exported g03 byte-for-byte; all
73 unconfigured XS pass through verbatim; bank-station text matches the block text;
IFA sentinel/transform behavior correct in both 'normal' (42998) and
'multiple_block' (43320) forms.  `_sterp_configs()` was rewritten to load
`RAS_xsedit/tests/xsedit_config.json` directly (title included) — no more
transcription; `_CONFIGURED_KEYS` removed (derived from the JSON).  Known limitation
recorded in Test_notes: a config whose segment source is 'B' where B has no such XS
(RS 42893) silently exports a verbatim copy of A — a warning is wanted; **deferred**.
Baseline: **107 passed / 0 failed / 0 skipped**.

### Session 8 changes (2026-07-04): XS block writers moved from merge.py into blocks/

Pure mechanical move, no behavior change: the fixed-format block writers that lived in
`geometry/merge.py` now live in `geometry/blocks/` next to their parsers, so read/write
format knowledge for each block is in one file:

- `_write_sta_elev_block` → `write_sta_elev()` in `blocks/xs_sta_elev.py`
- `_write_mann_block` → `write_mann()` in `blocks/xs_mann.py`
- `_write_ineff_block` → `write_ineff()` in `blocks/xs_ineff.py`
- `_write_bank_sta_line` → `write_bank_sta()` in `blocks/xs_bank_sta.py`
- `_fmt` / `_fmt_or_blank` / `_write_triplet_lines` → `blocks/base.py`

merge.py imports the writers; its unused `parse_bank_sta` import was dropped.
`_write_cutline_block` deliberately stays in merge.py for now: shift.py has its own
independent cutline writer (`_format_xs_gis_lines`) with a real line-wrap bug (see
below), and unifying the two into a shared `write_cutline()` in `blocks/xs_gis.py`
belongs to that bug fix, not to this move.

**Known bug (fixed in session 9, above) — shift.py cutline line wrap.** `_format_xs_gis_lines`
concatenates all 16-char coordinate fields into one flat string and slices it every
65 characters. 65 is not a multiple of 16, so each data line starts one character
deeper inside a field; from the 4th data line onward (≥7 XY pairs at typical 14-char
value widths) digits are split across line breaks, producing corrupt coordinates that
`parse_cutline` cannot read back (IndexError) and HEC-RAS would misread. Short cut
lines (≤6 points) survive because the drift only consumes leading padding spaces —
which is why the existing round-trip test (3-point cutline, RS 84816) never caught it.
Fix deferred until the user provides HEC-RAS-authored fixtures: a cutline with >7 XY
pairs, one with small coordinates (~45.2), one with large 7-digit coordinates.
shift.py also formats values as `{:16.6f}` while HEC-RAS itself writes ~9 significant
digits (`6451252.62`); merge.py's `_write_cutline_block` (`{:>16.9g}`, 4 values per
64-char line) matches HEC-RAS native format.

### Session 7 changes to `geometry/merge.py` (2026-07-02, bug-review fixes)

Four fixes from a code-review pass, each with regression tests in
`tests/test_geometry_merge.py`:

- **Trivial-config check now compares breakpoints against A's actual extent.**
  `_is_trivial_config` and the `se_unchanged` fast path in `_build_merged_xs_lines`
  previously only checked "single segment, source A, identity transform" — a
  truncated or extended all-A config was silently exported verbatim at A's full
  extent (while the GUI plot showed the truncation).  Both sites now also require
  `_stations_equal` on the outer breakpoints vs `xs_a.sta_elev[0]/[-1]`; a
  truncation/extension goes through the real merge pipeline (rebuilt/rounded
  Sta/Elev, clipped cut line, snapped banks).  Consequence: such XS are no longer
  byte-for-byte pass-throughs.  When A has no sta_elev the extent check is
  skipped (breakpoints are meaningless there).  Note: an extension beyond a
  source's data fabricates flat (clamped-elevation) points — reviewed with the
  user and explicitly accepted, no warning wanted.
- **`Bank Sta=` values are now rendered with `_fmt` (the #Sta/Elev block's own
  8-char formatter), not `:g`.**  `:g` is 6 significant digits and mangled
  stations like 10251.75 → 10251.8, so the bank station no longer matched any
  station in the block — the exact invariant bank-station snapping exists to
  guarantee.  The Bank Sta line itself stays comma-separated (it is not an
  8-char block); only the value text changed.  Values that cannot fit an 8-char
  field (e.g. 112421.75) shorten exactly the way the block field shortens them,
  so the two always agree.  `SterpCreek.g04`'s RS 43320 (user-built fixture with
  stationing stretched to -350..112421) covers this end-to-end.
- **`merge_manning` station collisions: the later value wins.**  When two
  n-entries snap to the same output station (typically the previous segment's
  last entry vs the next segment's opening entry at a breakpoint), the previous
  dedup kept the first and silently dropped the new segment's opening n-value.
  Now the later entry overwrites, matching the "vertex belongs to the segment
  that starts there" rule used by `merge_sta_elev`.
- **`master_source` parameter removed from `write_merged_geometry`.**  It was
  unreachable from the GUI (xsedit hardcoded "A"; the Swap button physically
  exchanges the files instead) and its 'B' path was broken — `_collect_xs_pairs`
  is A-structured, so master B emitted only the A∩B intersection.  Geometry A is
  now unconditionally the master, documented in the docstring.

### Session 5 changes to `geometry/merge.py` (2026-07-01)

Triggered by a real bug: in a merged cross-section, a Manning's n breakpoint was written
at a station (e.g. 0) that did not exist anywhere in the `#Sta/Elev=` block, which HEC-RAS
cannot open — n-value changes and bank stations must land exactly on a cross-section
station.

- **H Scale removed.**  `Transform` no longer has `h_scale`; stations are only ever
  translated (`new_station = old_station + h_offset`), never scaled.  `apply_station()`,
  `to_orig_station()`, and `inverse()` are correspondingly simpler — `inverse()` can no
  longer raise (there was nothing left to divide by zero).  The GUI's "H Scale" spinbox
  and its color-coding are gone; `MergeConfig`/`XSState`/JSON configs no longer carry
  `h_scale`.  Older config JSON files with an `h_scale` key still load fine — the key is
  simply never read.
- **`merge_sta_elev()` rewritten — every segment now guarantees a vertex at its own start
  station.**  The old version only included points that happened to already exist in the
  source data within a segment's range (`_filter_segment`, with a special left-inclusive
  case for the first segment), plus an ad hoc "gap interpolation" fallback for segments
  that turned out to have zero points.  The new version always calls `_vertex_at()` for
  each segment's start station — reusing an exact source point if one lands there (after
  rounding), otherwise interpolating — so a real vertex exists at every breakpoint
  regardless of whether the two source surveys happen to share a station there.  The old
  gap-interpolation fallback and its `warnings_out` plumbing are gone: what used to be an
  unusual, warned-about case (a segment landing on zero source points) is now simply the
  normal case for every segment.  `write_merged_geometry()` therefore returns `None`
  instead of a warnings list, and the GUI's post-export "Gap interpolation" popup is gone.
  A breakpoint's vertex always comes from the segment that *starts* there (not the one
  that ends there), so each station appears exactly once.
- **Rounding to 2 decimals is now the single definition of "does this station exist."**
  `_round_sta()` / `_stations_equal()` in `merge.py` replace several different ad hoc
  epsilon tolerances (1e-6 in `_insert_bank_station`, 1e-9 in `_n_at_station` and the old
  Manning's dedup check) that didn't agree with each other or with the 2-decimal precision
  actually written to the file.  For any cross-section that isn't a verbatim pass-through
  of A, the finalized `#Sta/Elev=` station/elevation values are rounded to 2 decimals
  before anything downstream (bank stations, Manning's n, `_write_sta_elev_block`) uses
  them, so everything that follows agrees with what's on disk.
- **Bank stations and Manning's n now snap onto the finalized `#Sta/Elev=` block** via
  `_snap_to_nearest_station()`, instead of being computed independently and hoped to land
  on a real station.  This is the direct fix for the reported bug.
- **Manning's n merge simplified — no more `mann_option`.**  `merge_manning()` no longer
  takes an `'A'` / `'B'` / `'merge'` choice; it always merges per-segment (using the same
  source assignment as the geometry) and always writes method `-1`.  The old `'A'`/`'B'`
  branches let a user pick "Manning's from B" while the geometry segments said otherwise,
  which never made sense.  `_transform_manning_def()` and the raw-`#Mann=`-passthrough
  special case in `_build_merged_xs_lines` (which depended on `mann_option`) are deleted;
  the GUI's Manning's n radio buttons are gone along with `MergeConfig.mann_option` /
  `XSState.mann_option`.
- **`bank_stations_override` and `mann_def_override`** (existing `MergeConfig` fields, not
  wired into the GUI) are unaffected in spirit — override insertion still happens before
  the rounding pass, and the final snap step is a no-op for them since the override values
  already exactly match a point that was just inserted.
- **`_write_mann_block()` now wraps at whole-triplet boundaries, not a flat 10-values/line.**
  Confirmed by exhaustively parsing every `#Mann=` block in `tests/data/Baxter/Baxter.g02`
  (173 blocks) and `tests/data/Beaver/beaver.g01` (57 blocks): every real HEC-RAS data line
  is 24, 48, or 72 characters — a whole number of triplets (max 3 per line) — never 80.
  The old flat `range(0, len(values), 10)` chunking (correct for 2-field `#Sta/Elev=`
  pairs, since 10 is a multiple of 2) desynced 3-field Manning triplets across the line
  break whenever the entry count wasn't a multiple of 10 — e.g. a station would end one
  line while its n-value and position code opened the next. HEC-RAS's own reader does not
  tolerate this, unlike our own `_read_n_floats`, which concatenates fields across lines
  regardless of boundary and so never caught it in round-trip tests. Confirmed against a
  cross-section HEC-RAS itself authored with a negative starting station and a genuine
  method=-1 n-value breakpoint at literal station 0 (not a "left-edge sentinel" — method=0's
  first triplet is always a real station that happens to equal the XS's own left edge, not a
  placeholder) that this specific station-0-mid-array shape is valid to HEC-RAS on its own;
  whether the triplet-wrap fix fully resolves the FLT_MAX corruption seen in the RAS GUI for
  such cases is still unconfirmed — a separate "only the first ~3 n-value entries render in
  the cross-section points table" limitation was also observed and may be a distinct,
  unrelated HEC-RAS UI cap. `#XS Ineff=` blocks are also triplet-based and likely have the
  same line-wrap issue, but that was explicitly out of scope for this fix.

### Session 4 changes to `geometry/merge.py` (2026-06-27)
- **Bank station bug fix**: bank stations are station-space values that index into
  `#Sta/Elev=`; they must follow the *geometry* source, not the GIS cut line source.
  Previously `cutline_source='B'` caused B's bank stations to appear in output even when
  all geometry came from A.  Fixed by decoupling bank station selection from
  `cutline_source` and basing it on whether the entire geometry is from B (`all_from_b`).
- **`all_from_b` robustness**: the original single-element check (`len==1 and [0]=='B'`)
  was replaced with `bool(...) and all(s == 'B' for s in segment_sources)` to handle
  multi-segment configs where every segment is assigned to B.
- **B-only XS excluded from `_collect_xs_pairs`**: XS that exist only in the secondary
  geometry are no longer appended to the navigation list.  The output has always followed
  A's structure; showing B-only XS in the GUI was misleading and clicking them produced
  nonsensical plots.  The docstring now reflects this.

### Session 6 changes to `geometry/merge.py` (2026-07-02): `#XS Ineff=` (Ineffective Flow Areas)

`MergeConfig.ineff_source: str = 'A'` selects, per cross-section, which source's whole
IFA list is carried into the merge (same shape as `cutline_source` — a single whole-XS
choice, not per-segment, since an IFA range can't be meaningfully split across sources).
Unlike `merge_manning()`, `merge_ineff()` does **not** force a single output format —
it preserves the chosen source's `ifa_type` verbatim ('normal' stays 'normal',
'multiple_block' stays 'multiple_block').

**"normal"-type sentinel fields are carried through untouched, not resolved.**
Confirmed against real HEC-RAS-authored fixtures (`RAS_xsedit/tests/data/Sterp Creek/
SterpCreek.g03/.g04/.g05`, all built by hand in the RAS GUI for XS 43320) that "normal"
IFAs are not a legacy 3-slot format like Manning's method=0 — every field is a real,
literal value *except* two specific ones: the left area's `start_sta` and the right
area's `end_sta`, which are written blank/`0.0` when the user leaves that side
unbounded, meaning "extend to whatever this cross-section's edge turns out to be."
That resolution is meant to happen whenever HEC-RAS *reads* the file, evaluated against
whatever geometry is actually in it at that time — not something to precompute and bake
in as a literal number at export time (an earlier version of this function did exactly
that, resolving against the merged output's first/last station; it worked, but freezing
today's edges into the file is strictly worse than just leaving the self-updating
sentinel alone, so it was reverted in favor of this simpler, more correct approach).
Those two fields are therefore written as literal `0.0`, and critically are **not**
run through the chosen source's `Transform` — shifting a sentinel by `h_offset` would
turn it into an arbitrary non-zero station and silently destroy its meaning. Every
other field (the non-sentinel station in a "normal" area, and every field in a
`multiple_block` area, which has no sentinel semantics at all) is shifted by the
`Transform` exactly like any other station/elevation value. A blank/`None`
("infinite height") elevation is carried through as `None` rather than resolved to a
number — `multiple_block` areas always have a real elevation already in a valid source
file, and a "normal" area's blank elevation is valid on its own terms, so there's
nothing to resolve there either. `_write_ineff_block()` writes `None` as a blank field
via a small `_fmt_or_blank()` wrapper around the shared `_write_triplet_lines()` chunker
(which also gained `Optional[float]` support for this). IFA boundaries are confirmed to
*not* need to land on an existing output station (unlike bank stations and Manning's n
breakpoints), so no `_snap_to_nearest_station` call is needed here, and `merge_ineff()`
doesn't need the merged station/elevation block passed in at all.

**Line-wrap.** `#XS Ineff=` triplets are confirmed to follow the exact same whole-triplet
line-wrap convention as `#Mann=` (`_write_ineff_block()` reuses the shared
`_write_triplet_lines()` helper, factored out of `_write_mann_block()` for this reuse).
`Permanent Ineff=` T/F flags are confirmed to never need more than one line, since the
10-area cap on `multiple_block` means at most 10 single-field flags (80 chars) fit in one
line regardless.

`"#XS Ineff="` (paired with its `Permanent Ineff=` follower, both consumed by
`parse_ineff()`'s single `lines_consumed` count) is registered in `_KEY_PREFIXES` /
`_KEY_PARSERS`, so `_scan_xs_content` — which matches prefixes line-by-line regardless of
where they appear — correctly recognizes it whether it comes before or after `#Mann=` in
the source file (confirmed this isn't fixed by HEC-RAS). For writing, the block is always
emitted between `#Mann=` and `Bank Sta=`, matching every real fixture's actual layout;
HEC-RAS does not enforce block order, so this doesn't need to track the source's original
position.

**Duplicate `#Sta/Elev=` rows after rounding, and vertical walls.** Two very close but
distinct source points (e.g. a natural survey point sitting right next to a point
HEC-RAS itself interpolated at a bank station — confirmed in `SterpCreek.g02`'s own
data around both bank stations for XS 43320) can round to the exact same
`(station, elevation)` pair, producing a genuine carbon-copy row that HEC-RAS rejects.
`_dedupe_exact_duplicates()` collapses adjacent rows only when **both** fields match
after rounding. Critically, HEC-RAS cross-sections can legitimately have two rows at the
*same* station with *different* elevations (a vertical wall — confirmed against a
hand-built test fixture, `RAS_xsedit/tests/data/TEMP/SterpCreek.g06`, with four such
walls), so a same-station-different-elevation pair must never be collapsed. Relatedly,
`_snap_to_nearest_station()` now breaks distance ties in favor of the higher elevation,
so bank-station/Manning's-n snapping onto a wall is deterministic rather than depending
on array order (though since `Bank Sta=`/`#Mann=` only ever write the station number,
not an elevation, this has no effect on the two wall-forming rows' text either way — it
matters for the general equidistant-different-station case). `_vertex_at()` deliberately
was **not** given the same tie-break: if a user-defined segment breakpoint happens to
land exactly on a wall, picking whichever side is encountered first while scanning is
good enough — landing a breakpoint exactly on a wall is a modeling error on the user's
part, not a case worth adding branching for.

## Future Features — Not Yet Implemented

**`docs/TODO.md` is the authoritative open-items list.** As of 2026-08-10 it has
two OPEN items: (A) writer / `merge.py` support for Blocked Obstructions
(`#Block Obstruct=`) and `Levee=` — described just below, currently parse-only;
and (B) dry-run/preview on the mutating ops (LOW PRIORITY). Everything else once
listed there is DONE: the session-13 plan-file-op gaps, the session-17 geometry
subsystem / rasmap cleanup / health inspector, and — both in session 20 — item D
(the `flows` subsystem) and item C (the `project.rasmap` bound accessor). Ask the
user before implementing.

### `#Block Obstruct=` (Blocked Obstructions) — now PARSED (read-only)

As of 2026-07-21 `#Block Obstruct=` and `Levee=` are parsed into the model
(`blocks/xs_block_obstruct.py` → `CrossSection.blocked_obstructions`;
`blocks/xs_levee.py` → `CrossSection.levee`) for the active-flow work.  Parsing is
read-only (on top of `raw_lines`, so the lossless roundtrip is unchanged); **no writer /
`merge.py` support yet**.  Verified against real 5.0.3 output
(`tests/data/Wisconsin Floodway/SterpCreek.g01`): blocked obstructions use the same 8-char
`[start, end, elevation]` triplet layout as IFAs, with `normal` (flag 0, left/right +
0.0-edge sentinels) vs `multiple_block` (flag -1, literal stations) — but with **no**
`Permanent` follower line (obstructions are always solid).  If merge/write support is
added later, the old plan (a `MergeConfig.obstruct_source` field, `_KEY_PREFIXES` entry,
a `merge_obstruct()` reusing `_write_triplet_lines()`, GUI wiring) still applies.

### Active-flow feature semantics (`geometry/active_flow.py`)

For **active** top width, IFAs, levees, and blocked obstructions all reduce to a *blocking
range* removed from the wetted extent, governed by one overtopping rule: a feature blocks
while `wse <= elevation` (a blank/`None` elevation always blocks); once `wse > elevation`
it has no active-top-width effect.  Levee (not overtopped) clips outboard
(`[min_sta, left_sta]` / `[right_sta, max_sta]`); obstruction (not submerged) removes its
`[start, end]`.  Levees and obstructions also reduce *inactive*/total width (IFAs don't),
but `active_flow.py` computes active width only.  The `0.0`→edge sentinel is resolved
**only for "normal"** features (`_resolve_area`); "multiple_block" stations are literal.

### `geometry/merge.py` — design notes (2026-06-24)

**Station/elevation merging — see session 5 (2026-07-01) above for the current design.**
`merge_sta_elev()` now guarantees a vertex at every segment's start station via
`_vertex_at()` (exact source point if one rounds to that station, else interpolated).
The `_filter_segment()`/left-inclusive rule and the zero-point "gap interpolation"
fallback described in earlier revisions of this note no longer exist — interpolating a
guaranteed vertex is now the normal path for every segment, not an exceptional fallback,
so there's nothing left to warn about.  `merge_sta_elev` and `write_merged_geometry` no
longer take/return a warnings list.

**Interstitial content ordering in `_build_merged_xs_lines`**
`_scan_xs_content()` now returns `(initial_lines, key_segments)` where `key_segments`
is an ordered list of `(key_prefix, interstitial_lines)` pairs — one entry per key
block found.  `interstitial_lines` are the non-key lines that immediately follow that
key block in the source file (e.g. `Node Last Edited Time=` lives between the cut-line
block and `#Sta/Elev=`; `XS Rating Curve=` lives after `Bank Sta=`).

`_build_merged_xs_lines` emits each new key block and then its original trailing
interstitials in one step, so non-key lines stay exactly where the source file had
them.  Previous design lumped all non-key post-block content into a single `post_key`
bucket, which caused `Node Last Edited Time=` to drift after `#Mann=` and `Bank Sta=`
to drift to the very end of the XS block.

`_scan_xs_content` also stops immediately on any `River Reach=` line, preventing the
next reach's header from leaking into the last XS of a reach.

**Verbatim Manning's n when not merging — removed in session 5 (2026-07-01).**
`mann_option` (and the `_raw_mann_lines()` verbatim-passthrough path keyed off it) no
longer exists.  Manning's n for any non-trivial config is always rebuilt via
`merge_manning()` and written through `_write_mann_block()`; only a fully trivial XS
(handled by `write_merged_geometry` before `_build_merged_xs_lines` is ever called) still
passes through byte-for-byte, `#Mann=` included.

**`write_merged_geometry` — B-only XS no longer corrupt reach ordering**
The `xs_master is None` guard (which skips XS that only exist in the secondary
geometry) now runs *before* the reach-header emission and `prev_reach_key` update.
Previously, B-only XS could advance `prev_reach_key` to their reach, causing a
subsequent A-reach XS to trigger a second (duplicate) reach header write.

**`_collect_xs_pairs` — reach interleaving preserved (2026-06-25)**
`_xs_in_file_order(geom)` sorts all XS by `_raw_line_start` before building the
ordered list. Previously the code iterated `geom.rivers.values()` which grouped all
reaches under the same river name together, destroying the original interleaved reach
order (e.g. West/Upper → East/East Branch → West/Trib became West/Upper → West/Trib →
West/Lower → East/East Branch).

**`_write_cutline_block` — header format matches HEC-RAS (2026-06-25)**
The `XS GIS Cut Line=` header is now written as `XS GIS Cut Line={n}` (no surrounding
spaces), matching the format produced by HEC-RAS itself. Previously an extra leading
space and trailing space were added, which showed up as a spurious diff on any merged XS.

**`_is_trivial_config` — now checks all configurable options (2026-06-25)**
Previously only checked geometry (segment sources + master transform). Now also checks
`mann_option` and `cutline_source`. A config is only trivial (verbatim master pass-through)
when ALL options point to master. Configs that only change Manning's n or the cut line
source were silently bypassed before this fix. Ineffective flow areas and blocked
obstructions are not yet configurable so no check is needed for them.

**Verbatim `#Sta/Elev=` when geometry is unchanged (2026-06-25)**
`_build_merged_xs_lines` now writes the raw `#Sta/Elev=` lines verbatim (via
`_raw_sta_elev_lines`, mirroring `_raw_mann_lines`) when all station/elevation data
comes from source A with an identity transform. Only reformats through
`_write_sta_elev_block` when geometry is genuinely rebuilt from two sources or a
transform is applied. This preserves idiosyncratic original spacing (e.g. `  25.802`)
so ExamDiff shows only the blocks that actually changed.

**Cut line drop when source has no cut line — by design**
When `cutline_source='B'` and B's XS has no `XS GIS Cut Line` block,
`build_merged_cutline` returns `None` and the cut line is omitted from the output.
This is intentional: if the user says "use B's cut line" and B has none, the output
has none.

## Known Constraints
- Windows environment
- No admin privileges
