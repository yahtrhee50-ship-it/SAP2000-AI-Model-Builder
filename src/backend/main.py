"""FastAPI application entry point."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from .routes.chat import router as chat_router
from .routes.sap2000 import router as sap_router
from .routes.preview import router as preview_router

app = FastAPI(
    title="SAP2000 AI Model Builder",
    description="AI-assisted structural model creation for SAP2000",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(sap_router)
app.include_router(preview_router)

# Serve frontend static files
_frontend = Path(__file__).parent.parent / "frontend"
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=str(_frontend / "css"), html=False), name="css")
    app.mount("/js", StaticFiles(directory=str(_frontend / "js"), html=False), name="js")

    @app.get("/", include_in_schema=False)
    async def root():
        return FileResponse(str(_frontend / "index.html"))

    @app.get("/demo", include_in_schema=False)
    async def demo():
        return FileResponse(str(_frontend / "demo.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
