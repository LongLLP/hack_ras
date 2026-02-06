# hack_ras/project/model.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass
class ProjectModel:
    title: Optional[str] = None
    geom_file_id: Optional[str] = None   # e.g., "g01"
    plan_file_id: Optional[str] = None   # e.g., "p01"
    unsteady_file_id: Optional[str] = None  # e.g., "u01"

    # Handy optional fields
    y_axis_title: Optional[str] = None
    x_axis_title_pf: Optional[str] = None
    x_axis_title_xs: Optional[str] = None
    dss_file: Optional[str] = None

    # A helper to resolve IDs to filenames (convention-based)
    def resolve_filename(self, basename_map: dict[str, str]) -> dict[str, Optional[str]]:
        """
        Given a map like {"g01": "Stream.g01", "p01":"Stream.p01", "u01":"Stream.u01"},
        return absolute or relative filenames for each referenced file id.
        """
        return {
            "geom": basename_map.get(self.geom_file_id) if self.geom_file_id else None,
            "plan": basename_map.get(self.plan_file_id) if self.plan_file_id else None,
            "unsteady": basename_map.get(self.unsteady_file_id) if self.unsteady_file_id else None,
        }