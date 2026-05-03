"""
run.py — SteadyCut Web UI server
=================================
Starts a FastAPI server, serves the static dashboard, and auto-opens the
browser 1.5 s after boot.

Usage:
    python run.py

The browser will open automatically at http://localhost:8000
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from steadycut_pipeline import run_pipeline

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="SteadyCut")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─────────────────────────────────────────────────────────────────────────────
# Shared pipeline state — written by background thread, read by /api/status
# ─────────────────────────────────────────────────────────────────────────────

_state: dict = {
    "running": False,
    "phase":   "idle",
    "percent": 0,
    "done":    False,
    "error":   None,
}


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    input_dir:          str
    output_xml:         str
    no_proxies:         bool  = False
    skip_proxies:       bool  = False
    skip_classification: bool = False
    threshold:          float = 2.0
    stable_secs:        float = 1.0
    fallback_fps:       float = 25.0
    yolo_model:         str   = "yolov8n.pt"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@app.post("/api/process")
def start_process(body: ProcessRequest, bg: BackgroundTasks):
    if _state["running"]:
        return {"error": "Pipeline is already running."}, 409
    _state.update({"running": True, "phase": "Starting…", "percent": 0,
                   "done": False, "error": None})
    bg.add_task(_pipeline_task, body)
    return {"status": "started"}


@app.get("/api/status")
def get_status() -> dict:
    return _state


# ─────────────────────────────────────────────────────────────────────────────
# Background task
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline_task(body: ProcessRequest) -> None:
    input_dir  = Path(body.input_dir).expanduser().resolve()
    output_xml = Path(body.output_xml).expanduser().resolve()
    proxy_dir  = input_dir / "proxies"
    yolo_model = None if body.skip_classification else body.yolo_model

    try:
        run_pipeline(
            input_dir=input_dir,
            output_xml=output_xml,
            proxy_dir=proxy_dir,
            no_proxies=body.no_proxies,
            skip_proxies=body.skip_proxies,
            threshold=body.threshold,
            stable_secs=body.stable_secs,
            fallback_fps=body.fallback_fps,
            yolo_model=yolo_model,
            state=_state,
        )
    except Exception as exc:
        _state.update({
            "running": False,
            "phase":   "Error",
            "done":    True,
            "error":   str(exc),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000")).start()
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=False)
