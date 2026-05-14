from typing import Any, Literal

from pydantic import BaseModel, Field
from uuid import uuid4


class ChatRequest(BaseModel):
    message: str
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    model: str | None = None


class StreamEvent(BaseModel):
    type: Literal["text_delta", "tool_start", "tool_result", "done", "error"]
    session_id: str
    content: str | None = None
    tool: str | None = None
    input: dict[str, Any] | None = None
    output: str | None = None
    error: str | None = None
