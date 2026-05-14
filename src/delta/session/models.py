from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionEntry:
    id: str
    session_id: str
    timestamp: str
    role: str  # "human" | "ai" | "tool"
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
