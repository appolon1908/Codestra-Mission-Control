from mission_control.agent_registry import AgentIdentity, AgentRegistry
from mission_control.store import MissionStore


def test_all_provider_types_have_visible_lane_identity(tmp_path):
    base = MissionStore(tmp_path / "mc.db")
    base.initialize()
    registry = AgentRegistry(base)
    registry.initialize()
    identities = [
        AgentIdentity("claude-01", "anthropic", "claude", "Claude Agent"),
        AgentIdentity("codex-01", "openai", "codex", "Codex Agent"),
        AgentIdentity("api-01", "openai", "api", "API Agent"),
        AgentIdentity("copilot-01", "github", "copilot", "Copilot Agent"),
        AgentIdentity("grok-01", "xai", "grok", "Grok Agent"),
    ]
    for identity in identities:
        registry.register(identity)
        registry.heartbeat(identity.agent_id, state="WORKING", repository="Middleware-",
                           area_id="api", subarea_id="contracts", mission_id="M1",
                           task_id=f"T-{identity.agent_id}", branch="mission/test",
                           worktree="/worktree", workstation="ubuntu-desktop")
    lanes = registry.lanes()
    assert {row["agent_type"] for row in lanes} == {"claude", "codex", "api", "copilot", "grok"}
    assert all(row["repository"] == "Middleware-" for row in lanes)


def test_unregistered_agent_cannot_claim_lane(tmp_path):
    base = MissionStore(tmp_path / "mc.db")
    base.initialize()
    registry = AgentRegistry(base)
    registry.initialize()
    try:
        registry.heartbeat("unknown", state="WORKING", task_id="T1")
    except KeyError as exc:
        assert "unregistered agent" in str(exc)
    else:
        raise AssertionError("unregistered agent was allowed into a lane")
