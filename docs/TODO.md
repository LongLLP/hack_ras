# hack_ras To-Do List

Discuss scope with the user before implementing any item (see `dev_rules.md`;
the user has asked to be consulted before hack_ras changes).

---

## OPEN ITEMS

Three items are open as whole pieces of work. In rough priority:

1. **Blocked Obstruction / Levee writer + `merge.py` support** — §A below.
2. **Interior flood-volume peak (E2)** — §E below.
3. **Dry-run / preview on the mutating ops** (LOW PRIORITY) — §B below.

Two more are half-built and easy to miss, because they are not on that list:
**E1** and **E3b** each have all their readers and none of the metric on top
(see the 2026-08-27 note below, and §E).

**E4 (the pumped-vs-gravity analysis script) is DONE** — built 2026-09-22 as
`hack_ras_scripts/Results_Pipe_Profile_Comparison/gravity_vs_pumped.py`.

**Closed 2026-09-14:** §F cross-mesh result comparison (built — see below). The
`Ditch_fix_RS_9580` GUI-visibility question was closed the same day as a RAS
Mapper display bug the user has chosen to leave alone; the measurement proving
the modification is live is in `ai_context.md`, and the operational conclusion —
never use the GUI to rule terrain modifications out — stands.

**Built 2026-08-27:** `read_pump_curves` / `pump_station_capacity` (E1's data
layer), `read_node_rims`, `read_node_max_wse`, `read_volume_accounting` /
`list_volume_accounting`, and `PathProfile.surcharge_margin` (E3a complete). What
remains of E1 is the metric on top of the readers; E2 needs the peak computation;
E3b needs the comparison on top of its two readers.

### A. Blocked Obstruction / Levee writer + `merge.py` support — OPEN, top priority

Both blocks PARSE (added 2026-07-21 for the active-flow work) and neither can be
WRITTEN. `CrossSection.blocked_obstructions` and `CrossSection.levee` are
read-only, so `RAS_xsedit` can draw both features and edit neither — every other
cross-section block has a writer. Nothing here changes what a levee or an
obstruction MEANS; the active-flow semantics in `ai_context.md` stand.

**These are two different jobs, because the two formats are not alike.** A line
in `dev_rules.md` lumps them together ("same 8-char triplet layout as IFAs");
that is true of `#Block Obstruct=` and false of `Levee=`.

**A1. `#Block Obstruct=` — the IFA twin.**

8-char fixed-width triplets, N x 3 = `(start_sta, end_sta, elevation)`. `flag` 0
-> `normal` (one area left of the channel, one right, with 0.0 station sentinels
for the XS edges); `flag` -1 -> `multiple_block` (1-10 areas at literal
stations). Blank station field -> 0.0, blank elevation -> `None`. There is NO
`Permanent` follower line — an obstruction is always solid.

So this is `#XS Ineff=` minus the Permanent block, and `merge_ineff()` is the
template. Most of the work is copying it:

  * `write_block_obstruct()` in `blocks/xs_block_obstruct.py` — the writer goes
    beside its parser per `dev_rules.md`, reusing `_write_triplet_lines()` and
    `_fmt_or_blank()` from `blocks/base.py`.
  * `MergeConfig.obstruct_source: str = 'A'` — a whole-XS choice like
    `ineff_source`, NOT per-segment: a blocked range cannot be meaningfully
    split between two surveys.
  * `merge_obstruct()`, preserving the chosen source's `obstr_type` verbatim
    (`normal` stays `normal`, `multiple_block` stays `multiple_block`), exactly
    as `merge_ineff` preserves `ifa_type`.
  * register `'#Block Obstruct='` in `_KEY_PREFIXES` / `_KEY_PARSERS` so
    `_scan_xs_content` finds it wherever it sits in the source file.

Carry the IFA sentinel rule over unchanged: a 0.0 edge sentinel is written back
as literal 0.0 and must **NOT** be shifted by the `Transform`'s `h_offset`.
Shifting it turns "the XS edge, whatever it turns out to be" into an arbitrary
station and destroys the self-updating meaning. Only `normal` has sentinels;
every `multiple_block` field is literal and does get shifted.

Obstruction boundaries do **not** need to land on an existing output station
(confirmed for IFAs), so no `_snap_to_nearest_station()` call — unlike bank
stations and Manning's n breakpoints.

**A2. `Levee=` — a single line, not a block.**

    Levee=<Lflag>,<Lsta>,<Lelev>,<Rflag>,<Rsta>,<Relev>,,<name>

Comma-separated, one line. A flag of -1 means that side's levee is present; 0 or
blank means absent, and that side's station/elevation fields are blank and parse
to `None`. Field 7 is always empty and field 8 onward is an optional name, which
may itself contain commas (`parse_levee` joins the tail back together).

The writer is therefore a single-line formatter shaped like `write_bank_sta()`,
not a triplet writer. Two things it must get right: reproduce the empty 7th
field, and re-emit a name containing commas the way RAS does.

**UNVERIFIED, and the one thing here that needs a GUI test rather than a code
decision:** a levee is one station per side, so it may be subject to the snapping
rule that IFAs escape. Check in HEC-RAS whether a levee station must coincide
with an existing `#Sta/Elev=` station before deciding whether to snap it. Getting
this wrong fails the way the original Manning's-n bug did — a geometry RAS cannot
open.

**The trap that will bite.** `_is_trivial_config()` must learn about both new
source options, and its docstring currently says the opposite in as many words:
"Blocked obstructions are not currently configurable; they always pass through
verbatim from A." It checks the breakpoint count, a single segment from A, an
identity transform, A's full station extent, `cutline_source` and `ineff_source`.
A config that changes ONLY the obstruction or levee source would be classified
trivial and exported as a byte-for-byte copy of A, silently discarding the edit.
The same class of bug has been found and fixed twice already — 2026-06-25 for
`mann_option` / `cutline_source`, and 2026-07-02 for truncated all-A configs.

Finally, GUI wiring in `RAS_xsedit` (and note its no-disabled-controls rule —
route to a fallback flow rather than greying a control out).

---

### B. Dry-run / preview on the mutating operations — OPEN, LOW PRIORITY

Before a renumber / delete / reorder / retitle / settings change touches a file,
report the complete change-set and write nothing.

**Why it is low priority:** the user backs up before every run, which already
covers the same risk. Confirmed 2026-07-28 that this item stands alone and
blocks nothing — the health inspector was built without it, and a preview only
ever reuses that rendering.

**Why it is cheap: the change-set already exists.** Every mutating op validates
everything up front and builds its complete mapping before writing the first
byte — that is the existing fail-fast contract (a bad spec changes nothing, on
any file). A preview is that same computation with the write suppressed, not a
new analysis.

**The one rule that matters: it must not be a second code path.** A preview that
recomputes the change-set independently will drift from the real one and
eventually lie, which is worse than having no preview at all. Suppress the write
inside the existing function; never mirror it.

Scope — the ops that mutate:

  * `plans`: `renumber_plan` / `renumber_plans`, `insert_plan_gap`,
    `compact_plans`, `reorder_plans`, `clone_plan`, `delete_plan` /
    `delete_plans`, `retitle_plan`
  * `geoms` and `flows`: the same families
  * `plan_settings.set_plan_settings`
  * `sync.sync_prj`, `sync.sort_prj_entries`
  * `rasmap`: `renumber_*`, `remove_*`, `retitle_in_rasmap`, `sort`

What a preview should show: every file renamed (old -> new) or deleted; `.prj`
entry lines added, removed or retokenized; `Geom File=` / `Flow File=` rewrites
in each referencing plan; `Restart Filename=` rewrites in `.u` files; `.rasmap`
token remaps and layer subtrees spliced out; `.hdf` title attributes written; and
every warning the real run would emit.

Open questions, for the scope discussion `dev_rules.md` requires:

  1. A `dry_run=True` keyword on each op, or a parallel `preview_*` function per
     op? The keyword keeps one code path, which argues for it — but it means
     every op can return a report for work it did not do, so the report needs an
     unambiguous "nothing was written" marker.
  2. Reuse each op's existing report dict as the preview shape, so preview and
     apply return the same structure? Or one uniform change-set type across all
     ops? The first is less work and less to learn; the second renders once.
  3. Net effect only, or the intermediate steps? A chained or cyclic renumber
     hops through `.renumtmp` to break cycles. A preview should almost certainly
     report `p02 -> p06` and not the temp hop — but that hides a state that
     genuinely exists on disk mid-operation.
  4. Where the renderer lives: `format_changes()` beside `format_health()` in
     `project/health.py`, or its own module. Health is the rendering precedent
     either way.

---

### E. Pipe-network analysis helpers — DEFERRED 2026-08-27

Requested as part of a pumped-vs-gravity interior drainage study, and explicitly
deferred by the user. The underlying HDF data was confirmed present before
deferring, so these are build-when-wanted, not research. User decisions recorded
2026-08-27 are marked **DECIDED**. Model-specific evidence lives in the agent
memory note, not in this repo.

---

**E1. "Pump capacity exceeded for N hours."**

Data: pump curves at `Geometry/Pump Stations/Pump Groups/Efficiency Curves Info`
(per-group `[start, count]`) and `.../Efficiency Curves Values` (`(N, 2)` float32;
column 0 = head, column 1 = flow). `read_pump_station` already returns per-group
flow, a `pumps_on` count, and each pump's `ws_on` / `ws_off`.

Definitions — all three are to be supported; the user's **first choice is (a)**:

  * **(a) FIRST CHOICE — inflow exceeding total pump station capacity**, summed
    over all pump groups and all pumps in the station.
  * (b) every pump in the station running at once (`pumps_on == n_pumps`).
  * (c) headwater stage climbing above a nominated elevation.

**DECIDED — capacity is the HEAD-DEPENDENT value interpolated from the pump
curve** at each time step, not a nameplate / best-head rating. The user's
reasoning: a theoretical best-head capacity is never "experienced" by the system
during the simulation, so it cannot tell you whether outflow was actually
pump-limited.

This is not a cosmetic choice. On the motivating model a station's summed
best-head capacity was ~28% higher than its summed worst-head capacity, and the
observed peak station flow fell BETWEEN the two — so "capacity exceeded" fires
under the head-dependent reading and does not fire under the nameplate reading.

Interpolate against the head actually seen at each step (`stage_hw` / `stage_tw`
from `read_pump_station`), summed over all groups and all pumps in the station.

**DECIDED — interpolate the crossing times.** Do not report a count of output
intervals: at a 30-minute output interval that quantizes every answer to 0.5 h,
which is coarse next to the duration being measured. Interpolate where the
inflow curve crosses the head-dependent capacity curve and sum the exceedance
durations.

---

**E2. Interior surface flood volume.**

**DECIDED** — the user must be able to specify either:

  * **(1) volume at a specific time code** — the ponded volume at one named
    output time stamp, and
  * **(2) peak ponded volume**, where each simulation is allowed its own,
    chronologically independent peak (plan A and plan B need not peak together).

Sources: end-of-run standing volume is available directly as group attributes at
`Results/Unsteady/Summary/Volume Accounting/Volume Accounting 2D/{area}/`
(`Vol Ending`, `Cum Inflow`, `Cum Outflow`, `Error`, `Error Percent`, all
acre-feet). A volume at an
arbitrary time, and a peak, must instead be built from `read_cell_volume_table` +
`interpolate_cell_volume` (both already exist) against the WSE at that time.

**DECIDED — the peak is the maximum of the TOTAL ponded-volume time series**,
i.e. a single physically consistent instant: "at some point in the run there was
this much water ponded in the area". NOT the sum of per-cell maxima, which is an
envelope that never actually occurred. The area is user-specified (defaults to the
interior drainage area), and each simulation finds its own peak independently in
time.

---

**E3. Two smaller items raised in the same conversation.**

**E3a. Surcharge along the profile — REFRAMED 2026-08-27, LARGELY ALREADY BUILT.**

Originally requested as "surcharge propagated N feet upstream". That framing was
abandoned once the profiles were plotted, because a single anchored number cannot
be defined without arbitrary choices, and the two real trunks examined broke it in
opposite directions:

  * on one trunk the surcharged reach began at the UPSTREAM end and stopped well
    short of the pump (the pump draws the wet well down), so a number anchored at
    the pump reports nothing useful;
  * on the other EVERY face was surcharged including the upstream end, so any
    number is a lower bound — the surcharge continues past the traced path.

**DECIDED — report surcharge as a SERIES along the whole profile, not a scalar.**
This dissolves the anchor question, the gap-tolerance question, and the
report-format question all at once: gaps and extents are simply visible in the
series, and any summary statistic a reader wants can be derived downstream
without the library having to pick a definition.

`PathProfile.is_surcharged` (boolean per face) already provides this. The only
work left is:

  * add `PathProfile.surcharge_margin` — `wse - crown`, positive when surcharged
    and negative freeboard when not. Strictly more informative than the boolean:
    it shows HOW MUCH, is continuous, and plots as a signed series against
    station. One line on the dataclass.
  * write both columns into the report alongside station / invert / crown / WSE.

**Still worth flagging in the output:** when the first or last face of a path is
surcharged, the reach extends beyond the traced path. That is a note on the
series endpoints, not a correction to a headline number, but a reader should not
have to notice it themselves.

**E3b. "Max HGL above rim at N nodes."** Needs (a) a max-over-time WSE for ALL
pipe nodes at once — from `.../Nodes/Water Surface`, since Summary Output's
`Maximum Water Surface` is per CELL, not per node — and (b) a rim accessor.

**DECIDED** — the rim source is user-selectable:

  * **(1) the node's terrain elevation** (`Geometry/Pipe Nodes/Attributes`
    `Terrain Elevation`, which equals `Invert Elevation + Depth` exactly), or
  * **(2) the terrain override elevation where one is set, falling back to the
    terrain elevation where it is not.**

`Terrain Elevation Override` is NaN on most nodes, so option (2) can differ from
option (1) at only a handful — small, but silent, so the two must be genuinely
distinct code paths and the output should report which rim source was used.

**DECIDED — report ALL nodes regardless of type, and include the node type as
a column** so the caller can filter afterwards. Do not pre-filter by type.

**DECIDED — "exceeded" is `>=`**, chosen to be conservative: a max HGL that
exactly reaches the rim counts as an exceedance. No additional tolerance.


---

**E4. The pumped-vs-gravity analysis script — DONE 2026-09-22.**

Built as `hack_ras_scripts/Results_Pipe_Profile_Comparison/gravity_vs_pumped.py` with
`config_gravity_vs_pumped.yaml`, comparing plan PAIRS inside one model. (A
sibling script comparing the same plan across two model DIRECTORIES existed
briefly and was deleted 2026-09-22 — its shape could not express the pairs, and
the two models it compared were gone.) Both requirements from the original entry
were met, plus three that only surfaced once real data was involved:

* **Routes are pinned, not discovered — and no longer retyped.** Originally the
  route was declared twice: `via` waypoints in each config, plus a hand-copied
  `expect_conduits` list. That left a one-directional hole — editing the
  waypoints in the exporter's config re-recorded the route silently, while
  editing them in the consumer's config aborted. Since 2026-09-22 the route is
  declared once, in `config_trunk_routes.yaml`, and crosses to the consumer as
  DATA: `export_trunk_paths.py` writes `trunk_routes.lock.yaml` via
  `ConduitPath.to_dict()`, and `gravity_vs_pumped.py` reads it and calls
  `verify_path` against EACH plan's own network before any results are read. The
  recipe (`segments` + `via`) travels with the answer (`conduits`), so the record
  re-runs and checks itself. The readable twin is the project's trunk-routes
  workbook.
* **A network fingerprint is provenance, not a contract.** The lock records
  `PipeNetwork.fingerprint()` for the geometry the routes were accepted against.
  It must NOT be enforced: the paired gravity and pumped geometries differ in
  node count, conduit count and total length by design, so a fingerprint gate
  would reject exactly the comparison the script exists for. Re-tracing per plan
  is both the weaker assumption and the stronger check.
* **Each side gets its own station column.** The two sides run different
  geometries, so the axes agree over the whole route EXCEPT the final conduit
  into the terminal node — the pumped geometry drops the outfall, which changes
  that conduit's face count. Measured: one trunk matched face-for-face
  (113 = 113), the other was 167 vs 166 with all three unmatched rows inside the
  last conduit. Each chart series therefore plots against its own side's
  stations.
* **The workbook is data only.** It carried a delta block and a formula
  summary until 2026-09-22; both were removed at the user's direction, along
  with the charts, because openpyxl cannot round-trip a chart and would destroy
  one on the next refresh. Charts and deltas now live in a companion workbook
  that references this one. The per-row station test survives in Python: the
  script still reports how many rows align within 0.01 ft and which conduit the
  rest fall in, on the console and the README, so nothing is ever subtracted
  across different stations.
* **Same-side axes are asserted.** All plans on one side share a geometry, so a
  differing axis there means something is wrong — the script exits rather than
  writing.

---

### F. Cross-mesh result comparison (`gis/wse_surface.py`) — DONE 2026-09-14

Requested by the user: compare two plans whose geometries do not share a mesh.

**Most of it already worked.** The output grid is snapped to the **terrain**, not
to the mesh, so two plans exported against the same terrain with the same forced
`bounds` land on an identical grid whatever their meshes look like, and
`difference_rasters` accepts them. What was actually built:

* **`same_mesh(hdf_a, hdf_b, areas=None, atol=1e-6)`** — do two plans compute on
  the same mesh, cell for cell? Compares area names, cell counts, and five arrays
  (`_MESH_IDENTITY_KEYS`) element for element. Within a tolerance, not exactly:
  two geometries written out separately differ in the last bits even when the mesh
  was never touched (Hillside g01 vs g03, worst 4e-9 ft over 4107 cells). Used to
  report which regime a run is in, and to gate anything that assumes shared cell
  indexing — a per-cell comparison between plans is meaningless when the cells are
  not the same cells.
* **`area_bounds` now takes several HDFs and unions them.** The runner was passing
  only the FIRST plan's bounds, which silently clipped wherever another plan's mesh
  reached further. That was a latent bug even on one mesh.
* **`export_wse_depth(allow_crs_mismatch=False)`** — the CRS guard, below.

**"No mesh" is treated as "dry", by the user's decision** (2026-09-14): when one
plan's mesh covers ground the other's does not, the difference there is reported as
a real change. The reasoning is that a mesh is typically extended *because* water
was seen creeping into new ground, so the earlier run's absence there is a genuine
"no water", not missing information. That decision removed the companion
mesh-footprint mask this item originally scoped — RAS Mapper already offers a way to
see where the footprints differ, so hack_ras does not duplicate it.

Tests: `tests/test_wse_surface.py` gained `TestSameMesh`, `TestCrossMeshBounds`,
`TestCrossMeshDifference` (19 with the CRS guard below). The differing-mesh fixture
is `Model.p07` / `Model.g05` — see the fixture table in `dev_rules.md`.

### G. Terrain CRS guard — DONE 2026-09-14

`export_wse_depth` refuses a terrain raster whose CRS differs from the model's.
The reference is the plan HDF's own root `Projection` attribute, so there is no
`.prj` hunting and no `.rasmap` parse.

The comparison is semantic (`pyproj.CRS.equals`), not string equality, because RAS
writes the ESRI dialect (`NAD_1983_StatePlane_Missouri_West_FIPS_2403`) and GDAL the
EPSG-style name (`NAD83 / Missouri West`) for the same system — verified no false
positive against Hillside's own terrain `.vrt` and a RAS Mapper result export.

It exists because this failure is silent: a terrain in the wrong CRS usually still
overlaps the mesh, so the "does not overlap" check never fires and every depth is
wrong by the offset between the two systems. The case that motivated it is real —
NAD83(HARN) vs NAD83(2011) for one state plane zone, about 3.5 ft apart on the PCA
model. Raises by default; `allow_crs_mismatch=True` overrides; a missing CRS on
either side warns and skips rather than failing, since that is absent metadata, not
evidence of a mismatch.

A companion guard comparing the terrain path against the geometry's own
`Terrain Filename` attribute was **considered and declined by the user** — pointing
the tool at a deliberately different terrain is a legitimate workflow.

---

## DONE (kept for design rationale)

### 2026-07-28 (session 17) — file-ops ergonomics, geometry subsystem, rasmap cleanup, inspector

All shipped; see `ai_context.md` session 17 for full details:
- **`project/geoms.py`** — geometry-file subsystem: `renumber_geom(s)`,
  `insert_geom_gap`, `compact_geoms`, `clone_geom`, `delete_geom` + bulk
  `delete_geoms`; plus `renumber_geoms_in_rasmap`.
- **`delete_plan` rasmap cleanup** (default on): removes RASPlan/RASResults
  layers, and — on `delete_unused_flow`/`delete_unused_geom` — the
  RASEventConditions / `<Geometries>` layers too. New rasmap ops
  `remove_plans_from_rasmap` / `remove_flows_from_rasmap` /
  `remove_geoms_from_rasmap` and `sort_rasmap_layers`. Fixes the number-reuse
  "zombie layer" problem.
- **`plans_with_unlisted_results`** + `result_plan_ids` — flag computed results
  RAS Mapper would append out of order.
- **`compact_plans`** + bulk **`delete_plans`** / **`delete_geoms`** (flexible
  id-spec, fail-fast).
- **Item A — read-only status/health inspector** (`project/health.py`:
  `project_health` → `ProjectHealth` + `format_health`; read-only rasmap queries
  `rasmap_layer_refs` / `result_plan_ids`).

### 2026-07-17 (session 13) — original six plan-file-op items

**ALL SIX ITEMS IMPLEMENTED** — see `ai_context.md` session 13 and the "Plan File
Operations" section for the shipped API. Kept below for design rationale. Both
validation follow-ups are resolved:
(a) `sort_prj_entries(project, kinds=...)` (project/sync.py) provides optional
ascending re-sorting of the .prj plan/geom/unsteady/steady lists (RAS accepts
non-sequential order, but its file-open dialogs present .prj order);
(b) stale rasmap layers after delete-then-reuse of a plan number were originally
declared out of scope (RAS Mapper self-heals), but the number-reuse zombie case
was later fixed properly in session 17 (delete_plan rasmap cleanup, above).

Approved-for-implementation decisions (2026-07-17): item 1 rewrites references and
renames artifacts by default (no warn-only mode) — "give the user what they asked
for"; arbitrary restart names like `banana.rst` carry no plan number and are left
alone. Item 2 is removal-only (clone_plan etc. already add entries when creating
files). Item 3 may use temp filenames like `Stream.p02.tmp` to break rename
cycles. Item 4 stays minimal. Item 5 warns, never blocks. Item 6 added same day at
the user's request. Items 1-5 all came out of the Model_PCA GMF_DFA plan
renumbering job, where each gap had to be worked around with one-off scripts.

#### 1. Restart-file awareness in `renumber_plan`
`.u` files embed plan numbers in restart references, e.g.
`Restart Filename=GMF_DFA.p06.02JAN2026 1200.rst`. Renaming a plan silently orphans
every such reference (and any `Base.pNN.<date>.rst` files on disk written by that
plan). Smallest useful version: scan the project's `.u` files and warn. Full
version: rewrite the references (and optionally rename matching `.rst` files).
This is the gap most likely to bite again.

#### 2. `.prj` sync/cleanup function
Remove `Plan File=` / `Geom File=` / `Unsteady File=` entries whose files do not
exist on disk. In the GMF_DFA job this was a hard prerequisite: `renumber_plan`
treats prj-listed-but-missing IDs as "in use", so no renumbering into those slots
was possible until the stale entries were removed.

#### 3. Bulk renumber from a mapping
`renumber_plans(project, {old: new, ...})` that validates the whole mapping up
front and computes a collision-free rename order (using temp names when the mapping
contains cycles). The GMF_DFA job needed ten renames hand-ordered so every target
was free when its turn came; a cycle (like the u-file rotation done later the same
day) additionally needs a temp name.

#### 4. `.rasmap` plan renumbering
Keep `<Plans>`/`<Results>` layer references in step when plans are renumbered:
remap `Filename` / `GeometryHDF` attribute tokens per the mapping. Verified
empirically (2026-07-17): display names self-heal on load (RAS Mapper reads titles
from the files), stale entries are flagged in the GUI and purgeable via
Tools > "remove missing layers", and hand-edited sections survive a GUI
save round-trip verbatim. Preserving a result layer's block through a rename only
matters when the renamed `pNN.hdf` actually exists.

#### 5. Breach-trigger validity check in `clone_plan`
Plan files store ALL breach trigger fields simultaneously; flags select which are
active (`Breach Start=F1,F2,F3,F4,F5,F6,F7,F8`: F1=True -> "WS Elev" mode,
F5=True -> "WS Elev + Duration" mode, both False -> "Set Time" mode using F3/F4).
`clone_plan` can swap the `Breach Start=` line via `line_edits`, but nothing warns
when a cloned plan's ACTIVE trigger is inconsistent with its new simulation window
— e.g. a Set Time trigger dated before the window start, which is exactly what
happened when Sunny-day breach plans were cloned into event windows in the GMF_DFA
job. Cheap check: parse the active trigger mode; if Set Time, verify the date/time
falls inside the plan's `Simulation Date=` window; warn otherwise.

#### 6. `delete_plan` — remove a plan and its outputs
Requested 2026-07-17. Delete a plan file plus everything keyed to its number:
`pNN.hdf`, `bNN`, `bcoNN`, `ic.oNN`, and `Base.pNN.*.rst` restart files, and the
`Plan File=` entry in the `.prj` (with `Current Plan=` fixup). Refuse when
`pNN.tmp.hdf` exists (a run is active). Optional flags delete the plan's `gNN` /
`uNN` (with their `.hdf` sidecars, and the geometry's `xNN`) when no other listed
plan references them. Warn when a surviving `.u` file's `Restart Filename=`
references the deleted plan's restart output. (The `.rasmap` was originally left
alone; session 17 later added the rasmap cleanup — see the DONE 2026-07-28 note.)
