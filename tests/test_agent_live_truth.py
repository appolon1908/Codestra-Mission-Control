from mission_control.store import MissionStore
from mission_control.agent_registry import AgentRegistry,AgentIdentity
def test_stale_declared_working_is_not_live(tmp_path):
 s=MissionStore(tmp_path/'db');s.initialize();r=AgentRegistry(s);r.initialize();r.register(AgentIdentity('a','p','t','A'));r.heartbeat('a',state='WORKING',repository='r',branch='b',worktree='/w')
 x=r.lanes()[0];assert x['state']=='IDLE' and not x['live']
