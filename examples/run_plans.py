# hack_ras/examples/run_plans.py
#
# Computes plans with HEC-RAS 7.0 (Ras.exe -c), one at a time, and prints a
# per-plan report. Safe while your own RAS is open. A failed run REPLACES that
# plan's previous results. See docs/plan_ops_recipes.md, "Run plans".
from hack_ras import RasProject, compute

project = RasProject(r"X:\...\Model.prj")

report = compute.run_plans(project, "1-20", "7.0")
print(compute.format_report(report))
