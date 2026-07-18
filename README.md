Tools for reading and manipulating HEC‑RAS project files. Built primarily for my own workflow and experimentation.
While the project is public, it’s not currently intended for external contributions. The code and documentation are written mainly for my own use, self-teaching, and future project work.

Purpose:
========
HEC-RAS uses text-based input files that can be manipulated outside the HEC-RAS GUI. This repo contains tools to:
1. Inspect and/or extract geometry information.
2. Make bulk edits.
3. Modify inputs based on user-defined files.

Current Capabilities:
=====================
Implemented so far:
1. Project file (.prj) parsing — key/value extraction, file ID resolution.
2. Geometry file (.g##) parsing — river/reach/cross-section structure, GIS cut lines; lossless roundtrip write-back.
3. Plan results (.p##.hdf) reading — 2D flow area cell geometry, water surface elevations, cell volume-elevation tables, SA 2D Area Conn (levee/lateral structure) HW/TW cell time series with sub-step accurate time-of-maximum, and pipe network geometry and time series.
4. GIS profile computation — ordered profile stations along a line with WSE assignment and cell volume interpolation.
5. Plan file operations — renumber plans (single or bulk with chain/cycle handling), clone with edits, delete with outputs; renames run artifacts and restart files, updates restart references in .u files and plan tokens in the .rasmap; .prj sync (drop entries for missing files) and entry sorting.

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

.p## - Plan Files
-----------------
Contains:
1. Reference to a single geometry and single flow file.
2. Simulation settings and parameters.

Project Goals (for now)
=======================
1. Complete cross-section data parsing (Sta/Elev, Manning, bank stations, inefficiency blocks).
2. Broaden test coverage for the project parser and utility modules.

Why This Repository Exists
==========================
To build tools to make my personal workflows easier and more systemetized.

Contributions
=============
This project is currently a personal learning project and I'm not seeking contributions.
