# hack_ras/gis/wse_surface.py
# Requires: pip install hack_ras[gis,results]
"""
Mapping 2D results as a WSE surface built on the mesh's own connectivity.

Two rules are available, chosen with `mode`:

`horizontal`
    Each cell renders flat at its own computed WSE.  This reproduces RAS
    Mapper's `Horizontal` render mode — **exactly**, not approximately.
    Measured 2026-09-14 against RAS Mapper exports of `NKC_Hillside_Levee`, all
    12 plans that have them, `Depth (Max)` *and* `WSE (Max)`, both terrain
    sources: **82,968,192 px per variable, 0.00000 ft mean, 0.000000 rms and
    0.00000 ft max difference** — every one of the 24 comparisons.  It is also
    the volume-faithful rule, for the reason given below, and the cheap one: no
    face, structure or neighbour is consulted.

`interpolated` (the default)
    The surface ramps between cell centres wherever a continuous water surface
    actually exists.  The rest of this docstring is about that rule — what it
    is for, when it is worse than `horizontal`, and the evidence behind each
    decision inside it.

Use `horizontal` to reproduce or replace a RAS Mapper depth export, and
`interpolated` where the mesh is steep and coarse enough that flat cells read as
blocky.  Both draw the same triangles from the same cell fans; only the vertex
values differ, so every other comparison in this module applies to either.

**Why not just use RAS Mapper.**  RAS Mapper renders (and writes) results with a
user-selected render mode — horizontal, sloping, or hybrid.  Horizontal draws each
cell's water surface flat at the cell's computed WSE; on a coarse mesh that is
blocky in a way that reads as wrong even where it is volumetrically right.
Sloping smooths the surface between cell centres, which looks better and can put
water where the solver never routed any — most visibly straight through an
embankment that the mesh resolves as a hydraulic barrier.  Neither mode exposes
the rule it used, so a reviewer cannot tell which artefacts are physics and which
are rendering.

This module builds the surface explicitly, with the interpolation rules stated and
each one testable.

**When NOT to use it.**  On a flat, finely-meshed area, RAS Mapper's `Horizontal`
is the better tool and this module should be left alone.  Measured on Hillside
p15 `Interior` — median cell relief 4.1 ft on 200 ft cells — the two agree to
**rms 0.095 ft** over 53.6M shared pixels, and horizontal is the better per-cell
volume performer (3.3% of cells off by more than 25%, against 17.9% here),
because horizontal is exactly what the volume-elevation curve assumes and any
interpolation just moves volume between cells.  What this module is for is the
opposite regime: steep, coarse cells, where `Sloping (Cell Corners)` maps +117%
of the water RAS stored and `Horizontal` is visibly blocky.  See
"When to use this instead of RAS Mapper's Horizontal" in `docs/ai_context.md`.

**Scope: 2D flow areas only** — there is no 1D cross-section mapping here.

**The solver's own assumption is a flat WSE per cell.**  HEC-RAS carries one WSE
per cell and gets the cell's storage from its subgrid volume-elevation curve, so a
horizontal cell surface is the volume-faithful one and any interpolation is a
departure from it.  That makes volume the accuracy test rather than appearance:
:func:`check_cell_volumes` integrates the mapped depth over each cell polygon and
compares it to :func:`~hack_ras.results.reader.interpolate_cell_volume`.  A
surface that conserves the volume RAS stored, cell by cell, is defensible; one
that merely looks smooth is not.

Interpolation rules
-------------------
The surface is a triangulation, not a raster interpolation.  Each *wet* cell is
fanned into triangles from its centre to consecutive vertices of its own outline,
so every triangle lies inside exactly one cell and no triangle edge ever crosses a
mesh face by accident.  Cell centres carry the HDF's WSE unaltered, which is why
the surface reproduces `read_wse` exactly at every centre.

A face point on the outline is shared by several cells, and its value is the mean
of the WSE of the cells around it — but only of those that are *communicating*
with the cell being drawn.  Two cells that share a face are not communicating when

* the face carries a structure (`Geometry/Structures/Default Weir Connectivity`),
  because RAS routes that exchange through the weir equation and the head drop
  across it is real; or
* the face is not submerged from *both* sides — that is, the *lower* of the two
  WSEs does not clear the face's minimum elevation (`Faces Minimum Elevation`);
  or
* one of the two cells is dry.

Testing the *higher* WSE instead asks only whether water crosses the face at
all, and that is far too permissive on a steep coarse mesh.  Measured on
`NKC_Hillside_Levee` p15, `RockCr` cell 933: WSE 836.39, cell minimum elevation
830.66, and a downslope neighbour so much lower that averaging put 813.65 at a
shared face point — 22.7 ft below the cell's own water level.  The resulting
plane sat below ground over all 206,255 pixels of the cell, so a cell holding
131,000 ft**3 by RAS's own volume curve mapped as completely dry.  476 of 1071
`RockCr` cells failed that way.  Water does spill across such a face; the two
pools still have separate surfaces, and only a face drowned from both sides
joins them into one.

A consequence worth stating plainly: a cell none of whose faces are drowned from
both sides is isolated, every vertex takes the cell's own WSE, and it renders
flat — which is horizontal mode, and is the volume-faithful answer.  The
interpolation switches itself on only where a continuous water surface actually
exists.

Around a face point the communicating cells are grouped into connected components
*locally*, over the faces incident on that point only — never through the global
mesh graph, which would let two cells separated by an embankment be averaged
together because they happen to connect a mile upstream around its end.  A face
point separating two components therefore carries two values, one per side, and
the surface steps across the barrier instead of ramping through it.

This matters concretely on `NKC_Hillside_Levee`: `C1 Walker Road` through
`C9 Clay Edwards` are connections whose US and DS 2D area are *both* `RockCr`, so
they are embankments crossing the interior of a single mesh.  A Delaunay
triangulation of cell centres — or any interpolation that does not know about
them — smears their head drop across cells whose equivalent side length runs 400
to 690 ft.  `L1`-`L7` are `RockCr` to `Interior` and need no special handling,
because the two areas are triangulated separately and mosaicked.

Because the mean is taken over the contributing cells, a face point value can
never exceed the range of the cells that produced it, so the surface cannot
overshoot.  Face point values are deliberately *not* raised to ground: where the
interpolated plane dives below terrain the depth clip removes it, which is what
lets partial wetting of a large cell emerge from the terrain rather than from a
rendering rule.

How accurate it is
------------------
Measured on `NKC_Hillside_Levee` p15 (`FC 100year`, g03) at the maximum
envelope, 2026-09-14, against the honest reference: the terrain integrated
directly at 1 ft under a flat water surface at each cell's own computed WSE.
Sample of 150 cells per area.

===============================  ===============  ===============
reference: terrain integrated     `Interior`       `RockCr`
===============================  ===============  ===============
this map, total                   **+0.06%**       **-0.35%**
this map, per-cell median         -0.00%           -0.00%
this map, per-cell IQR            -3.4% .. +1.4%   -2.7% .. +0.0%
RAS volume-elevation table        +2.39%           **+9.19%**
RAS table, per-cell median        +3.84%           +11.39%
===============================  ===============  ===============

So the map holds the right amount of water to a fraction of a percent, and the
apparent 2-6% shortfall against `check_cell_volumes` is the *reference* being
high, not the map being low: RAS's volume-elevation curve is piecewise linear
over roughly 47 tabulated points per cell, and linear interpolation of a convex
V(Z) overestimates between them.  That is not a defect in RAS — the solver
routes on that table, so the table is the model's storage — but it does mean a
map drawn on the terrain can never quite hold the volume the solver conserved.
Report the totals against the terrain, and read `check_cell_volumes` as the
comparison against RAS's own accounting that it is.

The per-cell tails are wide (p1 -66% / -93%, p99 +62%) and are the interpolation
working rather than failing: where the water surface genuinely slopes, volume
moves from one cell to its neighbour and nets out, which is why the totals hold
while individual cells move.  A cell whose surface is flat — the isolated case
above — matches to 0.00%.

Measured against RAS Mapper itself
----------------------------------
The user exported `WSE (Max)` for p15 from RAS Mapper in every render mode that
would compute (2026-09-14).  RAS 7.0 offers four, not three, and **RAS Mapper
crashes producing a WSE raster with `Use Depth-Weighted Faces ("Precip Mode")`
on**, so that one is untested.  The exports land on the full terrain grid and
ours is an integer-pixel window of it, so the comparison is pixel for pixel.

Volume against `interpolate_cell_volume` — the solver's own conserved storage,
which favours no rendering:

===================================  ===============  ===============
vs RAS volume-elevation table         `Interior`       `RockCr`
===================================  ===============  ===============
Sloping (Cell Corners)                +4.90%           **+117.22%**
Sloping (Corners + Face Centers)      +0.69%           +27.17%
... + Shallow reduces to Horizontal   +1.18%           +24.92%
Horizontal                            -2.19%           -7.91%
this module                           -2.19%           -5.43%
===================================  ===============  ===============

`Sloping (Cell Corners)` maps more than twice the water RAS stored in `RockCr`.
The excess is a function of **cell relief**, not of the structures — the head
drops across `C1`-`C9` are only 0.5-0.8 ft.  Binning `RockCr` cells by
`top_elevations - min_elevations`: relief < 10 ft (n=10) the sloping mode is
-16.7% and this module -3.2%; 10-30 ft (n=212) **+55.2%** vs -1.8%; > 30 ft
(n=849) **+138.8%** vs -6.6%.  Median `RockCr` relief is 40.4 ft across cells
400-690 ft wide, so the high-relief bin *is* the mesh.  Cell 777 — 77.8 ft of
relief, 7.82 ft of water at its deepest point — holds 18,698 ft**3 by RAS's own
curve and 1,520,698 ft**3 under `Sloping (Cell Corners)`, 81x over; this module
gives 15,620.

That is the cell-933 failure seen from the other side.  Averaging WSE across a
face that is not drowned from both sides pushes the plane too low on one side,
drying a wet cell, and too high on the other, flooding a hillside.  One test
removes both.

Pixel for pixel over the whole model, `ours - RAS` in ft: against `Horizontal`,
mean +0.016 and rms 0.301 with 2.1% of pixels over 1 ft; against
`Sloping (Cell Corners)`, mean -0.544 and rms 1.880 with a 32.13 ft extreme and
13.9% of pixels over 1 ft.  This module tracks horizontal's accuracy without its
cell-boundary steps, which is the whole point.

Known limitation: concave cells
-------------------------------
Fanning from the cell centre assumes the centre can see the whole outline, which
a concave cell breaks — a few triangles then reach slightly past the cell into
its neighbour, where the later-drawn cell simply overwrites them.  It is not
worth clipping every triangle to its polygon to prevent: measured 2026-09-14,
the cell centre lies inside its own polygon for every cell of every mesh checked
(Hillside `Interior` 2615 and `RockCr` 1071, the test fixture's two areas), which
is what bounds the spill, and the summed fan area exceeds RAS's own summed
`Cells Surface Area` by **+0.0015%** on `Interior` and **+0.013%** on `RockCr`.
Per cell the median excess is 0.0000%; 10 of 3686 Hillside cells exceed 0.1% and
the worst is 3.2%.  Hillside has 65 concave `Interior` cells and 104 concave
`RockCr` cells, so concavity is common and its consequence is still negligible.

Reproducing RAS Mapper's own output
-----------------------------------
`export_wse_depth_per_source` writes RAS Mapper's file layout: one raster per
terrain source, each on that source's own grid, tied by a `.vrt` cloned from the
terrain's.  Drop it beside a RAS Mapper export and the two compare pixel for
pixel with nothing resampled on either side.

Prefer it to `export_wse_depth` on a multi-source terrain, because **a terrain
`.vrt` read as a single raster returns the wrong source where sources overlap.**
GDAL composites a VRT in document order, so the *last* source wins; RAS Mapper
lists sources highest priority *first* and stitches them internally rather than
holing the lower tiles.  Measured on `NKC_Hillside_Levee` 2026-09-14: the c02
surveyed channel is `Priority` 0 and listed first, the s04 LiDAR is `Priority` 1
and listed last, and reading `02_Surveyed_Channel.vrt` hands back **LiDAR
throughout the channel, up to 2.5 ft above the surveyed bed** over the 249,405
ft**2 the survey covers.  See `vrt_sources`.

What is left between this module and a RAS Mapper export is one knob and one
deliberate choice.  The knob is `depth_tol`, and it **defaults to 0.0**, which
matches RAS exactly: RAS writes any positive depth — its smallest on p09 is
0.000977 ft, which is float32 granularity, not a threshold.  Raising it to 0.01
drops 25,669 px of p09's 7.4M s04 pixels, every one of them 0.0099 ft deep or
less; that is a display threshold, and it belongs in GIS where changing it does
not cost a re-map.  It also silently distorts any later comparison against a RAS
Mapper export, which applies no threshold at all.
The choice is that a lower-priority source is holed wherever a higher-priority
one has data; RAS Mapper leaves a partial fringe there instead, 4,826 px on p09,
which its own `.vrt` then covers with the higher-priority tile anyway.

RAS Mapper's `WSE (Max)` export carries the **same mask as its depth** — the wet
pixel counts agree exactly on all 12 plans — so this masks WSE by depth too.  A
WSE raster that runs past the water's edge is nobody's intent.

One pixel goes the other way, the same one in all 12 plans: c02 row 3381 col
6551, valid terrain at 745.69 and 12.7 ft submerged, which RAS leaves NoData and
this writes.  One pixel in 243,219; recorded so nobody re-investigates it.

Terrain
-------
Depth is the surface minus the terrain the model computed with, clipped at zero.
Cell elevations in the HDF are the *effective* terrain — the RAS Mapper terrain
layer with its modifications applied — and cannot be reproduced by sampling the
parent raster; see the "Cell min/max are EFFECTIVE ground" measurements in
`docs/ai_context.md`.  This module subtracts a terrain raster supplied by the
caller and does not burn modifications itself.

For `NKC_Hillside_Levee` the gap that leaves is small and was measured rather than
assumed: `Terrain\\02_Surveyed_Channel.hdf` carries exactly one modification,
`Ditch_fix_RS_9580`, a `SetIfLower` channel 3.5 ft wide with 1:1 side slopes along
a **14.1 ft** polyline at (2766081, 1088562), profile 760.78 to 760.75, max reach
10 ft.  It perturbs on the order of 100 ft**2 — about 100 pixels at 1 ft — so the
bare `.vrt` is an acceptable stand-in here.  On a model with substantial
modifications it is not, and the terrain must be exported from RAS Mapper with
them applied.

**That modification is live, and it does not appear in the RAS Mapper GUI.**
Measured 2026-09-14 on p15: `RockCr` cell 1035 contains it, its
`Cells Minimum Elevation` is 760.746, the bare `.vrt` minimum inside the same
cell is 761.797, and the modification's profile bottoms at 760.752 — effective
ground 1.05 ft below bare terrain, within 0.006 ft of the profile.  So a bare
raster runs up to about a foot shallow there.  The consequence for any model:
*"I looked in the GUI and there are no terrain modifications" is not evidence.*
Read the terrain HDF's `Modifications` group instead.  See `docs/TODO.md` §G.

The output grid is snapped to the terrain raster's own grid so the subtraction is
pixel for pixel with no resampling of either surface.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import h5py
import numpy as np

__all__ = [
    "WseSurface",
    "build_wse_surface",
    "barrier_faces",
    "rasterize_surface",
    "export_wse_depth",
    "export_wse_depth_per_source",
    "vrt_sources",
    "clone_source_vrt",
    "difference_rasters",
    "grid_overlap",
    "area_bounds",
    "same_mesh",
    "check_cell_volumes",
]

# A cell counts as wet when its WSE clears its minimum elevation by this much.
# Below it the cell holds a film of water that RAS reports but that maps as noise.
DRY_TOL = 0.01  # ft


@dataclass
class WseSurface:
    """A triangulated water surface for one 2D flow area.

    Attributes
    ----------
    area : str
        Name of the 2D flow area.
    mode : str
        `"interpolated"` or `"horizontal"` — which rule produced `values`.
    points : (P, 2) float64
        Triangulation vertices in project coordinates.  A face point shared by
        two non-communicating cells appears more than once, once per side.
    values : (P,) float64
        WSE at each vertex.
    triangles : (T, 3) int32
        Vertex indices, counter-clockwise.
    cell_of_triangle : (T,) int32
        Which mesh cell each triangle was fanned from, so a rasterised surface
        can be integrated back per cell.
    n_wet, n_cells : int
        Wet and total (non-dummy) cell counts, for reporting.
    n_cell_slots : int
        Length of the HDF's cell arrays, perimeter dummy cells included, so a
        per-cell accumulator can be indexed directly by `cell_of_triangle`.
    n_barrier_faces, n_split_points : int
        How many faces were treated as barriers and how many face points ended up
        carrying more than one value.  Both are worth printing: zero split points
        on a mesh with internal connections means the barrier detection missed.

        In `horizontal` mode both are reported differently and should be read
        differently: no face is ever consulted, so `n_barrier_faces` is 0, and
        every face point between two cells of differing WSE is a split, so
        `n_split_points` counts cell boundaries rather than barriers.
    """

    area: str
    mode: str
    points: np.ndarray
    values: np.ndarray
    triangles: np.ndarray
    cell_of_triangle: np.ndarray
    n_wet: int
    n_cells: int
    n_cell_slots: int
    n_barrier_faces: int
    n_split_points: int

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(xmin, ymin, xmax, ymax) of the triangulated surface."""
        if len(self.points) == 0:
            return (np.nan,) * 4
        x0, y0 = self.points.min(axis=0)
        x1, y1 = self.points.max(axis=0)
        return float(x0), float(y0), float(x1), float(y1)


# ---------------------------
# Barrier faces
# ---------------------------

def barrier_faces(hdf_path: str, area: str) -> set[int]:
    """Faces of `area` that carry a structure.

    `Geometry/Structures/Default Weir Connectivity` lists, per structure and per
    side, the face points the structure's centreline runs along, as decimal
    strings in the `RS/FP` field.  A face both of whose face points belong to the
    same structure-and-side chain lies on the structure.

    Face point indices are area-local, so the chains are matched against this
    area's `Faces FacePoint Indexes` only; a chain belonging to another area
    cannot produce a hit except by coincidence of index, which the both-endpoints
    test makes vanishingly unlikely.

    Returns an empty set when the geometry has no structures.
    """
    with h5py.File(hdf_path, "r") as hdf:
        return _barrier_faces(hdf, area)


def _barrier_faces(hdf, area: str) -> set[int]:
    """`barrier_faces` against an already-open handle."""
    if "Geometry/Structures/Default Weir Connectivity" not in hdf:
        return set()
    conn = hdf["Geometry/Structures/Default Weir Connectivity"][:]
    attrs = hdf["Geometry/Structures/Attributes"][:]
    face_fp = hdf[f"Geometry/2D Flow Areas/{area}/Faces FacePoint Indexes"][:]

    chains: dict[tuple[int, bytes], set[int]] = {}
    for row in conn:
        sid = int(row["SID"])
        if sid >= len(attrs):
            continue
        side = bytes(row["HW/TW"])
        # Scope each chain to the area its face point indices are numbered in.
        # Skipping this and matching every chain against every area produced an
        # expected few false positives per mesh on NKC_Hillside_Levee: with ~1300
        # face points and ~30 chains, some face's two endpoints land inside an
        # unrelated chain by coincidence.
        owner = attrs[sid]["US SA/2D" if side == b"HW" else "DS SA/2D"]
        if isinstance(owner, bytes):
            owner = owner.decode(errors="replace")
        if str(owner).strip() != area:
            continue
        fp = row["RS/FP"]
        text = fp.decode() if isinstance(fp, bytes) else str(fp)
        try:
            idx = int(text)
        except ValueError:
            # A 1D-side structure stores a river station here, not a face point.
            continue
        chains.setdefault((sid, side), set()).add(idx)

    barriers: set[int] = set()
    for face, (a, b) in enumerate(face_fp):
        for pts in chains.values():
            if a in pts and b in pts:
                barriers.add(face)
                break
    return barriers


# ---------------------------
# Surface construction
# ---------------------------

def _cell_rings(grp):
    """Per-cell outline as (vertex_xy, face_point_index_or_-1), in walk order.

    Mirrors `results.reader._perimeter_polygons` — walk the cell's faces in stored
    order, splicing in the intermediate vertices RAS keeps in
    `Faces Perimeter Values` where the mesh boundary bends between a face's two
    end points — but keeps the face point index alongside each vertex, which the
    polygon builder discards and the triangulation needs.
    """
    fp_xy = grp["FacePoints Coordinate"][:]
    face_info = grp["Cells Face and Orientation Info"][:]
    face_vals = grp["Cells Face and Orientation Values"][:]
    face_fp = grp["Faces FacePoint Indexes"][:]
    perim_info = grp["Faces Perimeter Info"][:]
    perim_vals = grp["Faces Perimeter Values"][:]

    rings = []
    for start, count in face_info:
        if count < 3:
            rings.append([])
            continue
        ring = []
        for face, orient in face_vals[start:start + count]:
            a, b = face_fp[face]
            if orient < 0:
                a = b
            p_start, p_count = perim_info[face]
            mid = perim_vals[p_start:p_start + p_count]
            if orient < 0:
                mid = mid[::-1]
            ring.append((fp_xy[a], int(a)))
            ring.extend((m, -1) for m in mid)
        rings.append(ring)
    return rings


def _facepoint_components(grp, wet, wse, barriers, face_min_elev):
    """Group the cells around each face point into communicating components.

    Returns `{face_point: {cell: mean_wse}}` — for each face point, the value each
    surrounding wet cell should read at that point.

    Components are found over the faces incident on the face point *only*.  Using
    the global cell graph instead would merge cells that are separated here but
    connected elsewhere, which is exactly the C1-C9 embankment failure this module
    exists to avoid.
    """
    fp_face_info = grp["FacePoints Face and Orientation Info"][:]
    fp_face_vals = grp["FacePoints Face and Orientation Values"][:]
    face_cells = grp["Faces Cell Indexes"][:]

    out: dict[int, dict[int, float]] = {}
    for fp in range(len(fp_face_info)):
        start, count = fp_face_info[fp]
        faces = fp_face_vals[start:start + count, 0]

        # Union-find over the handful of cells meeting at this point.
        parent: dict[int, int] = {}

        def find(c):
            while parent[c] != c:
                parent[c] = parent[parent[c]]
                c = parent[c]
            return c

        for face in faces:
            for c in face_cells[face]:
                c = int(c)
                if c >= 0 and wet[c]:
                    parent.setdefault(c, c)
        if not parent:
            continue

        for face in faces:
            left, right = (int(c) for c in face_cells[face])
            if left < 0 or right < 0 or not (wet[left] and wet[right]):
                continue
            if face in barriers:
                continue
            if min(wse[left], wse[right]) <= face_min_elev[face]:
                # The face must be submerged from BOTH sides.  Testing the max
                # instead asks only whether water crosses the face at all, which
                # is true of a one-way spill down a hillside — and a spill is not
                # a shared water surface.  See the module docstring.
                continue
            a, b = find(left), find(right)
            if a != b:
                parent[a] = b

        groups: dict[int, list[int]] = {}
        for c in parent:
            groups.setdefault(find(c), []).append(c)

        vals: dict[int, float] = {}
        for members in groups.values():
            mean = float(np.mean([wse[c] for c in members]))
            for c in members:
                vals[c] = mean
        out[fp] = vals
    return out


def build_wse_surface(
    hdf_path: str,
    area: str,
    wse: np.ndarray,
    *,
    dry_tol: float = DRY_TOL,
    mode: str = "interpolated",
) -> WseSurface:
    """Triangulate one 2D flow area's water surface on the mesh connectivity.

    Parameters
    ----------
    hdf_path : str
        Plan HDF (`.p##.hdf`).  The geometry datasets this needs are also present
        in a `.g##.hdf`, but the WSE is not, so a plan file is the useful input.
    area : str
        2D flow area name, from `results.reader.list_areas`.
    wse : (N,) array
        Cell WSE, e.g. from `results.reader.read_wse`.  Length must match the
        area's cell count.
    dry_tol : float
        A cell is wet when `wse > min_elevation + dry_tol`.
    mode : str
        `"interpolated"` (default) applies the rules in the module docstring:
        a face point takes the mean of the WSE of the cells communicating with
        it, so the surface ramps where a continuous water surface exists.

        `"horizontal"` gives every vertex of a cell — centre and outline alike —
        that cell's own WSE, so each cell renders as a flat plane at its computed
        water level and the surface steps at every cell boundary.  This is
        RAS Mapper's `Horizontal` render mode, and it is the *volume-faithful*
        one: HEC-RAS carries one WSE per cell and takes the cell's storage from
        its subgrid volume-elevation curve, so a flat cell surface is exactly
        what the solver assumed.  No face, structure or neighbour is consulted,
        which also makes it the cheap mode — `_barrier_faces` and
        `_facepoint_components` are both skipped.

    Returns
    -------
    WseSurface

    Raises
    ------
    ValueError
        If `wse` does not match the area's cell count, or `mode` is not one of
        `"interpolated"` / `"horizontal"`.
    KeyError
        If the HDF predates the perimeter face datasets (pre-RAS-7.0); there is no
        approximate fallback here, because a cell outline that does not follow the
        mesh boundary produces triangles that hang outside the mesh.
    """
    if mode not in ("interpolated", "horizontal"):
        raise ValueError(
            f"mode must be 'interpolated' or 'horizontal', not {mode!r}"
        )

    base = f"Geometry/2D Flow Areas/{area}"
    with h5py.File(hdf_path, "r") as hdf:
        grp = hdf[base]
        required = (
            "Cells Face and Orientation Info",
            "Cells Face and Orientation Values",
            "Faces FacePoint Indexes",
            "Faces Perimeter Info",
            "Faces Perimeter Values",
            "FacePoints Face and Orientation Info",
            "FacePoints Face and Orientation Values",
            "Faces Cell Indexes",
            "Faces Minimum Elevation",
        )
        missing = [k for k in required if k not in grp]
        if missing:
            raise KeyError(
                f"{hdf_path} / {area} lacks {missing}; a pre-RAS-7.0 mesh cannot be "
                "triangulated on its own connectivity"
            )

        centers = grp["Cells Center Coordinate"][:]
        min_elev = grp["Cells Minimum Elevation"][:].astype(np.float64)
        face_min_elev = grp["Faces Minimum Elevation"][:].astype(np.float64)
        rings = _cell_rings(grp)

        wse = np.asarray(wse, dtype=np.float64)
        if wse.shape[0] != centers.shape[0]:
            raise ValueError(
                f"wse has {wse.shape[0]} values but {area} has {centers.shape[0]} cells"
            )

        # Perimeter dummy cells carry a NaN minimum elevation and no real outline.
        real = ~np.isnan(min_elev)
        wet = real & np.isfinite(wse) & (wse > min_elev + dry_tol)

        if mode == "horizontal":
            # Nothing to consult: every vertex will fall back to the cell's own
            # WSE below, which is what a flat cell surface means.
            barriers = set()
            fp_vals: dict[int, dict[int, float]] = {}
        else:
            barriers = _barrier_faces(hdf, area)
            fp_vals = _facepoint_components(grp, wet, wse, barriers, face_min_elev)

    points: list[np.ndarray] = []
    values: list[float] = []
    triangles: list[tuple[int, int, int]] = []
    cell_of: list[int] = []
    # One vertex per (face point, value) pair, so a split point yields two.
    shared: dict[tuple[int, int], int] = {}

    def vertex(xy, value, key=None):
        if key is not None:
            hit = shared.get(key)
            if hit is not None:
                return hit
        points.append(np.asarray(xy, dtype=np.float64))
        values.append(float(value))
        idx = len(points) - 1
        if key is not None:
            shared[key] = idx
        return idx

    n_split = 0
    seen_fp_values: dict[int, set[int]] = {}

    for cell in np.flatnonzero(wet):
        ring = rings[cell]
        if len(ring) < 3:
            continue
        centre = vertex(centers[cell], wse[cell])

        ring_idx = []
        for xy, fp in ring:
            if fp < 0:
                # Boundary bend vertex — interior to a single perimeter face, so
                # it belongs to this cell alone.
                ring_idx.append(vertex(xy, wse[cell]))
                continue
            value = fp_vals.get(fp, {}).get(int(cell), wse[cell])
            quantised = int(round(value * 1e6))
            seen_fp_values.setdefault(fp, set()).add(quantised)
            ring_idx.append(vertex(xy, value, key=(fp, quantised)))

        for i in range(len(ring_idx)):
            a = ring_idx[i]
            b = ring_idx[(i + 1) % len(ring_idx)]
            triangles.append((centre, a, b))
            cell_of.append(int(cell))

    n_split = sum(1 for v in seen_fp_values.values() if len(v) > 1)

    surface = WseSurface(
        area=area,
        mode=mode,
        points=np.asarray(points, dtype=np.float64).reshape(-1, 2),
        values=np.asarray(values, dtype=np.float64),
        triangles=np.asarray(triangles, dtype=np.int32).reshape(-1, 3),
        cell_of_triangle=np.asarray(cell_of, dtype=np.int32),
        n_wet=int(wet.sum()),
        n_cells=int(real.sum()),
        n_cell_slots=int(len(min_elev)),
        n_barrier_faces=len(barriers),
        n_split_points=n_split,
    )
    logging.info(
        "%s [%s]: %d/%d cells wet, %d triangles, %d barrier faces, "
        "%d split face points",
        area, mode, surface.n_wet, surface.n_cells, len(surface.triangles),
        surface.n_barrier_faces, surface.n_split_points,
    )
    return surface


# ---------------------------
# Rasterisation
# ---------------------------

def rasterize_surface(
    surface: "WseSurface",
    transform,
    row_off: int,
    col_off: int,
    height: int,
    width: int,
    *,
    out: np.ndarray | None = None,
    cells_out: np.ndarray | None = None,
):
    """Scan-convert one surface into a window of a raster grid.

    Each triangle is filled by barycentric interpolation of its three vertex WSEs,
    which is exact — the triangle is planar by construction — so the only
    approximation in the whole raster is the pixel centre sampling.

    Triangles are scan-converted one at a time rather than evaluated through a
    global point-location structure.  There are only a few tens of thousands of
    them and each covers thousands of pixels, so the per-triangle Python overhead
    is negligible while the alternative would point-locate every one of the
    hundreds of millions of output pixels.

    Parameters
    ----------
    surface : WseSurface
    transform : affine.Affine
        Transform of the *full* output grid, north-up with square pixels.
    row_off, col_off, height, width : int
        The window to fill, in full-grid pixel coordinates.
    out : (height, width) float32, optional
        Destination, NaN-filled if not supplied.  Passing one lets several areas
        mosaic into the same window.
    cells_out : (height, width) int32, optional
        If supplied, receives the mesh cell index behind each written pixel
        (-1 elsewhere).  Used by the volume check.

    Returns
    -------
    (out, cells_out)
    """
    if out is None:
        out = np.full((height, width), np.nan, dtype=np.float32)
    if len(surface.triangles) == 0:
        return out, cells_out

    a, e = transform.a, transform.e
    c0, f0 = transform.c, transform.f

    tri = surface.points[surface.triangles]           # (T, 3, 2)
    val = surface.values[surface.triangles]           # (T, 3)

    # Vertex positions in pixel-centre index space: pixel (r, c) sits at (c, r).
    u = (tri[:, :, 0] - c0) / a - 0.5
    v = (tri[:, :, 1] - f0) / e - 0.5

    lo_c = np.maximum(np.ceil(u.min(axis=1)).astype(np.int64), col_off)
    hi_c = np.minimum(np.floor(u.max(axis=1)).astype(np.int64), col_off + width - 1)
    lo_r = np.maximum(np.ceil(v.min(axis=1)).astype(np.int64), row_off)
    hi_r = np.minimum(np.floor(v.max(axis=1)).astype(np.int64), row_off + height - 1)

    hits = np.flatnonzero((lo_c <= hi_c) & (lo_r <= hi_r))
    for t in hits:
        u0, u1, u2 = u[t]
        v0, v1, v2 = v[t]
        det = (v1 - v2) * (u0 - u2) + (u2 - u1) * (v0 - v2)
        if det == 0.0:
            continue  # degenerate sliver, contributes nothing
        cc = np.arange(lo_c[t], hi_c[t] + 1)
        rr = np.arange(lo_r[t], hi_r[t] + 1)
        gu = cc[None, :]
        gv = rr[:, None]
        w0 = ((v1 - v2) * (gu - u2) + (u2 - u1) * (gv - v2)) / det
        w1 = ((v2 - v0) * (gu - u2) + (u0 - u2) * (gv - v2)) / det
        w2 = 1.0 - w0 - w1
        # A tiny negative tolerance keeps pixels exactly on a shared edge from
        # falling through the crack between two triangles.
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        if not inside.any():
            continue
        z = w0 * val[t, 0] + w1 * val[t, 1] + w2 * val[t, 2]
        sub = (slice(lo_r[t] - row_off, hi_r[t] - row_off + 1),
               slice(lo_c[t] - col_off, hi_c[t] - col_off + 1))
        block = out[sub]
        np.copyto(block, z.astype(np.float32), where=inside)
        out[sub] = block
        if cells_out is not None:
            cblock = cells_out[sub]
            np.copyto(cblock, np.int32(surface.cell_of_triangle[t]), where=inside)
            cells_out[sub] = cblock

    return out, cells_out


def _snapped_grid(bounds, terrain_transform, terrain_width, terrain_height, pad=2):
    """Window of the terrain grid that covers `bounds`, snapped to its pixels."""
    x0, y0, x1, y1 = bounds
    a, e = terrain_transform.a, terrain_transform.e
    c0, f0 = terrain_transform.c, terrain_transform.f
    col_lo = int(np.floor((x0 - c0) / a)) - pad
    col_hi = int(np.ceil((x1 - c0) / a)) + pad
    row_lo = int(np.floor((y1 - f0) / e)) - pad   # e < 0, so y1 is the low row
    row_hi = int(np.ceil((y0 - f0) / e)) + pad
    col_lo = max(col_lo, 0)
    row_lo = max(row_lo, 0)
    col_hi = min(col_hi, terrain_width)
    row_hi = min(row_hi, terrain_height)
    return row_lo, col_lo, row_hi - row_lo, col_hi - col_lo


# ---------------------------
# Export
# ---------------------------

def _check_terrain_crs(hdf_path: str, terrain_path: str, allow_mismatch: bool):
    """Refuse a terrain raster that is not in the model's coordinate system.

    The plan HDF carries the project's projection as a WKT string in its root
    `Projection` attribute, so the reference needs no `.prj` hunting and no
    `.rasmap` parse.

    This has to be a semantic comparison, not a string one: RAS writes the ESRI
    dialect (`NAD_1983_StatePlane_Missouri_West_FIPS_2403`) and GDAL writes the
    EPSG-style name (`NAD83 / Missouri West`) for the same system, and
    `pyproj.CRS.equals` sees through that — verified on `NKC_Hillside_Levee`
    against both its own terrain `.vrt` and a RAS Mapper result export, both
    True.

    The failure it exists to catch is the quiet one.  A terrain in the wrong CRS
    usually still overlaps the mesh, so the "does not overlap" check never
    fires and every depth is wrong by the offset between the two systems.
    Measured on the PCA model: NAD83(HARN) vs NAD83(2011) for the same state
    plane zone are about 3.5 ft apart and `equals` returns False, which is the
    whole point.

    Skipped with a warning, not an error, when either side declares no CRS —
    that is missing metadata rather than evidence of a mismatch.
    """
    import rasterio

    with h5py.File(hdf_path, "r") as hdf:
        wkt = hdf.attrs.get("Projection")
    if wkt is None:
        logging.warning(
            "%s has no root 'Projection' attribute; cannot check that %s is in "
            "the model's coordinate system", hdf_path, terrain_path)
        return
    if isinstance(wkt, bytes):
        wkt = wkt.decode(errors="replace")

    with rasterio.open(terrain_path) as terr:
        terrain_crs = terr.crs
    if terrain_crs is None:
        logging.warning(
            "%s declares no CRS; cannot check it against the model's coordinate "
            "system", terrain_path)
        return

    from pyproj import CRS

    model = CRS.from_wkt(str(wkt))
    terrain = CRS.from_wkt(terrain_crs.to_wkt())
    if model.equals(terrain):
        return
    message = (
        f"terrain CRS does not match the model: {hdf_path} is in "
        f"{model.name!r} but {terrain_path} is in {terrain.name!r}. Depths "
        f"would be sampled at the wrong ground. Reproject the terrain, or pass "
        f"allow_crs_mismatch=True if the two really are the same system."
    )
    if allow_mismatch:
        logging.warning("%s (continuing: allow_crs_mismatch=True)", message)
        return
    raise ValueError(message)


def export_wse_depth(
    hdf_path: str,
    terrain_path: str,
    out_dir: str,
    *,
    areas: list[str] | None = None,
    wse_type: str = "Maximum",
    prefix: str | None = None,
    tile: int = 2048,
    depth_tol: float = 0.0,
    dry_tol: float = DRY_TOL,
    bounds: tuple[float, float, float, float] | None = None,
    allow_crs_mismatch: bool = False,
    volume_check: bool = True,
    mode: str = "interpolated",
) -> dict:
    """Write WSE and depth GeoTIFFs for one plan, on one grid.

    The output grid is a window of the terrain raster's own grid, so terrain is
    never resampled and depth is a pixel-for-pixel subtraction.  Both rasters
    carry the same mask: a pixel is written only where the interpolated surface
    stands more than `depth_tol` above terrain.  A WSE raster that extends past
    the water's edge is not useful, and masking both the same way keeps them
    consistent with each other.

    Areas are rasterised independently and mosaicked.  Separate 2D flow areas do
    not overlap, so the order is immaterial; where one somehow did, first wet
    wins.

    Parameters
    ----------
    hdf_path : str
        Plan HDF (`.p##.hdf`) holding both the geometry and the results.
    terrain_path : str
        Terrain raster — the `.vrt` or a GeoTIFF exported from RAS Mapper.  See
        the module docstring on when the bare `.vrt` is and is not adequate.
    out_dir : str
        Directory for the outputs; created if absent.
    areas : list[str], optional
        2D flow areas to map.  Default: all of them.
    wse_type : str
        Passed to `results.reader.read_wse` — `"Maximum"`,
        `"Maximum from Time Series"`, or a timestamp string.
    prefix : str, optional
        Output file stem.  Default: the plan HDF stem plus the WSE type.
    tile : int
        Output block size in pixels.  The full grid is far too large to hold in
        memory at 1 ft, so everything is done a tile at a time.
    depth_tol : float
        Pixels at or below this depth are left as NoData.  Default 0.0, which
        writes any positive depth and so matches RAS Mapper's wet extent
        exactly.  Raising it trims a fringe at the water's edge — symbology
        rather than data, and better applied in GIS where it costs no re-map.
    dry_tol : float
        Passed to `build_wse_surface`.
    bounds : (x0, y0, x1, y1), optional
        Force the output extent instead of deriving it from the wet area.  Two
        plans exported with the same `bounds` against the same terrain land on
        the same grid and can be differenced directly; `area_bounds` supplies the
        mesh perimeter box, which is the natural choice.  Pass the union across
        every plan being compared, not one plan's, or a differing mesh gets
        clipped.
    allow_crs_mismatch : bool
        Proceed even when the terrain's CRS differs from the model's.  Off by
        default: a terrain in the wrong system still overlaps the mesh, so the
        error is silent and every depth is wrong.
    volume_check : bool
        Accumulate the mapped volume per cell during the tiling pass, for
        `check_cell_volumes`.  Costs nothing measurable.
    mode : str
        Passed to `build_wse_surface`: `"interpolated"` or `"horizontal"`.

    Returns
    -------
    dict
        `wse_path`, `depth_path`, `surfaces` (`{area: WseSurface}`),
        `mapped_volumes` (`{area: (N,) float64 ft**3}`, zeros when
        `volume_check` is False), `wse` (`{area: (N,) float64}` as read),
        `bounds`, `shape`, `mode`.

    See Also
    --------
    export_wse_depth_per_source : one output per terrain source, at that
        source's own resolution, tied by a `.vrt` — which is what RAS Mapper
        writes, and the only way to get the terrain right when a source other
        than the last one in the `.vrt` has priority.  See `vrt_sources`.
    """
    import os

    import rasterio

    from hack_ras.results.reader import list_areas, read_wse

    _check_terrain_crs(hdf_path, terrain_path, allow_crs_mismatch)

    if areas is None:
        areas = list_areas(hdf_path)
    if not areas:
        raise ValueError(f"{hdf_path} has no 2D flow areas to map")

    surfaces = {}
    wse_by_area = {}
    for area in areas:
        wse_by_area[area] = read_wse(hdf_path, area, wse_type)
        surfaces[area] = build_wse_surface(
            hdf_path, area, wse_by_area[area], dry_tol=dry_tol, mode=mode
        )
    live = [s for s in surfaces.values() if len(s.triangles)]
    if not live:
        raise ValueError(
            f"no wet cells in {areas} of {hdf_path} at wse_type={wse_type!r}"
        )

    if bounds is None:
        xs0 = min(s.bounds[0] for s in live)
        ys0 = min(s.bounds[1] for s in live)
        xs1 = max(s.bounds[2] for s in live)
        ys1 = max(s.bounds[3] for s in live)
    else:
        xs0, ys0, xs1, ys1 = bounds

    os.makedirs(out_dir, exist_ok=True)
    if prefix is None:
        stem = os.path.splitext(os.path.basename(hdf_path))[0]
        prefix = f"{stem}_{wse_type.replace(' ', '_')}"
    wse_path = os.path.join(out_dir, f"{prefix}_WSE.tif")
    depth_path = os.path.join(out_dir, f"{prefix}_Depth.tif")

    mapped = {a: np.zeros(s.n_cell_slots, dtype=np.float64)
              for a, s in surfaces.items()}

    with rasterio.open(terrain_path) as terr:
        grid, height, width = _write_depth_grid(
            surfaces, terr, (xs0, ys0, xs1, ys1), wse_path, depth_path,
            mapped=mapped, tile=tile, depth_tol=depth_tol,
            volume_check=volume_check, label=terrain_path,
        )

    return {
        "wse_path": wse_path,
        "depth_path": depth_path,
        "surfaces": surfaces,
        "wse": wse_by_area,
        "mapped_volumes": mapped,
        "bounds": (grid.c, grid.f + grid.e * height,
                   grid.c + grid.a * width, grid.f),
        "shape": (height, width),
        "mode": mode,
    }


NODATA = -9999.0


def _write_depth_grid(
    surfaces,
    terr,
    bounds,
    wse_path,
    depth_path,
    *,
    mapped,
    tile: int,
    depth_tol: float,
    volume_check: bool,
    label: str,
    mask_sources=(),
):
    """Rasterise every surface onto one terrain raster's grid and write it.

    The shared body of both exports.  `terr` is an open reader; the output grid
    is the window of *its* grid covering `bounds`, so the terrain being
    subtracted is never resampled.

    `mask_sources` is an iterable of open readers for terrain sources that
    outrank `terr`.  A pixel whose centre lands on valid data in any of them is
    left NoData here, so a lower-priority source never paints over ground a
    higher-priority one already describes.  Empty for a single-grid export.

    Each such source's valid/invalid mask is held in memory for the whole pass,
    one byte per source pixel, and indexed by computed row and column rather than
    resampled.  Letting GDAL decimate the mask into each tile instead looked
    equivalent and was not: measured on Hillside 2026-09-14, a boundless
    `out_shape` read of the 1 ft c02 source into 3.28 ft tiles over-masked
    30,891 px whose centres lie nowhere near c02, dropping 0.58% of the water.
    Indexing is exact, and holding the mask is affordable because the
    higher-priority source is the detail patch, not the base — c02 is 38 MB.

    Returns `(grid_transform, height, width)`.  `mapped` is accumulated in place.
    """
    import rasterio
    from rasterio.windows import Window
    from rasterio.windows import transform as window_transform

    row_off, col_off, height, width = _snapped_grid(
        bounds, terr.transform, terr.width, terr.height
    )
    if height <= 0 or width <= 0:
        raise ValueError(
            "the mesh does not overlap the terrain raster; check that "
            f"{label} is the terrain this geometry was built on"
        )
    win = Window(col_off, row_off, width, height)
    grid = window_transform(win, terr.transform)
    px_area = abs(grid.a * grid.e)

    profile = dict(
        driver="GTiff", dtype="float32", count=1, nodata=NODATA,
        width=width, height=height, transform=grid, crs=terr.crs,
        tiled=True, blockxsize=256, blockysize=256,
        compress="deflate", predictor=3, zlevel=6, BIGTIFF="YES",
    )

    masks = []
    for higher in mask_sources:
        hnd = higher.nodata
        block = higher.read(1)
        masks.append(((block != hnd) if hnd is not None
                      else np.ones(block.shape, dtype=bool), higher.transform))
        del block

    with rasterio.open(wse_path, "w", **profile) as dst_w, \
         rasterio.open(depth_path, "w", **profile) as dst_d:
        for r0 in range(0, height, tile):
            h = min(tile, height - r0)
            for c0 in range(0, width, tile):
                w = min(tile, width - c0)
                out_w = np.full((h, w), NODATA, dtype=np.float32)
                out_d = np.full((h, w), NODATA, dtype=np.float32)

                ground = terr.read(
                    1, window=Window(col_off + c0, row_off + r0, w, h)
                ).astype(np.float64)
                if terr.nodata is not None:
                    ground[ground == terr.nodata] = np.nan

                if masks:
                    # Output pixel centres, in project coordinates.
                    xs = grid.c + (c0 + np.arange(w) + 0.5) * grid.a
                    ys = grid.f + (r0 + np.arange(h) + 0.5) * grid.e
                    for valid, ht in masks:
                        hh, hw_ = valid.shape
                        hc = np.floor((xs - ht.c) / ht.a).astype(np.int64)
                        hr = np.floor((ys - ht.f) / ht.e).astype(np.int64)
                        okc = (hc >= 0) & (hc < hw_)
                        okr = (hr >= 0) & (hr < hh)
                        if not (okc.any() and okr.any()):
                            continue
                        sub = valid[np.ix_(hr[okr], hc[okc])]
                        ground[np.ix_(okr, okc)] = np.where(
                            sub, np.nan, ground[np.ix_(okr, okc)]
                        )

                wrote = False
                for area, surf in surfaces.items():
                    if not len(surf.triangles):
                        continue
                    arr = np.full((h, w), np.nan, dtype=np.float32)
                    cel = (np.full((h, w), -1, dtype=np.int32)
                           if volume_check else None)
                    # `grid` is already the output window's transform, so the
                    # tile offsets are output-grid, not terrain-grid;
                    # row_off/col_off belong only to the terrain read above.
                    rasterize_surface(surf, grid, r0, c0,
                                      h, w, out=arr, cells_out=cel)
                    if not np.isfinite(arr).any():
                        continue
                    depth = arr - ground
                    wet = np.isfinite(depth) & (depth > depth_tol)
                    if not wet.any():
                        continue
                    wrote = True
                    if volume_check:
                        mapped[area] += np.bincount(
                            cel[wet], weights=depth[wet] * px_area,
                            minlength=surf.n_cell_slots,
                        )
                    fresh = wet & (out_d == NODATA)
                    out_w[fresh] = arr[fresh]
                    out_d[fresh] = depth[fresh].astype(np.float32)

                if wrote:
                    dst_w.write(out_w, 1, window=Window(c0, r0, w, h))
                    dst_d.write(out_d, 1, window=Window(c0, r0, w, h))

    return grid, height, width


# ---------------------------
# Per-source export (RAS Mapper's own layout)
# ---------------------------

def vrt_sources(vrt_path: str) -> list[str]:
    """Source rasters of a terrain `.vrt`, in RAS Mapper priority order.

    RAS Mapper builds a terrain from one raster per source dataset and writes a
    `.vrt` tying them together.  The `.hdf` beside it records a `Priority` per
    source under `Terrain/<name>`, **0 being the highest**, and the `.vrt` lists
    the sources in that same order — highest priority first.

    That correspondence is not assumed here.  When a sibling `.hdf` carries
    `Priority` attributes they are read and used, and a disagreement with the
    `.vrt`'s own order is logged as a warning rather than silently resolved one
    way; without them the `.vrt` order stands.  Surveyed across every terrain in
    this workspace 2026-09-14 — 38 `.vrt`, 36 of them with `Priority` — the two
    agreed **36 times out of 36**, single-source and multi (Hillside's 2, the
    Baxter example's 5, Muncie's `TerrainWithChannel` 2).  The two without
    `Priority` are old HEC example projects, one of them multi-source
    (`BaldEagleCrkMulti2D/Terrain50`: `dtm_20ft` then `baldeagledem`), which is
    why the `.vrt` order has to remain a working fallback.

    **GDAL renders a VRT in the opposite order.**  Sources are composited in
    document order, so the *last* one wins wherever it has data — and RAS Mapper
    does not hole the lower-priority terrain tiles, it stitches them internally
    instead (`Terrain/Stitch TIN *` in the `.hdf`).  The consequence, measured on
    `NKC_Hillside_Levee` `02_Surveyed_Channel` 2026-09-14: the c02 surveyed
    channel is `Priority` 0 and listed first, the s04 LiDAR is `Priority` 1 and
    listed last, and **reading the `.vrt` returns the LiDAR throughout the
    channel** — up to 2.5 ft above the surveyed bed over the 249,405 ft**2 the
    survey covers.  So a depth raster computed against the bare `.vrt` is wrong
    by that much in exactly the place a ditch model cares about.

    `export_wse_depth_per_source` avoids this by computing against each source
    raster directly and masking out whatever a higher-priority source covers,
    which is also what RAS Mapper's own depth export does — verified on the same
    model: over the 52,493 px where its c02 and s04 depth tiles overlap,
    `c02 + depth_c02` equals `s04 + depth_s04` exactly, and its s04 depth tile is
    NoData over 79% of the c02 footprint.

    Returns absolute paths, `relativeToVRT` resolved.  Raises `ValueError` when
    the file has no sources.
    """
    import os
    import xml.etree.ElementTree as ET

    root = ET.parse(vrt_path).getroot()
    base = os.path.dirname(os.path.abspath(vrt_path))
    out = []
    for node in root.iter("SourceFilename"):
        name = (node.text or "").strip()
        if not name:
            continue
        if node.get("relativeToVRT") == "1":
            name = os.path.join(base, name)
        out.append(os.path.abspath(name))
    if not out:
        raise ValueError(f"{vrt_path} lists no source rasters")

    ranked = _hdf_priority_order(vrt_path, out)
    if ranked is not None and ranked != out:
        logging.warning(
            "%s lists its sources in a different order than the Priority "
            "attributes in the terrain HDF beside it; using the HDF. "
            "vrt=%s hdf=%s",
            os.path.basename(vrt_path),
            [os.path.basename(x) for x in out],
            [os.path.basename(x) for x in ranked],
        )
        return ranked
    return out


def _hdf_priority_order(vrt_path: str, sources: list[str]) -> list[str] | None:
    """`sources` reordered by the sibling terrain HDF's `Priority`, or None.

    None when there is no readable sibling `.hdf`, when it carries no `Priority`
    attributes, or when its groups do not cover every source — any of which
    leaves the `.vrt`'s own order as the only evidence.
    """
    import os

    hdf_path = os.path.splitext(vrt_path)[0] + ".hdf"
    if not os.path.exists(hdf_path):
        return None
    try:
        with h5py.File(hdf_path, "r") as hdf:
            grp = hdf.get("Terrain")
            if grp is None:
                return None
            prio = {k: int(grp[k].attrs["Priority"])
                    for k in grp
                    if hasattr(grp[k], "attrs") and "Priority" in grp[k].attrs}
    except (OSError, KeyError, ValueError):
        # An unreadable or unexpected HDF is not a reason to fail the export.
        return None
    if not prio:
        return None

    # The group name is the source's stem, which is how RAS ties the two files.
    by_stem = {os.path.splitext(os.path.basename(s))[0]: s for s in sources}
    if set(prio) != set(by_stem):
        return None
    return [by_stem[k] for k in sorted(prio, key=lambda k: prio[k])]


def clone_source_vrt(terrain_vrt: str, out_vrt: str, replacements: dict) -> str:
    """Clone a terrain `.vrt`, pointing each source at another raster instead.

    Cloning rather than rebuilding keeps the grid bit for bit: RAS Mapper places
    a source at a fractional `DstRect` offset (8446.30497208284 px on Hillside),
    and a rebuilt VRT would round it.  `replacements` maps each source's absolute
    path — as `vrt_sources` returns it — to the file that replaces it; a source
    with no entry is dropped from the clone.

    Every replacement must sit on its source's own full grid, which is what
    `export_wse_depth_per_source` writes, because the clone keeps the terrain's
    `SrcRect` and `DstRect`.  Use it to wrap anything derived source by source —
    a set of per-source differences, say.

    Band statistics and histograms describe the terrain, not whatever replaced
    it, so they are stripped; GDAL recomputes them on demand.
    """
    import os
    import xml.etree.ElementTree as ET

    tree = ET.parse(terrain_vrt)
    root = tree.getroot()
    base = os.path.dirname(os.path.abspath(terrain_vrt))

    for band in root.findall("VRTRasterBand"):
        for tag in ("Metadata", "Histograms"):
            for stale in band.findall(tag):
                band.remove(stale)
        for src in list(band):
            fn = src.find("SourceFilename")
            if fn is None:
                continue
            name = (fn.text or "").strip()
            if fn.get("relativeToVRT") == "1":
                name = os.path.join(base, name)
            hit = replacements.get(os.path.abspath(name))
            if hit is None:
                band.remove(src)
                continue
            fn.set("relativeToVRT", "1")
            fn.text = os.path.basename(hit)

    tree.write(out_vrt, encoding="utf-8", xml_declaration=False)
    return out_vrt


def export_wse_depth_per_source(
    hdf_path: str,
    terrain_vrt: str,
    out_dir: str,
    *,
    areas: list[str] | None = None,
    wse_type: str = "Maximum",
    prefix: str | None = None,
    tile: int = 2048,
    depth_tol: float = 0.0,
    dry_tol: float = DRY_TOL,
    allow_crs_mismatch: bool = False,
    volume_check: bool = True,
    mode: str = "horizontal",
) -> dict:
    """Write one WSE and depth raster per terrain source, plus a `.vrt`.

    This is the layout RAS Mapper itself writes — `Depth (Max).<terrain>.<source>
    .tif` for each source at that source's own resolution, tied by a `.vrt`
    cloned from the terrain's — so the output drops straight in beside a RAS
    Mapper export and compares to it pixel for pixel, with no resampling of
    either side.

    Two reasons to prefer it over `export_wse_depth`:

    * **The terrain is right.**  Depth is computed against each source raster
      directly, so a high-priority source is not silently overwritten by a
      coarser one the way it is when the `.vrt` is read as a single raster.  See
      `vrt_sources` for the measurement.
    * **The output is the size it should be.**  A 1 m source upsampled to a 1 ft
      grid is ~11x the pixels for no information.

    Every source keeps its own grid, so the outputs are *not* a single array and
    cannot be differenced across sources — read them through the `.vrt`, or use
    `export_wse_depth` when a single grid is what is wanted.  Differencing two
    *plans* needs no care at all here, though: there is no `bounds` parameter
    because each output covers its source's **full extent**, exactly as RAS
    Mapper writes it, so every plan mapped against one terrain lands on the same
    grid per source and `difference_rasters` works file for file.  Writing the
    full extent is also what keeps the cloned `.vrt` correct — its `SrcRect` and
    `DstRect` are the terrain's, and a windowed output would not fit them.  The
    cost is only NoData, which deflates to almost nothing.

    Parameters
    ----------
    hdf_path, out_dir, areas, wse_type, prefix, tile, depth_tol, dry_tol,
    allow_crs_mismatch, volume_check, mode
        As `export_wse_depth`.  `mode` defaults to `"horizontal"` here, because
        reproducing RAS Mapper's own output is what this layout is for.
    terrain_vrt : str
        Terrain `.vrt`.  A plain GeoTIFF has one source and works, but then
        `export_wse_depth` is the simpler call.

    Returns
    -------
    dict
        `wse_path` and `depth_path` (the two `.vrt` files), `wse_paths` and
        `depth_paths` (`{source: tif}` in priority order), `surfaces`, `wse`,
        `mapped_volumes`, `shapes` (`{source: (h, w)}`), `mode`.
    """
    import os

    import rasterio

    from hack_ras.results.reader import list_areas, read_wse

    _check_terrain_crs(hdf_path, terrain_vrt, allow_crs_mismatch)

    sources = vrt_sources(terrain_vrt)
    if areas is None:
        areas = list_areas(hdf_path)
    if not areas:
        raise ValueError(f"{hdf_path} has no 2D flow areas to map")

    surfaces = {}
    wse_by_area = {}
    for area in areas:
        wse_by_area[area] = read_wse(hdf_path, area, wse_type)
        surfaces[area] = build_wse_surface(
            hdf_path, area, wse_by_area[area], dry_tol=dry_tol, mode=mode
        )
    live = [s for s in surfaces.values() if len(s.triangles)]
    if not live:
        raise ValueError(
            f"no wet cells in {areas} of {hdf_path} at wse_type={wse_type!r}"
        )
    mesh = (min(s.bounds[0] for s in live), min(s.bounds[1] for s in live),
            max(s.bounds[2] for s in live), max(s.bounds[3] for s in live))

    os.makedirs(out_dir, exist_ok=True)
    if prefix is None:
        stem = os.path.splitext(os.path.basename(hdf_path))[0]
        prefix = f"{stem}_{wse_type.replace(' ', '_')}"

    mapped = {a: np.zeros(s.n_cell_slots, dtype=np.float64)
              for a, s in surfaces.items()}
    wse_paths: dict[str, str] = {}
    depth_paths: dict[str, str] = {}
    shapes: dict[str, tuple[int, int]] = {}

    # Priority order, highest first, so each source masks out every source
    # already written above it.
    for i, source in enumerate(sources):
        src_stem = os.path.splitext(os.path.basename(source))[0]
        w_path = os.path.join(out_dir, f"{prefix}_WSE.{src_stem}.tif")
        d_path = os.path.join(out_dir, f"{prefix}_Depth.{src_stem}.tif")
        opened = [rasterio.open(s) for s in sources[:i]]
        try:
            with rasterio.open(source) as terr:
                b = terr.bounds
                if (b.right <= mesh[0] or b.left >= mesh[2]
                        or b.top <= mesh[1] or b.bottom >= mesh[3]):
                    # A source that does not reach the mesh at all is normal on a
                    # terrain assembled from tiles; drop it from the mosaic.
                    logging.info("%s does not reach the mesh; skipped", src_stem)
                    continue
                # The source's own full extent, so the cloned .vrt still fits.
                _, h, w = _write_depth_grid(
                    surfaces, terr, (b.left, b.bottom, b.right, b.top),
                    w_path, d_path,
                    mapped=mapped, tile=tile, depth_tol=depth_tol,
                    volume_check=volume_check, label=source,
                    mask_sources=opened,
                )
        finally:
            for r in opened:
                r.close()
        wse_paths[source] = w_path
        depth_paths[source] = d_path
        shapes[source] = (h, w)

    if not depth_paths:
        raise ValueError(
            f"no source of {terrain_vrt} overlaps the mesh of {hdf_path}"
        )

    wse_vrt = clone_source_vrt(
        terrain_vrt, os.path.join(out_dir, f"{prefix}_WSE.vrt"), wse_paths)
    depth_vrt = clone_source_vrt(
        terrain_vrt, os.path.join(out_dir, f"{prefix}_Depth.vrt"), depth_paths)

    return {
        "wse_path": wse_vrt,
        "depth_path": depth_vrt,
        "wse_paths": wse_paths,
        "depth_paths": depth_paths,
        "surfaces": surfaces,
        "wse": wse_by_area,
        "mapped_volumes": mapped,
        "shapes": shapes,
        "mode": mode,
    }



# ---------------------------
# Validation
# ---------------------------

def check_cell_volumes(hdf_path: str, area: str, wse, mapped_volumes):
    """Compare the volume the map holds against the volume RAS stored.

    This is the accuracy test the module is built around.  RAS gets each cell's
    storage from its subgrid volume-elevation curve at the computed WSE; the
    mapped depth raster, integrated over the same cell, should reproduce it.  Any
    surface that slopes the water level between cell centres necessarily moves
    volume between cells, so the per-cell error measures exactly what the
    interpolation cost, and the total measures whether the map as a whole holds
    the right amount of water.

    Cells at the water's edge will not balance and are not expected to: RAS's
    volume-elevation curve integrates the subgrid terrain over the whole cell at
    1 ft (or finer), whereas the map clips against the same terrain only where
    the interpolated plane stands above it.  Read the interior cells for the
    interpolation error and the total for the mass balance.

    Returns a `pandas.DataFrame` indexed by cell with columns `ras_volume`,
    `mapped_volume`, `error`, `error_pct`, `wse`, `min_elev`, `wet`, plus attrs
    `total_ras`, `total_mapped`, `total_error_pct`.
    """
    import pandas as pd

    from hack_ras.results.reader import (
        interpolate_cell_volume,
        read_area_geometry,
        read_cell_volume_table,
    )

    geom = read_area_geometry(hdf_path, area)
    table = read_cell_volume_table(hdf_path, area)
    wse = np.asarray(wse, dtype=np.float64)
    mapped_volumes = np.asarray(mapped_volumes, dtype=np.float64)

    n = len(mapped_volumes)
    ras = np.zeros(n, dtype=np.float64)
    for i in range(n):
        if not np.isfinite(wse[i]) or np.isnan(geom.min_elevations[i]):
            continue
        ras[i] = interpolate_cell_volume(table, i, wse[i], geom.plan_areas[i])

    err = mapped_volumes - ras
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(ras > 0, 100.0 * err / ras, np.nan)

    df = pd.DataFrame({
        "ras_volume": ras,
        "mapped_volume": mapped_volumes,
        "error": err,
        "error_pct": pct,
        "wse": wse[:n],
        "min_elev": geom.min_elevations[:n],
        "wet": (mapped_volumes > 0) | (ras > 0),
    })
    df.attrs["total_ras"] = float(ras.sum())
    df.attrs["total_mapped"] = float(mapped_volumes.sum())
    df.attrs["total_error_pct"] = (
        100.0 * (mapped_volumes.sum() - ras.sum()) / ras.sum() if ras.sum() else np.nan
    )
    return df


def grid_overlap(sa, sb, tol_px: float = 1e-6):
    """The ground two open rasters both describe, as a window into each.

    Returns `(window_a, window_b)` — always the same size — or `None` when the
    two cannot be compared without resampling.  When the rasters are the same
    grid the windows are simply both rasters in full, so this subsumes an
    equality test.

    Two conditions, and only two.  **Same pixel size**, because a 1 ft raster
    and a 3.28 ft raster describe the same ground with different samples and no
    window makes them line up.  And **origins a whole number of pixels apart**,
    so the two pixel lattices interlock: a sub-pixel offset means every pixel of
    one straddles four of the other, which again is resampling.  Given both, the
    overlapping pixels correspond exactly, one for one, and differencing them
    invents nothing.

    Neither test is bit-exact, for the same reason the pixel size is compared
    loosely: two producers can derive one grid and disagree in the last ULP.
    RAS Mapper writes the Hillside s04 tile at 3.2808333333333586 ft and
    `export_wse_depth_per_source` at 3.2808333333333555 (measured 2026-09-15) —
    3e-15 ft, accumulating to 3e-11 ft across the 10,888 rows, in a 3.28 ft
    pixel.  Refusing over that is a false negative, and it blocked comparing the
    two producers at all.  `tol_px` sits five orders above that round-off and
    six below the one whole pixel a real misalignment moves a corner.

    Extents need NOT match.  Two plans mapped in separate runs on one terrain
    land on the same lattice but cover the union of whatever meshes were in each
    run — measured on the test fixture: p02 (g02) and p07 (g05) come out
    3101x1559 and 3101x1897, origins exactly 338 pixels apart.  Those overlap
    perfectly over the ground they share, and the old shape-equality guard
    refused them for no reason a caller could act on.
    """
    from rasterio.windows import Window

    ta, tb = sa.transform, sb.transform
    if ta.b or ta.d or tb.b or tb.d:
        return None                       # rotated; no axis-aligned window fits
    if (abs(ta.a - tb.a) > tol_px * abs(ta.a)
            or abs(ta.e - tb.e) > tol_px * abs(ta.e)):
        return None                       # different resolution

    # Where b's origin sits in a's pixel index space.  Must be a whole pixel.
    dx, dy = (tb.c - ta.c) / ta.a, (tb.f - ta.f) / ta.e
    if abs(dx - round(dx)) > tol_px or abs(dy - round(dy)) > tol_px:
        return None                       # lattices interleave, not interlock
    dx, dy = round(dx), round(dy)

    c0, r0 = max(0, dx), max(0, dy)
    c1, r1 = min(sa.width, dx + sb.width), min(sa.height, dy + sb.height)
    if c1 <= c0 or r1 <= r0:
        return None                       # same lattice, but disjoint ground
    w, h = c1 - c0, r1 - r0
    return Window(c0, r0, w, h), Window(c0 - dx, r0 - dy, w, h)


def difference_rasters(path_a, path_b, out_path, *, tile: int = 2048,
                       treat_dry_as_zero: bool = True):
    """Write `b - a` over the ground two rasters both cover.

    The inputs must share a pixel lattice — same resolution, origins a whole
    number of pixels apart — which `grid_overlap` decides and which is exactly
    the condition under which the two can be subtracted without resampling.
    They will when both came from `export_wse_depth` against one terrain, and
    also when one is a RAS Mapper export of the same terrain source.  Nothing is
    resampled and no tolerance is applied to the values, so the result is exact
    wherever both are wet.

    **The extents need not match.**  The output covers their intersection, and
    its transform is that window's, so it is the same lattice cropped to the
    ground where an answer exists.  Where one input has data and the other does
    not reach at all, there is nothing to difference — that is absence of
    evidence, not a change of zero, and writing it would be a lie a depth map
    cannot be distinguished from real water.  A caller that needs to know it
    happened should compare the returned raster's shape against its inputs'.

    Disjoint inputs raise, as do inputs at different resolutions or on
    interleaved lattices; none of those can be honoured without resampling.

    `treat_dry_as_zero` governs the only real decision here.  With it on (the
    default, and the right one for a *depth* difference) a pixel wet in one run
    and dry in the other is differenced against zero, so the change in
    inundation extent shows up as the depth that appeared or disappeared, which
    is usually the point of the comparison.  Pixels dry in both stay NoData.
    With it off, only pixels wet in both are written — which is what a *WSE*
    difference wants, since the elevation of a dry pixel is not zero and
    differencing against zero would produce a meaningless several-hundred-foot
    value.

    Returns `out_path`.
    """
    import rasterio
    from rasterio.windows import Window
    from rasterio.windows import transform as window_transform

    with rasterio.open(path_a) as sa, rasterio.open(path_b) as sb:
        pair = grid_overlap(sa, sb)
        if pair is None:
            raise ValueError(
                f"{path_a} and {path_b} cannot be differenced without "
                "resampling: they must share a pixel size and sit a whole "
                "number of pixels apart, and must overlap"
            )
        win_a, win_b = pair
        height, width = int(win_a.height), int(win_a.width)

        nodata = -9999.0
        profile = sa.profile.copy()
        profile.update(dtype="float32", nodata=nodata, compress="deflate",
                       predictor=3, tiled=True, blockxsize=256, blockysize=256,
                       BIGTIFF="YES", width=width, height=height,
                       transform=window_transform(win_a, sa.transform))
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0 in range(0, height, tile):
                h = min(tile, height - r0)
                for c0 in range(0, width, tile):
                    w = min(tile, width - c0)
                    win = Window(c0, r0, w, h)
                    a = sa.read(1, window=Window(
                        win_a.col_off + c0, win_a.row_off + r0, w, h)
                    ).astype(np.float64)
                    b = sb.read(1, window=Window(
                        win_b.col_off + c0, win_b.row_off + r0, w, h)
                    ).astype(np.float64)
                    wet_a = a != (sa.nodata if sa.nodata is not None else nodata)
                    wet_b = b != (sb.nodata if sb.nodata is not None else nodata)
                    if treat_dry_as_zero:
                        keep = wet_a | wet_b
                        a = np.where(wet_a, a, 0.0)
                        b = np.where(wet_b, b, 0.0)
                    else:
                        keep = wet_a & wet_b
                    if not keep.any():
                        continue
                    out = np.full((h, w), nodata, dtype=np.float32)
                    out[keep] = (b - a)[keep].astype(np.float32)
                    dst.write(out, 1, window=win)
    return out_path


def area_bounds(hdf_paths, areas: list[str] | None = None):
    """Union bounding box of the 2D flow area perimeters, as (x0, y0, x1, y1).

    Pass it to `export_wse_depth(bounds=...)` so several plans land on the same
    grid and can be differenced without resampling.  Using the mesh perimeter
    rather than the wet extent makes the grid a property of the geometry, so it
    is identical for every plan on that geometry regardless of how far the water
    spread.

    `hdf_paths` takes one path or several.  **Pass every plan being compared.**
    With one shared mesh the union is that mesh and nothing changes; when the
    meshes differ, taking the bounds from one plan silently clips wherever the
    other reaches further.
    """
    from hack_ras.results.reader import list_areas

    if isinstance(hdf_paths, (str, bytes, os.PathLike)):
        hdf_paths = [hdf_paths]
    hdf_paths = [str(p) for p in hdf_paths]
    if not hdf_paths:
        raise ValueError("area_bounds needs at least one plan HDF")

    xs0 = ys0 = np.inf
    xs1 = ys1 = -np.inf
    for path in hdf_paths:
        wanted = list_areas(path) if areas is None else areas
        with h5py.File(path, "r") as hdf:
            for area in wanted:
                key = f"Geometry/2D Flow Areas/{area}/Perimeter"
                if key not in hdf:
                    # An area named for one plan need not exist in another.
                    continue
                perim = hdf[key][:]
                xs0 = min(xs0, float(perim[:, 0].min()))
                xs1 = max(xs1, float(perim[:, 0].max()))
                ys0 = min(ys0, float(perim[:, 1].min()))
                ys1 = max(ys1, float(perim[:, 1].max()))
    if not np.isfinite([xs0, ys0, xs1, ys1]).all():
        raise ValueError(f"no 2D flow area perimeters found in {hdf_paths}")
    return xs0, ys0, xs1, ys1


# Arrays that have to agree, element for element, for two plans to be sharing a
# mesh.  Coordinates alone are not enough: a geometry edit can leave the cell
# centres alone and still move the terrain under them.
_MESH_IDENTITY_KEYS = (
    "Cells Center Coordinate",
    "Cells Minimum Elevation",
    "Cells Surface Area",
    "FacePoints Coordinate",
    "Faces Minimum Elevation",
)


def same_mesh(hdf_a: str, hdf_b: str, areas: list[str] | None = None,
              atol: float = 1e-6) -> bool:
    """Do two plans compute on the same mesh, cell for cell?

    True only when both carry the same 2D flow areas, each with the same cell
    count, and every array in `_MESH_IDENTITY_KEYS` agrees element for element
    within `atol`.  That is the condition under which cell *i* means the same
    cell in both plans, so results can be compared per cell with no
    interpolation and no resampling anywhere.

    Two geometries written out separately are not bit-identical even when the
    mesh was never touched — re-serialisation moves the last bits.  Measured on
    `NKC_Hillside_Levee` g01 (`EC gravity flow`) vs g03 (`FC gravity flow`), the
    worst disagreement across all five arrays and all 4107 cells is **4e-9 ft**,
    which is why this compares within a tolerance rather than exactly.  Those
    two differ in infiltration, land cover and flow, not in the mesh.

    The counterexample is in the test fixture: `Model.g02` vs `Model.g05` keep
    the same `Interior` footprint but refine it from 59 cells to 196, and both
    refine *and* extend `Watershed`, 59 cells to 386 over an extra 1.43M ft**2.

    NaN is treated as equal to NaN, because perimeter dummy cells carry NaN
    minimum elevations by design.
    """
    from hack_ras.results.reader import list_areas

    names_a = set(list_areas(hdf_a))
    names_b = set(list_areas(hdf_b))
    wanted = (names_a & names_b) if areas is None else set(areas)
    if areas is None and names_a != names_b:
        return False
    if not wanted:
        return False

    with h5py.File(hdf_a, "r") as fa, h5py.File(hdf_b, "r") as fb:
        for area in sorted(wanted):
            base = f"Geometry/2D Flow Areas/{area}"
            if base not in fa or base not in fb:
                return False
            ga, gb = fa[base], fb[base]
            for key in _MESH_IDENTITY_KEYS:
                if (key in ga) != (key in gb):
                    return False
                if key not in ga:
                    continue
                a, b = ga[key][:], gb[key][:]
                if a.shape != b.shape:
                    return False
                if not np.allclose(a, b, atol=atol, rtol=0, equal_nan=True):
                    return False
    return True
