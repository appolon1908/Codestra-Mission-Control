from __future__ import annotations
from dataclasses import dataclass,asdict
from datetime import UTC,datetime
import json
from urllib.request import Request,urlopen

@dataclass(frozen=True)
class RealtimeEvent:
    type:str
    payload:dict
    ts:str
    @classmethod
    def create(cls,event_type,payload):
        return cls(event_type,payload,datetime.now(UTC).isoformat())

class RealtimePublisher:
    def __init__(self,ingest_url:str|None=None): self.ingest_url=ingest_url
    def encode(self,event:RealtimeEvent)->str: return json.dumps(asdict(event),sort_keys=True)
    def publish_http(self,event:RealtimeEvent)->bool:
        if not self.ingest_url:return False
        req=Request(self.ingest_url,data=self.encode(event).encode(),headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=3) as response: return 200<=response.status<300
