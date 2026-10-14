from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Service:
    service_id:str; owner:str; repository:str; environment:str
    health:str; base_url:str; metrics:str|None=None; openapi:str|None=None
    dependencies:tuple[str,...]=(); status:str="ACTIVE"

DEFAULT_SERVICES=(
 Service("agent-brain","Codestra Platform","Codestra-Mission-Control","development",
         "http://127.0.0.1:8790/platform/v1/dashboard/agents","http://127.0.0.1:8790",
         openapi="OpenAPI/services/agent-brain/openapi.json",dependencies=("realtime-gateway",)),
 Service("realtime-gateway","Codestra Platform","websocket","development",
         "http://127.0.0.1:8791/healthz","ws://127.0.0.1:8791",dependencies=("agent-brain",)),
)

class ServiceCatalog:
    def __init__(self,services=DEFAULT_SERVICES): self.services=services
    def all(self): return [asdict(s) for s in self.services]
    def get(self,service_id): return next((asdict(s) for s in self.services if s.service_id==service_id),None)
