# hack_ras/project/model.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProjectModel:
    title: Optional[str] = None
    geom_file_ids: list[str]     = field(default_factory=list)
    plan_file_ids: list[str]     = field(default_factory=list)
    unsteady_file_ids: list[str] = field(default_factory=list)

    y_axis_title: Optional[str]    = None
    x_axis_title_pf: Optional[str] = None
    x_axis_title_xs: Optional[str] = None
    dss_file: Optional[str]        = None

    def resolve_filenames(self, basename_map: dict[str, str]) -> dict[str, list[str]]:
        """Map file IDs to filenames for all referenced files of each type."""
        def _resolve(ids: list[str]) -> list[str]:
            return [basename_map[i] for i in ids if i in basename_map]
        return {
            "geom":     _resolve(self.geom_file_ids),
            "plan":     _resolve(self.plan_file_ids),
            "unsteady": _resolve(self.unsteady_file_ids),
        }
