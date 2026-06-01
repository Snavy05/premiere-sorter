"""
run.py — SteadyCut desktop app
===============================
Starts FastAPI in a background thread, then opens a native OS window via
pywebview. No browser required — the app is a proper desktop window.

  macOS  → WKWebView  (built-in, zero extra install)
  Windows → WebView2   (ships with Win10/11, zero extra install)

Usage:
    python run.py        # dev
    ./SteadyCut.app      # packaged
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from steadycut_pipeline import run_pipeline


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging() -> Path:
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
log.info("SteadyCut starting — log: %s", _log_file)


def _static_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"  # type: ignore[attr-defined]
    return Path(__file__).parent / "static"


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="SteadyCut")
app.mount("/static", StaticFiles(directory=str(_static_dir())), name="static")

_state: dict = {
    "running":        False,
    "phase":          "idle",
    "percent":        0,
    "done":           False,
    "error":          None,
    "ffmpeg_ready":   False,
    "ffmpeg_status":  "checking",
    "ffmpeg_message": "Checking for FFmpeg…",
}


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_ensure_ffmpeg_background, daemon=True).start()


def _ensure_ffmpeg_background() -> None:
    from ffmpeg_helper import ensure_ffmpeg, is_ffmpeg_available
    if is_ffmpeg_available():
        _state.update({"ffmpeg_ready": True, "ffmpeg_status": "ready",
                        "ffmpeg_message": "FFmpeg is available."})
        return
    _state.update({"ffmpeg_status": "downloading", "ffmpeg_message": "Downloading FFmpeg…"})
    def _cb(msg: str) -> None:
        _state["ffmpeg_message"] = msg
    try:
        ensure_ffmpeg(status_callback=_cb)
        _state.update({"ffmpeg_ready": True, "ffmpeg_status": "ready",
                        "ffmpeg_message": "FFmpeg ready."})
    except Exception as exc:
        _state.update({"ffmpeg_ready": False, "ffmpeg_status": "error",
                        "ffmpeg_message": str(exc)})


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
    cut_on_action_mode:  str   = "off"  # "off" | "mark" | "cut"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@app.post("/api/process")
def start_process(body: ProcessRequest, bg: BackgroundTasks):
    if not _state["ffmpeg_ready"]:
        return {"error": "FFmpeg is not ready yet — please wait."}, 503
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
    return {"ready": _state["ffmpeg_ready"], "status": _state["ffmpeg_status"],
            "message": _state["ffmpeg_message"]}


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
            cut_on_action_mode=body.cut_on_action_mode,
        )
    except Exception as exc:
        _state.update({"running": False, "phase": "Error", "done": True, "error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# pywebview API — JS calls these via window.pywebview.api.*
# ─────────────────────────────────────────────────────────────────────────────

class SteadyCutAPI:
    """Methods exposed to the UI via window.pywebview.api.*"""

    def pick_folder(self) -> str | None:
        """Open a native folder picker. Returns the chosen path or None."""
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return str(result[0])
        return None

    def pick_save_file(self, default_name: str = "Sequence.xml") -> str | None:
        """Open a native save-file dialog. Returns the chosen path or None."""
        result = webview.windows[0].create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_name,
            file_types=("XML files (*.xml)", "All files (*.*)")
        )
        if isinstance(result, str):
            return result
        if result and len(result) > 0:
            return str(result[0])
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _start_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    PORT = 8765

    server_thread = threading.Thread(
        target=_start_server,
        kwargs={"host": "127.0.0.1", "port": PORT},
        daemon=True,
    )
    server_thread.start()

    # Wait until the server is accepting connections (max 10s)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

    # Open native desktop window — no browser
    api = SteadyCutAPI()
    window = webview.create_window(
        title="SteadyCut",
        url=f"http://127.0.0.1:{PORT}",
        js_api=api,
        width=860,
        height=740,
        min_size=(680, 580),
        background_color="#0d0d10",
    )
    webview.start(debug=False)
    # When the window closes, the daemon server thread dies with the process.
