from mission_control.convergence_store import ConvergenceStore
from mission_control.mission_router import AtomicTask
from mission_control.router_store import RouterStore
from mission_control.security import ROLE_PERMISSIONS, KeycloakVerifier
from mission_control.store import MissionStore


def test_disabled_local_auth_is_explicit_admin():
    p = KeycloakVerifier(mode="disabled").require(None, "mission:write")
    assert p.subject == "local-development"
    assert p.allows("evidence:certify")


def test_role_matrix_is_fail_closed():
    assert "mission:write" not in ROLE_PERMISSIONS["Viewer"]
    assert "router:lease" in ROLE_PERMISSIONS["Operator"]
    assert "evidence:certify" in ROLE_PERMISSIONS["Reviewer"]
    assert "task:claim" in ROLE_PERMISSIONS["Agent"]


def test_lease_exclusion_heartbeat_and_certification(tmp_path):
    s = MissionStore(tmp_path / "mc.db")
    s.initialize()
    rs = RouterStore(s)
    rs.initialize()
    rs.upsert_task(AtomicTask("T1", "Repo", "A", "S", "M"))
    c = ConvergenceStore(s)
    c.initialize()
    lease = c.lease("T1", "A1", 30)
    assert lease["leased_to"] == "A1"
    try:
        c.lease("T1", "A2", 30)
        assert False
    except ValueError as e:
        assert str(e) == "TASK_ALREADY_LEASED"
    hb = c.heartbeat(
        {
            "task_id": "T1",
            "agent_id": "A1",
            "lease_token": lease["lease_token"],
            "current_sha": "a" * 40,
            "status": "ACTIVE",
            "changed_files_count": 2,
        }
    )
    assert hb["acknowledged"]
    cert = c.certify(
        {
            "task_id": "T1",
            "exact_sha": "a" * 40,
            "test_pass_rate": 100,
            "artifact_url": "file:///tmp/evidence",
        }
    )
    assert cert["status"] == "CERTIFIED"
