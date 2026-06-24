"""Chat / interview routes — streaming and non-streaming."""
from __future__ import annotations
import uuid
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from ..services.interview_engine import InterviewSession, SYSTEM_PROMPT
from ..services.ai_providers import ClaudeProvider, OpenAIProvider

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# In-memory session store (keyed by session_id)
_sessions: dict[str, InterviewSession] = {}


def _get_provider(provider_name: str, api_key: str):
    if provider_name == "openai":
        return OpenAIProvider(api_key)
    return ClaudeProvider(api_key)


class StartRequest(BaseModel):
    provider: str = "claude"
    api_key: str

    @field_validator("api_key")
    @classmethod
    def key_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("api_key must not be empty")
        return v.strip()

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, v: str) -> str:
        if v not in ("claude", "openai"):
            raise ValueError("provider must be 'claude' or 'openai'")
        return v


class MessageRequest(BaseModel):
    session_id: str
    message: str


@router.post("/start")
async def start_session(req: StartRequest):
    """Create a new interview session."""
    session_id = str(uuid.uuid4())
    provider = _get_provider(req.provider, req.api_key)
    session = InterviewSession(session_id, provider)
    _sessions[session_id] = session

    try:
        greeting = await session.provider.chat(
            [{"role": "user", "content": "Hello, I'm ready to start."}],
            SYSTEM_PROMPT,
        )
    except Exception as exc:
        del _sessions[session_id]
        log.error("AI provider error on start: %s", exc)
        raise HTTPException(502, f"AI provider error: {exc}")

    session.messages.append({"role": "user", "content": "Hello, I'm ready to start."})
    session.messages.append({"role": "assistant", "content": greeting})

    return {"session_id": session_id, "message": greeting}


@router.post("/message")
async def send_message(req: MessageRequest):
    """Send a message and get a full (non-streaming) response."""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    try:
        response = await session.send(req.message)
    except Exception as exc:
        log.error("AI provider error on message: %s", exc)
        raise HTTPException(502, f"AI provider error: {exc}")
    preview = session.get_preview_data()
    return {
        "message": response,
        "preview": preview,
        "model_ready": session.model_finalized,
    }


@router.post("/message/stream")
async def send_message_stream(req: MessageRequest):
    """Send a message and stream the response as SSE."""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    async def event_stream():
        async for chunk in session.send_stream(req.message):
            yield f"data: {chunk}\n\n"
        preview = session.get_preview_data()
        import json
        yield f"event: preview\ndata: {json.dumps(preview)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/preview/{session_id}")
async def get_preview(session_id: str):
    """Get current 3D preview data for the session."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.get_preview_data()


@router.get("/model/{session_id}")
async def get_model(session_id: str):
    """Return the full structured model JSON."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.model.model_dump()


@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"status": "deleted"}
