# hack_ras/gis/wse_surface.py
# Requires: pip install hack_ras[gis,results]
"""
Mapping 2D results as a WSE surface interpolated on the mesh's own connectivity.

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
    "difference_rasters",
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
    """

    area: str
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

    Returns
    -------
    WseSurface

    Raises
    ------
    ValueError
        If `wse` does not match the area's cell count.
    KeyError
        If the HDF predates the perimeter face datasets (pre-RAS-7.0); there is no
        approximate fallback here, because a cell outline that does not follow the
        mesh boundary produces triangles that hang outside the mesh.
    """
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
        "%s: %d/%d cells wet, %d triangles, %d barrier faces, %d split face points",
        area, surface.n_wet, surface.n_cells, len(surface.triangles),
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
    depth_tol: float = 0.01,
    dry_tol: float = DRY_TOL,
    bounds: tuple[float, float, float, float] | None = None,
    allow_crs_mismatch: bool = False,
    volume_check: bool = True,
) -> dict:
    """Write WSE and depth GeoTIFFs for one plan.

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
        Pixels at or below this depth are left as NoData.
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

    Returns
    -------
    dict
        `wse_path`, `depth_path`, `surfaces` (`{area: WseSurface}`),
        `mapped_volumes` (`{area: (N,) float64 ft**3}`, zeros when
        `volume_check` is False), `wse` (`{area: (N,) float64}` as read),
        `bounds`, `shape`.
    """
    import os

    import rasterio
    from rasterio.windows import Window
    from rasterio.windows import transform as window_transform

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
            hdf_path, area, wse_by_area[area], dry_tol=dry_tol
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

    nodata = -9999.0
    mapped = {a: np.zeros(s.n_cell_slots, dtype=np.float64)
              for a, s in surfaces.items()}

    with rasterio.open(terrain_path) as terr:
        row_off, col_off, height, width = _snapped_grid(
            (xs0, ys0, xs1, ys1), terr.transform, terr.width, terr.height
        )
        if height <= 0 or width <= 0:
            raise ValueError(
                "the mesh does not overlap the terrain raster; check that "
                f"{terrain_path} is the terrain this geometry was built on"
            )
        win = Window(col_off, row_off, width, height)
        grid = window_transform(win, terr.transform)
        px_area = abs(grid.a * grid.e)

        profile = dict(
            driver="GTiff", dtype="float32", count=1, nodata=nodata,
            width=width, height=height, transform=grid, crs=terr.crs,
            tiled=True, blockxsize=256, blockysize=256,
            compress="deflate", predictor=3, zlevel=6, BIGTIFF="YES",
        )

        with rasterio.open(wse_path, "w", **profile) as dst_w, \
             rasterio.open(depth_path, "w", **profile) as dst_d:
            for r0 in range(0, height, tile):
                h = min(tile, height - r0)
                for c0 in range(0, width, tile):
                    w = min(tile, width - c0)
                    out_w = np.full((h, w), nodata, dtype=np.float32)
                    out_d = np.full((h, w), nodata, dtype=np.float32)

                    ground = terr.read(
                        1, window=Window(col_off + c0, row_off + r0, w, h)
                    ).astype(np.float64)
                    if terr.nodata is not None:
                        ground[ground == terr.nodata] = np.nan

                    wrote = False
                    for area, surf in surfaces.items():
                        if not len(surf.triangles):
                            continue
                        arr = np.full((h, w), np.nan, dtype=np.float32)
                        cel = (np.full((h, w), -1, dtype=np.int32)
                               if volume_check else None)
                        # `grid` is already the output window's transform, so
                        # the tile offsets are output-grid, not terrain-grid;
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
                        fresh = wet & (out_d == nodata)
                        out_w[fresh] = arr[fresh]
                        out_d[fresh] = depth[fresh].astype(np.float32)

                    if wrote:
                        dst_w.write(out_w, 1, window=Window(c0, r0, w, h))
                        dst_d.write(out_d, 1, window=Window(c0, r0, w, h))

    return {
        "wse_path": wse_path,
        "depth_path": depth_path,
        "surfaces": surfaces,
        "wse": wse_by_area,
        "mapped_volumes": mapped,
        "bounds": (grid.c, grid.f + grid.e * height,
                   grid.c + grid.a * width, grid.f),
        "shape": (height, width),
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


def difference_rasters(path_a, path_b, out_path, *, tile: int = 2048,
                       treat_dry_as_zero: bool = True):
    """Write `b - a` for two rasters that share a grid.

    Both inputs must have identical transform and shape — which they will when
    both were produced by `export_wse_depth` with the same `bounds` against the
    same terrain.  Nothing is resampled and no tolerance is applied, so the
    result is exact wherever both are wet.

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

    with rasterio.open(path_a) as sa, rasterio.open(path_b) as sb:
        if (sa.transform != sb.transform) or (sa.width, sa.height) != (sb.width, sb.height):
            raise ValueError(
                f"{path_a} and {path_b} are not on the same grid; re-export both "
                "with the same bounds before differencing"
            )
        nodata = -9999.0
        profile = sa.profile.copy()
        profile.update(dtype="float32", nodata=nodata, compress="deflate",
                       predictor=3, tiled=True, blockxsize=256, blockysize=256,
                       BIGTIFF="YES")
        with rasterio.open(out_path, "w", **profile) as dst:
            for r0 in range(0, sa.height, tile):
                h = min(tile, sa.height - r0)
                for c0 in range(0, sa.width, tile):
                    w = min(tile, sa.width - c0)
                    win = Window(c0, r0, w, h)
                    a = sa.read(1, window=win).astype(np.float64)
                    b = sb.read(1, window=win).astype(np.float64)
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
