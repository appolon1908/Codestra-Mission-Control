from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RealtimeEvent:
    event_id:str
    sequence_no:int
    namespace:str
    event_type:str
    timestamp:str
    source_service:str
    payload:dict
    correlation_id:str|None=None
    causation_id:str|None=None
    schema_version:str="1.0"

    @property
    def type(self): return f"{self.namespace}.{self.event_type}"
    @property
    def ts(self): return self.timestamp

    @classmethod
    def create(cls,event_type,payload,*,sequence_no=1,source_service="agent-brain",correlation_id=None,causation_id=None):
        namespace,_,name=event_type.partition(".")
        if not name: raise ValueError("event_type must include namespace")
        return cls(str(uuid.uuid4()),sequence_no,namespace,name,datetime.now(UTC).isoformat(),source_service,payload,correlation_id,causation_id)

class RealtimePublisher:
    def __init__(self,ingest_url:str|None=None,token:str|None=None):
        self.ingest_url=ingest_url;self.token=token or os.getenv("MISSION_CONTROL_REALTIME_TOKEN");self._sequence=0
    def encode(self,event:RealtimeEvent)->str: return json.dumps(asdict(event),sort_keys=True)
    def publish_http(self,event:RealtimeEvent)->bool:
        if not self.ingest_url:return False
        self._sequence+=1
        data=asdict(event);data["sequence_no"]=self._sequence
        headers={"Content-Type":"application/json"}
        if self.token:headers["Authorization"]=f"Bearer {self.token}"
        req=Request(self.ingest_url,data=json.dumps(data,sort_keys=True).encode(),headers=headers,method="POST")
        with urlopen(req,timeout=3) as response:return 200<=response.status<300
