# Developer Rules – hack_ras

## Package Dependencies
Do not install new packages or attempt to work around a missing package. If a package
would be useful, **ask the user to install it** and wait. The user runs Anaconda without
admin privileges and must handle installs themselves.

## Invariants — Do Not Break
- **Lossless roundtrip**: `GeometryFile.raw_lines` must be preserved exactly as read.
  Any parser change that drops or modifies raw lines will break the roundtrip test.
  Parse structured fields on top of raw lines, never instead of them.
- **Typed exceptions over None**: Functions that resolve files or look up data must raise
  a typed exception (`ValueError`, `GeometryFileNotFound`, etc.) on failure. Never return
  `None` to signal not-found.
- **No module-level mutable state**: Lookup tables are fine as private constants
  (`_KEYMAP`-style). Do not add variables that functions modify at runtime.

## Adding a New Geometry Block Type
1. Add a handler function in `hack_ras/geometry/blocks/` — one file per block type.
2. The function signature should return the parsed object and the number of lines consumed.
3. Dispatch to it from `GeometryParser.parse()` in `hack_ras/geometry/parser.py`.
4. Add a test using the fixture at `tests/data/Beaver/beaver.g01` or a new fixture in
   `tests/data/`.

Implemented block parsers (as of 2026-06-17):
- `blocks/xs_sta_elev.py` — `#Sta/Elev= N`: reads N station/elevation pairs from
  8-char fixed-width fields; returns `(List[Tuple[float,float]], lines_consumed)`.
  Populates `CrossSection.sta_elev`.
- `blocks/xs_ineff.py` — `#XS Ineff= N , flag` plus the immediately following
  `Permanent Ineff=` block: reads N×3 8-char fields (start_sta, end_sta, elevation),
  then T/F permanent flags; returns `(IneffFlowAreas, lines_consumed)`.
  Sentinel rules: blank station field → `0.0`; blank elevation field → `None`.
  Populates `CrossSection.ineff`.

## Mapping RAS Stations to GIS Coordinates
When a script needs to place station-referenced XS features (IFAs, bank stations,
Manning breaks, etc.) in GIS space, use the helpers in `hack_ras/geometry/xs_interp.py`:

```python
from hack_ras.geometry.xs_interp import station_to_xy, clip_xs_polyline

xy = station_to_xy(xs, station)                      # -> (x, y)
pts = clip_xs_polyline(xs, sta_start, sta_end)       # -> List[(x, y)]
```

**Why fractional mapping is required:** HEC-RAS stationing (from `#Sta/Elev=`) is
independent of the GIS cut-line arc length (from `XS GIS Cut Line`).  A station value
cannot be used as a direct arc-length offset.  `xs_interp.py` converts stations to a
fraction of the full XS station range, then walks that fraction of the cut-line arc
length.  Do NOT implement ad-hoc station-to-XY conversion in scripts — always use these
helpers to avoid subtle geometry errors.

## Adding a New HEC-RAS File Type
Create a new package under `hack_ras/` with:
- `model.py` — dataclasses for the parsed structure (always required)
- `parser.py` — parser that reads lines and populates the model (always required)
- `writer.py` — writer that reproduces the original file from raw lines (add only when a
  write-back use case exists; do not add preemptively)
- `blocks/` subpackage — add this when the file type has more than 2–3 distinct block
  formats; follow the `geometry/blocks/` pattern (one file per block type, return parsed
  object and lines consumed)

File discovery and ID resolution for new file types belong in `hack_ras/resolve.py`,
not inside the new package.

Follow the existing `project/` and `geometry/` packages as reference.

## Adding a New Results Reader Function
All HDF5 reader functions live in `hack_ras/results/reader.py`. Follow this pattern:

1. Add a dataclass in `hack_ras/results/model.py` if the function returns structured data.
2. Import the dataclass in `reader.py`.
3. Open the HDF file inside the function with `h5py.File(hdf_path, "r")` — do not hold
   files open across calls.
4. Convert HDF arrays to numpy immediately (`[:]` or `[()]`) so the file can be closed.
5. Use `KeyError` for missing HDF paths, `ValueError` for bad argument values (e.g.,
   unknown timestamp). Never silently return None.
6. Add a test in `tests/` using the real HDF fixture at
   `tests/data/2D culvert bridge levee precip pipes/Model.p02.hdf`.
   Use `@unittest.skipUnless(HAS_HDF, "…")` so CI without the fixture still passes.

## Test Fixtures
Real sample files live in `tests/data/`, organised into subfolders by model:

```
tests/data/
  Beaver/
    beaver.g01                          ← geometry parse / roundtrip tests
  Baxter/
    Baxter.g02                          ← XS GIS cut line shifting tests (georeferenced 1D model,
                                           Baxter River / Upper Reach / Tule Creek / Lower Reach,
                                           167 cut lines with projected coordinates)
  2D culvert bridge levee precip pipes/
    Model.p02.hdf                       ← results reader / pipe network tests
    Model.p02                           ← plan sidecar (used by read_plan_metadata)
    Features/
      Profile Lines.shp  (.dbf .shx .prj)  ← GIS profile line tests; line extends beyond mesh by design
```

Do not generate synthetic test data inline in tests unless the fixture is trivially small
(a few lines of key=value). When adding a new real-data fixture, place it in the
appropriate existing subfolder or create a new one named after the model.

## Git Workflow
- Do not push branches; the user pushes themselves.
- `git commit -am` only stages already-tracked files. New untracked files always need
  an explicit `git add` first (`git add .` from the repo root, then `git commit`).
