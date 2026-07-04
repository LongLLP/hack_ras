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
| `hack_ras/geometry/` | Parse and transform `.g##` geometry files; `shift.py` translates XS GIS cut lines along their alignment; `xs_interp.py` maps RAS station values to GIS cut-line XY coordinates |
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
*(Last updated: 2026-07-02, session 6)*
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
  PySide6 + pyqtgraph; uses the `xsedit_cf` conda environment. The `hack_ras` test suite
  uses the `Hillside_Levee` conda environment.
- `tests/test_geometry_merge.py` covers `write_merged_geometry` using Sterp Creek fixtures
  in the sibling `RAS_xsedit` repo. See `RAS_xsedit/tests/README.md` for how to add cases.
- **`SterpCreek.g03` (in RAS_xsedit `tests/data/Sterp Creek/`) is stale** — it predates the
  session 5 redesign (H Scale removal, always-interpolate-at-breakpoints, rounding,
  bank/Manning's snapping) and will not byte-for-byte match new output.
  `test_merge_matches_known_good_output` is expected to fail until that fixture is
  regenerated; the other tests in `test_geometry_merge.py` don't depend on exact byte
  content and pass.  Effective baseline: 102 passing, 1 known-failing (103 total,
  after the five session-7 regression tests).
- Test coverage for `project/catalog.py` and `utils/` modules not yet written

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

**Known bug, not yet fixed — shift.py cutline line wrap.** `_format_xs_gis_lines`
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

### `#Block Obstruct=` (Blocked Obstructions)

Still parsed into `raw_lines` and passed through verbatim from source A unconditionally
via `_scan_xs_content` — the same state IFAs were in before session 6 (see above). The
same pattern (a `MergeConfig.obstruct_source` field, a `_KEY_PREFIXES`/`_KEY_PARSERS`
entry, a `merge_obstruct()`-shaped function reusing `_write_triplet_lines()`, GUI wiring,
an `_is_trivial_config` check) would apply if support is added later — but note this
block's own format conventions (sentinel fields, if any) haven't been verified against
real HEC-RAS output the way Manning's n and IFAs now have, and shouldn't be assumed to
match either one without doing that verification first.

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
