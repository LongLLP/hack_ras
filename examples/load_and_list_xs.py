# hack_ras/examples/load_and_list_xs.py
from pathlib import Path
from hack_ras.geometry.parser import GeometryParser

data = Path(__file__).resolve().parents[1] / "tests" / "data" / "beaver.g01"

gp = GeometryParser()
geom = gp.parse_file(str(data))

print("Geometry Title:", geom.title)
for river_name, river in geom.rivers.items():
    for reach_name, reach in river.reaches.items():
        print(f"\n{river_name} / {reach_name}")
        for cs in reach.cross_sections[:5]:  # print just a few to keep output short
            print("  XS:", cs.station, "Cutline points:", len(cs.cutline.points) if cs.cutline else "NONE")