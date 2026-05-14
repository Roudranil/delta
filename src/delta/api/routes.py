from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from delta.agent.runner import store, stream_response
from delta.api.streaming import format_sse
from delta.config import settings
from delta.models import ChatRequest

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "model": settings.default_model}


@router.post("/chat")
async def chat(request: ChatRequest):
    async def _generate():
        async for event in stream_response(request):
            yield format_sse(event)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    entries = store.load_raw(session_id)
    return {"session_id": session_id, "entries": [e.__dict__ for e in entries]}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    deleted = store.delete(session_id)
    return {"deleted": deleted}
