# Plan-ops recipes

Copy-paste recipes for the project-level API: health, plan cloning, deletion,
geometry compaction and plan settings. Paths below are from the Hillside levee
model, kept as provenance — swap in your own `.prj`.

Several of these **delete files**. Read the comment above each block before running
it.

## Model status

Run this first, run it again after edits, diff the two.

```python
from hack_ras import RasProject
from hack_ras.project.health import project_health, format_health

project = RasProject(r"C:\Users\2161jap\Desktop\hack_ras_local\Model_Hillside\Current_Model_20260824\NKC_Hillside_Levee.prj")

print(format_health(project_health(project)))
```

## New plan suite, changing the geometry file

```python
from hack_ras import RasProject
from hack_ras.project.plans import clone_plan

project = RasProject(r"C:\Users\2161jap\Desktop\hack_ras_local\Model_Hillside\Current_Model_20260821_update\NKC_Hillside_Levee.prj")

jobs = [
    ("p51", "FC 002year n only"),
    ("p52", "FC 010year n only"),
    ("p53", "FC 050year n only"),
    ("p54", "FC 100year n only"),
    ("p55", "FC 500year n only"),
    ("p56", "FC 050year breach L4 n only"),
    ("p57", "FC 100year breach L4 n only"),
    ("p58", "FC 500year breach L4 n only"),
    ("p59", "FC 050year breach L7 n only"),
    ("p60", "FC 100year breach L7 n only"),
    ("p61", "FC 500year breach L7 n only"),
]

for source, title in jobs:
    new_id = clone_plan(project, source, title, line_edits={"Geom File=": "Geom File=g09"})
    print(source, "->", new_id, title)
```

## Delete plans

Takes the `.p##`, `.p##.hdf`, run artifacts, `.rst` files, the `.prj` entry, and the
`RASPlan` / `RASResults` layers in the `.rasmap`.

```python
from hack_ras import RasProject
from hack_ras.project.plans import delete_plans

project = RasProject(r"C:\Users\2161jap\Desktop\hack_ras_local\Model_Hillside\Current_Model_20260824\NKC_Hillside_Levee.prj")

report = delete_plans(project, "25-61")

print(len(report["deleted_plans"]), "plans,", len(report["deleted"]), "files")
for w in report["warnings"]:
    print("WARN", w)
```

## Delete unused geometries, then close the numbering gap

`compact_geoms` rewrites `Geom File=` in every plan and the `.rasmap` along with the
files.

```python
from hack_ras import RasProject
from hack_ras.project.geoms import delete_geoms, compact_geoms

project = RasProject(r"C:\Users\2161jap\Desktop\hack_ras_local\Model_Hillside\Current_Model_20260824\NKC_Hillside_Levee.prj")

delete_geoms(project, "g08-g09")   # add force=True if a plan still uses one
print(compact_geoms(project))      # {'g10': 'g08'}
```

## Plan settings

Output intervals, computation interval, time window, title.

GUI label -> keyword: `Mapping Output` = `mapping_interval`,
`Hydrograph Output` = `hydrograph_interval`, `Detailed Output` = `detailed_interval`.
`output_intervals=` sets those three at once. Nothing is written unless every value
and every plan validates.

```python
from hack_ras import RasProject
from hack_ras.project.plan_settings import read_plan_settings, set_plan_settings

project = RasProject(r"C:\Users\2161jap\Desktop\hack_ras_local\Model_Hillside\Current_Model\NKC_Hillside_Levee.prj")

print(set_plan_settings(project, "5-24", output_intervals="5MIN"))

print(set_plan_settings(project, "5-24", computation_interval="3SEC"))

print(set_plan_settings(project, "p05", start="02JAN2025,0100", end="03JAN2025,2400"))

print(set_plan_settings(project, "p05", title="002year 5min", short_id="002yr5"))

for pid in [f"p{n:02d}" for n in range(1, 25)]:
    s = read_plan_settings(project, pid)
    print(pid, s.computation_interval, s.mapping_interval, s.hydrograph_interval,
          s.detailed_interval, s.window_raw)
```
