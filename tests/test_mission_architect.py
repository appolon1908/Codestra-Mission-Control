import pytest

from mission_control.mission_architect import MissionArchitect, MissionBrief


def brief():
 return MissionBrief("M1","Middleware-","API","Commands","Implement command API",
  "Caddy -> Kong -> Middleware :8095",("no production effects",),("green tests",),("implement POST command",),("unit + Postman",),
  ("/platform/v1/commands",),("identity",))

def test_agent_receives_frozen_goal_architecture_and_atomic_implementation_task():
 b=brief(); c=MissionArchitect().agent_contract(b,"implement POST command")
 assert c["goal"]==b.goal and c["architecture"]==b.architecture
 assert c["mission_digest"]==b.digest
 assert c["instruction"].startswith("IMPLEMENT")

def test_agent_cannot_use_stale_mission_memory():
 b=brief(); c=MissionArchitect().agent_contract(b,"implement POST command")
 with pytest.raises(ValueError,match="stale mission"):
  MissionArchitect.verify_agent_digest(c,"different")

def test_vague_or_incomplete_mission_fails_before_assignment():
 with pytest.raises(ValueError):
  MissionArchitect().validate(MissionBrief("M","","","","","","","","",""))
