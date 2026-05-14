from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from delta.agent.graph import graph
from delta.config import settings
from delta.models import ChatRequest, StreamEvent
from delta.session.models import SessionEntry
from delta.session.store import SessionStore

store = SessionStore()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_ai(session_id: str, msg: AIMessage) -> None:
    store.append(SessionEntry(
        id=uuid4().hex,
        session_id=session_id,
        timestamp=_now(),
        role="ai",
        content=msg.content if isinstance(msg.content, str) else None,
        tool_calls=list(msg.tool_calls) if msg.tool_calls else None,
    ))


def _persist_tool(session_id: str, msg: ToolMessage) -> None:
    store.append(SessionEntry(
        id=uuid4().hex,
        session_id=session_id,
        timestamp=_now(),
        role="tool",
        content=msg.content,
        tool_call_id=msg.tool_call_id,
    ))


async def stream_response(request: ChatRequest) -> AsyncIterator[StreamEvent]:
    model = request.model or settings.default_model
    history = store.load(request.session_id)

    store.append(SessionEntry(
        id=uuid4().hex,
        session_id=request.session_id,
        timestamp=_now(),
        role="human",
        content=request.message,
    ))

    state = {
        "messages": history + [HumanMessage(content=request.message)],
        "session_id": request.session_id,
        "iteration": 0,
        "model": model,
    }

    async for event in graph.astream_events(state, version="v2"):
        kind = event["event"]
        name = event.get("name", "")

        if kind == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            if chunk and chunk.content:
                yield StreamEvent(
                    type="text_delta",
                    session_id=request.session_id,
                    content=chunk.content,
                )

        elif kind == "on_tool_start":
            yield StreamEvent(
                type="tool_start",
                session_id=request.session_id,
                tool=name,
                input=event["data"].get("input"),
            )

        elif kind == "on_tool_end":
            yield StreamEvent(
                type="tool_result",
                session_id=request.session_id,
                tool=name,
                output=str(event["data"].get("output", "")),
            )

        elif kind == "on_chain_end":
            # Capture new messages from each node's output for persistence
            output = event["data"].get("output") or {}
            for msg in output.get("messages", []):
                if isinstance(msg, AIMessage):
                    _persist_ai(request.session_id, msg)
                elif isinstance(msg, ToolMessage):
                    _persist_tool(request.session_id, msg)

    yield StreamEvent(type="done", session_id=request.session_id)
