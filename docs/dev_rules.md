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

All tests must pass. The baseline is 786 passing tests (plus any added in the current
session), and 1 skipped by design — a 5.0.3 fixture HDF that has no culvert table.
If a new test is added, the new count becomes the baseline.

The geometry merge tests (`test_geometry_merge.py`) require the sibling `RAS_xsedit`
repo to be present. See `RAS_xsedit/tests/README.md` for how those fixtures are
organised and how to add new merge test cases.

## Invariants — Do Not Break
- **Lossless roundtrip**: `GeometryFile.raw_lines` must be preserved exactly as read.
  Any parser change that drops or modifies raw lines will break the roundtrip test.
  Parse structured fields on top of raw lines, never instead of them.
  `utils/lines.py` holds the same line for a read/write cycle: a leading UTF-8 BOM is
  stripped on read (so line-1 key matches work) and re-attached on write when the
  destination had one, so rewriting a file in place is byte-identical. **Do not switch
  `write_lines` to a write-to-temp-then-rename scheme without moving the BOM lookup to
  the final path** — the temp file has no BOM to find and preservation silently breaks.
  `tests/test_bom_handling.py` guards this.
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
- `blocks/connection.py` — the `Connection=` block family (SA/2D connections).
  Header gives name + label anchor (a GUI draw position, NOT a geometry point);
  `Connection Line=` is the 16-char XY layout of `XS GIS Cut Line=` and
  `Conn Weir SE=` / `Connection Centerline Profile=` the 8-char layout of
  `#Sta/Elev=`, so both delegate to those parsers rather than re-implementing the
  field mechanics.  Populates `GeometryFile.connections` (a dict keyed by name).
  Station mapping lives in `geometry/conn_interp.py` — see **Mapping RAS Stations
  to GIS Coordinates**, and note it is NOT the cross-section rule.

## Comparing River / Reach Names — always normalize both sides

```python
from hack_ras.utils.names import normalize_name
if normalize_name(typed) == normalize_name(from_file): ...
```

HEC-RAS stores river and reach names in fixed-width fields, so they arrive padded
**on the inside too**: a reach the GUI shows as "Upper Reach B" is written
`'Upper Reach  B'` with two interior spaces (real case, Starkweather
`StarkweatherW`). Never compare raw strings, and never normalize only the value
read from the file — a hand-typed name in a spreadsheet or YAML config must not
have to reproduce RAS's padding. Getting this wrong fails *silently*: the lookup
misses and the caller falls through to a default that often looks plausible.

`normalize_name` strips outer whitespace, collapses interior whitespace runs, and
case-folds. `geometry.shift._normalize_names` and `results.model._normalize_name`
are aliases of it; `tests/test_names.py` pins them so they cannot drift.

## Mapping RAS Stations to GIS Coordinates
**Cross sections and SA/2D connections use opposite rules. Pick the right module.**

### Cross sections — fractional (`geometry/xs_interp.py`)
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

### SA/2D connections — direct arc length (`geometry/conn_interp.py`)
```python
from hack_ras.geometry import conn_interp

xy   = conn_interp.station_to_xy(conn, station)       # -> (x, y)
pts  = conn_interp.clip_polyline(conn, sta0, sta1)    # -> List[(x, y)]
elev = conn_interp.elev_at(conn, station)             # weir crest elevation
ok   = conn_interp.stationing_ok(conn)                # RAS's length contract
```

A connection's `Conn Weir SE=` stationing **is** arc length along its
`Connection Line=` polyline, so the fractional mapping must NOT be used here.  HEC-RAS
requires the station/elevation length to match the GIS centerline length to within
**1 ft or 0.5%, whichever is smaller**, and refuses to run the model otherwise — GIS was
bolted onto 1D modeling after the fact, while the 2D side was built with a tight
GIS-to-model coupling.  Verified deliberately: truncating a levee's weir profile made
RAS reject the geometry.

The rule applies to **weir-mode connections only** (`Conn Routing Type= 1`,
`Mode = Weir/Gate/Culverts`).  Surveyed over all 221 connections in Model_Hillside,
Model_PCA, Model_LAX and the test fixtures: 178 of 178 weir-mode connections comply,
worst drift 0.032 ft; 12 of 32 `Bridge Opening` connections (`Conn Routing Type= 32`)
violate it — five in LAX_River_2D.g01/g12 by up to 21.9 ft and GMF_DFA.g01/g02's
`CTHE` by 419.7 ft — in geometries that run fine;
their station/elevation data is a bridge opening profile, not a spillway spanning the
line.  `stationing_enforced()` encodes that split, and an unrecognized routing type is
reported as unenforced rather than failed.

The named violators are a **dated snapshot** of live project models that keep being
edited — re-run the survey rather than trusting the counts.  The weir-mode/bridge-mode
split is the stable part, and the only part the code depends on.

Placement functions clamp rather than validate; call `stationing_ok()` (flag) or
`check_stationing()` (raise) yourself when a bad geometry should be surfaced.

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
    Model.g04 / .g04.hdf                ← carbon copy of g02 with "Use Momentum (GIS/2D only)"
                                           checked in the RAS 7.0 GUI (2026-09-04). The ONLY
                                           diff from g02 is the culvert line's LAST field
                                           (0 -> -1); the HDF stores that same flag as 1. Ground
                                           truth for the ASCII-vs-HDF culvert cross-check.
    Model.p02.hdf                       ← results reader / pipe network tests
    Model.p02                           ← plan sidecar (used by read_plan_metadata)
    Model.p06 / .p06.hdf / .rst         ← BREACH FIELD FINGERPRINT, authored in the RAS
                                           7.0 GUI (2026-09-08) so every breach control
                                           holds a distinct, identifiable value:
                                           Center Station 1234, Final Bottom Width 111,
                                           Final Bottom Elevation 742, Left Side Slope 2,
                                           Right Side Slope 3 (ASYMMETRIC ON PURPOSE —
                                           this is what pins Breach Geom field order and
                                           exercises the top-width geometry, since every
                                           other breach fixture has vertical walls),
                                           Failure Mode Piping, Piping Coefficient 0.44,
                                           Initial Piping Elev 750.5, Formation Time
                                           3.75 hr, Breach Weir Coef 2.11, Set Time
                                           trigger. Breaches the `Levee` connection on
                                           g02, and uses u02 (shared with p02/p04).
                                           test_breach.py asserts these GUI values, so it
                                           checks the decoder against HEC-RAS itself.
                                           The weir breach sits LOWER than the cells it
                                           connects — RAS warns and runs anyway — and the
                                           run ends before the breach finishes forming, so
                                           its realised side slopes (0.61/0.92) are
                                           partway to 2/3: the fixture that proves the
                                           realised breach is the widest state REACHED,
                                           not the plan's terminal geometry.
                                           Registered in the .rasmap Results tree like
                                           every other run plan, so the untouched fixture
                                           reports no unlisted results; the tests that
                                           need a flagged plan still make one by calling
                                           remove_plans_from_rasmap on a temp copy.
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
  Wisconsin Floodway/
    SterpCreek.g02 / .p02 / .p02.hdf     ← carbon copy of g01/p01 built in RAS 5.0.3, then
                                           run in RAS 7.0 -> a real 7.0 steady results fixture
                                           (compound geometry layout: Cross Sections/Attributes,
                                           no flat River Names). Same features as g01. Used to
                                           exercise the version-aware WSE reader's compound path
                                           on genuine data (test_levee_obstruct parameterizes the
                                           active-flow check over both 5.0.3 p01 and 7.0 p02).
    SterpCreek.g01 / .p01 / .p01.hdf / .prj / .f01   ← RAS 5.0.3 steady model; the
                                           'Sterp West / Upper' reach carries hand-placed
                                           LEVEES (RS 43320 both, 42528 L overtopped, 40641 R
                                           behind IFA), a NORMAL blocked obstruction (41868 R,
                                           overtopped), a MULTIPLE-BLOCK obstruction (40813,
                                           one submerged + one piercing), and a multiple-block
                                           IFA (41868 L). Ground truth for the active-flow
                                           tests = plan HDF Additional Variables/Top Width
                                           Total (= active top width). Feature behaviour is
                                           documented in Model_DCRA/Images (screenshots +
                                           Image log.xlsx). Two Type-3 bridge nodes (43084,
                                           40447) have no #Sta/Elev and are skipped by the
                                           active-flow check. Used by test_levee_obstruct.py.
    SterpCreek.g03 / .g03.hdf           ← CULVERT FORMAT fixture, authored in the RAS 7.0 GUI
                                           (2026-09-04) then TRUNCATED in the GUI to 3 reaches
                                           / 127 XS, keeping all 9 culvert groups
                                           (5 `Culvert=` + 4 `Multiple Barrel Culv=`). Covers
                                           every ASCII culvert case in one file: a 7-barrel
                                           group (RS 26212 — RAS REWROTE the keyword from
                                           `Culvert=` to `Multiple Barrel Culv=` when barrels
                                           went past 1, and its barrel NAME rows came back
                                           reordered), Use Momentum on (27637; ASCII -1 /
                                           HDF 1), Solution Criteria = Inlet Control (32233)
                                           and Outlet Control (33759), a box->circular
                                           conversion (25772), and a from-scratch group
                                           'hack_testing' (23612) whose every field was given a
                                           distinct value so each position is individually
                                           identifiable. Deliberately unused by any plan.
                                           NOTE: the box->circular switch first wrote a BLANK
                                           span (`1,3,,59`), but the truncation re-save
                                           NORMALIZED every blank circular span to equal rise
                                           (`1,3,3,59`) — so this fixture no longer covers the
                                           blank-span parse case; cover that with a synthetic
                                           one-line `Culvert=` in the test.
    SterpCreek.O01 / .O02 / .r01 / .r02  ← the STEADY run artifacts RAS wrote for p01/p02.
                                           LOAD-BEARING: test_steady_flow.py uses this model
                                           (on a temp copy) as the only steady fixture that
                                           exercises delete/renumber of .O##/.r##. Do not
                                           delete them as "just outputs".
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
