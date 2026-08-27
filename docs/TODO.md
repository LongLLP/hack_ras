# hack_ras To-Do List

Discuss scope with the user before implementing any item (see `dev_rules.md`;
the user has asked to be consulted before hack_ras changes).

---

## OPEN ITEMS

Four items are open. In rough priority:

1. **Blocked Obstruction / Levee writer + `merge.py` support** — §A below.
2. **Interior flood-volume peak (E2) and the analysis script (E4)** — §E below.
3. **Dry-run / preview on the mutating ops** (LOW PRIORITY) — §B below.

**Built 2026-08-27:** `read_pump_curves` / `pump_station_capacity` (E1's data
layer), `read_node_rims`, `read_node_max_wse`, `read_volume_accounting` /
`list_volume_accounting`, and `PathProfile.surcharge_margin` (E3a complete). What
remains of E1 is the metric on top of the readers; E2 needs the peak computation;
E3b needs the comparison on top of its two readers.

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

**E4. The pumped-vs-gravity analysis script.** Phase 3, still to build. A new
config-driven folder under `Scripts/`, comparing plan PAIRS within ONE model
(Existing vs Future, pumped vs gravity) rather than two models. Two things to
build in from the start:

  * **Persist the `ConduitPath` conduit list in the output**, so a later run can
    pin the identical route with one line instead of re-deriving it. Node
    sequences are already recoverable from an existing workbook's Node column,
    but the conduit list is the exact handle.
  * **Match by NAME across the paired geometries** — a pumped geometry drops the
    outfall conduits, so row-position matching will not work. `read_path_profile`
    already raises rather than reading silently when a path's conduits are absent
    from a plan.

The existing `Scripts/Pipe_Profile_Comparison` workbook machinery (sheets, chart
styling, delta blocks) carries over; only the comparison axis changes.

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
