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
| `hack_ras/project/` | Parse `.prj` project files; `ProjectModel` dataclass |
| `hack_ras/geometry/` | Parse and transform `.g##` geometry files; `shift.py` translates XS GIS cut lines along their alignment |
| `hack_ras/results/` | Read plan HDF5 files — cell geometry, WSE, volume tables, pipe networks |
| `hack_ras/gis/` | GIS operations — profile line sampling, station computation |
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
  parsed on top of the raw lines, not instead of them
- **Typed exceptions over None**: resolution and lookup functions raise typed exceptions
  (`ValueError`, `GeometryFileNotFound`, etc.) rather than returning `None`
- **`.prj` is authoritative**: the project file is the definitive list of which files belong
  to a project. Files that exist on disk but are not referenced by the `.prj` (orphans) are
  not part of the project and are silently excluded from all discovery.

## File Naming Conventions
HEC-RAS uses a base name plus a typed numeric suffix:
| Suffix | File type |
|--------|-----------|
| `.prj` | Project file (key=value pairs, links to all others) |
| `.g##` | Geometry file (rivers, reaches, cross-sections, GIS cut lines) |
| `.p##` | Plan file (text sidecar — plan title, geometry reference) |
| `.p##.hdf` | Plan results file (HDF5 — cell geometry, WSE, pipe network results) |
| `.u##` | Unsteady flow file |
| `.f##` | Steady flow file |

`##` is a two-digit number (`01`, `02`, …). A project may have multiple geometry or plan files.
Multiple plans may share the same geometry (same `g##` ID), which is important for grouping.

## Project Entry Point — `RasProject`

`from hack_ras import RasProject` is the recommended way to work with a HEC-RAS project.
Pass the absolute path to the `.prj` file; `ValueError` is raised if the file is missing
or is not a HEC-RAS project (e.g. an ESRI shapefile projection file with the same extension).

```python
project = RasProject(r"C:\path\to\NKC_Hillside_Levee.prj")
project.folder        # directory containing the .prj
project.base_name     # "NKC_Hillside_Levee"
project.title         # project title string from the .prj
project.model         # ProjectModel — parsed .prj content (list fields below)
project.plan_hdfs()               # all .p##.hdf files listed in the .prj that exist on disk
project.plan_hdfs(['p14','p15'])   # filtered subset
project.crs_prj()     # ESRI .prj CRS file (via RAS Mapper or folder search)
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
- `unsteady_file_ids: list[str]` — all unsteady flow IDs

Scalar fields (`title`, `y_axis_title`, etc.) work as before.
`resolve_filenames(basename_map)` maps all IDs of each type to filenames, returning
`{'geom': [...], 'plan': [...], 'unsteady': [...]}`.

## Parsing Strategy — Geometry
- **Block-driven**: `GeometryParser` dispatches to specialized handlers per block type
  (`River Reach=`, `Type RM Length=`, `XS GIS Cut Line=`, etc.)
- **Lossless roundtrip**: `GeometryFile` stores original raw lines; `GeometryWriter`
  writes them back unchanged.
- **Strict errors**: resolution functions raise typed exceptions rather than `None`.

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
| `Cells Volume Elevation Info` | (N, 2) | int32 | Per-cell `[start_idx, count]` into Values array; count=0 for perimeter dummies |
| `Cells Volume Elevation Values` | (total_pairs, 2) | float32 | Packed `[elevation, volume]` pairs for all cells |

Key facts:
- **Perimeter dummy cells** have NaN `Cells Minimum Elevation` and `count=0` in the Info
  array. They must be excluded from profile output and spatial queries.
- `Cells Minimum Elevation` is bit-for-bit identical to the first elevation entry in each
  cell's volume table — confirmed empirically. The two datasets are redundant by design.
- Cell polygons are reconstructed from `FacePoints Coordinate[Cells FacePoint Indexes[i]]`
  (strip negative padding indices before building the `Polygon`).

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
- `cell_plan_area` comes from `AreaGeometry.polygons[cell_idx].area` (shapely Polygon area
  in the model's projected coordinate units).

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
Base path: `Results/.../Unsteady Time Series/2D Bridges/{bridge_name}/`

2D Bridges are road bridges modelled inside a 2D flow area.

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Headwater Cells` | (N_hw,) int32 | Upstream face cells |
| `Tailwater Cells` | (N_tw,) int32 | Downstream face cells |
| `Cell WS US` | (T, N_hw) | WSE on the upstream side of each bridge cell |
| `Cell WS DS` | (T, N_tw) | WSE on the downstream side |
| `Face Flow` | (T, N_faces) | Flow through each bridge face |
| `Structure Variables` | (T, 6) | Flow (cfs), Stage HW, Stage TW, Head loss, Drag Factor, Error HW |

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

### Results/Summary (run-level metadata)
Path: `Results/Summary/` — **attributes** on this group (not datasets):

| Attribute | Example | Notes |
|-----------|---------|-------|
| `Solution` | `'Unsteady Finished Successfully'` | Check first to confirm a run completed |
| `Computation Time Total` | `'00:05:45'` | Wall-clock runtime (HH:MM:SS) |
| `Maximum WSEL Error` | `0.0` | Max residual at end of run |
| `Run Time Window` | `'20MAY2026 14:52:34 to …'` | When RAS executed the plan |
| `Time Solution Went Unstable` | `nan` | NaN if stable throughout |

Volume accounting totals are nested in `Results/Summary/Volume Accounting/` and its
sub-groups (`Volume Accounting 2D/{area}/`, `Volume Accounting Pipe Networks/{network}/`):
`Cum Inflow`, `Cum Outflow`, `Error`, `Error Percent`, `Vol Ending` — all float32, acre-ft.

## Results Package API (`hack_ras/results/`)

### `model.py` — dataclasses

| Class | Fields | Notes |
|-------|--------|-------|
| `PlanMetadata` | `geom_id: str`, `plan_title: str` | Parsed from `.p##` text sidecar |
| `AreaGeometry` | `cell_centers (N,2)`, `min_elevations (N,)`, `polygons list`, `boundary Polygon`, `cell_gdf GeoDataFrame` | Non-dummy cells only in `cell_gdf`; `polygons[i]` is `None` if fewer than 3 face points |
| `CellVolumeTable` | `info (N_cells,2) int32`, `values (total_pairs,2) float32` | `info[i] = [start, count]`; `values[:,0]` = elevation, `values[:,1]` = volume |
| `Sa2dCell` | `cell_idx: int`, `station: float`, `wse (T,) float64`, `station_start: float`, `station_end: float` | `station` = mean of segment midpoint stations (center); `station_start`/`station_end` = min/max face-point stations bounding the cell's segments; default `nan` |
| `Sa2dConnection` | `name: str`, `timestamps (T,) str`, `hw_cells list[Sa2dCell]`, `tw_cells list[Sa2dCell]` | Both cell lists sorted by station ascending |
| `PipeNode` | `name: str`, `system_name: str` | From `Geometry/Pipe Nodes/Attributes` |
| `PipeConduit` | `name: str`, `us_node: str`, `ds_node: str` | From `Geometry/Pipe Conduits/Attributes` |
| `PipeNetwork` | `name`, `nodes dict[str,int]`, `conduits dict[str,PipeConduit]`, `conduit_index dict[str,int]`, `upstream_of dict`, `downstream_of dict` | `nodes[name]` → results column index |
| `NodeTimeSeries` | `timestamps`, `depth`, `wse`, `inlet_flow`, `flow_in`, `flow_out` — all `(T,) float64` | `flow_in` = sum of `Pipe Flow DS` for conduits draining into node |
| `ConduitTimeSeries` | `timestamps`, `flow_us`, `flow_ds`, `vel_us`, `vel_ds` — all `(T,) float64` | US/DS ends of the conduit |

### `reader.py` — public functions

#### Discovery
| Function | Returns | Notes |
|----------|---------|-------|
| `list_areas(hdf_path)` | `list[str]` | Names of 2D flow areas; empty list if none |
| `list_sa2d_connections(hdf_path)` | `list[str]` | Names of SA 2D Area Conn groups; empty if none |
| `list_pipe_networks(hdf_path)` | `list[str]` | Names of pipe networks; empty if none |

#### Plan / geometry
| Function | Returns | Notes |
|----------|---------|-------|
| `read_plan_metadata(hdf_path)` | `PlanMetadata` | Parses `.p##` text sidecar; raises `FileNotFoundError` if missing |
| `read_area_geometry(hdf_path, area)` | `AreaGeometry` | Reads cell centres, face-point polygons, perimeter, min elevation; excludes perimeter dummy cells from `cell_gdf` |
| `read_cell_volume_table(hdf_path, area)` | `CellVolumeTable` | Raw info + values arrays; use `interpolate_cell_volume` to query |
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
| `read_sa2d_connection(hdf_path, connection)` | `Sa2dConnection` | HW and TW cell WSE time series + stations; cells sorted by station |
| `read_sa2d_areas(hdf_path, connection)` | `tuple[str, str]` | `(hw_area, tw_area)` — looks up `US SA/2D` / `DS SA/2D` via `SNN ID == Node Pointer` |

#### Pipe networks
| Function | Returns | Notes |
|----------|---------|-------|
| `read_pipe_network(hdf_path, network)` | `PipeNetwork` | Geometry, index maps, adjacency dicts for one network |
| `read_node_timeseries(hdf_path, network, node_name)` | `NodeTimeSeries` | Depth, WSE, inlet flow, computed flow_in / flow_out |
| `read_conduit_timeseries(hdf_path, network, conduit_name)` | `ConduitTimeSeries` | Flow and velocity at US and DS ends |

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
`Hillside_Levee_Scripts/XS GIS Shifter/shift_xs_gis.py` (YAML-configured,
`python shift_xs_gis.py config.yaml`).

Config keys: `prj_path`, `geom_in` (e.g. `g16`), `geom_out` (e.g. `g17`),
`excel_path`, `geom_name_out` (required — see HEC-RAS title uniqueness note
below).  The script resolves full geometry paths from the project file via
`resolve_id`, writes the new geometry, and appends `Geom File=<geom_out>` to
the `.prj` so HEC-RAS recognises the new file without a manual edit.

## Current Work
*(Last updated: 2026-05-28)*
- `results/`, `gis/`, `project/`, and `geometry/shift` packages are complete and in production use
- `RasProject` is the stable top-level entry point; user scripts reference a `.prj` path
- Completing cross-section data parsing: Sta/Elev, Manning, bank stations, and
  inefficiency blocks are recognised as "to do" in `geometry/parser.py` but currently skipped
- Test coverage for `project/catalog.py` and `utils/` modules not yet written

## Known Constraints
- Windows environment
- No admin privileges
