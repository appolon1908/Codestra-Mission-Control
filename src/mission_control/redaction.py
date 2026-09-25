from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

SECRET_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|credential|cookie|authorization|"
    r"private[_-]?key|session[_-]?key|bearer)",
    re.IGNORECASE,
)

_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Provider API keys and OAuth tokens (Anthropic, OpenAI, GitHub, Linear, Notion, Slack).
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{8,}"),
    re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{8,}"),
    re.compile(r"\b(?:secret|ntn)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    # Account identifiers are not secrets but must not leak into shared ledgers.
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
)


def redact_text(value: str, *, limit: int = 500) -> str:
    text = value
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text[:limit]


def secret_keys(payload: Any, *, prefix: str = "") -> list[str]:
    """Return dotted paths of keys that look like they carry secret material."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, child in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if SECRET_KEY_PATTERN.search(str(key)):
                found.append(path)
            found.extend(secret_keys(child, prefix=path))
    elif isinstance(payload, list):
        for index, child in enumerate(payload):
            found.extend(secret_keys(child, prefix=f"{prefix}[{index}]"))
    return found
