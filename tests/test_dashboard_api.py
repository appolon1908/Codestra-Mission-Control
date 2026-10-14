import json
import threading
from urllib.request import urlopen

from mission_control.agent_registry import AgentRegistry
from mission_control.dashboard_api import DashboardAPI
from mission_control.mission_graph import MissionGraphStore
from mission_control.oversight import OversightStore
from mission_control.realtime_events import RealtimeEvent, RealtimePublisher
from mission_control.store import MissionStore


def test_dashboard_http_and_event_contract(tmp_path):
 s=MissionStore(tmp_path/"db");s.initialize();MissionGraphStore(s).initialize();AgentRegistry(s).initialize();OversightStore(s).initialize()
 g=MissionGraphStore(s);g.add_node("api","Middleware-","AREA","API")
 server=DashboardAPI(s).server();threading.Thread(target=server.serve_forever,daemon=True).start()
 try:
  h,p=server.server_address
  with urlopen(f"http://{h}:{p}/platform/v1/dashboard/repository?repository=Middleware-") as r:data=json.load(r)
  assert data["graph"][0]["title"]=="API"
 finally:server.shutdown();server.server_close()
 event=RealtimeEvent.create("mission.task.completed",{"task_id":"T1"})
 encoded=RealtimePublisher().encode(event)
 assert json.loads(encoded)["payload"]["task_id"]=="T1"
