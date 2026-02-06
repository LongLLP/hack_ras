# hack_ras/geometry/blocks/xs_metadata.py

from __future__ import annotations
from ..model import CrossSection

def parse_type_rm_length(lines, index, river, reach):
    """
    Parses the XS header which includes the River Station.
    Returns a CrossSection and number of lines consumed.
    """
    line = lines[index].strip()
    # Format like: Type RM Length= 1 ,21 ,1368.73,1358.08,1355.88
    parts = line.split("=")[1].split(",")
    rm = parts[1].strip()
    station = rm  # RAS uses RM as station identifier

    cs = CrossSection(river=river, reach=reach, station=station)
    cs.rm_length = line

    return cs, 1