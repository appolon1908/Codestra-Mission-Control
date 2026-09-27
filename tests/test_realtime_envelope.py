import json
from mission_control.realtime_events import RealtimeEvent,RealtimePublisher

def test_event_uses_governed_envelope():
 e=RealtimeEvent.create("mission.state_transition",{"task_id":"T1"},sequence_no=7)
 x=json.loads(RealtimePublisher().encode(e))
 assert x["namespace"]=="mission" and x["event_type"]=="state_transition"
 assert x["sequence_no"]==7 and x["schema_version"]=="1.0"
 assert len(x["event_id"])==36
