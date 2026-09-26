from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse
from .dashboard_read_model import DashboardReadModel
from .agent_registry import AgentRegistry
from .oversight import OversightStore

PREFIX="/platform/v1/dashboard"

class DashboardAPI:
    def __init__(self,store): self.store=store
    def server(self,host="127.0.0.1",port=0):
        model=DashboardReadModel(self.store); agents=AgentRegistry(self.store); oversight=OversightStore(self.store)
        class Handler(BaseHTTPRequestHandler):
            def send_json(self,status,payload):
                body=json.dumps(payload,default=str).encode()
                self.send_response(status);self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_GET(self):
                p=urlparse(self.path);q=parse_qs(p.query)
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
