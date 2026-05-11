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

import logging
import sys
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


def _setup_logging() -> Path:
    """
    Route all log output to a file in the platform log directory.
    Returns the log file path so the startup message can print it.
    On macOS: ~/Library/Logs/SteadyCut/steadycut.log
    On Windows: %APPDATA%/SteadyCut/logs/steadycut.log
    """
    import os
    if sys.platform == "darwin":
        log_dir = Path.home() / "Library" / "Logs" / "SteadyCut"
    elif sys.platform == "win32":
        log_dir = Path(os.environ.get("APPDATA", Path.home())) / "SteadyCut" / "logs"
    else:
        log_dir = Path.home() / ".local" / "share" / "SteadyCut" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "steadycut.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


_log_file = _setup_logging()
log = logging.getLogger("steadycut.server")
log.info("SteadyCut starting — log file: %s", _log_file)


def _static_dir() -> Path:
    """Resolve the static/ directory for both dev and PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"  # type: ignore[attr-defined]
    return Path(__file__).parent / "static"


app = FastAPI(title="SteadyCut")
app.mount("/static", StaticFiles(directory=str(_static_dir())), name="static")

# ─────────────────────────────────────────────────────────────────────────────
# Shared pipeline state — written by background thread, read by /api/status
# ─────────────────────────────────────────────────────────────────────────────

_state: dict = {
    "running":         False,
    "phase":           "idle",
    "percent":         0,
    "done":            False,
    "error":           None,
    "ffmpeg_ready":    False,
    "ffmpeg_status":   "checking",   # "checking" | "downloading" | "ready" | "error"
    "ffmpeg_message":  "Checking for FFmpeg…",
}


# ─────────────────────────────────────────────────────────────────────────────
# FFmpeg startup check — runs in background thread on server start
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_ffmpeg_background() -> None:
    from ffmpeg_helper import ensure_ffmpeg, is_ffmpeg_available
    if is_ffmpeg_available():
        _state.update({
            "ffmpeg_ready":   True,
            "ffmpeg_status":  "ready",
            "ffmpeg_message": "FFmpeg is available.",
        })
        return

    _state.update({
        "ffmpeg_status":  "downloading",
        "ffmpeg_message": "Downloading FFmpeg…",
    })

    def _cb(msg: str) -> None:
        _state["ffmpeg_message"] = msg

    try:
        ensure_ffmpeg(status_callback=_cb)
        _state.update({
            "ffmpeg_ready":   True,
            "ffmpeg_status":  "ready",
            "ffmpeg_message": "FFmpeg ready.",
        })
    except Exception as exc:
        _state.update({
            "ffmpeg_ready":   False,
            "ffmpeg_status":  "error",
            "ffmpeg_message": str(exc),
        })


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_ensure_ffmpeg_background, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    input_dir:           str
    output_xml:          str
    no_proxies:          bool  = False
    skip_proxies:        bool  = False
    skip_classification: bool  = False
    threshold:           float = 2.0
    max_threshold:       float = 100.0
    stable_secs:         float = 1.0
    fallback_fps:        float = 25.0
    yolo_model:          str   = "yolov8n.pt"


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@app.post("/api/process")
def start_process(body: ProcessRequest, bg: BackgroundTasks):
    if not _state["ffmpeg_ready"]:
        return {"error": "FFmpeg is not ready yet — please wait for setup to complete."}, 503
    if _state["running"]:
        return {"error": "Pipeline is already running."}, 409
    _state.update({"running": True, "phase": "Starting…", "percent": 0,
                   "done": False, "error": None})
    bg.add_task(_pipeline_task, body)
    return {"status": "started"}


@app.get("/api/status")
def get_status() -> dict:
    return _state


@app.get("/api/ffmpeg-status")
def ffmpeg_status() -> dict:
    return {
        "ready":   _state["ffmpeg_ready"],
        "status":  _state["ffmpeg_status"],
        "message": _state["ffmpeg_message"],
    }


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
            max_threshold=body.max_threshold,
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

def _open_browser_when_ready(url: str) -> None:
    """Poll until the server accepts connections, then open the browser."""
    import subprocess
    import urllib.request
    import time

    for _ in range(60):          # wait up to 60 s (torch import is slow)
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except Exception:
            time.sleep(1)

    # On macOS use the 'open' command — more reliable inside a .app bundle
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        webbrowser.open(url)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    threading.Thread(
        target=_open_browser_when_ready,
        args=("http://localhost:8000",),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
