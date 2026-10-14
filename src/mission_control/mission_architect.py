from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class MissionBrief:
    mission_id:str; repository:str; area:str; subarea:str; goal:str
    architecture:str; constraints:tuple[str,...]; acceptance:tuple[str,...]
    atomic_tasks:tuple[str,...]; required_tests:tuple[str,...]
    api_contracts:tuple[str,...]=(); dependencies:tuple[str,...]=()

    @property
    def digest(self)->str:
        payload=json.dumps(self.__dict__,sort_keys=True,separators=(",",":"))
        return hashlib.sha256(payload.encode()).hexdigest()

class MissionArchitect:
    def validate(self, brief:MissionBrief)->None:
        required=(brief.mission_id,brief.repository,brief.area,brief.subarea,brief.goal,
                  brief.architecture,brief.constraints,brief.acceptance,brief.atomic_tasks,
                  brief.required_tests)
        if not all(required): raise ValueError("mission brief is incomplete")
        if any(len(x.strip())<4 for x in brief.atomic_tasks): raise ValueError("atomic task too vague")

    def agent_contract(self, brief:MissionBrief, task:str)->dict:
        self.validate(brief)
        if task not in brief.atomic_tasks: raise ValueError("task not in frozen mission")
        return {"mission_id":brief.mission_id,"mission_digest":brief.digest,
                "repository":brief.repository,"area":brief.area,"subarea":brief.subarea,
                "goal":brief.goal,"architecture":brief.architecture,
                "constraints":brief.constraints,"acceptance":brief.acceptance,
                "atomic_task":task,"required_tests":brief.required_tests,
                "api_contracts":brief.api_contracts,"dependencies":brief.dependencies,
                "instruction":"IMPLEMENT THIS ATOMIC TASK. Do not substitute a review-only deliverable."}

    @staticmethod
    def verify_agent_digest(contract:dict,current_digest:str)->None:
        if contract.get("mission_digest")!=current_digest:
            raise ValueError("stale mission contract: refresh before implementation")
