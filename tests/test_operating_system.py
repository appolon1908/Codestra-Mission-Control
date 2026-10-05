import pytest

from mission_control.evidence_matrix import EvidenceMatrix
from mission_control.mission_auditor import AuditVerdict, MissionAuditor


def test_mission_auditor_requires_complete_engineering_coverage():
    coverage = {
        k: True
        for k in (
            "core",
            "security",
            "api",
            "routes",
            "data",
            "integrations",
            "observability",
            "testing",
            "delivery",
            "rollback",
        )
    }
    audit = MissionAuditor().audit(coverage, {"api": ("core",), "testing": ("api",)})
    assert audit.verdict is AuditVerdict.READY_FOR_IMPLEMENTATION


def test_mission_auditor_rejects_missing_area_and_cycles():
    audit = MissionAuditor().audit({"core": True}, {})
    assert audit.verdict is AuditVerdict.NEEDS_ARCHITECTURE and "security" in audit.missing
    with pytest.raises(ValueError, match="circular"):
        MissionAuditor().audit(
            {
                k: True
                for k in (
                    "core",
                    "security",
                    "api",
                    "routes",
                    "data",
                    "integrations",
                    "observability",
                    "testing",
                    "delivery",
                    "rollback",
                )
            },
            {"a": ("b",), "b": ("a",)},
        )


def test_evidence_matrix_makes_certification_mathematical():
    matrix = EvidenceMatrix()
    required = matrix.required(has_api=True, uses_postgres=True, security_sensitive=True)
    status = matrix.status(required, {k: True for k in required})
    assert status["certified"] is True
    failed = matrix.status(required, {k: True for k in required if k != "postman"})
    assert failed["certified"] is False and failed["missing"] == ("postman",)
