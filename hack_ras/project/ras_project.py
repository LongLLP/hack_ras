# hack_ras/project/ras_project.py
from __future__ import annotations

import os
from functools import cached_property
from typing import Optional

from hack_ras.resolve import (
    CrsProjectionFileNotFound,
    PlanHdfNotFound,
    discover_family,
    expand_id_spec,
    find_crs_prj,
    is_hecras_prj,
    list_available_ids,
    read_crs_wkt,
)
from hack_ras.project.model import ProjectModel
from hack_ras.project.parser import parse_project_file
from hack_ras.project.rasmap import RasMap


class RasProject:
    """Entry point for a HEC-RAS project, rooted at its .prj file.

    The .prj file is the authoritative list of which files belong to this project.
    All file discovery is scoped accordingly — sibling projects in the same folder
    are never accidentally included.
    """

    def __init__(self, prj_path: str) -> None:
        """
        Raises ValueError if prj_path does not exist or is not a HEC-RAS project file.
        """
        abs_path = os.path.abspath(prj_path)
        if not os.path.isfile(abs_path):
            raise ValueError(f"Project file not found: {abs_path}")
        if not is_hecras_prj(abs_path):
            raise ValueError(
                f"File does not appear to be a HEC-RAS project file: {abs_path}"
            )
        self.prj_path: str = abs_path

    # ------------------------------------------------------------------
    # Core identity properties
    # ------------------------------------------------------------------

    @cached_property
    def folder(self) -> str:
        """Absolute path of the directory containing the .prj file."""
        return os.path.dirname(self.prj_path)

    @cached_property
    def base_name(self) -> str:
        """Stem of the .prj filename (e.g. 'NKC_Hillside_Levee')."""
        return os.path.splitext(os.path.basename(self.prj_path))[0]

    @cached_property
    def model(self) -> ProjectModel:
        """Parsed contents of the .prj file."""
        return parse_project_file(self.prj_path)

    @property
    def title(self) -> Optional[str]:
        """Project title from the .prj file."""
        return self.model.title

    @cached_property
    def rasmap_path(self) -> str:
        """Absolute path of this project's .rasmap (existence NOT checked —
        many projects have none; use `self.rasmap.exists()`)."""
        return os.path.join(self.folder, f"{self.base_name}.rasmap")

    @cached_property
    def rasmap(self) -> RasMap:
        """Bound accessor for this project's .rasmap: `project.rasmap.sort()`,
        `.remove_plans([...])`, `.result_plan_ids()`, and so on.

        Thin sugar over the stateless functions in project/rasmap.py — it binds
        (path, base_name) and forwards. It parses nothing and caches no file
        content, so it cannot go stale; caching the accessor itself is safe
        because both bound values are fixed for the project's lifetime.
        """
        return RasMap(self.rasmap_path, self.base_name)

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------

    def plan_hdfs(self, plan_ids: Optional[list[str]] = None) -> list[str]:
        """Return sorted absolute paths of plan HDF files for this project.

        Only plans explicitly listed in the .prj are candidates; files on disk
        that are not referenced by the project are excluded. From those, only
        plans that have a corresponding .p##.hdf file are returned.

        If plan_ids is given, only those plans are returned. Entries accept the
        flexible id syntax of `expand_id_spec` — bare numbers, `p`-prefixed ids,
        and inclusive ranges, e.g. ['01', 'p03', '14-16']. Raises PlanHdfNotFound
        if no HDF files exist for listed plans, or if any requested plan ID is
        missing (every expanded id must exist — ranges are not filtered).
        """
        listed = self.model.plan_file_ids
        found: dict[str, str] = {}
        for pid in listed:
            path = os.path.join(self.folder, f"{self.base_name}.{pid}.hdf")
            if os.path.isfile(path):
                found[pid] = os.path.abspath(path)

        if not found:
            raise PlanHdfNotFound(
                f"No plan HDF files found for '{self.base_name}' in {self.folder}. "
                f"Plans listed in .prj: {listed}"
            )

        if plan_ids:
            result: dict[str, str] = {}
            for pid in expand_id_spec(plan_ids, "p"):
                if pid not in found:
                    raise PlanHdfNotFound(
                        f"Plan '{pid}' not found or not listed in "
                        f"{self.base_name}.prj. Available HDFs: {sorted(found.keys())}"
                    )
                result[pid] = found[pid]
            return [result[k] for k in sorted(result.keys())]

        return [found[k] for k in sorted(found.keys())]

    def crs_prj(self, specified: Optional[str] = None) -> str:
        """Find the ESRI .prj CRS file for this project.

        Delegates to find_crs_prj(self.folder, specified).
        Raises CrsProjectionFileNotFound if none can be located.
        """
        return find_crs_prj(self.folder, specified)

    def crs_wkt(self, specified: Optional[str] = None) -> str:
        """The project's CRS as a WKT string, ready for geopandas/pyproj.

        Delegates to read_crs_wkt(self.folder, specified) — i.e. crs_prj() plus
        reading the file.  Raises CrsProjectionFileNotFound if none can be located.
        """
        return read_crs_wkt(self.folder, specified)

    def layer_associations(self) -> dict:
        """RAS Mapper's Manage Layer Associations dialog, as
        {'g01': LayerAssociations, ..., 'p01': LayerAssociations, ...}.

        Geometries first, then plans, each in .prj order. Only files listed in
        the .prj whose .g##.hdf / .p##.hdf exists are included (a geometry never
        preprocessed or a plan never run has no row). See project/associations.py.
        """
        from hack_ras.project.associations import read_layer_associations

        out = {}
        for fid in self.model.geom_file_ids + self.model.plan_file_ids:
            path = os.path.join(self.folder, f"{self.base_name}.{fid}.hdf")
            if os.path.isfile(path):
                out[fid] = read_layer_associations(path)
        return out

    def family(self) -> dict[str, list[str]]:
        """All sibling files belonging to this project, grouped by type.

        Returns {'geom': [...], 'plan': [...], 'unsteady': [...], 'steady': [...]}.
        """
        return discover_family(self.prj_path)

    def available_ids(self) -> dict[str, list[str]]:
        """Available file IDs per type (e.g. {'geom': ['g01'], 'plan': ['p01', 'p02']})."""
        return list_available_ids(self.prj_path)

    def __repr__(self) -> str:
        return f"RasProject({self.prj_path!r})"
