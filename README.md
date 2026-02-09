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
Right now, the tools are incomplete. They focus on understanding file formats.
Implemented so far:
1. Basic parsing logic for selected text-based files.
2. Simple extraction of structured data.
3. Minimal data manipulation utilities.
4. Tools organized into early modular components.

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
1. Improve and stabilize geometry parsing.
2. Add simple writer utilities.

Why This Repository Exists
==========================
To build tools to make my personal workflows easier and more systemetized.

Contributions
=============
This project is currently a personal learning project and I'm not seeking contributions.
