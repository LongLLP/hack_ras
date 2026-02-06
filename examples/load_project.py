# examples/load_project.py
from hack_ras.project.parser import parse_project_file

prj = parse_project_file("C:\Stream.prj")  # or your actual .prj path

print("Project Title:", prj.title)
print("Geometry ID :", prj.geom_file_id)
print("Plan ID     :", prj.plan_file_id)
print("Unsteady ID :", prj.unsteady_file_id)
print("DSS File    :", prj.dss_file)