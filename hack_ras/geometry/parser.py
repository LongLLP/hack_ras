# hack_ras/geometry/parser.py

from __future__ import annotations
from typing import List, Optional
from .model import GeometryFile, CrossSection, XSGISCutLine
from .blocks import river_reach, xs_metadata, xs_gis, xs_sta_elev, xs_ineff
from .blocks import xs_mann, xs_bank_sta, xs_levee, xs_block_obstruct

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

        # Track (xs, start_line) for post-loop end-line assignment
        _xs_starts: List[tuple] = []  # (CrossSection, start_line_index)

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
                current_xs._raw_line_start = i
                geom.add_cross_section(current_xs)
                _xs_starts.append((current_xs, i))
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

            # --- #Sta/Elev= ---
            if line.startswith("#Sta/Elev="):
                if current_xs is None:
                    raise ValueError("Found #Sta/Elev= before an XS was created.")
                sta_elev, consumed = xs_sta_elev.parse_sta_elev(lines, i)
                current_xs.sta_elev = sta_elev
                i += consumed
                continue

            # --- #Mann= ---
            if line.startswith("#Mann="):
                if current_xs is None:
                    raise ValueError("Found #Mann= before an XS was created.")
                manning_def, consumed = xs_mann.parse_mann(lines, i)
                current_xs.manning_def = manning_def
                i += consumed
                continue

            # --- Bank Sta= ---
            if line.startswith("Bank Sta="):
                if current_xs is None:
                    raise ValueError("Found Bank Sta= before an XS was created.")
                bank_sta, consumed = xs_bank_sta.parse_bank_sta(lines, i)
                current_xs.bank_stations = bank_sta
                i += consumed
                continue

            # --- #XS Ineff= ---
            if line.startswith("#XS Ineff="):
                if current_xs is None:
                    raise ValueError("Found #XS Ineff= before an XS was created.")
                ineff, consumed = xs_ineff.parse_ineff(lines, i)
                current_xs.ineff = ineff
                i += consumed
                continue

            # --- Levee= ---
            if line.startswith("Levee="):
                if current_xs is None:
                    raise ValueError("Found Levee= before an XS was created.")
                levee, consumed = xs_levee.parse_levee(lines, i)
                current_xs.levee = levee
                i += consumed
                continue

            # --- #Block Obstruct= ---
            if line.startswith("#Block Obstruct="):
                if current_xs is None:
                    raise ValueError("Found #Block Obstruct= before an XS was created.")
                obstr, consumed = xs_block_obstruct.parse_block_obstruct(lines, i)
                current_xs.blocked_obstructions = obstr
                i += consumed
                continue

            i += 1

        # Assign _raw_line_end for each XS: end = start of the next XS (or EOF)
        for j, (xs, start) in enumerate(_xs_starts):
            if j + 1 < len(_xs_starts):
                xs._raw_line_end = _xs_starts[j + 1][1]
            else:
                xs._raw_line_end = N

        return geom
