from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum

class AuditVerdict(StrEnum):
    READY_FOR_IMPLEMENTATION="READY_FOR_IMPLEMENTATION"
    NEEDS_ARCHITECTURE="NEEDS_ARCHITECTURE"

@dataclass(frozen=True)
class MissionAudit:
    verdict:AuditVerdict
    missing:tuple[str,...]
    coverage:dict[str,bool]

REQUIRED=("core","security","api","routes","data","integrations","observability","testing","delivery","rollback")

class MissionAuditor:
    def audit(self, coverage:dict[str,bool], dependencies:dict[str,tuple[str,...]])->MissionAudit:
        missing=tuple(k for k in REQUIRED if not coverage.get(k,False))
        self._assert_acyclic(dependencies)
        return MissionAudit(AuditVerdict.NEEDS_ARCHITECTURE if missing else AuditVerdict.READY_FOR_IMPLEMENTATION,missing,{k:bool(coverage.get(k)) for k in REQUIRED})

    @staticmethod
    def _assert_acyclic(graph:dict[str,tuple[str,...]])->None:
        visiting:set[str]=set(); done:set[str]=set()
        def visit(node:str):
            if node in visiting: raise ValueError(f"circular dependency at {node}")
            if node in done:return
            visiting.add(node)
            for dep in graph.get(node,()): visit(dep)
            visiting.remove(node);done.add(node)
        for node in graph: visit(node)
