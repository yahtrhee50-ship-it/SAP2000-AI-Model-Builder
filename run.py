"""Entry point — starts the FastAPI server."""
import os
import sys
import webbrowser
import threading
import time

import uvicorn
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))


def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    print(f"\n  SAP2000 AI Model Builder")
    print(f"  Running at  http://{HOST}:{PORT}")
    print(f"  API docs    http://{HOST}:{PORT}/docs")
    print(f"  Press Ctrl+C to stop\n")

    # Open browser automatically
    threading.Thread(target=open_browser, daemon=True).start()

    uvicorn.run(
        "src.backend.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        reload_dirs=["src"],
    )
