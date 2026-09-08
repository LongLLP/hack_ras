# hack_ras/geometry/parser.py

from __future__ import annotations
from typing import List, Optional
from .model import GeometryFile, CrossSection, XSGISCutLine, StorageArea2D, Connection
from .blocks import river_reach, xs_metadata, xs_gis, xs_sta_elev, xs_ineff
from .blocks import xs_mann, xs_bank_sta, xs_levee, xs_block_obstruct
from .blocks import storage_area_2d, connection as conn_block

class GeometryParser:
    """
    A block-driven parser that reads line-by-line, identifies block starts,
    and dispatches to specific block handlers in hack_ras.geometry.blocks.
    """

    def parse_file(self, path: str) -> "GeometryFile":
            # utf-8-sig drops a leading UTF-8 BOM if present (some Windows
            # editors add one). Without it the BOM lands in raw_lines as
            # '﻿', which hides the line-1 'Geom Title=' key and makes
            # GeometryWriter raise UnicodeEncodeError. Every GeometryWriter
            # call site writes a NEW file (shifter, merge, Mesh_Health
            # snapper), so there is no source BOM to preserve here.
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as f:
                return self.parse(f.readlines())

    def parse(self, lines: List[str]) -> GeometryFile:
        geom = GeometryFile()
        geom.raw_lines = lines[:]  # store unmodified

        current_river = None
        current_reach = None
        current_xs = None
        current_storage_area = None  # name of the storage area currently being read
        current_conn = None          # Connection currently being read

        # Track (xs, start_line) for post-loop end-line assignment
        _xs_starts: List[tuple] = []  # (CrossSection, start_line_index)
        _conn_starts: List[tuple] = []  # (Connection, start_line_index)

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

            # --- Storage Area= (2D flow area name header) ---
            # Matches only the name line ("Storage Area=Perimeter 1  ,,"), not
            # the sibling "Storage Area <thing>=" lines (Surface Line, Type,
            # 2D Points, Mannings, ...) or "BC Line Storage Area=".
            if line.startswith("Storage Area="):
                current_storage_area = line.split("=", 1)[1].split(",")[0].strip()
                i += 1
                continue

            # --- Storage Area 2D Points= (2D-mesh cell seeds) ---
            if line.startswith("Storage Area 2D Points="):
                pts, consumed = storage_area_2d.parse_2d_points(lines, i)
                geom.storage_areas_2d.append(StorageArea2D(
                    name=current_storage_area or "",
                    points=pts,
                    _header_line=i,
                    _data_start=i + 1,
                    _data_end=i + consumed,
                ))
                i += consumed
                continue

            # --- Connection= (SA/2D connection name header) ---
            # Matches only the name line ("Connection=L4 Ozark-Holmes ,x,y"),
            # never the sibling "Connection <thing>=" / "Conn <thing>=" lines.
            if line.startswith("Connection="):
                name, label_xy = conn_block.parse_connection_header(line)
                current_conn = Connection(name=name, label_xy=label_xy,
                                          _raw_line_start=i)
                geom.connections[name] = current_conn
                _conn_starts.append((current_conn, i))
                i += 1
                continue

            if current_conn is not None:
                # --- Connection Line= (centerline polyline) ---
                if line.startswith("Connection Line="):
                    pts, consumed = conn_block.parse_connection_line(lines, i)
                    current_conn.centerline = pts
                    i += consumed
                    continue

                # --- Conn Weir SE= (spillway / levee crest profile) ---
                if line.startswith("Conn Weir SE="):
                    pairs, consumed = conn_block.parse_conn_sta_elev(lines, i)
                    current_conn.weir_profile = pairs
                    i += consumed
                    continue

                # --- Connection Centerline Profile= (terrain under centerline) ---
                if line.startswith("Connection Centerline Profile="):
                    pairs, consumed = conn_block.parse_conn_sta_elev(lines, i)
                    current_conn.terrain_profile = pairs
                    i += consumed
                    continue

                if line.startswith("Connection Desc="):
                    current_conn.description = conn_block.parse_text_field(line)
                    i += 1
                    continue
                if line.startswith("Connection Up SA="):
                    current_conn.up_sa = conn_block.parse_text_field(line)
                    i += 1
                    continue
                if line.startswith("Connection Dn SA="):
                    current_conn.dn_sa = conn_block.parse_text_field(line)
                    i += 1
                    continue
                if line.startswith("Connection Last Edited Time="):
                    current_conn.last_edited = conn_block.parse_text_field(line)
                    i += 1
                    continue
                if line.startswith("Conn Weir Coef="):
                    current_conn.weir_coef = conn_block.parse_float_field(line)
                    i += 1
                    continue
                if line.startswith("Conn Weir WD="):
                    current_conn.weir_width = conn_block.parse_float_field(line)
                    i += 1
                    continue
                if line.startswith("Conn Routing Type="):
                    rt = conn_block.parse_float_field(line)
                    current_conn.routing_type = None if rt is None else int(rt)
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

        # Same for connections: end = start of the next connection (or EOF).
        for j, (conn, start) in enumerate(_conn_starts):
            if j + 1 < len(_conn_starts):
                conn._raw_line_end = _conn_starts[j + 1][1]
            else:
                conn._raw_line_end = N

        return geom
