from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse
from .dashboard_read_model import DashboardReadModel
from .agent_registry import AgentRegistry
from .oversight import OversightStore
from .assignments import AssignmentStore
from .realtime_events import RealtimeEvent, RealtimePublisher
from .repository_sync import RepositorySyncStore
from .repository_control import RepositoryControlCenter
from .dashboard_contract import dashboard_contract
from .monitoring_evidence import snapshot as monitoring_snapshot
from .monitoring_evidence import snapshot as monitoring_snapshot

PREFIX="/platform/v1/dashboard"

class DashboardAPI:
    def __init__(self,store): self.store=store
    def server(self,host="127.0.0.1",port=0):
        model=DashboardReadModel(self.store); agents=AgentRegistry(self.store); oversight=OversightStore(self.store); assignments=AssignmentStore(self.store); assignments.initialize(); RepositorySyncStore(self.store).initialize(); repo_control=RepositoryControlCenter(self.store); publisher=RealtimePublisher("http://127.0.0.1:8791/events")
        class Handler(BaseHTTPRequestHandler):
            def send_json(self,status,payload):
                body=json.dumps(payload,default=str).encode()
                self.send_response(status);self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store")
                self.send_header("Access-Control-Allow-Origin","http://127.0.0.1:8793")
                self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin","http://127.0.0.1:8793")
                self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.end_headers()
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin","http://127.0.0.1:8793")
                self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.end_headers()
            def do_POST(self):
                p=urlparse(self.path)
                if p.path=="/platform/v1/assignments":
                    length=int(self.headers.get("Content-Length","0"))
                    try:
                        body=json.loads(self.rfile.read(length) or b"{}")
                        required=("task_id","agent_id","repository","area","subarea")
                        if any(not body.get(k) for k in required):
                            return self.send_json(400,{"error":"assignment_fields_required"})
                        claim=assignments.claim(**{k:body[k] for k in required})
                        agents.heartbeat(body["agent_id"],state="CLAIMED",repository=body["repository"],area_id=body["area"],subarea_id=body["subarea"],task_id=body["task_id"],mission_id=body.get("mission_id"),workstation="UBUNTU_DESKTOP")
                        publisher.publish_http(RealtimeEvent.create("mission.task.claimed",{"task_id":body["task_id"],"agent_id":body["agent_id"],"repository":body["repository"]}))
                        return self.send_json(201,claim.__dict__)
                    except ValueError as exc:
                        return self.send_json(409,{"error":str(exc)})
                return self.send_json(404,{"error":"not_found"})
            def do_GET(self):
                p=urlparse(self.path);q=parse_qs(p.query)
                if p.path==PREFIX+"/monitoring-governance":
                    return self.send_json(200,monitoring_snapshot())
                if p.path==PREFIX+"/monitoring-governance":
                    return self.send_json(200,monitoring_snapshot())
                if p.path==PREFIX+"/contract":
                    return self.send_json(200,dashboard_contract())
                if p.path==PREFIX+"/health":
                    return self.send_json(200,{"status":"ok","service":"agent-brain-dashboard-api"})
                if p.path==PREFIX+"/repositories":
                    return self.send_json(200,{"repositories":repo_control.rows()})
                if p.path==PREFIX+"/sources":
                    return self.send_json(200,{"sources":{
                      "repositories":"GitHub repository inventory + local reconciler",
                      "sync":"Git/GitHub remote/local SHA reconciler",
                      "prs":"GitHub pull requests",
                      "ci":"GitHub checks/CI",
                      "agents":"Agent Brain heartbeat/lease registry",
                      "progress":"Mission Router atomic tasks + certification evidence",
                      "apis":"OpenAPI authority + API catalog",
                      "realtime":"standalone WebSocket gateway :8791"}})
                if p.path==PREFIX+"/repository":
                    repo=q.get("repository",[None])[0]
                    if not repo:return self.send_json(400,{"error":"repository_required"})
                    return self.send_json(200,model.repository(repo))
                if p.path==PREFIX+"/agents":
                    return self.send_json(200,{"agents":agents.lanes()})
                if p.path==PREFIX+"/notifications":
                    return self.send_json(200,{"notifications":oversight.notifications()})
                return self.send_json(404,{"error":"not_found"})
            def log_message(self,*args): return
        return ThreadingHTTPServer((host,port),Handler)
