from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceRequirement:
    name: str
    required: bool = True


class EvidenceMatrix:
    BASE = ("implementation", "unit", "integration", "review", "ci", "merge", "post_merge")
    API = ("api_contract", "postman")
    DB = ("postgres",)
    SECURITY = ("security",)

    def required(
        self, *, has_api=False, uses_postgres=False, security_sensitive=False
    ) -> tuple[str, ...]:
        rows = list(self.BASE)
        if has_api:
            rows += self.API
        if uses_postgres:
            rows += self.DB
        if security_sensitive:
            rows += self.SECURITY
        return tuple(rows)

    def status(self, required: tuple[str, ...], evidence: dict[str, bool]) -> dict:
        missing = [x for x in required if not evidence.get(x, False)]
        return {
            "required": required,
            "green": tuple(x for x in required if evidence.get(x, False)),
            "missing": tuple(missing),
            "certified": not missing,
        }
