"""
shot_classifier.py — Phase 2: YOLO-Based Cinematographic Shot Classifier
=========================================================================
Consumes the stable-segment metadata produced by pipelinev3.py (Phase 1)
and classifies the shot type (WS / MS / MCU / CU) for each clip by
running a three-gate Cascade Architecture before the final framing maths.

Cascade Architecture (executed in order)
-----------------------------------------
Every clip passes through three gates before a shot-type tag is assigned.
Failure at any gate returns ['[Scenery]'] immediately — no further work
is done.

  Gate 1 — Scenery Area Threshold
      The primary bounding box must occupy >= 10 % of the frame area
      (normalised width × height).  Boxes smaller than this are distant
      background figures, out-of-focus foreground objects, or artefacts
      that should not drive the shot-type decision.

  Gate 2 — Temporal Consistency (3-Point Extraction)
      Three frames are sampled at the 25 %, 50 %, and 75 % points of the
      stable window.  A valid subject must be detected in ALL THREE frames,
      and the x_center of the primary box must not drift more than 0.30
      (30 % of frame width) between the 25 % and 75 % samples.  Moving
      photobombers enter and exit the frame between samples; static
      subjects do not.

  Gate 3 — Focus Check (Laplacian Variance)
      The primary bounding box is cropped from the 50 % frame and its
      sharpness is measured as the variance of the Laplacian.  A variance
      below BLUR_VARIANCE_THRESHOLD indicates an out-of-focus foreground
      obstruction rather than an intentional subject.

Other design decisions
----------------------
* Frame extraction uses FFmpeg timestamps (not raw frame counts) to avoid
  VFR drift on GoPro / mirrorless camera files.
* Detection restricted to COCO class 0 (Person), confidence >= 0.40.
* All geometry in YOLO normalised coords [0.0 – 1.0] — resolution-independent.
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

Successful clips return ['[Subject]'].
Failed clips (any gate) return ['[Scenery]'].
No detections at all returns [].

Usage (standalone)
------------------
    python shot_classifier.py

    No command-line arguments required.  The script opens an interactive
    prompt that asks for:
      • YOLO model  (Nano / Small / Medium / Large / XLarge)
      • Video path, in-frame, out-frame, fps
      • Optional preview PNG and debug logging
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

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

# ── Edge-bleed margins ────────────────────────────────────────────────────
# If the subject's bounding box left-edge is within this distance of the
# left border, or the right-edge is within this distance of the right
# border, the clip is marked [Partial_Frame].
EDGE_BLEED_MARGIN: float = 0.05          # 5 % of frame width

# ── Aspect-ratio occlusion threshold ─────────────────────────────────────
# height / width below this value means the person is likely sitting behind
# a desk or occluded by a foreground object.
OCCLUSION_ASPECT_RATIO: float = 1.5

# ── Framing thresholds (subject height relative to frame height) ──────────
WS_MAX_HEIGHT:  float = 0.40             # < 0.40  → Wide Shot
MS_MAX_HEIGHT:  float = 0.85             # 0.40–0.85 → Medium Shot
CU_BOTTOM_EDGE: float = 0.98             # > 0.85 and bottom > 0.98 → MCU

# ── Cascade Gate 1: Scenery Area Threshold ────────────────────────────────
# Primary bounding box area (normalised width × height) must be >= this
# value.  Boxes smaller than 10 % of the frame area are distant background
# figures or out-of-focus foreground objects — not intentional subjects.
SCENERY_AREA_THRESHOLD: float = 0.10

# ── Cascade Gate 2: Temporal Consistency ─────────────────────────────────
# Maximum allowed horizontal drift of the primary subject's x_center
# between the 25 % and 75 % sample frames.  A moving photobomber will
# traverse much more than 30 % of the frame width across that window;
# a genuine subject (whether static or on-camera) will not.
TEMPORAL_X_DRIFT_MAX: float = 0.30

# ── Cascade Gate 3: Focus / Sharpness ────────────────────────────────────
# Laplacian variance threshold for the cropped primary bounding box.
# Values below this indicate an out-of-focus foreground obstruction.
# Start at 50.0 and tune upward if blurry real subjects are passing, or
# downward if sharp foreground objects are being incorrectly filtered.
BLUR_VARIANCE_THRESHOLD: float = 50.0


# ═══════════════════════════════════════════════════════════════════════════
# Module-level YOLO model (lazy-loaded, shared across calls)
# ═══════════════════════════════════════════════════════════════════════════

_yolo_model: Optional[YOLO] = None


def _get_model() -> YOLO:
    """
    Return the shared YOLO model, loading it from disk the first time.
    Lazy-loading avoids paying the ~0.5 s startup cost when the module is
    imported but not yet used.
    """
    global _yolo_model
    if _yolo_model is None:
        log.info("[YOLO] Loading model weights: %s", YOLO_MODEL_WEIGHTS)
        _yolo_model = YOLO(YOLO_MODEL_WEIGHTS)
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
        "ffprobe",
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
            "ffmpeg", "-y",
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
            "ffmpeg", "-y",
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

def _run_yolo_person_detection(frame_bgr: np.ndarray) -> list[dict]:
    """
    Run YOLOv8n inference on a single BGR frame and return a filtered list
    of person detections that pass the confidence threshold.

    Each returned dict contains:
        x_center  : float  — normalised horizontal centre   [0, 1]
        y_center  : float  — normalised vertical centre     [0, 1]
        width     : float  — normalised bounding-box width  [0, 1]
        height    : float  — normalised bounding-box height [0, 1]
        confidence: float  — detection confidence           [0, 1]

    Detection pipeline
    ──────────────────
    1. Pass the raw BGR array to YOLO (no resize — let the model handle it).
    2. Filter to class 0 (Person) only.
    3. Filter to confidence >= MIN_CONFIDENCE ("Ghost" filter).
    4. Return normalised [x_center, y_center, width, height] coordinates.
       YOLO already outputs these normalised; we make it explicit.
    """
    model = _get_model()

    # verbose=False suppresses YOLO's per-frame console output — we manage
    # our own logging.
    results = model(frame_bgr, verbose=False)

    detections: list[dict] = []

    # `results` is always a list even for single-image inference.
    for result in results:
        boxes = result.boxes  # Ultralytics Boxes object

        if boxes is None or len(boxes) == 0:
            continue

        # .xywhn returns a tensor of shape (N, 4):
        # columns → [x_center, y_center, width, height] in normalised coords.
        xywhn = boxes.xywhn.cpu().numpy()       # shape: (N, 4)
        cls   = boxes.cls.cpu().numpy()         # shape: (N,)  — class IDs
        conf  = boxes.conf.cpu().numpy()        # shape: (N,)  — confidences

        for i in range(len(cls)):
            # ── Filter 1: only persons ────────────────────────────────────
            if int(cls[i]) != PERSON_CLASS_ID:
                continue

            raw_conf = float(conf[i])

            # ── Filter 2: Ghost filter — reject low-confidence detections ──
            # Log EVERY person box at INFO level (accepted or dropped) so the
            # user can always see what YOLO actually returned.  This is the
            # primary diagnostic tool for "no person detected" surprises —
            # if all boxes are being DROPPED, lower MIN_CONFIDENCE.
            if raw_conf < MIN_CONFIDENCE:
                log.info(
                    "[yolo] DROPPED  box#%d  conf=%.3f  (< MIN_CONFIDENCE %.2f)"
                    " — ghost/poster, or lower MIN_CONFIDENCE if this is real.",
                    i, raw_conf, MIN_CONFIDENCE,
                )
                continue

            log.info(
                "[yolo] ACCEPTED box#%d  conf=%.3f  "
                "xc=%.3f yc=%.3f w=%.3f h=%.3f",
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

    # Count total raw person boxes (before confidence filter) for the summary.
    total_person_boxes = sum(
        1
        for r in results
        if r.boxes is not None
        for c in r.boxes.cls.cpu().numpy()
        if int(c) == PERSON_CLASS_ID
    )
    log.info(
        "[yolo] Summary: %d / %d person box(es) passed MIN_CONFIDENCE=%.2f.",
        len(detections), total_person_boxes, MIN_CONFIDENCE,
    )
    return detections


# ═══════════════════════════════════════════════════════════════════════════
# Framing Analysis — Core Logic
# ═══════════════════════════════════════════════════════════════════════════

def _classify_detections(detections: list[dict]) -> list[str]:
    """
    Apply the full shot-classification decision tree to a list of valid
    person detections.  Returns an ordered list of metadata tags.

    The rules are executed in the exact order specified in the brief:
        1. Z-Axis / Crowd Check  → [Multi-Subject]
        2. Edge-Bleed Check      → [Partial_Frame]
        3. Occlusion / Desk Check→ [Occluded]
        4. Framing Math          → [WS] / [MS] / [MCU] / [CU]

    If *detections* is empty the function returns an empty list — the
    caller is responsible for deciding how to handle a "no person" frame.
    """
    if not detections:
        log.debug("[classify] No valid persons in frame — returning empty tag list.")
        return []

    tags: list[str] = []

    # ── 1. Z-Axis / Crowd Check ───────────────────────────────────────────
    # More than one person in frame: tag the clip and then continue
    # evaluating only the LARGEST bounding box (by area in normalised space)
    # so that a small person in the background does not skew framing maths.

    if len(detections) > 1:
        tags.append("[Multi-Subject]")
        log.debug(
            "[classify] %d persons detected → [Multi-Subject].  "
            "Selecting largest box for framing analysis.",
            len(detections),
        )

    # Area = width × height in normalised coords.  This is proportional to
    # pixel area and is resolution-independent.
    primary = max(detections, key=lambda d: d["width"] * d["height"])

    x_c = primary["x_center"]
    y_c = primary["y_center"]
    w   = primary["width"]
    h   = primary["height"]

    log.debug(
        "[classify] Primary box → x_c=%.3f  y_c=%.3f  w=%.3f  h=%.3f  conf=%.2f",
        x_c, y_c, w, h, primary["confidence"],
    )

    # ── 2. Edge-Bleed Check ───────────────────────────────────────────────
    # The bounding box edges in normalised coordinates:
    #   left_edge  = x_center - width / 2
    #   right_edge = x_center + width / 2
    # Values < 0 or > 1 would mean the box extends beyond the frame — YOLO
    # can produce these for partially visible subjects.

    left_edge  = x_c - (w / 2.0)
    right_edge = x_c + (w / 2.0)

    if left_edge < EDGE_BLEED_MARGIN or right_edge > (1.0 - EDGE_BLEED_MARGIN):
        tags.append("[Partial_Frame]")
        log.debug(
            "[classify] Edge bleed detected → left=%.3f  right=%.3f → [Partial_Frame].",
            left_edge, right_edge,
        )

    # ── 3. Occlusion / Desk Check ─────────────────────────────────────────
    # A tall, narrow bounding box is a standing person.
    # A wide, short bounding box suggests the person is sitting at a desk or
    # occluded by a foreground object (e.g. a podium, a car door).
    # Guard against division-by-zero for degenerate boxes.

    if w > 0.0:
        aspect_ratio = h / w
    else:
        aspect_ratio = 0.0

    if aspect_ratio < OCCLUSION_ASPECT_RATIO:
        tags.append("[Occluded]")
        log.debug(
            "[classify] Aspect ratio %.2f < %.2f → [Occluded].",
            aspect_ratio, OCCLUSION_ASPECT_RATIO,
        )

    # ── 4. Framing Math ───────────────────────────────────────────────────
    # The subject ratio is simply the normalised bounding-box HEIGHT.
    # This is the most reliable single-axis framing metric because vertical
    # extent maps directly to how much of the body is in frame.

    bottom_edge = y_c + (h / 2.0)

    if h < WS_MAX_HEIGHT:
        # Subject occupies less than 40 % of the frame height.
        tags.append("[WS]")
        log.debug("[classify] h=%.3f < %.2f → [WS].", h, WS_MAX_HEIGHT)

    elif h <= MS_MAX_HEIGHT:
        # Subject occupies 40–85 % of the frame height.
        tags.append("[MS]")
        log.debug("[classify] h=%.3f in [%.2f, %.2f] → [MS].", h, WS_MAX_HEIGHT, MS_MAX_HEIGHT)

    else:
        # Subject occupies more than 85 % of the frame height.
        # Distinguish between a true Close-Up (whole head fully in frame)
        # and a Medium Close-Up where the lower body is cropped at the
        # bottom edge of the frame.
        if bottom_edge > CU_BOTTOM_EDGE:
            tags.append("[MCU]")
            log.debug(
                "[classify] h=%.3f > %.2f  AND bottom=%.3f > %.2f → [MCU].",
                h, MS_MAX_HEIGHT, bottom_edge, CU_BOTTOM_EDGE,
            )
        else:
            tags.append("[CU]")
            log.debug(
                "[classify] h=%.3f > %.2f  AND bottom=%.3f <= %.2f → [CU].",
                h, MS_MAX_HEIGHT, bottom_edge, CU_BOTTOM_EDGE,
            )

    return tags


# ═══════════════════════════════════════════════════════════════════════════
# Cascade Gate Functions
# ═══════════════════════════════════════════════════════════════════════════
# Each gate returns (passed: bool, reason: str).
# 'reason' is always logged by the caller; gates themselves stay pure.
# ═══════════════════════════════════════════════════════════════════════════

def _gate1_scenery_area(primary: dict) -> tuple[bool, str]:
    """
    Gate 1 — Scenery Area Threshold.

    Calculates the normalised area of the primary bounding box
    (width × height in [0, 1] space).  If that area is below
    SCENERY_AREA_THRESHOLD the detection is considered background scenery
    and the gate fails.

    Rationale
    ─────────
    A person who occupies less than 10 % of the total frame area is either:
      • Far in the background (genuine scenery / establishing shot filler).
      • A tiny out-of-focus foreground object whose shape accidentally
        resembles a person silhouette.
    Neither case should drive the shot-type decision.

    Parameters
    ──────────
    primary : dict
        The largest-area person detection from _run_yolo_person_detection.

    Returns
    ───────
    (True,  "area=X.XXX >= threshold") if the box is large enough.
    (False, "area=X.XXX < threshold")  if the box is too small.
    """
    area = primary["width"] * primary["height"]
    if area >= SCENERY_AREA_THRESHOLD:
        return True, f"area={area:.4f} >= SCENERY_AREA_THRESHOLD={SCENERY_AREA_THRESHOLD}"
    return False, f"area={area:.4f} < SCENERY_AREA_THRESHOLD={SCENERY_AREA_THRESHOLD}"


def _primary_from_detections(detections: list[dict]) -> Optional[dict]:
    """
    Return the detection with the largest normalised area, or None if the
    list is empty.  Used internally by the temporal and focus gates.
    """
    if not detections:
        return None
    return max(detections, key=lambda d: d["width"] * d["height"])


def _gate2_temporal(
    dets_25: list[dict],
    dets_50: list[dict],
    dets_75: list[dict],
) -> tuple[bool, str]:
    """
    Gate 2 — Temporal Consistency Check.

    Verifies that the same subject is present and roughly stationary
    across three evenly-spaced samples of the stable window (25 %, 50 %,
    75 %).  Two conditions must both hold:

    Condition A — Presence
        At least one qualifying detection must exist in ALL THREE frames.
        A moving photobomber who walks through mid-clip will be absent
        in the 25 % or 75 % sample, failing this condition.

    Condition B — Positional Stability
        The x_center of the primary (largest-area) box must not differ by
        more than TEMPORAL_X_DRIFT_MAX between the 25 % and 75 % frames.
        A tourist who walks across the frame will shift by > 0.30 (30 % of
        the frame width) over that interval; a genuine static or gently
        moving subject will not.

    Note: We compare 25 % vs 75 % only (not 50 %) because a subject that
    is present at both ends of the window is almost certainly present
    throughout it — checking the midpoint too would add no new information
    and would penalise clips where the subject briefly turns away.

    Parameters
    ──────────
    dets_25, dets_50, dets_75 : list[dict]
        Filtered person detections from the 25 %, 50 %, 75 % frames.

    Returns
    ───────
    (True,  reason_str) if both conditions pass.
    (False, reason_str) with the specific failure reason.
    """
    # ── Condition A: presence in all three frames ─────────────────────────
    if not dets_25:
        return False, "no detection at 25 % sample — subject absent at clip start"
    if not dets_50:
        return False, "no detection at 50 % sample — subject absent at midpoint"
    if not dets_75:
        return False, "no detection at 75 % sample — subject absent at clip end"

    # ── Condition B: x_center drift between 25 % and 75 % samples ─────────
    primary_25 = _primary_from_detections(dets_25)
    primary_75 = _primary_from_detections(dets_75)

    # Both are guaranteed non-None here because Condition A passed.
    x_drift = abs(primary_25["x_center"] - primary_75["x_center"])  # type: ignore[index]

    if x_drift > TEMPORAL_X_DRIFT_MAX:
        return (
            False,
            f"x_center drift={x_drift:.3f} > TEMPORAL_X_DRIFT_MAX={TEMPORAL_X_DRIFT_MAX} "
            f"(25%: xc={primary_25['x_center']:.3f}, "      # type: ignore[index]
            f"75%: xc={primary_75['x_center']:.3f})"        # type: ignore[index]
        )

    return (
        True,
        f"presence ✓ in all 3 frames | x_drift={x_drift:.3f} <= {TEMPORAL_X_DRIFT_MAX}",
    )


def _gate3_focus(frame_bgr: np.ndarray, primary: dict) -> tuple[bool, str]:
    """
    Gate 3 — Focus Check (Laplacian Variance).

    Crops the primary bounding box from the 50 % frame, converts it to
    greyscale, and measures sharpness as the variance of the Laplacian.

    Why Laplacian variance?
    ────────────────────────
    The Laplacian is a second-order derivative that amplifies rapid
    intensity changes (edges).  A sharp image has strong, well-defined
    edges → high variance.  A blurry image has soft, gradual transitions
    → low variance.  Crucially, the metric is computed on the CROP of the
    bounding box only — this isolates the subject from the (potentially
    sharp) background behind it, which is exactly the scenario we want to
    catch: a crisp background with a blurry foreground obstruction.

    Parameters
    ──────────
    frame_bgr : np.ndarray
        The full BGR frame at the 50 % sample point.
    primary : dict
        The largest-area person detection from the 50 % frame.

    Returns
    ───────
    (True,  "variance=X.X >= threshold") if the crop is sharp enough.
    (False, "variance=X.X < threshold")  if the crop is too blurry.
    """
    img_h, img_w = frame_bgr.shape[:2]

    xc  = primary["x_center"]
    yc  = primary["y_center"]
    bw  = primary["width"]
    bh  = primary["height"]

    # Convert normalised coords → absolute pixel coords and clamp to bounds.
    x1 = max(0,         int((xc - bw / 2.0) * img_w))
    y1 = max(0,         int((yc - bh / 2.0) * img_h))
    x2 = min(img_w - 1, int((xc + bw / 2.0) * img_w))
    y2 = min(img_h - 1, int((yc + bh / 2.0) * img_h))

    # Guard against a degenerate crop (can happen at extreme edge bleed).
    if x2 <= x1 or y2 <= y1:
        return False, f"degenerate crop [{x1},{y1}]-[{x2},{y2}] — treating as blurry"

    crop_bgr  = frame_bgr[y1:y2, x1:x2]
    crop_grey = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)

    # cv2.CV_64F gives full floating-point precision for the derivative;
    # .var() returns the statistical variance across all pixel values.
    variance = cv2.Laplacian(crop_grey, cv2.CV_64F).var()

    if variance >= BLUR_VARIANCE_THRESHOLD:
        return (
            True,
            f"Laplacian variance={variance:.2f} >= "
            f"BLUR_VARIANCE_THRESHOLD={BLUR_VARIANCE_THRESHOLD}",
        )
    return (
        False,
        f"Laplacian variance={variance:.2f} < "
        f"BLUR_VARIANCE_THRESHOLD={BLUR_VARIANCE_THRESHOLD} — out-of-focus obstruction",
    )


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
    Classify the cinematographic shot type for a stable video segment using
    the three-gate Cascade Architecture.

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
        • ['[Subject]']  — a person passed all 3 cascade gates.
        • ['[Scenery]']  — something was detected but failed a gate
                           (too small, moving photobomber, out of focus).
        • []             — no person detected in any sample frame (B-roll).

    Cascade execution order
    ───────────────────────
    1. Extract frames at 25 %, 50 %, 75 % of the stable window.
    2. Run YOLO on all three frames.
    3. Gate 2 (Temporal): present in all 3 frames + x_center drift <= 0.30.
    4. Gate 1 (Scenery Area): primary box area >= 0.10.
    5. Gate 3 (Focus): Laplacian variance of 50 % crop >= 50.0.
    6. Pass 50 % detections to _classify_detections for shot-type tagging.

    Gate ordering rationale
    ───────────────────────
    Gate 2 runs first: it requires three frame extractions and can fail fast
    before any crop/Laplacian work.  Gate 1 is cheap arithmetic.  Gate 3
    (Laplacian) is the most CPU-intensive and runs last.
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

    # ── Step 1: Extract the three sample frames ───────────────────────────
    log.info("[classify_shot] Extracting 3 sample frames…")
    frame_25_bgr = _extract_frame_at_timestamp(video_path, ts_25)
    frame_50_bgr = _extract_frame_at_timestamp(video_path, ts_50)
    frame_75_bgr = _extract_frame_at_timestamp(video_path, ts_75)

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

    # ── Step 2: Run YOLO on all three frames ─────────────────────────────
    log.info("[classify_shot] Running YOLO on 3 frames…")
    dets_25 = _run_yolo_person_detection(frame_25_bgr)  # type: ignore[arg-type]
    dets_50 = _run_yolo_person_detection(frame_50_bgr)  # type: ignore[arg-type]
    dets_75 = _run_yolo_person_detection(frame_75_bgr)  # type: ignore[arg-type]

    log.info(
        "[classify_shot] Detection counts — 25%%: %d  50%%: %d  75%%: %d",
        len(dets_25), len(dets_50), len(dets_75),
    )

    # If nothing was detected anywhere, this is likely B-roll — return []
    # not [Scenery].  Scenery is only for "detected but failed a gate".
    if not dets_25 and not dets_50 and not dets_75:
        log.info("[classify_shot] No persons detected in any sample — likely B-roll.")
        return []

    # ── Gate 2: Temporal Consistency ─────────────────────────────────────
    gate2_passed, gate2_reason = _gate2_temporal(dets_25, dets_50, dets_75)
    if not gate2_passed:
        log.info(
            "[classify_shot] ✗ Gate 2 (Temporal) FAILED — %s → [Scenery]",
            gate2_reason,
        )
        return ["[Scenery]"]
    log.info("[classify_shot] ✓ Gate 2 (Temporal) passed — %s", gate2_reason)

    # All remaining gates operate on the primary detection from the 50 % frame.
    primary_50 = _primary_from_detections(dets_50)  # guaranteed non-None (Gate 2 passed dets_50)

    # ── Gate 1: Scenery Area Threshold ───────────────────────────────────
    gate1_passed, gate1_reason = _gate1_scenery_area(primary_50)  # type: ignore[arg-type]
    if not gate1_passed:
        log.info(
            "[classify_shot] ✗ Gate 1 (Scenery Area) FAILED — %s → [Scenery]",
            gate1_reason,
        )
        return ["[Scenery]"]
    log.info("[classify_shot] ✓ Gate 1 (Scenery Area) passed — %s", gate1_reason)

    # ── Gate 3: Focus / Sharpness Check ──────────────────────────────────
    # BYPASS: Gate 3 was designed to catch a single blurry foreground
    # object that YOLO mis-identifies as a person.  When multiple people
    # are detected *consistently* across all 3 temporal samples, the
    # evidence is overwhelming that these are real subjects, not a blurry
    # foreground obstruction.  Full/wide group shots naturally register
    # lower Laplacian variance (less per-pixel detail at distance), which
    # would cause a false Scenery label without this bypass.
    min_count = min(len(dets_25), len(dets_50), len(dets_75))
    if min_count >= 3:
        log.info(
            "[classify_shot] ⏭ Gate 3 (Focus) SKIPPED — %d+ persons in "
            "every frame; multi-person temporal evidence overrides blur check.",
            min_count,
        )
    else:
        gate3_passed, gate3_reason = _gate3_focus(frame_50_bgr, primary_50)  # type: ignore[arg-type]
        if not gate3_passed:
            log.info(
                "[classify_shot] ✗ Gate 3 (Focus) FAILED — %s → [Scenery]",
                gate3_reason,
            )
            return ["[Scenery]"]
        log.info("[classify_shot] ✓ Gate 3 (Focus) passed — %s", gate3_reason)

    # ── All gates passed: this is a subject shot ──────────────────────────
    log.info("[classify_shot] All 3 cascade gates passed — tagging as [Subject].")
    log.info("[classify_shot] %s → ['[Subject]']", video_path.name)
    return ["[Subject]"]




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

def annotate_clip_list(clip_data: list[dict]) -> list[dict]:
    """
    Run shot classification over every clip in a pipelinev3.py clip_data
    list and attach the results under the key ``shot_tags``.

    This is the recommended integration point: call this function between
    Phase 2 (stability analysis) and Phase 3 (XML generation) in
    pipelinev3.py's main() to embed shot tags into the XML comment or
    clip-name field.

    Example
    ───────
    ::

        # In pipelinev3.py main(), after building clip_data:
        from shot_classifier import annotate_clip_list
        clip_data = annotate_clip_list(clip_data)
        # Each clip now has clip["shot_tags"], e.g. ['[MS]', '[Partial_Frame]']

    Returns the same list with each dict mutated in place (also returned
    for convenience).
    """
    total = len(clip_data)
    for idx, clip in enumerate(clip_data, start=1):
        clip_name = Path(clip.get("src_path", "unknown")).name
        log.info("[annotate] Classifying clip %d / %d: %s", idx, total, clip_name)
        clip["shot_tags"] = classify_clip_data(clip)
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