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
| `hack_ras/geometry/` | Parse `.g##` geometry files (rivers, reaches, cross-sections, GIS cut lines) |
| `hack_ras/results/` | Read plan HDF5 files — cell geometry, WSE, volume tables, pipe networks |
| `hack_ras/gis/` | GIS operations — profile line sampling, station computation |
| `hack_ras/utils/` | Shared utilities (logging, line helpers) |
| `hack_ras/resolve.py` | File discovery and ID resolution (lower-level module) |

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

### Water Surface Elevation Results
Base path: `Results/Unsteady/Output/Output Blocks/Base Output/`

| Path (relative to base) | Shape | Notes |
|--------------------------|-------|-------|
| `Summary Output/2D Flow Areas/{area}/Maximum Water Surface` | (2, N) | Row 0 = max WSE (sub-step accuracy, may exceed any time-series value); row 1 = time of maximum as **decimal days from midnight** of the simulation start date |
| `Unsteady Time Series/2D Flow Areas/{area}/Water Surface` | (T, N) | WSE at every output time step |
| `Unsteady Time Series/Time Date Stamp` | (T,) | Timestamp strings: `'01Jan2025 00:30:00'` |

WSE type options (used throughout `results.reader`):
- `"Maximum"` — reads Summary Output row 0 (sub-step accuracy, may exceed any single time step)
- `"Maximum from Time Series"` — `nanmax` across the full time series
- `"<timestamp>"` — match against Time Date Stamp array (case-insensitive)

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
`(station[j] + station[j+1]) / 2`. Each unique cell's representative station = mean of
midpoints across all segments where that cell appears. Use `list_sa2d_connections()` and
`read_sa2d_connection()` from `hack_ras.results.reader` to get a typed `Sa2dConnection`
object with `hw_cells` / `tw_cells` lists sorted by station.

### Pipe Network Geometry & Results
```
Geometry/Pipe Networks/{network}/Node Indices      # global→local mapping
Geometry/Pipe Networks/{network}/Conduit Indices
Geometry/Pipe Nodes/Attributes                     # structured array: Name, System Name
Geometry/Pipe Conduits/Attributes                  # structured array: Name, US Node, DS Node

Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Depth         (T, N_nodes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Water Surface  (T, N_nodes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Nodes/Top + Side Inlet Flow
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Pipe Flow DS   (T, N_pipes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Pipe Flow US   (T, N_pipes)
Results/…/Unsteady Time Series/Pipe Networks/{network}/Pipes/Vel US / Vel DS
```

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

## Current Work
*(Last updated: 2026-05-26)*
- `results/`, `gis/`, and `project/` packages are complete and in production use
- `RasProject` is the stable top-level entry point; user scripts reference a `.prj` path
- Completing cross-section data parsing: Sta/Elev, Manning, bank stations, and
  inefficiency blocks are recognised as "to do" in `geometry/parser.py` but currently skipped
- Test coverage for `project/catalog.py` and `utils/` modules not yet written

## Known Constraints
- Windows environment
- No admin privileges
