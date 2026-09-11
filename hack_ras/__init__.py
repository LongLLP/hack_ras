"""hack_ras — tools for parsing and manipulating HEC-RAS model files.

`RasProject` is the entry point for any project. The project-operations
modules are re-exported here purely for convenience, so a script needs one
import line instead of one per subpackage:

    from hack_ras import RasProject, plans, sync
    plans.reorder_plans(project, [...])
    sync.sort_prj_entries(project, kinds=("plan",))

The modules themselves are re-exported rather than their functions: `plans`,
`geoms`, and `flows` have deliberately parallel APIs (renumber/insert gap/
compact/reorder/clone/delete), so the module name at the call site is what
says which file type is being operated on. Their canonical home is unchanged —
`hack_ras.project.plans` and `hack_ras.plans` are the same module object.
None of them import h5py/geopandas at module level, so this costs nothing.
"""
from hack_ras.project import (
    flows, geoms, health, plan_settings, plans, rasmap, sync,
)
from hack_ras.project.ras_project import RasProject

__all__ = ["RasProject", "flows", "geoms", "health", "plan_settings",
           "plans", "rasmap", "sync"]
