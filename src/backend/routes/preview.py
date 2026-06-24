"""Stateless preview endpoint — no session required."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from ..models.structural import StructuralModel
from ..services.interview_engine import InterviewSession

router = APIRouter(prefix="/api/preview", tags=["preview"])

# Reuse a throw-away session just for preview_data computation
class _NullProvider:
    pass


@router.post("")
async def compute_preview(model_data: dict):
    """
    Accept a partial or complete StructuralModel dict and return 3D preview data.
    No AI / API key required.
    """
    try:
        model = StructuralModel(**model_data)
    except Exception as exc:
        raise HTTPException(422, f"Invalid model data: {exc}")

    sess = InterviewSession("preview-only", _NullProvider())  # type: ignore[arg-type]
    sess.model = model
    return sess.get_preview_data()
