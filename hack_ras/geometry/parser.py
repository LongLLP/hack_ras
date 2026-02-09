# hack_ras/geometry/parser.py

from __future__ import annotations
from typing import List, Optional
from .model import GeometryFile, CrossSection, XSGISCutLine
from .blocks import river_reach, xs_metadata, xs_gis

class GeometryParser:
    """
    A block-driven parser that reads line-by-line, identifies block starts,
    and dispatches to specific block handlers in hack_ras.geometry.blocks.
    """

    def parse_file(self, path: str) -> "GeometryFile":
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return self.parse(f.readlines())

    def parse(self, lines: List[str]) -> GeometryFile:
        geom = GeometryFile()
        geom.raw_lines = lines[:]  # store unmodified

        current_river = None
        current_reach = None
        current_xs = None
        
        i = 0
        N = len(lines)

        while i < N:
            line = lines[i].rstrip("\n")

            # --- Geom Title ---
            if line.startswith("Geom Title="):
                geom.title = line.split("=", 1)[1].strip()
                i += 1
                continue

            # --- River Reach= ---
            if line.startswith("River Reach="):
                river, reach = river_reach.parse_river_reach(line)
                current_river = river
                current_reach = reach
                i += 1
                continue

            # --- Type RM Length (XS metadata header) ---
            if line.startswith("Type RM Length"):
                current_xs, consumed = xs_metadata.parse_type_rm_length(
                    lines, i, current_river, current_reach
                )
                geom.add_cross_section(current_xs)
                i += consumed
                continue

            # --- XS GIS Cut Line= ---
            if line.startswith("XS GIS Cut Line="):
                if current_xs is None:
                    raise ValueError("Found XS GIS Cut Line before an XS was created.")
                cline, consumed = xs_gis.parse_cutline(lines, i)
                current_xs.cutline = cline
                i += consumed
                continue

            # To do:
            #   #Sta/Elev blocks
            #   #Mann blocks
            #   Bank Sta=...
            #   Inefficiency blocks
            #   etc.
            # For now we skip
            i += 1

        return geom