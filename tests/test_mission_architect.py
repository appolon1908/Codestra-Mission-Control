import pytest
from mission_control.mission_architect import MissionArchitect, MissionCharter
from mission_control.store import MissionStore

def setup(tmp_path):
    store=MissionStore(tmp_path/"mc.db"); store.initialize()
    arch=MissionArchitect(store); arch.initialize(); return arch

def charter(version=1,goal="Build router"):
    return MissionCharter("M-1","Middleware-","Mission Router",goal,("green CI","post-merge evidence"),
        architecture=("Caddy -> Kong -> Middleware",),constraints=("production effects off",),
        required_evidence=("tests","PR","CI"),areas=("API","Workers"),version=version)

def test_agent_must_ack_exact_active_mission_digest(tmp_path):
    arch=setup(tmp_path); c=charter(); digest=arch.publish(c)
    assert arch.agent_context("M-1","codex-1")["acknowledged"] is False
    arch.acknowledge("M-1",1,"codex-1",digest)
    assert arch.agent_context("M-1","codex-1")["acknowledged"] is True

def test_new_mission_version_invalidates_old_agent_memory(tmp_path):
    arch=setup(tmp_path); old=charter(); old_digest=arch.publish(old); arch.acknowledge("M-1",1,"claude-1",old_digest)
    new=charter(version=2,goal="Build router and live handoff"); arch.publish(new)
    assert arch.agent_context("M-1","claude-1")["acknowledged"] is False
    with pytest.raises(ValueError):
        arch.acknowledge("M-1",2,"claude-1",old_digest)
