from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Endpoint:
    method:str; path:str; domain:str; purpose:str
    auth:str="service-or-user"; effect:str="READ"; reusable:bool=True

ENDPOINTS=(
 Endpoint("GET","/platform/v1/dashboard/repository","dashboard","repository mission graph and PR evidence"),
 Endpoint("GET","/platform/v1/dashboard/agents","dashboard","agent/lane presence"),
 Endpoint("GET","/platform/v1/dashboard/notifications","dashboard","notification ledger"),
 Endpoint("GET","/platform/v1/mission-router/snapshot","router","hierarchy and progress"),
 Endpoint("GET","/platform/v1/mission-router/next","router","ranked next tasks"),
 Endpoint("POST","/platform/v1/assignments","router","claim/assign atomic task",effect="CONTROL"),
 Endpoint("POST","/platform/v1/assignments/{task_id}/move","router","move task between eligible agents",effect="CONTROL"),
 Endpoint("POST","/platform/v1/agents/{agent_id}/heartbeat","agents","agent heartbeat",effect="CONTROL"),
 Endpoint("GET","/platform/v1/agents/{agent_id}/lane","agents","current agent lane"),
 Endpoint("GET","/platform/v1/missions/{mission_id}","missions","frozen mission charter"),
 Endpoint("GET","/platform/v1/missions/{mission_id}/graph","missions","mission dependency graph"),
 Endpoint("POST","/platform/v1/missions/{mission_id}/ack","missions","acknowledge exact mission digest",effect="CONTROL"),
 Endpoint("GET","/platform/v1/repositories/{repository}/prs","git","PR/task evidence"),
 Endpoint("GET","/platform/v1/tasks/{task_id}/evidence","certification","evidence matrix"),
 Endpoint("GET","/platform/v1/notifications","notifications","notification feed"),
 Endpoint("POST","/platform/v1/notifications/{id}/ack","notifications","acknowledge incident",effect="CONTROL"),
 Endpoint("POST","/platform/v1/events","events","publish governed realtime event",effect="CONTROL"),
 Endpoint("GET","/platform/v1/services","catalog","service catalog"),
 Endpoint("GET","/platform/v1/connectors","catalog","connector catalog"),
)

class APICatalog:
    def all(self): return [asdict(x) for x in ENDPOINTS]
    def openapi_paths(self):
        paths={}
        for e in ENDPOINTS:
            paths.setdefault(e.path,{})[e.method.lower()]={"summary":e.purpose,"tags":[e.domain],
                "x-codestra-effect":e.effect,"x-codestra-reusable":e.reusable,
                "responses":{"200":{"description":"Success"}}}
        return paths
    def document(self):
        return {"openapi":"3.1.0","info":{"title":"Codestra Agent Brain Platform API","version":"0.1.0"},
                "paths":self.openapi_paths()}
    def json(self): return json.dumps(self.document(),indent=2,sort_keys=True)
