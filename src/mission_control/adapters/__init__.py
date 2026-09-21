"""Agent adapter implementations."""

from .base import AgentAssignment, AgentExecution, AgentHandoff
from .claude import ClaudeAdapter
from .codex import CodexAdapter

__all__ = [
    "AgentAssignment",
    "AgentExecution",
    "AgentHandoff",
    "ClaudeAdapter",
    "CodexAdapter",
]
