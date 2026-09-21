# hack_ras/examples/project_health_report.py
#
# Read-only. Prints the plan/geometry/flow table and the orphan checks for a
# project. This is the first thing to run on any model, and the thing to run
# again afterward so the two can be diffed.
from pathlib import Path

from hack_ras import RasProject
from hack_ras.project.health import project_health, format_health

prj = Path(__file__).resolve().parents[1] / "tests" / "data" / "Baxter" / "Baxter.prj"

project = RasProject(str(prj))

print(format_health(project_health(project)))
