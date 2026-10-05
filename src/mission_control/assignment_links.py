from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class AssignmentLink:
    repository: str
    area: str
    subarea: str
    task_id: str
    agent_id: str | None = None


class AssignmentLinkGenerator:
    def __init__(self, dashboard_base: str = "https://mission.codestra.local") -> None:
        self.dashboard_base = dashboard_base.rstrip("/")

    def generate(self, assignment: AssignmentLink) -> str:
        query = urlencode(
            {
                k: v
                for k, v in {
                    "repository": assignment.repository,
                    "area": assignment.area,
                    "subarea": assignment.subarea,
                    "task_id": assignment.task_id,
                    "agent_id": assignment.agent_id,
                }.items()
                if v
            }
        )
        return f"{self.dashboard_base}/assign?{query}"

    def section_link(self, repository: str, area: str, subarea: str | None = None) -> str:
        query = urlencode(
            {
                k: v
                for k, v in {"repository": repository, "area": area, "subarea": subarea}.items()
                if v
            }
        )
        return f"{self.dashboard_base}/section?{query}"
