# examples/load_project.py
from hack_ras.project.parser import parse_project_file

prj = parse_project_file("C:\Stream.prj")  # or your actual .prj path

print("Project Title:", prj.title)
print("Geometry IDs :", prj.geom_file_ids)
print("Plan IDs     :", prj.plan_file_ids)
print("Unsteady IDs :", prj.unsteady_file_ids)
print("DSS File     :", prj.dss_file)
