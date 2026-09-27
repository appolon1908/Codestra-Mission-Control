from mission_control.assignments import AssignmentStore
from mission_control.agent_registry import AgentRegistry,AgentIdentity
from mission_control.store import MissionStore
import pytest

def test_claim_is_exclusive_and_leased(tmp_path):
 s=MissionStore(tmp_path/"db");s.initialize();a=AgentRegistry(s);a.initialize();a.register(AgentIdentity("codex-1","openai","codex","Codex"))
 q=AssignmentStore(s);q.initialize();claim=q.claim(task_id="T1",agent_id="codex-1",repository="r",area="API",subarea="Contracts")
 assert claim.lease_id and claim.expires_at
 with pytest.raises(ValueError,match="already_claimed"):q.claim(task_id="T1",agent_id="codex-1",repository="r",area="API",subarea="Contracts")
