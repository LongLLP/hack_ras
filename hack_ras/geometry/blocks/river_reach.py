# hack_ras/geometry/blocks/river_reach.py

from __future__ import annotations

def parse_river_reach(line: str) -> tuple[str, str]:
    # River Reach=RiverName,ReachName
    _, rest = line.split("=", 1)
    river, reach = rest.split(",", 1)
    return river.strip(), reach.strip()
