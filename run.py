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
    import datetime

    # Reconfigure stdout to UTF-8 so Unicode chars don't crash on Windows CP1252.
    # sys.stdout is None under pythonw.exe — guard every access.
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Local logs/ folder next to the app — easy to find and share
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).parent  # type: ignore[attr-defined]
    else:
        app_dir = Path(__file__).parent

    local_log_dir = app_dir / "logs"
    local_log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    local_log_file = local_log_dir / f"steadycut_{ts}.txt"

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Force-reset root logger handlers: steadycut_pipeline's module-level
    # basicConfig() runs before this function (import order) and adds a
    # console-only StreamHandler, making subsequent basicConfig() calls no-ops
    # and leaving the FileHandler never registered.
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(local_log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Only add console handler when stdout is available (not pythonw.exe)
    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    return local_log_file


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
    "running":          False,
    "phase":            "idle",
    "percent":          0,
    "done":             False,
    "error":            None,
    "stopped":          False,
    "ffmpeg_ready":     False,
    "ffmpeg_status":    "checking",
    "ffmpeg_message":   "Checking for FFmpeg…",
    "clip_current":     0,
    "clip_total":       0,
    "dev_report":       {},
    "dev_report_path":  None,
    "rejects_xml_path": None,
    "phase_timings":    {},
    "pipeline_start_ts": None,
    "pipeline_total_s": None,
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
    skip_stability:      bool  = False
    skip_classification: bool  = False
    threshold:           float = 2.0
    max_threshold:       float = 100.0
    stable_secs:         float = 1.0
    fallback_fps:        float = 25.0
    yolo_model:          str   = "yolov8n.pt"
    cut_on_action_mode:  str   = "off"    # "off" | "mark" | "cut"
    coa_sensitivity:     float = 0.02    # detect_cut_frame sensitivity
    tail_trim_frames:    int   = 0       # frames to trim from stable window end
    head_trim_frames:    int   = 0       # frames to trim from stable window start
    proxy_cpu_preset:    str   = "high"  # "low" | "medium" | "high"
    proxy_use_gpu:       bool  = False   # use hardware H.264 encoder if available
    analysis_mode:              str   = "stability"  # "stability" | "action" | "both"
    skip_multi_person_action:   bool  = True
    action_velocity_threshold:  float = 3.0
    pose_model:                 str   = "yolov8n-pose.pt"


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
                   "done": False, "error": None,
                   "phase_timings": {}, "pipeline_start_ts": None, "pipeline_total_s": None})
    bg.add_task(_pipeline_task, body)
    return {"status": "started"}


@app.get("/api/status")
def get_status() -> dict:
    return _state


@app.post("/api/stop")
def stop_process():
    from pipelinev3 import request_stop
    request_stop()
    _state["phase"] = "Stopping…"
    return {"status": "stopping"}


@app.get("/api/log-path")
def get_log_path() -> dict:
    return {"path": str(_log_file)}


@app.get("/api/ffmpeg-status")
def ffmpeg_status() -> dict:
    return {"ready": _state["ffmpeg_ready"], "status": _state["ffmpeg_status"],
            "message": _state["ffmpeg_message"]}


@app.get("/api/detect-gpu")
def detect_gpu_endpoint() -> dict:
    """
    Probe FFmpeg for available hardware H.264 encoders.
    Returns the best encoder name and a human-readable label, or null if none found.
    """
    from pipelinev3 import detect_gpu_encoder
    encoder = detect_gpu_encoder()
    labels = {
        "h264_nvenc":        "NVIDIA NVENC",
        "h264_amf":          "AMD AMF",
        "h264_qsv":          "Intel Quick Sync",
        "h264_videotoolbox": "Apple VideoToolbox",
    }
    return {
        "encoder": encoder,
        "label":   labels.get(encoder, None) if encoder else None,
        "available": encoder is not None,
    }


@app.get("/api/dev-report")
def get_dev_report() -> dict:
    """Dev-only: returns all detected stable windows + COA peaks from the last run."""
    return {
        "dev_report_path": _state.get("dev_report_path"),
        "clips":           _state.get("dev_report", {}),
    }


_VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".m4v", ".r3d", ".braw"}

@app.get("/api/clip-count")
def get_clip_count(dir: str) -> dict:
    try:
        p = Path(dir).expanduser().resolve()
        if not p.is_dir():
            return {"count": 0, "valid": False}
        count = sum(
            1 for f in p.iterdir()
            if f.is_file()
            and f.suffix.lower() in _VIDEO_EXTS
            and not f.name.startswith("._")
        )
        return {"count": count, "valid": True}
    except Exception:
        return {"count": 0, "valid": False}


def _pipeline_task(body: ProcessRequest) -> None:
    from pipelinev3 import clear_stop, PipelineStoppedError
    clear_stop()
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
            skip_stability=body.skip_stability,
            threshold=body.threshold,
            max_threshold=body.max_threshold,
            stable_secs=body.stable_secs,
            fallback_fps=body.fallback_fps,
            yolo_model=yolo_model,
            state=_state,
            cut_on_action_mode=body.cut_on_action_mode,
            coa_sensitivity=body.coa_sensitivity,
            tail_trim_frames=body.tail_trim_frames,
            head_trim_frames=body.head_trim_frames,
            proxy_cpu_preset=body.proxy_cpu_preset,
            proxy_use_gpu=body.proxy_use_gpu,
            analysis_mode=body.analysis_mode,
            skip_multi_person_action=body.skip_multi_person_action,
            action_velocity_threshold=body.action_velocity_threshold,
            pose_model=body.pose_model,
        )
    except PipelineStoppedError:
        _state.update({"running": False, "phase": "Stopped", "percent": 0,
                       "done": True, "error": None, "stopped": True})
    except Exception as exc:
        _state.update({"running": False, "phase": "Error", "done": True,
                       "error": str(exc), "stopped": False})


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

    def reveal_in_finder(self, path: str) -> bool:
        """Reveal the file in Finder (macOS) / Explorer (Windows) / file manager (Linux)."""
        import subprocess
        try:
            p = Path(path)
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(p)], check=False)
            elif sys.platform == "win32":
                subprocess.run(["explorer", "/select,", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p.parent)], check=False)
            return True
        except Exception:
            return False

    def open_log(self, path: str) -> bool:
        """Open the log file in the system's default text viewer."""
        import subprocess
        try:
            p = Path(path)
            if sys.platform == "darwin":
                subprocess.run(["open", str(p)], check=False)
            elif sys.platform == "win32":
                subprocess.run(["notepad", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
            return True
        except Exception:
            return False


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
