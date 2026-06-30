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

import datetime
import faulthandler
import logging
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Crash handler — installed FIRST, using only stdlib, so a failure during the
# heavy third-party imports below (or anywhere else) is always written to disk.
# Without this a packaged .app that crashes at startup just vanishes with no log,
# which is exactly what testers reported.
# ─────────────────────────────────────────────────────────────────────────────

def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent  # type: ignore[attr-defined]
    return Path(__file__).parent


def _crash_log_path() -> Path:
    """Always-writable location for crash reports. Tries app/logs, then home."""
    for candidate in (_app_base_dir() / "logs", Path.home() / "Desktop", Path.home()):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".steadycut_write_test"
            probe.write_text("ok")
            probe.unlink(missing_ok=True)
            return candidate / "steadycut_crash.txt"
        except Exception:
            continue
    import tempfile
    return Path(tempfile.gettempdir()) / "steadycut_crash.txt"


_CRASH_LOG = _crash_log_path()


def _write_crash(header: str, text: str) -> None:
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_CRASH_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {header} — {ts} =====\n{text}\n")
    except Exception:
        pass  # last resort: never let the crash handler itself crash


def _install_crash_handler() -> None:
    # faulthandler catches hard crashes (segfaults in native libs like OpenCV/torch)
    try:
        faulthandler.enable(open(_CRASH_LOG, "a", encoding="utf-8"))
    except Exception:
        pass

    def _hook(exc_type, exc_value, exc_tb) -> None:
        _write_crash("UNCAUGHT EXCEPTION",
                     "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook

    def _thread_hook(args) -> None:
        _write_crash("UNCAUGHT THREAD EXCEPTION",
                     "".join(traceback.format_exception(
                         args.exc_type, args.exc_value, args.exc_traceback)))

    threading.excepthook = _thread_hook


_install_crash_handler()


# Heavy third-party imports — wrapped so an import failure in the frozen bundle
# (missing hidden import, bad wheel, etc.) lands in the crash log instead of a
# silent disappearing window.
try:
    import uvicorn
    import webview
    from fastapi import BackgroundTasks, FastAPI
    from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel

    from _version import __version__
    from steadycut_pipeline import run_pipeline
except BaseException:
    # BaseException (not just Exception) so a SystemExit from a module's import
    # guard — which would otherwise exit the frozen app silently — is captured.
    _write_crash("STARTUP IMPORT FAILURE", traceback.format_exc())
    raise


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def _setup_logging() -> Path:
    # Reconfigure stdout to UTF-8 so Unicode chars don't crash on Windows CP1252.
    # sys.stdout is None under pythonw.exe — guard every access.
    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # Local logs/ folder next to the app — easy to find and share
    local_log_dir = _app_base_dir() / "logs"
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
    adaptive:            bool  = False   # T1: per-clip median+k·MAD threshold (no sweep)
    sensitivity:         float = 3.0     # T1: the k in median + k·MAD (higher = keep more)
    fallback_fps:        float = 25.0
    yolo_model:          str   = "yolov8n.pt"
    cut_on_action_mode:  str   = "off"    # "off" | "mark" | "cut"
    target_nle:          str   = "premiere"  # "premiere" | "resolve" | "resolve_api"
    resolve_dir:         str | None = None    # optional Resolve install-folder override (resolve_api)
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


@app.get("/api/version")
def get_version() -> dict:
    return {"version": __version__}


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
            return {"count": 0, "valid": False, "proxies_found": False, "proxy_count": 0}
        count = sum(
            1 for f in p.iterdir()
            if f.is_file()
            and f.suffix.lower() in _VIDEO_EXTS
            and not f.name.startswith("._")
        )
        proxy_dir = p / "proxies"
        proxy_count = 0
        if proxy_dir.is_dir():
            proxy_count = sum(
                1 for f in proxy_dir.iterdir()
                if f.is_file()
                and f.suffix.lower() in _VIDEO_EXTS
                and not f.name.startswith("._")
            )
        return {
            "count": count,
            "valid": True,
            "proxies_found": proxy_dir.is_dir(),
            "proxy_count": proxy_count,
            "proxy_dir": str(proxy_dir) if proxy_dir.is_dir() else None,
        }
    except Exception:
        return {"count": 0, "valid": False, "proxies_found": False, "proxy_count": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Keep/Cut review — grade a processed job window-by-window.
# Review unit = one stable window (a clip can yield several). The screen serves
# windows, you press K/X, verdicts append to <job>/verdicts.jsonl (resumable).
# ponytail: windows are served in pipeline order for now; keep_score sorting is
# the next loop, measured BY this screen — order doesn't block grading.
# ─────────────────────────────────────────────────────────────────────────────

_review = {"job_dir": None, "proxy_dir": None}


def _proxy_name(clip: str) -> str:
    """RHYCO..._7874.MP4 -> RHYCO..._7874_Proxy.mp4 (proxies are per source file)."""
    return f"{Path(clip).stem}_Proxy.mp4"


def _flatten_windows(clips: dict) -> list[dict]:
    """{filename: [window,...]} -> flat review cards, one per (clip, window)."""
    cards = []
    for clip, windows in clips.items():
        for w in windows:
            dur = float(w.get("duration_secs") or 0.0)
            in_f, out_f = w.get("in_frame", 0), w.get("out_frame", 0)
            # fps isn't stored; derive it from the window itself so any job works.
            fps = (out_f - in_f) / dur if dur > 0 and out_f > in_f else 25.0
            in_secs = in_f / fps if fps else 0.0
            cards.append({
                "id":       f"{clip}#{w.get('window_index', 1)}",
                "clip":     clip,
                "window":   w.get("window_index", 1),
                "proxy":    _proxy_name(clip),
                "in_secs":  round(in_secs, 3),
                "out_secs": round(in_secs + dur, 3),
                "dur":      round(dur, 2),
                "in_frame": in_f,                 # source frame of window start
                "fps":      round(fps, 3),        # so the UI can map a trim mark -> source frame
                "in_tc":    w.get("in_tc", ""),
            })
    return cards


@app.get("/api/review/load")
def review_load(job: str = "eval_runs") -> dict:
    """Load the newest *_dev_report.json under <job>, plus existing verdicts."""
    import json
    job_dir = Path(job).expanduser().resolve()
    if not job_dir.is_dir():
        return JSONResponse({"error": f"Not a folder: {job_dir}"}, status_code=400)
    reports = sorted(job_dir.glob("*_dev_report.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        return JSONResponse({"error": f"No *_dev_report.json in {job_dir}"}, status_code=404)
    clips = json.loads(reports[-1].read_text()).get("clips", {})
    proxy_dir = job_dir / "proxies"
    _review.update({"job_dir": str(job_dir), "proxy_dir": str(proxy_dir)})

    decided = {}
    vfile = job_dir / "verdicts.jsonl"
    if vfile.exists():
        for line in vfile.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    decided[rec["id"]] = rec   # last line wins (undo + re-grade)
                except (ValueError, KeyError):
                    continue

    cards = _flatten_windows(clips)
    for c in cards:
        rec = decided.get(c["id"])  # None = not yet graded
        c["decision"]   = rec["decision"] if rec else None
        c["note"]       = rec.get("note", "") if rec else ""
        c["needs_trim"] = rec.get("needs_trim", False) if rec else False
    return {"job_dir": str(job_dir), "report": reports[-1].name,
            "count": len(cards), "cards": cards}


@app.get("/api/review/proxy/{name}")
def review_proxy(name: str):
    """Serve one proxy file from the loaded job. Basename only — no traversal."""
    pdir = _review.get("proxy_dir")
    if not pdir:
        return JSONResponse({"error": "No job loaded"}, status_code=409)
    safe = Path(name).name  # strip any path component
    fp = Path(pdir) / safe
    if not fp.is_file():
        return JSONResponse({"error": f"Proxy not found: {safe}"}, status_code=404)
    return FileResponse(str(fp), media_type="video/mp4")


class Verdict(BaseModel):
    id:         str
    clip:       str
    window:     int
    decision:   str            # "keep" | "cut"
    note:       str  = ""      # free-text reason — raw material for keep_score weights
    needs_trim: bool = False   # kept, but window boundaries are off (mis-cut signal)
    trim_mark_secs:  float | None = None  # playhead offset into the window when T pressed
    trim_mark_frame: int   | None = None  # same point as a SOURCE frame (in_frame + offset*fps)


@app.post("/api/review/verdict")
def review_verdict(v: Verdict) -> dict:
    """Append one keep/cut decision (+ optional note/trim flag) to <job>/verdicts.jsonl."""
    import json
    job_dir = _review.get("job_dir")
    if not job_dir:
        return JSONResponse({"error": "No job loaded"}, status_code=409)
    if v.decision not in ("keep", "cut"):
        return JSONResponse({"error": "decision must be keep|cut"}, status_code=400)
    rec = {"id": v.id, "clip": v.clip, "window": v.window,
           "decision": v.decision, "note": v.note.strip(),
           "needs_trim": v.needs_trim,
           "trim_mark_secs": v.trim_mark_secs, "trim_mark_frame": v.trim_mark_frame,
           "ts": time.time()}
    with (Path(job_dir) / "verdicts.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return {"ok": True}


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
            adaptive=body.adaptive,
            sensitivity=body.sensitivity,
            fallback_fps=body.fallback_fps,
            yolo_model=yolo_model,
            state=_state,
            cut_on_action_mode=body.cut_on_action_mode,
            target_nle=body.target_nle,
            resolve_dir=(body.resolve_dir or None),
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


def _main() -> None:
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


if __name__ == "__main__":
    try:
        _main()
    except BaseException:
        # Last-resort net under the entry point. The excepthook already logs,
        # but this guarantees the crash file is written even if the hook was
        # replaced (e.g. by webview) before the failure, or the failure is a
        # SystemExit that the hook would skip.
        _write_crash("FATAL — app exited at startup", traceback.format_exc())
        log.exception("SteadyCut crashed at startup")
        raise
