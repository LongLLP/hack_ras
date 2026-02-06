# hack_ras/examples/load_and_list_xs.py

from hack_ras.geometry.parser import GeometryParser

with open("UHM.g01") as f:
    lines = f.readlines()

parser = GeometryParser()
geom = parser.parse(lines)

print("Geometry Title:", geom.title)

for river_name, river in geom.rivers.items():
    for reach_name, reach in river.reaches.items():
        print(f"\n{river_name} / {reach_name}")
        for cs in reach.cross_sections:
            print("  XS:", cs.station,
                  "Cutline points:", len(cs.cutline.points) if cs.cutline else "NONE")