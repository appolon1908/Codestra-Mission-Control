from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent_registry import AgentRegistry
from .assignments import AssignmentStore
from .control_plane_read import ControlPlaneReadModel, SourceUnavailable
from .convergence_store import ConvergenceStore
from .dashboard_contract import dashboard_contract
from .dashboard_read_model import DashboardReadModel
from .discovery_engine import DiscoveryEngine
from .local_work_discovery import LocalWorkDiscovery
from .monitoring_evidence import snapshot as monitoring_snapshot
from .monitoring_lock_certificate import snapshot as monitoring_lock_snapshot
from .oversight import OversightStore
from .realtime_events import RealtimeEvent, RealtimePublisher
from .repository_sync import RepositorySyncStore
from .router_store import RouterStore
from .security import AuthError, KeycloakVerifier

PREFIX="/platform/v1/dashboard"

class DashboardAPI:
    def __init__(self,store,*,authorization=None,control_plane=None,allow_mutations=False):
        self.store=store
        # Never implicitly enable anonymous Administrator in the real dashboard.
        self.authorization=authorization
        self.control_plane=control_plane
        # This dashboard defaults to read-only even for authenticated operators.
        # Mutation flows belong to separately reviewed development entrypoints.
        self.allow_mutations=allow_mutations
    def server(self,host="127.0.0.1",port=0):
        started=time.monotonic()
        # Bind the runtime report to the exact source loaded at startup.
        # Computing HEAD on every health request falsely reports new commits
        # for old Python code kept alive in an existing process.
        checkout=Path(__file__).resolve().parents[2]
        try:
            loaded_sha=subprocess.check_output(
                ["git","rev-parse","HEAD"],cwd=checkout,text=True,
                stderr=subprocess.DEVNULL,timeout=2,
            ).strip()
            loaded_tree_clean=not subprocess.check_output(
                ["git","status","--porcelain"],cwd=checkout,text=True,
                stderr=subprocess.DEVNULL,timeout=2,
            ).strip()
        except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired):
            loaded_sha=None
            loaded_tree_clean=False
        model=DashboardReadModel(self.store); local_work=LocalWorkDiscovery(); discovery_engine=DiscoveryEngine(local_work,interval_seconds=int(os.getenv("MISSION_CONTROL_DISCOVERY_INTERVAL","30")),snapshot_path=os.getenv("MISSION_CONTROL_DISCOVERY_SNAPSHOT","/tmp/mission-control-local-work.json")); discovery_engine.start(); convergence=ConvergenceStore(self.store); convergence.initialize(); auth=self.authorization if self.authorization is not None else KeycloakVerifier(mode="required"); control=self.control_plane if self.control_plane is not None else ControlPlaneReadModel(); router_store=RouterStore(self.store); router_store.initialize(); agents=AgentRegistry(self.store); oversight=OversightStore(self.store); assignments=AssignmentStore(self.store); assignments.initialize(); RepositorySyncStore(self.store).initialize(); publisher=RealtimePublisher(os.getenv("MISSION_CONTROL_REALTIME_URL","http://127.0.0.1:8791/events"))
        class Handler(BaseHTTPRequestHandler):
            def send_json(self,status,payload):
                body=json.dumps(payload,default=str).encode()
                self.send_response(status);self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("X-Frame-Options","DENY")
                self.send_header("Referrer-Policy","no-referrer")
                approved_origin=os.getenv("MISSION_CONTROL_ALLOWED_ORIGIN","")
                if approved_origin and self.headers.get("Origin")==approved_origin:
                    self.send_header("Access-Control-Allow-Origin",approved_origin)
                    self.send_header("Vary","Origin")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_OPTIONS(self):
                approved=os.getenv("MISSION_CONTROL_ALLOWED_ORIGIN","")
                if not approved or self.headers.get("Origin")!=approved:
                    return self.send_json(403,{"error":"origin_not_allowed"})
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin",approved)
                self.send_header("Vary","Origin")
                self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS" if self.server.allow_mutations else "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization")
                self.end_headers()
            def _allowed_origin(self):
                incoming=self.headers.get("Origin")
                approved=os.getenv("MISSION_CONTROL_ALLOWED_ORIGIN","")
                if incoming and incoming != approved:
                    self.send_json(403,{"error":"origin_not_allowed"})
                    return False
                return True
            def _control(self, function):
                try:
                    return self.send_json(200,function())
                except SourceUnavailable as exc:
                    return self.send_json(503,{"error_code":"SOURCE_UNAVAILABLE","message":str(exc)})
                except ValueError as exc:
                    return self.send_json(400,{"error_code":"INVALID_QUERY","message":str(exc)})

            def _principal(self,permission):
                try:return auth.require(self.headers.get("Authorization"),permission)
                except AuthError as exc:self.send_json(exc.status,{"error_code":exc.code,"message":exc.code,"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()});return None
            def _principal_any(self,*permissions):
                try:
                    principal=auth.verify(self.headers.get("Authorization"))
                    if any(principal.allows(p) for p in permissions): return principal
                    raise AuthError("insufficient_permission",403)
                except AuthError as exc:self.send_json(exc.status,{"error_code":exc.code,"message":exc.code,"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()});return None
            def _body(self):
                length=int(self.headers.get("Content-Length","0"));return json.loads(self.rfile.read(length) or b"{}")
            def do_POST(self):
                if not self._allowed_origin():return
                if not self.server.allow_mutations:
                    return self.send_json(403,{"error_code":"DASHBOARD_MUTATIONS_DISABLED"})
                p=urlparse(self.path)
                if p.path=="/api/v1/missions":
                    if not self._principal("mission:write"):return
                    try:
                        body=self._body()
                        if not body.get("product_goal") or not body.get("business_reason"):return self.send_json(400,{"error_code":"MISSION_FIELDS_REQUIRED","message":"product_goal and business_reason required","timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                        return self.send_json(201,convergence.create_mission(body))
                    except (KeyError, ValueError, TypeError) as exc:return self.send_json(400,{"error_code":"MISSION_INVALID","message":str(exc),"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                m=re.fullmatch(r"/api/v1/tasks/([^/]+)/lease",p.path)
                if m:
                    principal=self._principal_any("router:lease","task:claim")
                    if not principal:return
                    body=self._body();tid=m.group(1)
                    if not any(t.task_id==tid for t in router_store.tasks()):return self.send_json(404,{"error_code":"TASK_NOT_FOUND","message":"task not found","timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                    try:
                        lease=convergence.lease(tid,body["agent_id"],int(body.get("heartbeat_interval_sec",30)))
                        task=next(t for t in router_store.tasks() if t.task_id==tid)
                        with self.server.store.connection() as c: ec=c.execute("select * from execution_contracts where task_id=?",(tid,)).fetchone()
                        contract=dict(ec) if ec else {}
                        parse=lambda k: json.loads(contract.get(k) or "[]")
                        return self.send_json(200,{"task_id":tid,"stage":contract.get("stage") or "IMPLEMENTATION","product_goal":"Mission Control governed execution","business_reason":contract.get("objective") or "Execute the smallest safe ready task","target_repository":task.repository,"canonical_checkout":contract.get("worktree"),"branch":contract.get("branch"),"base_sha":contract.get("base_sha"),"writable_scope":parse("writable_scope_json"),"read_only_scope":parse("readonly_dependencies_json"),"forbidden_scope":parse("forbidden_scope_json"),"dependencies":list(task.dependencies),"collision_set":sorted(task.collision_keys),"acceptance_criteria":parse("acceptance_json"),"required_evidence":parse("evidence_json"),"apis":parse("api_operations_json"),"headers":parse("headers_json"),"lease":lease})
                    except ValueError as exc:return self.send_json(409,{"error_code":str(exc),"message":str(exc),"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                if p.path=="/api/v1/agents/heartbeat":
                    principal=self._principal("agent:heartbeat")
                    if not principal:return
                    body=self._body()
                    if auth.mode=="required":
                        bound=principal.claims.get("agent_id") or principal.claims.get("preferred_username") or principal.subject
                        if body.get("agent_id")!=bound:return self.send_json(403,{"error_code":"agent_identity_mismatch","message":"agent identity does not match token","timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                    try:return self.send_json(200,convergence.heartbeat(body))
                    except (KeyError,ValueError) as exc:return self.send_json(404,{"error_code":str(exc),"message":str(exc),"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                if p.path=="/api/v1/certifications":
                    principal=self._principal("evidence:certify")
                    if not principal:return
                    try:
                        body=self._body()
                        if not re.fullmatch(r"[0-9a-f]{40}",body.get("exact_sha","")):raise ValueError("exact_sha_invalid")
                        result=convergence.certify(body);return self.send_json(201 if result["status"]=="CERTIFIED" else 422,result)
                    except (KeyError, ValueError, TypeError) as exc:return self.send_json(422,{"error_code":"EVIDENCE_INVALID","message":str(exc),"timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                if p.path=="/platform/v1/assignments":
                    if not self._principal_any("agent:assign","task:claim"):return
                    length=int(self.headers.get("Content-Length","0"))
                    try:
                        body=json.loads(self.rfile.read(length) or b"{}")
                        required=("task_id","agent_id","repository","area","subarea")
                        if any(not body.get(k) for k in required):
                            return self.send_json(400,{"error":"assignment_fields_required"})
                        claim=assignments.claim(**{k:body[k] for k in required})
                        agents.heartbeat(body["agent_id"],state="CLAIMED",repository=body["repository"],area_id=body["area"],subarea_id=body["subarea"],task_id=body["task_id"],mission_id=body.get("mission_id"),workstation="UBUNTU_DESKTOP")
                        try: publisher.publish_http(RealtimeEvent.create("mission.task.claimed",{"task_id":body["task_id"],"agent_id":body["agent_id"],"repository":body["repository"]}))
                        except OSError as exc: logging.getLogger(__name__).warning('mission_control_realtime_publish_failed: %s',type(exc).__name__)
                        return self.send_json(201,claim.__dict__)
                    except ValueError as exc:
                        return self.send_json(409,{"error":str(exc)})
                return self.send_json(404,{"error":"not_found"})
            def do_GET(self):
                if not self._allowed_origin():return
                p=urlparse(self.path);q=parse_qs(p.query)
                if p.path=="/healthz":
                    return self.send_json(200,{
                        "status":"OK",
                        "service_name":"agent-brain-backend",
                        "active_sha":loaded_sha,
                        "source_clean_at_start":loaded_tree_clean,
                        "uptime_seconds":round(time.monotonic()-started,3),
                    })
                if p.path=="/readyz":
                    try:
                        with control.connection() as cursor:
                            cursor.execute("SELECT 1")
                            cursor.fetchone()
                        return self.send_json(200,{"status":"ready","data_source":"postgres_control_plane_readonly"})
                    except SourceUnavailable:
                        return self.send_json(503,{"status":"not_ready","data_source":"unavailable"})
                if p.path=="/api/v1/repositories/discover":
                    if not self._principal("mission:read"):return
                    return self.send_json(200,discovery_engine.latest)
                if p.path=="/api/v1/missions":
                    if not self._principal("mission:read"):return
                    try:return self.send_json(200,convergence.missions(q.get("status",[None])[0],min(100,int(q.get("limit",["20"])[0])),max(0,int(q.get("offset",["0"])[0]))))
                    except ValueError:return self.send_json(400,{"error_code":"PAGINATION_INVALID","message":"invalid pagination","timestamp":__import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()})
                if p.path=="/openapi.json":
                    try:return self.send_json(200,json.loads(open("openapi/mission-control/openapi.json").read()))
                    except (OSError, json.JSONDecodeError):return self.send_json(404,{"error":"openapi_missing"})
                # Every dashboard source is authenticated, not just mutating APIs.
                # Only globally delegated Operator/Reviewer/Administrator may
                # inspect the cross-tenant control plane. Viewer cannot enumerate
                # unrelated tenants or workstation paths.
                if p.path.startswith(PREFIX+"/") and not self._principal("dashboard:read"):
                    return
                if p.path==PREFIX+"/monitoring-lock":
                    return self.send_json(200,monitoring_lock_snapshot())
                if p.path==PREFIX+"/monitoring-governance":
                    return self.send_json(200,monitoring_snapshot())
                if p.path==PREFIX+"/contract":
                    return self.send_json(200,dashboard_contract())
                if p.path==PREFIX+"/health":
                    return self.send_json(200,{"status":"ok","service":"agent-brain-dashboard-api"})
                if p.path==PREFIX+"/repositories":
                    return self._control(lambda:{"repositories":control.repositories(),"source":"postgres_control_plane_readonly"})
                if p.path==PREFIX+"/local-work":
                    repo=q.get("repository",[None])[0]
                    try: hours=int(q.get("recent_hours",["48"])[0])
                    except ValueError: return self.send_json(400,{"error":"recent_hours_invalid"})
                    return self._control(lambda:control.local_work(repo,hours))
                if p.path==PREFIX+"/sources":
                    return self.send_json(200,{"sources":{
                      "repositories":"PostgreSQL products; GitHub sync not available on this adapter",
                      "sync":"UNVERIFIED (no live Git fetch)",
                      "prs":"UNAVAILABLE (GitHub PR API not connected)",
                      "ci":"UNAVAILABLE (exact-SHA CI not connected)",
                      "agents":"PostgreSQL active workstation heartbeat and lease registry",
                      "progress":"PostgreSQL workstations and certifications",
                      "apis":"OpenAPI authority + API catalog",
                      "realtime":"UNAVAILABLE until authenticated WebSocket gateway is certified"}})
                if p.path==PREFIX+"/tasks":
                    repo=q.get("repository",[None])[0]
                    if not repo:return self.send_json(400,{"error":"repository_required"})
                    return self._control(lambda:{"repository":repo,"tasks":control.tasks(repo),"source":"postgres_control_plane_readonly"})
                if p.path==PREFIX+"/task":
                    tid=q.get("task_id",[None])[0]
                    if not tid:return self.send_json(400,{"error":"task_id_required"})
                    try: task=control.task(tid)
                    except SourceUnavailable as exc:
                        return self.send_json(503,{"error_code":"SOURCE_UNAVAILABLE","message":str(exc)})
                    if task is None:return self.send_json(404,{"error":"canonical_task_missing"})
                    return self.send_json(200,{"task":task})
                if p.path==PREFIX+"/launch-readiness":
                    repo=q.get("repository",[None])[0]
                    if not repo:return self.send_json(400,{"error":"repository_required"})
                    with self.server.store.connection() as c:
                        try:
                            t=c.execute("select count(*) n,sum(certified) cert,avg(completion_percent) wip from atomic_tasks where repository=?",(repo,)).fetchone()
                            x=c.execute("select count(*) n,sum(case when worktree!='UNRESOLVED' and branch!='UNRESOLVED' and base_sha!='UNRESOLVED' then 1 else 0 end) ready from execution_contracts where repository=?",(repo,)).fetchone()
                            return self.send_json(200,{"repository":repo,"wip_percent":round(t["wip"] or 0,2),"certified_tasks":t["cert"] or 0,"total_tasks":t["n"] or 0,"execution_contracts":x["n"] or 0,"execution_ready":x["ready"] or 0})
                        except (sqlite3.Error, ValueError, TypeError):return self.send_json(503,{"error":"launch_intelligence_not_initialized"})
                if p.path==PREFIX+"/repository":
                    repo=q.get("repository",[None])[0]
                    if not repo:return self.send_json(400,{"error":"repository_required"})
                    if control is False:
                        return self.send_json(200,model.repository(repo))
                    return self._control(lambda:control.repository(repo))
                if p.path==PREFIX+"/agents":
                    return self._control(lambda:{"agents":control.agents(),"source":"postgres_control_plane_readonly"})
                if p.path==PREFIX+"/notifications":
                    return self.send_json(200,{"notifications":oversight.notifications()})
                return self.send_json(404,{"error":"not_found"})
            def log_message(self,*args): return
        srv=ThreadingHTTPServer((host,port),Handler);srv.store=self.store
        srv.allow_mutations=bool(self.allow_mutations)
        return srv
