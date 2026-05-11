"""
shot_classifier.py — Phase 2: YOLO-Based Person-Count Shot Classifier
=====================================================================
Consumes the stable-segment metadata produced by pipelinev3.py (Phase 1)
and classifies each clip by the number of people detected in the stable
window.

Classification Logic
--------------------
1. Extract three sample frames at 25 %, 50 %, 75 % of the stable window.
2. Run YOLOv8 person detection on all three frames.
3. Use the 50 % frame as the authoritative detection set (fall back to
   25 % or 75 % if the 50 % frame yielded no detections).
4. If no person is detected at all, or the largest bounding box is below
   SCENERY_AREA_THRESHOLD -> [BRolls].
5. 1-2 persons -> [<2 People].
6. 3+ persons  -> [Multiple Subjects].

Other design decisions
----------------------
* Frame extraction uses FFmpeg timestamps (not raw frame counts) to avoid
  VFR drift on GoPro / mirrorless camera files.
* Detection restricted to COCO class 0 (Person), confidence >= 0.40.
* All geometry in YOLO normalised coords [0.0 - 1.0] — resolution-independent.
* Strictly headless: no cv2.imshow(), no plt.show().

Public API
----------
    classify_shot(
        video_path : str | Path,
        in_frame   : int,
        out_frame  : int,
        fps        : float,
    ) -> list[str]

    classify_clip_data(clip: dict) -> list[str]   # convenience wrapper

Return values:
    ['[BRolls]']            — no person / background figure.
    ['[<2 People]']         — 1-2 persons detected.
    ['[Multiple Subjects]'] — 3+ persons detected.

Usage (standalone)
------------------
    python shot_classifier.py

    No command-line arguments required.  The script opens an interactive
    prompt that asks for:
      - YOLO model  (Nano / Small / Medium / Large / XLarge)
      - Video path, in-frame, out-frame, fps
      - Optional preview PNG and debug logging
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ffmpeg_helper import get_ffmpeg, get_ffprobe

# ---------------------------------------------------------------------------
# Optional heavy imports — surfaced early so the user gets a clear error
# message before any processing begins.
# ---------------------------------------------------------------------------
try:
    from ultralytics import YOLO
except ImportError as exc:
    raise ImportError(
        "ultralytics is required.  Install it with:  pip install ultralytics"
    ) from exc

try:
    import cv2
except ImportError as exc:
    raise ImportError(
        "opencv-python is required.  Install it with:  pip install opencv-python-headless"
    ) from exc


# ═══════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════

log = logging.getLogger("shot_classifier")


# ═══════════════════════════════════════════════════════════════════════════
# Constants — tweak here, not inside functions
# ═══════════════════════════════════════════════════════════════════════════

# YOLOv8 Nano checkpoint — fastest model; sufficient for person detection.
YOLO_MODEL_WEIGHTS: str = "yolov8n.pt"

# COCO class index for "person".
PERSON_CLASS_ID: int = 0

# Minimum confidence to treat a detection as a real human being.
# Below this threshold the box is assumed to belong to a poster / statue /
# TV screen (the "Ghost" filter).
MIN_CONFIDENCE: float = 0.40
# WHY 0.40 and not 0.75?
# ─────────────────────────────────────────────────────────────────────────
# YOLOv8 Nano returns noticeably lower confidence in several real-world
# situations that are common in event/wedding videography:
#
#   • Group / crowd shots — overlapping bodies push individual box scores
#     into the 0.40–0.65 range even when every person is clearly visible.
#
#   • Non-standard clothing — white wedding dresses, floral ao dai, dark
#     suits against a busy floral backdrop all reduce contrast and lower
#     confidence.
#
#   • Wide shots — small subjects give the model less pixel data to work
#     with, pushing scores down.
#
# 0.40 is empirically safe: false positives (posters, TV screens, statues)
# consistently score below 0.30 with the Nano model.
#
# If you process only tight studio close-ups you can safely raise this to
# 0.60–0.70.  Run with --debug to see every raw confidence score.

# ── Scenery Area Threshold ────────────────────────────────────────────────
# Primary bounding box area (normalised width x height) must be >= this
# value.  Boxes smaller than 10 % of the frame area are distant background
# figures — not intentional subjects.  Used to filter B-roll.
SCENERY_AREA_THRESHOLD: float = 0.03


# ═══════════════════════════════════════════════════════════════════════════
# YOLO model path resolution (handles PyInstaller bundles)
# ═══════════════════════════════════════════════════════════════════════════

def _get_weights_cache_dir() -> Path:
    """Return the user-writable weights directory for the current platform."""
    import os
    system = __import__("platform").system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / "SteadyCut" / "weights"


def _resolve_yolo_model_path(weights: str) -> str:
    """
    Return a usable path for *weights*.

    When running as a PyInstaller bundle, yolov8n.pt is packed inside
    sys._MEIPASS/models/.  On first run it is copied to the user-writable
    weights cache so ultralytics can find it without touching the (read-only)
    bundle.  Falls back to the bare filename so ultralytics auto-downloads
    any model that isn't bundled.
    """
    import sys

    cache_dir = _get_weights_cache_dir()
    cached = cache_dir / weights
    if cached.exists():
        return str(cached)

    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "models" / weights
        if bundled.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(bundled, cached)
            log.info("[YOLO] Copied bundled model to weights cache: %s", cached)
            return str(cached)

    # Not bundled — let ultralytics download it on demand
    return weights


# ═══════════════════════════════════════════════════════════════════════════
# Module-level YOLO model (lazy-loaded, shared across calls)
# ═══════════════════════════════════════════════════════════════════════════

_yolo_model: Optional[YOLO] = None
_model_lock     = threading.Lock()   # guards lazy model load
_inference_lock = threading.Lock()   # serialises YOLO forward passes


def _get_model() -> YOLO:
    """
    Return the shared YOLO model, loading it from disk the first time.
    Double-checked locking makes this safe when called from multiple threads.
    """
    global _yolo_model
    if _yolo_model is None:
        with _model_lock:
            if _yolo_model is None:
                resolved = _resolve_yolo_model_path(YOLO_MODEL_WEIGHTS)
                log.info("[YOLO] Loading model weights: %s", resolved)
                _yolo_model = YOLO(resolved)
                log.info("[YOLO] Model loaded.")
    return _yolo_model


# ═══════════════════════════════════════════════════════════════════════════
# Frame Extraction (timestamp-based via FFmpeg)
# ═══════════════════════════════════════════════════════════════════════════

def _get_video_duration(video_path: Path) -> Optional[float]:
    """
    Return the duration of *video_path* in seconds using ffprobe, or None
    on failure.

    Why probe duration before seeking?
    ────────────────────────────────────
    Camera files (GoPro, Sony, DJI) are frequently split into short clips
    of 4–12 minutes.  If Phase 1 produces frame indices that extend beyond
    a single clip's actual duration — or if the caller supplies a wrong FPS
    value — the computed mid-point timestamp can silently exceed the file
    length.  FFmpeg then seeks past the end, encodes zero frames, and
    writes an empty file with rc=0, so no error is raised.

    Probing first lets us catch and clamp this before FFmpeg runs.
    """
    cmd = [
        get_ffprobe(),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        raw = result.stdout.decode().strip()
        if raw and raw != "N/A":
            return float(raw)
        log.warning("[probe] Could not read duration from %s — skipping clamp.", video_path.name)
        return None
    except Exception as exc:
        log.warning("[probe] ffprobe failed (%s) — skipping duration clamp.", exc)
        return None


def _run_ffmpeg_extract(video_path: Path, timestamp_sec: float,
                        tmp_path: Path, input_seek: bool) -> subprocess.CompletedProcess:
    """
    Build and run a single FFmpeg extract command.

    *input_seek=True*  → -ss before -i  (fast keyframe seek, default)
    *input_seek=False* → -ss after  -i  (slow but frame-accurate fallback)

    The `-update 1` flag intentionally omitted: it was introduced to allow
    FFmpeg 5.1+ image2 muxer to overwrite a fixed output path, but it
    interacts badly with the image2 muxer in FFmpeg 8.x when combined with
    -frames:v 1, causing the output file to be written as zero bytes.
    Because we use a unique NamedTemporaryFile path there is no need for
    it anyway.
    """
    if input_seek:
        # Fast path: seek in the container before opening the decoder.
        # Lands on the nearest keyframe at or before the target timestamp.
        cmd = [
            get_ffmpeg(), "-y",
            "-ss", f"{timestamp_sec:.6f}",   # INPUT seek — fast
            "-i", str(video_path),
            "-frames:v", "1",
            "-an",
            str(tmp_path),
        ]
    else:
        # Slow path: decode every frame up to the target timestamp.
        # Use when input seek produces empty output (e.g. the target lands
        # in the first GOP where there is no prior keyframe to jump to).
        cmd = [
            get_ffmpeg(), "-y",
            "-i", str(video_path),
            "-ss", f"{timestamp_sec:.6f}",   # OUTPUT seek — frame-accurate
            "-frames:v", "1",
            "-an",
            str(tmp_path),
        ]

    log.debug("[extract] %s seek  cmd=%s",
              "input" if input_seek else "output", " ".join(cmd))

    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,                         # 4K H.264 output-seek can be slow
    )


def _extract_frame_at_timestamp(
    video_path: Path,
    timestamp_sec: float,
) -> Optional[np.ndarray]:
    """
    Extract a single frame from *video_path* at *timestamp_sec* using
    FFmpeg.  Returns a BGR numpy array or None on failure.

    Strategy
    ────────
    1. ffprobe the clip duration and clamp the timestamp so we never seek
       past the end of the file (the most common cause of empty output).
    2. Try a fast INPUT seek (-ss before -i).
    3. If that produces an empty file, fall back to a slow but reliable
       OUTPUT seek (-ss after -i) which decodes every frame up to the
       target position.

    Why FFmpeg instead of OpenCV's CAP_PROP_POS_FRAMES?
    ─────────────────────────────────────────────────────
    For VFR files (GoPro, mirrorless cameras) OpenCV uses the container's
    average FPS to map frame numbers to timestamps — a value that is wrong
    whenever frames are not uniformly spaced.  FFmpeg operates on the
    presentation timestamp written into each packet, so it is always
    accurate regardless of VFR.
    """
    # ── 1. Probe duration and clamp timestamp ────────────────────────────
    duration = _get_video_duration(video_path)
    if duration is not None:
        if timestamp_sec >= duration:
            clamped = max(0.0, duration - 0.5)   # 0.5 s before the very end
            log.warning(
                "[extract] Requested ts=%.3fs is at or beyond clip duration "
                "%.3fs — clamping to %.3fs.  "
                "Check that --fps matches the actual video frame rate and that "
                "--out-frame does not exceed the clip's total frame count.",
                timestamp_sec, duration, clamped,
            )
            timestamp_sec = clamped
        else:
            log.info("[extract] Clip duration=%.3fs  target ts=%.3fs  ✓ within range.",
                     duration, timestamp_sec)

    # Write to a unique temp file — no need for -update flag.
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # ── 2. Fast input-seek attempt ───────────────────────────────────
        result = _run_ffmpeg_extract(video_path, timestamp_sec, tmp_path,
                                     input_seek=True)

        empty = (not tmp_path.exists() or tmp_path.stat().st_size == 0)

        if result.returncode != 0 or empty:
            log.warning(
                "[extract] Input-seek produced no output (rc=%d, empty=%s) — "
                "falling back to output seek (slower but reliable).",
                result.returncode, empty,
            )
            # ── 3. Slow output-seek fallback ─────────────────────────────
            result = _run_ffmpeg_extract(video_path, timestamp_sec, tmp_path,
                                         input_seek=False)
            empty = (not tmp_path.exists() or tmp_path.stat().st_size == 0)

            if result.returncode != 0 or empty:
                log.error(
                    "[extract] Both seek strategies failed for ts=%.3fs.\n"
                    "FFmpeg stderr:\n%s",
                    timestamp_sec,
                    result.stderr.decode(errors="replace")[-800:],
                )
                return None

        frame = cv2.imread(str(tmp_path))
        if frame is None:
            log.error("[extract] cv2.imread returned None for %s — "
                      "file may be corrupt.", tmp_path)
        return frame

    except FileNotFoundError:
        log.error("[extract] FFmpeg not found — ensure it is on PATH.")
        return None
    except subprocess.TimeoutExpired:
        log.error("[extract] FFmpeg timed out at ts=%.3fs.", timestamp_sec)
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ═══════════════════════════════════════════════════════════════════════════
# YOLO Detection — Person Detections Only
# ═══════════════════════════════════════════════════════════════════════════

def _parse_yolo_result(result) -> list[dict]:
    """
    Parse a single Ultralytics Results object into filtered person detections.
    Shared by both single-frame and batch inference paths.
    """
    detections: list[dict] = []
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return detections

    xywhn = boxes.xywhn.cpu().numpy()
    cls   = boxes.cls.cpu().numpy()
    conf  = boxes.conf.cpu().numpy()

    for i in range(len(cls)):
        if int(cls[i]) != PERSON_CLASS_ID:
            continue
        raw_conf = float(conf[i])
        if raw_conf < MIN_CONFIDENCE:
            log.info(
                "[yolo] DROPPED  box#%d  conf=%.3f  (< MIN_CONFIDENCE %.2f)"
                " — ghost/poster, or lower MIN_CONFIDENCE if this is real.",
                i, raw_conf, MIN_CONFIDENCE,
            )
            continue
        log.info(
            "[yolo] ACCEPTED box#%d  conf=%.3f  xc=%.3f yc=%.3f w=%.3f h=%.3f",
            i, raw_conf,
            float(xywhn[i, 0]), float(xywhn[i, 1]),
            float(xywhn[i, 2]), float(xywhn[i, 3]),
        )
        detections.append({
            "x_center":   float(xywhn[i, 0]),
            "y_center":   float(xywhn[i, 1]),
            "width":      float(xywhn[i, 2]),
            "height":     float(xywhn[i, 3]),
            "confidence": raw_conf,
        })

    total_person_boxes = sum(1 for c in cls if int(c) == PERSON_CLASS_ID)
    log.info(
        "[yolo] Summary: %d / %d person box(es) passed MIN_CONFIDENCE=%.2f.",
        len(detections), total_person_boxes, MIN_CONFIDENCE,
    )
    return detections


def _run_yolo_person_detection(frame_bgr: np.ndarray) -> list[dict]:
    """Single-frame YOLO inference. Kept for standalone/preview usage."""
    model = _get_model()
    with _inference_lock:
        results = model(frame_bgr, verbose=False)
    return _parse_yolo_result(results[0])




# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def classify_shot(
    video_path: "str | Path",
    in_frame: int,
    out_frame: int,
    fps: float,
) -> list[str]:
    """
    Classify a stable video segment by person count.

    Extracts frames at 25 %, 50 %, 75 % of the stable window, runs YOLO
    person detection on each, then uses the 50 % frame as the
    authoritative source (falling back to 25 % or 75 %).  Classification
    is based purely on how many qualifying person detections remain after
    filtering out distant background figures (area < SCENERY_AREA_THRESHOLD).

    Parameters
    ──────────
    video_path : str | Path
        Path to the source video file.
    in_frame : int
        First frame index of the stable window (from Phase 1).
    out_frame : int
        Last frame index of the stable window (from Phase 1).
    fps : float
        Frames-per-second used to convert frame indices to timestamps.

    Returns
    ───────
    list[str]
        • ['[BRolls]']            — no person detected, or primary box
                                    too small (background figure).
        • ['[<2 People]']         — 1–2 persons detected.
        • ['[Multiple Subjects]'] — 3+ persons detected.
    """
    video_path = Path(video_path)

    if not video_path.exists():
        log.error("[classify_shot] File not found: %s", video_path)
        return []

    if fps <= 0.0:
        log.error("[classify_shot] Invalid fps=%.2f — must be > 0.", fps)
        return []

    if in_frame > out_frame:
        log.error(
            "[classify_shot] in_frame (%d) > out_frame (%d) — invalid window.",
            in_frame, out_frame,
        )
        return []

    # ── Compute the three sample timestamps ──────────────────────────────
    # Integer frame arithmetic keeps us on exact frame boundaries; dividing
    # by fps converts to the wall-clock seconds FFmpeg requires.
    window_len = out_frame - in_frame
    frame_25   = in_frame + int(window_len * 0.25)
    frame_50   = in_frame + int(window_len * 0.50)
    frame_75   = in_frame + int(window_len * 0.75)
    ts_25      = frame_25 / fps
    ts_50      = frame_50 / fps
    ts_75      = frame_75 / fps

    log.info(
        "[classify_shot] %s | window=[%d, %d]  "
        "samples: 25%%=frame%d(%.3fs)  50%%=frame%d(%.3fs)  75%%=frame%d(%.3fs)",
        video_path.name,
        in_frame, out_frame,
        frame_25, ts_25,
        frame_50, ts_50,
        frame_75, ts_75,
    )

    # ── Step 1: Extract the three sample frames in parallel ──────────────
    log.info("[classify_shot] Extracting 3 sample frames in parallel…")
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_25 = pool.submit(_extract_frame_at_timestamp, video_path, ts_25)
        fut_50 = pool.submit(_extract_frame_at_timestamp, video_path, ts_50)
        fut_75 = pool.submit(_extract_frame_at_timestamp, video_path, ts_75)
        frame_25_bgr = fut_25.result()
        frame_50_bgr = fut_50.result()
        frame_75_bgr = fut_75.result()

    failed = [
        pct for pct, f in [("25%", frame_25_bgr), ("50%", frame_50_bgr), ("75%", frame_75_bgr)]
        if f is None
    ]
    if failed:
        log.error(
            "[classify_shot] Frame extraction failed for sample(s): %s — "
            "cannot run cascade.  Check timestamps vs clip duration.",
            ", ".join(failed),
        )
        return []

    # ── Step 2: Run YOLO on all three frames in a single batch call ───────
    log.info("[classify_shot] Running YOLO batch inference on 3 frames…")
    model = _get_model()
    with _inference_lock:
        batch_results = model(
            [frame_25_bgr, frame_50_bgr, frame_75_bgr],  # type: ignore[arg-type]
            verbose=False,
        )
    dets_25 = _parse_yolo_result(batch_results[0])
    dets_50 = _parse_yolo_result(batch_results[1])
    dets_75 = _parse_yolo_result(batch_results[2])

    log.info(
        "[classify_shot] Detection counts — 25%%: %d  50%%: %d  75%%: %d",
        len(dets_25), len(dets_50), len(dets_75),
    )

    # ── Person-count classification ────────────────────────────────────────
    # Use the 50 % frame as authoritative (most representative midpoint).
    # Fall back to 25 % or 75 % only when the 50 % frame had zero hits.
    authoritative = dets_50 or dets_25 or dets_75

    if not authoritative:
        log.info("[classify_shot] No persons detected — tagging as [BRolls].")
        return ["[BRolls]"]

    n = len(authoritative)

    # ── Group-shot fast path (3+ detections) ──────────────────────────────
    # In a wide group shot each person's bounding box is small (often below
    # SCENERY_AREA_THRESHOLD) because many subjects share the frame.  When
    # YOLO returns 3+ high-confidence person boxes, these are clearly real
    # subjects — skip the area filter and classify immediately.
    if n >= 3:
        log.info(
            "[classify_shot] %d persons detected — group shot, "
            "skipping area filter → [Multiple Subjects].", n,
        )
        return ["[Multiple Subjects]"]

    # ── Solo / duo: reject distant background figures ─────────────────────
    # With only 1–2 detections the area check is still meaningful: a single
    # tiny box in an otherwise empty frame is a distant background figure.
    primary = max(authoritative, key=lambda d: d["width"] * d["height"])
    primary_area = primary["width"] * primary["height"]

    if primary_area < SCENERY_AREA_THRESHOLD:
        log.info(
            "[classify_shot] Primary box area %.4f < %.4f — tagging as [BRolls].",
            primary_area, SCENERY_AREA_THRESHOLD,
        )
        return ["[BRolls]"]

    log.info("[classify_shot] %d person(s) detected — tagging as [<2 People].", n)
    return ["[<2 People]"]




def classify_clip_data(clip: dict) -> list[str]:
    """
    Convenience wrapper for the clip-data dicts produced by pipelinev3.py.

    Expected keys (all present in the dicts built by pipelinev3's main()):
        src_path  : str   — original video file path
        in_frame  : int   — stable window start frame
        out_frame : int   — stable window end frame
        fps       : float — frame rate

    Returns the same list[str] as classify_shot().
    """
    return classify_shot(
        video_path = clip["src_path"],
        in_frame   = clip["in_frame"],
        out_frame  = clip["out_frame"],
        fps        = clip["fps"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Integration Helper — Augment Pipeline Output
# ═══════════════════════════════════════════════════════════════════════════

def annotate_clip_list(
    clip_data: list[dict],
    on_clip_done: "Optional[Callable[[int, int], None]]" = None,
) -> list[dict]:
    """
    Run shot classification over every clip and attach results under ``shot_tags``.

    Parameters
    ----------
    clip_data     : list of clip dicts from pipelinev3.py
    on_clip_done  : optional callback(clips_done, total) called after each clip
                    completes — use to report live progress to a UI.
    """
    total = len(clip_data)
    _get_model()   # load on main thread before spawning workers
    clips_done = 0

    def _classify_one(args: tuple[int, dict]) -> tuple[int, list[str]]:
        idx, clip = args
        clip_name = Path(clip.get("src_path", "unknown")).name
        log.info("[annotate] Classifying clip %d / %d: %s", idx, total, clip_name)
        return idx, classify_clip_data(clip)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_classify_one, (idx, clip)): idx
                   for idx, clip in enumerate(clip_data, start=1)}
        from concurrent.futures import as_completed
        for fut in as_completed(futures):
            try:
                idx, tags = fut.result()
                clip_data[idx - 1]["shot_tags"] = tags
            except Exception as exc:
                idx = futures[fut]
                log.warning("[annotate] Classification failed for clip %d: %s", idx, exc)
                clip_data[idx - 1]["shot_tags"] = []
            clips_done += 1
            if on_clip_done is not None:
                on_clip_done(clips_done, total)

    return clip_data


# ═══════════════════════════════════════════════════════════════════════════
# Preview — draw bounding boxes and tags onto the extracted frame
# ═══════════════════════════════════════════════════════════════════════════

def _draw_preview(
    frame_bgr: np.ndarray,
    detections: list[dict],
    tags: list[str],
    output_path: Path,
) -> None:
    """
    Draw YOLO bounding boxes and shot-classification tags onto *frame_bgr*
    and save the result as a PNG.
    """
    canvas = frame_bgr.copy()
    h_px, w_px = canvas.shape[:2]

    # Colours
    BOX_COLOR     = (0, 255, 0)    # green
    PRIMARY_COLOR = (0, 200, 255)  # orange-yellow for the primary box
    TEXT_BG       = (0, 0, 0)

    # Find the primary (largest) detection
    primary_idx = -1
    if detections:
        areas = [d["width"] * d["height"] for d in detections]
        primary_idx = areas.index(max(areas))

    for i, det in enumerate(detections):
        xc, yc, bw, bh = det["x_center"], det["y_center"], det["width"], det["height"]
        conf = det["confidence"]

        # Convert normalised coords → pixel coords
        x1 = int((xc - bw / 2) * w_px)
        y1 = int((yc - bh / 2) * h_px)
        x2 = int((xc + bw / 2) * w_px)
        y2 = int((yc + bh / 2) * h_px)

        color = PRIMARY_COLOR if i == primary_idx else BOX_COLOR
        thickness = 3 if i == primary_idx else 2

        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)

        # Confidence label above the box
        label = f"Person {conf:.0%}"
        if i == primary_idx:
            label += " (primary)"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(canvas, (x1, y1 - th - 8), (x1 + tw + 4, y1), TEXT_BG, -1)
        cv2.putText(canvas, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)

    # Draw shot tags at the top-left corner
    tag_str = "  ".join(tags) if tags else "(no person detected)"
    (tw, th), _ = cv2.getTextSize(tag_str, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
    cv2.rectangle(canvas, (10, 10), (20 + tw, 20 + th + 10), TEXT_BG, -1)
    cv2.putText(canvas, tag_str, (15, 15 + th),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), canvas)
    log.info("[preview] Saved → %s", output_path)


# ═══════════════════════════════════════════════════════════════════════════
# Interactive Prompt Helpers
# ═══════════════════════════════════════════════════════════════════════════

# Available YOLO model weights with descriptions.
# Heavier models are slower but catch occluded / small / overlapping subjects
# much more reliably — especially the "kid half-hidden behind adults" case.
_YOLO_MODELS: list[tuple[str, str]] = [
    (
        "yolov8n.pt",
        "Nano  — fastest (~real-time), good for clear shots. "
        "May miss occluded or very small people in crowds.",
    ),
    (
        "yolov8s.pt",
        "Small — ~2× slower than Nano, noticeably better at overlapping / "
        "partially-hidden subjects (recommended for group/crowd footage).",
    ),
    (
        "yolov8m.pt",
        "Medium — ~4× slower than Nano, strong occlusion handling. "
        "Good balance of accuracy vs. speed for complex scenes.",
    ),
    (
        "yolov8l.pt",
        "Large  — ~8× slower than Nano, highest accuracy. "
        "Use when missing detections are unacceptable and speed is secondary.",
    ),
    (
        "yolov8x.pt",
        "XLarge — ~12× slower than Nano, maximum accuracy. "
        "Best for very dense crowds or heavily occluded subjects.",
    ),
]


def _prompt(label: str, default: str = "") -> str:
    """Print a prompt and return stripped input; re-prompt if empty and no default."""
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"  {label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print("  ⚠  This field is required — please enter a value.")


def _prompt_model() -> str:
    """
    Show the model menu and return the chosen weights filename.
    The user types a number (1–5); Enter alone selects the default (1 = Nano).
    """
    print()
    print("  ┌─ YOLO Model Selection ──────────────────────────────────────────────┐")
    for i, (weights, desc) in enumerate(_YOLO_MODELS, start=1):
        default_marker = "  ← default" if i == 1 else ""
        print(f"  │  [{i}] {weights:<15}  {desc}{default_marker}")
    print("  └────────────────────────────────────────────────────────────────────┘")

    while True:
        raw = input("  Choose model [1]: ").strip()
        if raw == "":
            return _YOLO_MODELS[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(_YOLO_MODELS):
            return _YOLO_MODELS[int(raw) - 1][0]
        print(f"  ⚠  Enter a number between 1 and {len(_YOLO_MODELS)}.")


def _prompt_bool(label: str, default: bool = False) -> bool:
    """Prompt for a yes/no answer.  Returns bool."""
    default_str = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {label} [{default_str}]: ").strip().lower()
        if raw in ("", "y", "yes"):
            return True if raw in ("y", "yes") else default
        if raw in ("n", "no"):
            return False
        print("  ⚠  Please enter y or n.")


def _gather_inputs() -> dict:
    """
    Interactively collect all parameters needed to run a classification.
    Returns a plain dict with keys matching the old argparse namespace.
    """
    print()
    print("════════════════════════════════════════════════")
    print("  shot_classifier.py — Interactive Setup")
    print("════════════════════════════════════════════════")
    print()

    # ── YOLO model ────────────────────────────────────────────────────────
    model = _prompt_model()

    print()

    # ── Video / clip parameters ───────────────────────────────────────────
    video_raw  = _prompt("Video file path")
    in_frame   = int(_prompt("In-frame  (stable window start)", "0"))
    out_frame  = int(_prompt("Out-frame (stable window end)"))
    fps        = float(_prompt("Frame rate (fps)", "25.0"))

    # ── Optional flags ────────────────────────────────────────────────────
    print()
    preview    = _prompt_bool("Save preview PNG with bounding boxes?", default=False)
    preview_out: Optional[Path] = None
    if preview:
        raw_out = input("  Preview output path [<video_stem>_preview.png]: ").strip()
        preview_out = Path(raw_out) if raw_out else None

    debug      = _prompt_bool("Enable DEBUG logging?", default=False)

    print()
    print("════════════════════════════════════════════════")
    print()

    return {
        "model":       model,
        "video":       Path(video_raw),
        "in_frame":    in_frame,
        "out_frame":   out_frame,
        "fps":         fps,
        "preview":     preview,
        "preview_out": preview_out,
        "debug":       debug,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI Entry-Point (for standalone testing)
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Gather inputs interactively (no CLI args required) ────────────────
    cfg = _gather_inputs()

    if cfg["debug"]:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Apply chosen model globally before the lazy loader fires ──────────
    global YOLO_MODEL_WEIGHTS
    YOLO_MODEL_WEIGHTS = cfg["model"]
    log.info("[config] Using YOLO model: %s", YOLO_MODEL_WEIGHTS)

    video_path = cfg["video"]

    # ── Run classification ─────────────────────────────────────────────────
    tags = classify_shot(
        video_path = video_path,
        in_frame   = cfg["in_frame"],
        out_frame  = cfg["out_frame"],
        fps        = cfg["fps"],
    )

    # ── Preview mode: re-extract the 50 % frame and draw boxes ───────────
    if cfg["preview"]:
        # Mirror the same 50 % timestamp calculation used inside classify_shot
        # so the preview always shows the exact frame that was classified.
        window_len    = cfg["out_frame"] - cfg["in_frame"]
        frame_50      = cfg["in_frame"] + int(window_len * 0.50)
        mid_timestamp = frame_50 / cfg["fps"]

        frame_bgr = _extract_frame_at_timestamp(video_path, mid_timestamp)
        if frame_bgr is not None:
            detections = _run_yolo_person_detection(frame_bgr)
            preview_path = cfg["preview_out"] or Path(f"{video_path.stem}_preview.png")
            _draw_preview(frame_bgr, detections, tags, preview_path)
        else:
            print("  ⚠ Preview skipped — frame extraction failed.")

    # ── Print results ──────────────────────────────────────────────────────
    print("\n─────────────────────────────────────")
    if tags:
        print(f"  Shot tags  : {tags}")
    else:
        print("  Shot tags  : [] (no qualifying person detected)")
    print("─────────────────────────────────────\n")


if __name__ == "__main__":
    main()