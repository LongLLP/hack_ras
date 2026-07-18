# Developer Rules – hack_ras

## Package Dependencies
Do not install new packages or attempt to work around a missing package. If a package
would be useful, **ask the user to install it** and wait. The user runs Anaconda without
admin privileges and must handle installs themselves.

## Run Tests After Every Code Change

After any edit to `hack_ras`, run the full test suite before reporting the task complete.
Use the `Hillside_Levee` conda environment (the base env is missing h5py/geopandas/shapely):

```
cd C:\Users\2161jap\Desktop\hack_ras_local\hack_ras
pytest tests\
```

All tests must pass. The baseline is 152 passing tests (plus any added in the current
session). If a new test is added, the new count becomes the baseline.

The geometry merge tests (`test_geometry_merge.py`) require the sibling `RAS_xsedit`
repo to be present. See `RAS_xsedit/tests/README.md` for how those fixtures are
organised and how to add new merge test cases.

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

If the block will be written (not just parsed), the write function belongs in the same
block module as its parser, so read/write format knowledge stays in one place —
`write_sta_elev()`, `write_mann()`, `write_ineff()`, and `write_bank_sta()` follow this
pattern. Shared 8-char fixed-width helpers (`_fmt`, `_fmt_or_blank`,
`_write_triplet_lines`) live in `blocks/base.py` next to `read_fixed_fields`.

Implemented block parsers (as of 2026-06-23):
- `blocks/xs_sta_elev.py` — `#Sta/Elev= N`: reads N station/elevation pairs from
  8-char fixed-width fields; returns `(List[Tuple[float,float]], lines_consumed)`.
  Populates `CrossSection.sta_elev`.
- `blocks/xs_ineff.py` — `#XS Ineff= N , flag` plus the immediately following
  `Permanent Ineff=` block: reads N×3 8-char fields (start_sta, end_sta, elevation),
  then T/F permanent flags; returns `(IneffFlowAreas, lines_consumed)`.
  Sentinel rules: blank station field → `0.0`; blank elevation field → `None`.
  Populates `CrossSection.ineff`.
- `blocks/xs_mann.py` — `#Mann= N , method , 0`: all methods use the same
  `(station, n_value, position_code)` triplet format in 8-char fixed-width
  fields; position_code is discarded.  Returns
  `(ManningDef(method=<int>, entries=[(station, n_value), …]), consumed)`.
  method=0 → "Horizontal Variation" OFF, always N=3, stations at XS-left/left-bank/
  right-bank (LOB/Channel/ROB).  method=-1 or method=1 → "Horizontal Variation" ON,
  arbitrary N entries.  Write -1 for any new horizontal variation output.
  Populates `CrossSection.manning_def`.
- `blocks/xs_bank_sta.py` — `Bank Sta=left,right`: single-line parse; returns
  `((float, float), 1)`.  Populates `CrossSection.bank_stations`.

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
Real sample files live in `tests/data/`, organised into subfolders by model.

```
tests/data/
  Beaver/
    beaver.g01                          ← geometry parse / roundtrip tests
  Baxter/
    Baxter.g02                          ← XS GIS cut line shifting tests (georeferenced 1D model,
                                           Baxter River / Upper Reach / Tule Creek / Lower Reach,
                                           167 cut lines with projected coordinates)
  2D culvert bridge levee precip pipes/
    Model.prj / .p02 .p04 .p05 / .g02-.g03 / .u02 .u04   ← full runnable mini model, rebuilt by
                                           the user in the RAS 7.0 GUI (2026-07-17): p02 has a
                                           LEVEE BREACH (WS Elev trigger with blank inactive
                                           date fields) and writes a restart; u04 consumes
                                           p04's restart; every plan run so the real artifacts
                                           exist (.b##, .bco##, .ic.o##, .p##.hdf, .rst,
                                           .rasmap, .x##). LOAD-BEARING STALE STATE: the .prj
                                           deliberately still lists 'Plan File=p03' and
                                           'Unsteady File=u03' whose files were deleted in the
                                           GUI — the plan-ops integration tests
                                           (test_plan_ops_fixture.py, run on a temp copy) rely
                                           on that stale state to exercise sync_prj on a
                                           genuine GUI leftover. Do not "clean up" those
                                           entries. (.rasmap.backup / .dss / .tmp.hdf are
                                           gitignored — regenerated by RAS, never committed.)
    Model.p02.hdf                       ← results reader / pipe network tests
    Model.p02                           ← plan sidecar (used by read_plan_metadata)
    Terrain/_ESRI projection StatePlane.prj  ← CRS-resolution tests (find_crs_prj via rasmap;
                                           ESRI-prj-rejection test)
    Features/
      Profile Lines.shp  (.dbf .shx .prj)  ← GIS profile line tests; line extends beyond mesh by design
  XSCutLines stress test/
    XSCut_stress_test.g01               ← HEC-RAS-authored cut line format fixture: three XS
                                           (11/9/12-point cut lines at 7-digit, 5-digit, and
                                           2-digit coordinates), incl. fully packed 16-char
                                           fields with no whitespace; cut line writer tests
    XSCut_stress_test.g02 / .g03        ← g01 shifted right/left with the fixed shifter
                                           (RS 3000 ±100 ft, RS 2000 ±105 ft, RS 1000 ±10 ft);
                                           user-verified to display correctly in HEC-RAS
    XS_Cutline_input.csv                ← the exact values typed into the RAS GUI (some with
                                           more decimals than a field holds — documents RAS's
                                           truncate-to-fit behavior)
  Massive XS stations/
    Massive.g01                         ← HEC-RAS-authored stretched-stationing fixture:
                                           RS 1000 (normal, -350..777.71) and RS 500 (same XS
                                           with LOB/Ch/ROB scaled x1000 in RAS: -350..1127361,
                                           banks 451530.8/474140.8, organically packed 8-char
                                           #Sta/Elev fields); bank-station shortening test
    RS_input_to_RAS.xlsx                ← the exact values typed into the RAS GUI (banks
                                           entered as 451530.795/474140.825 — RAS ROUNDED them
                                           to fit the 8-char field, unlike the 16-char cut
                                           line fields where it truncates)
```

Geometry merge tests reference fixtures from the sibling `RAS_xsedit` repo via a
relative path — do NOT copy those files into `hack_ras/tests/data/`. See
`RAS_xsedit/tests/README.md` for the full fixture layout and instructions for
adding new merge test cases.

Do not generate synthetic test data inline in tests unless the fixture is trivially small
(a few lines of key=value). When adding a new real-data fixture, place it in the
appropriate existing subfolder or create a new one named after the model.

## Git Workflow
- Do not push or pull unless explicitly asked by the user.
- `git commit -am` only stages already-tracked files. New untracked files always need
  an explicit `git add` first (`git add .` from the repo root, then `git commit`).
