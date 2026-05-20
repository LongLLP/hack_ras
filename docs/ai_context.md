# Project AI Context – hack_ras

## What This Project Does
Python tools for parsing and manipulating HEC-RAS model files. HEC-RAS is hydraulic engineering software that stores model data in plain-text files with fixed formats and numeric suffixes (`.g01`, `.p02`, etc.).

## Key Design Principles
- One package per HEC-RAS file type (`project/` for `.prj`, `geometry/` for `.g##`, etc.)
- No admin dependencies — must run in Anaconda/Spyder without elevated privileges
- Emphasis on reproducibility and auditability
- Fail gracefully — do not crash on partial or malformed data; raise explicit exceptions instead

## File Naming Conventions
HEC-RAS uses a base name plus a typed numeric suffix:
| Suffix | File type |
|--------|-----------|
| `.prj` | Project file (key=value pairs, links to all others) |
| `.g##` | Geometry file (rivers, reaches, cross-sections, GIS cut lines) |
| `.p##` | Plan file |
| `.u##` | Unsteady flow file |
| `.f##` | Steady flow file |

`##` is a two-digit number (`01`, `02`, …). A project may have multiple geometry or plan files.

## Parsing Strategy
- **Block-driven**: `GeometryParser` dispatches to specialized handlers per block type (`River Reach=`, `Type RM Length=`, `XS GIS Cut Line=`, etc.)
- **Lossless roundtrip**: `GeometryFile` stores original raw lines; `GeometryWriter` writes them back unchanged. Structured fields are parsed on top of the raw lines, not instead of them.
- **Strict errors**: resolution functions (`resolve_default_geom`, etc.) raise typed exceptions (`ValueError`, `GeometryFileNotFound`) rather than returning `None`.

## Coding Standards
- Explicit error handling — no silent failures
- Functions should be testable in isolation
- Avoid global state — do not use module-level mutable variables; all state lives in dataclass instances created fresh per use

## Current Work
*(Last updated: 2026-05-20)*
- Completing cross-section data parsing: Sta/Elev, Manning, bank stations, and inefficiency blocks are recognized as "to do" in the parser but currently skipped
- Adding test coverage for the project parser (catalog.py) and utility modules (utils/)

## Known Constraints
- Windows environment
- No admin privileges
