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

### SA 2D Area Conn Results
Base path: `Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series/SA 2D Area Conn/{connection}/`

SA 2D Area Conn features (levees, lateral structures) have **no Summary Output group** — only
time-series data. Time of maximum WSE must be derived from the time series via `nanargmax`.

| Dataset | Shape | Notes |
|---------|-------|-------|
| `Headwater Cells` | (N_hw,) int32 | Unique cell indices in the 2D mesh on the upstream/HW side |
| `Tailwater Cells` | (N_tw,) int32 | Unique cell indices on the downstream/TW side |
| `HW TW Cells/Water Surface HW Cells` | (T, N_hw) float32 | WSE time series per unique HW cell |
| `HW TW Cells/Water Surface TW Cells` | (T, N_tw) float32 | WSE time series per unique TW cell |
| `HW TW Segments/HW TW Station` | (N_segs+1,) float32 | Face-point stations along the structure |
| `HW TW Segments/Headwater Cells` | (N_segs,) \|S10 | Cell index string per segment, e.g. `b'1008'` |
| `HW TW Segments/Tailwater Cells` | (N_segs,) \|S10 | Same, TW side |

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
*(Last updated: 2026-05-22)*
- `results/`, `gis/`, and `project/` packages are complete and in production use
- `RasProject` is the stable top-level entry point; user scripts reference a `.prj` path
- Completing cross-section data parsing: Sta/Elev, Manning, bank stations, and
  inefficiency blocks are recognised as "to do" in `geometry/parser.py` but currently skipped
- Test coverage for `project/catalog.py` and `utils/` modules not yet written

## Known Constraints
- Windows environment
- No admin privileges
