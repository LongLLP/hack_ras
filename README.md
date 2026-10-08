Tools for reading and manipulating HEC‑RAS project files. Built primarily for my own workflow and experimentation.
While the project is public, it’s not currently intended for external contributions. The code and documentation are written mainly for my own use, self-teaching, and future project work.

Purpose:
========
HEC-RAS uses text-based input files that can be manipulated outside the HEC-RAS GUI. This repo contains tools to:
1. Inspect and/or extract geometry information.
2. Make bulk edits.
3. Modify inputs based on user-defined files.
4. Read results, map them, and run plans without the GUI.

Current Capabilities:
=====================
The full reference is docs/ai_context.md (one section per subsystem); copy-paste
recipes are in docs/plan_ops_recipes.md, and short scripts in examples/.

Project files and plan management (hack_ras/project/):
1. Project entry point — RasProject: parses the .prj (the authoritative file list), resolves file IDs, finds the model CRS, and gives a bound .rasmap accessor.
2. Plan file operations — renumber (single or bulk, chain/cycle-safe), insert a numbering gap, compact, reorder, clone with edits, retitle, delete with outputs; carries run artifacts and restart files along and updates restart references in .u files and tokens in the .rasmap.
3. Geometry and flow file operations — the same operations for .g## files and for steady (.f##) and unsteady (.u##) flow files, rewriting every referencing plan.
4. Plan settings — read and set computation/output intervals, the simulation time window, and the plan title/short ID, validated against what the RAS GUI offers.
5. .prj sync and sorting, and .rasmap layer cleanup and sorting.
6. Project health inspector — read-only inventory of plans/geometries/flows with cross-references, layer associations (terrain, Manning's n, infiltration), and consistency checks (orphan files, stale .prj entries, duplicate titles, unused files, .rasmap problems, active runs).
7. Breach definitions — decodes every Breach Loc/Geom/Start field of a plan.
8. Run plans headlessly — HEC-RAS 7.0 and later only: Ras.exe -c per plan, success read from the results HDF, exact-version guard, restart-order check, and detection of runs stopped on an error dialog.

Geometry files (hack_ras/geometry/):
9. Geometry parsing with lossless round-trip write-back — rivers/reaches, cross sections (station/elevation, Manning's n, ineffective flow areas, bank stations, levees, blocked obstructions), GIS cut lines, SA/2D connections, culvert groups, and 2D cell-seed points.
10. Geometry edits — shift cross-section GIS cut lines along their alignment, and merge cross sections from two geometry files (used by the sibling RAS_xsedit app).
11. Active flow — active (effective) flow extent per cross section from ineffective areas, levees and obstructions.

Results (hack_ras/results/):
12. Plan results (.p##.hdf) — 2D cell geometry, water surface elevations and time of maximum, volume-elevation tables, volume accounting, SA/2D connections and bridges, culvert groups, breach results, pipe networks (nodes, conduits, profiles, rims) and pump stations; steady cross-section results. Layout differences between RAS versions are detected from the HDF itself.
13. Steady binary output (.O##) — water surface elevations from the steady output file, the only results a RAS 4.x plan has.

GIS (hack_ras/gis/):
14. WSE and depth mapping of 2D results (the horizontal mode matches RAS Mapper exactly) and raster differencing between plans.
15. Profile lines through 2D meshes with WSE and volume, mesh face Manning's n export, and line-in-polygon measurement (e.g. floodway widths).

Overview of HEC-RAS File Types:
===============================
.prj - Project File
-------------------
Contains:
1. Project metadata.
2. List of geometry, plan, and flow files.

.g## - Geometry Files
---------------------
Contains:
1. River network (river, reaches)
2. Cross-sections
3. Bridges, culverts, lateral structures, storage areas, junctions, etc.

.u## - Unsteady Flow Files
--------------------------
Contains:
1. Time series of flow or stage.
2. Other boundary conditions such as normal depth or rating curves.

.f## - Steady Flow Files
------------------------
Contains:
1. Peak flow values per profile.
2. Steady boundary conditions.

Note: the .prj registers steady flow as "Flow File=f##" and unsteady as
"Unsteady File=u##". Inside a .p## plan file, "Flow File=" is that plan's flow
reference and may name either an f## or a u## — same key, different meaning.

.p## - Plan Files
-----------------
Contains:
1. Reference to a single geometry and single flow file.
2. Simulation settings and parameters.

Project Goals (for now)
=======================
docs/TODO.md is the authoritative list. As of 2026-10-08 the open items are:
1. A writer (and merge support) for blocked obstructions and levees, which are parsed but not yet written.
2. The interior flood-volume peak for pipe-network analysis.
3. A dry-run/preview mode for the file operations (low priority).

Out of scope: running plans in HEC-RAS versions older than 7.0, and any use of the HEC-RAS COM controller.

Why This Repository Exists
==========================
To build tools to make my personal workflows easier and more systematized.

Contributions
=============
This project is currently a personal learning project and I'm not seeking contributions.
