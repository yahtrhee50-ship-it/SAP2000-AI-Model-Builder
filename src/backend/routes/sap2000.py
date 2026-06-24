"""SAP2000 model building routes."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ..services.sap2000.connector import get_connection
from ..services.sap2000.builder import ModelBuilder
from ..models.structural import StructuralModel

router = APIRouter(prefix="/api/sap2000", tags=["sap2000"])


class BuildRequest(BaseModel):
    save_path: str = ""
    run_analysis: bool = False
    visible: bool = True


class ConnectRequest(BaseModel):
    visible: bool = True


@router.post("/connect")
async def connect_sap2000(req: ConnectRequest):
    """Connect to or start SAP2000. Runs in thread pool so it doesn't block the event loop."""
    def _connect():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        conn = get_connection()
        conn.connect(visible=req.visible)
        return {"status": "connected"}

    try:
        return await asyncio.to_thread(_connect)
    except Exception as exc:
        raise HTTPException(500, f"SAP2000 connection failed: {exc}")


@router.post("/build/{session_id}")
async def build_model(session_id: str, req: BuildRequest):
    """Build the SAP2000 model from the interview session."""
    from .chat import _sessions
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    if not session.model.is_complete():
        missing = [k for k, v in session.model.completion_summary().items() if not v]
        raise HTTPException(400, f"Model incomplete. Missing: {missing}")

    def _build():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        conn = get_connection()
        if conn._model is None:
            conn.connect(visible=True)
        conn.initialize_new_model(session.model.project.unit_system.value)
        builder = ModelBuilder(conn)
        report = builder.build(session.model)
        if req.save_path:
            conn.save(req.save_path)
            report["saved_to"] = req.save_path
        if req.run_analysis:
            conn.run_analysis()
            report["analysis"] = "completed"
        return {"status": "success", "report": report}

    try:
        return await asyncio.to_thread(_build)
    except Exception as exc:
        raise HTTPException(500, f"Build failed: {exc}")


@router.post("/build-from-json")
async def build_from_json(model_data: dict):
    """Build SAP2000 model directly from a StructuralModel JSON payload."""
    try:
        model = StructuralModel(**model_data)
    except Exception as exc:
        raise HTTPException(422, f"Invalid model data: {exc}")

    def _build():
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
        conn = get_connection()
        if conn._model is None:
            conn.connect(visible=True)
        conn.initialize_new_model(model.project.unit_system.value)
        builder = ModelBuilder(conn)
        return {"status": "success", "report": builder.build(model)}

    try:
        return await asyncio.to_thread(_build)
    except Exception as exc:
        raise HTTPException(500, f"Build failed: {exc}")


@router.get("/status")
async def sap2000_status():
    """Check whether SAP2000 is connected."""
    conn = get_connection()
    return {"connected": conn._model is not None}
