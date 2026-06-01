"""
pipeline.py — Unified Video Stabilisation Pipeline
====================================================
Combines all three phases into a single interactive script:

    Phase 1 — FFmpeg proxy generation  (720p H.264)
    Phase 2 — OpenCV stability analysis (Lucas-Kanade optical flow)
    Phase 3 — FCP7 XML generation       (Premiere Pro import)

Usage:
    python3 pipeline.py                              # interactive wizard
    python3 pipeline.py --input DIR --proxies DIR    # CLI flags (skip prompts)

The generated XML links back to the ORIGINAL high-quality clips,
not the lightweight proxies used for analysis.
"""

import json
import math
import sys
import logging
import argparse
import subprocess
import functools
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import cv2
import numpy as np

from ffmpeg_helper import get_ffmpeg, get_ffprobe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mxf", ".avi", ".mkv", ".m4v", ".r3d", ".braw"}

DEFAULT_THRESHOLD_PX     = 2.0
DEFAULT_MAX_THRESHOLD_PX = 100.0
DEFAULT_STABLE_SECS      = 1.0
DEFAULT_FPS              = 25.0

FEATURE_MAX_CORNERS  = 200
FEATURE_QUALITY      = 0.01
FEATURE_MIN_DIST     = 10.0
LK_WIN_SIZE          = (21, 21)
LK_MAX_LEVEL         = 3


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1 — Proxy Generation
# ═══════════════════════════════════════════════════════════════════════════════

def generate_proxy(input_path: Path, proxy_path: Path) -> bool:
    """Transcode a single raw video to a 720p H.264 proxy. Returns True on success."""
    proxy_path.parent.mkdir(parents=True, exist_ok=True)

    if proxy_path.exists():
        log.info("  Already exists, skipping: %s", proxy_path.name)
        return True

    # Scale so the longer dimension is at most 1280, preserving aspect ratio.
    # Works for landscape (16:9, 4:3), portrait (9:16, 3:4), and square.
    # -2 ensures both output dimensions are divisible by 2 (required by libx264).
    cmd = [
        get_ffmpeg(), "-y",
        "-i", str(input_path),
        "-vf", "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))'",
        "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(proxy_path),
    ]

    log.info("  Transcoding → %s", proxy_path.name)
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        if result.returncode != 0:
            log.error("  FFmpeg error for %s:\n%s", input_path.name, result.stderr.decode(errors="replace"))
            return False
        return True
    except FileNotFoundError:
        log.error("  FFmpeg not found. Install it and ensure it is on PATH.")
        return False
    except subprocess.TimeoutExpired:
        log.error("  FFmpeg timed out on %s.", input_path.name)
        return False


def generate_proxies(
    input_dir: Path,
    proxy_dir: Path,
    on_proxy_done: "Optional[Callable[[int, int], None]]" = None,
) -> dict[Path, Path]:
    """Generate proxies for all supported videos. Returns {original: proxy} mapping.

    on_proxy_done(done, total) is called after each proxy job finishes (including skips).
    """
    proxy_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[Path, Path] = {}

    raw_files = sorted(
        f for f in input_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXTENSIONS
        and not f.name.startswith("._")
    )

    if not raw_files:
        log.warning("No supported video files found in %s", input_dir)
        return mapping

    log.info("Found %d clip(s) in %s — generating proxies in parallel (max 4 jobs)", len(raw_files), input_dir)

    jobs = [(raw, proxy_dir / (raw.stem + "_Proxy.mp4")) for raw in raw_files]
    total = len(jobs)
    done = 0

    def _proxy_job(raw: Path, proxy: Path) -> tuple[Path, Path | None]:
        return raw, (proxy if generate_proxy(raw, proxy) else None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_proxy_job, raw, proxy): raw for raw, proxy in jobs}
        for fut in as_completed(futures):
            try:
                raw, proxy = fut.result()
            except Exception as exc:
                raw = futures[fut]
                log.warning("Proxy job raised exception for %s: %s", raw.name, exc)
                proxy = None
            if proxy is not None:
                mapping[raw] = proxy
            else:
                log.warning("Skipping clip due to proxy failure: %s", raw.name)
            done += 1
            if on_proxy_done is not None:
                on_proxy_done(done, total)

    log.info("%d / %d proxies ready.", len(mapping), len(raw_files))
    return mapping


def _is_ntsc(fps: float) -> bool:
    """
    Return True for drop-frame NTSC rates (23.976, 29.97, 59.94).
    These require <ntsc>TRUE</ntsc> and drop-frame timecode in FCP7 XML.
    """
    return abs(fps - round(fps)) > 0.005


def _tc_display_format(fps: float) -> str:
    """Return 'DF' for 29.97/59.94 drop-frame, 'NDF' for everything else."""
    return "DF" if abs(fps - 29.97) < 0.01 or abs(fps - 59.94) < 0.01 else "NDF"


def _parse_timecode(tc_str: str, fps: float) -> int:
    """
    Convert a camera timecode string to an absolute frame number.

    Accepts HH:MM:SS:FF (non-drop) and HH:MM:SS;FF (drop-frame).
    Returns 0 for any format that cannot be parsed.
    """
    try:
        parts = tc_str.replace(";", ":").split(":")
        if len(parts) != 4:
            return 0
        h, m, s, f = map(int, parts)
        return round((h * 3600 + m * 60 + s) * fps) + f
    except Exception:
        return 0


@functools.lru_cache(maxsize=None)
def probe_video_info(file_path: Path) -> dict:
    """
    Use ffprobe to read a clip's video AND audio stream properties.

    Returns:
        width, height    — display frame dimensions (rotation-corrected)
        fps              — exact frame rate (float)
        codec            — video codec name
        sample_rate      — audio sample rate in Hz (default 48000)
        channels         — audio channel count (default 2)
        total_frames     — total frame count of the source file
        tc_string        — embedded starting timecode string (e.g. "11:09:04:12")
        tc_frame         — tc_string converted to absolute frame number
    """
    defaults = {
        "width": 1920, "height": 1080, "fps": 25.0, "codec": "unknown",
        "sample_rate": 48000, "channels": 2,
        "total_frames": 0, "tc_string": "00:00:00:00", "tc_frame": 0,
    }
    try:
        # Probe streams + rotation/timecode tags + format duration in one call.
        cmd = [
            get_ffprobe(), "-v", "error",
            "-show_entries",
            (
                "stream=index,codec_type,width,height,r_frame_rate,codec_name,"
                "sample_rate,channels:stream_tags=rotate,timecode:"
                "format=duration"
            ),
            "-of", "json",
            str(file_path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode != 0:
            log.warning("  ffprobe failed for %s — using defaults", file_path.name)
            return defaults

        data    = json.loads(result.stdout)
        streams = data.get("streams", [])

        # Split into video / audio streams
        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]

        # ── Video ─────────────────────────────────────────────────────────────
        vs = v_streams[0] if v_streams else {}
        width  = int(vs.get("width",  1920))
        height = int(vs.get("height", 1080))
        codec  = vs.get("codec_name", "unknown")

        r_fps = vs.get("r_frame_rate", "25/1")
        if "/" in r_fps:
            num, den = r_fps.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 25.0
        else:
            fps = float(r_fps)

        # ── Rotation correction ───────────────────────────────────────────────
        # Phones store portrait video as landscape pixels + a rotate=90/270 tag.
        # Swap width/height so everything downstream sees display dimensions.
        tags   = vs.get("tags", {})
        rotate = int(tags.get("rotate", 0))
        if rotate in (90, 270):
            width, height = height, width
            log.info("  Rotation metadata: %d° — swapped to display dimensions %dx%d",
                     rotate, width, height)

        # ── Embedded timecode ─────────────────────────────────────────────────
        # Camera clips store time-of-day in stream tags (e.g. "11:09:04:12").
        # FCP7 XML <in>/<out> are absolute timecode frame numbers, so every
        # in_frame / out_frame from Phase 2 must be offset by tc_frame.
        tc_string = tags.get("timecode", "00:00:00:00")
        tc_frame  = _parse_timecode(tc_string, fps)
        if tc_frame:
            log.info("  Embedded timecode: %s  (frame offset %d)", tc_string, tc_frame)

        # ── Total frame count (from container duration × fps) ─────────────────
        duration_secs = float(data.get("format", {}).get("duration", 0))
        total_frames  = round(duration_secs * fps) if duration_secs else 0

        # ── Audio ─────────────────────────────────────────────────────────────
        as_ = a_streams[0] if a_streams else {}
        sample_rate = int(as_.get("sample_rate", 48000))
        channels    = int(as_.get("channels",    2))

        log.info("  Probed %s: %dx%d  %.4f fps  codec=%s  audio=%dch@%dHz  frames=%d",
                 file_path.name, width, height, fps, codec, channels, sample_rate,
                 total_frames)

        return {
            "width": width, "height": height, "fps": fps, "codec": codec,
            "sample_rate": sample_rate, "channels": channels,
            "total_frames": total_frames,
            "tc_string": tc_string, "tc_frame": tc_frame,
        }

    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        log.warning("  ffprobe error for %s: %s — using defaults", file_path.name, e)
        return defaults


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Stability Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def _seed_features(gray_frame: np.ndarray) -> np.ndarray | None:
    """Detect Shi-Tomasi corners in a greyscale frame."""
    return cv2.goodFeaturesToTrack(
        gray_frame,
        maxCorners=FEATURE_MAX_CORNERS,
        qualityLevel=FEATURE_QUALITY,
        minDistance=FEATURE_MIN_DIST,
        blockSize=7,
    )


def _optical_flow_translation(
    prev_gray: np.ndarray, curr_gray: np.ndarray, prev_pts: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    """Track points via Lucas-Kanade and return (dx, dy, surviving_points)."""
    curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, prev_pts, None,
        winSize=LK_WIN_SIZE, maxLevel=LK_MAX_LEVEL,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )

    if status is None:
        return 0.0, 0.0, prev_pts

    good_prev = prev_pts[status == 1]
    good_curr = curr_pts[status == 1]

    if len(good_prev) < 4:
        return 0.0, 0.0, good_curr.reshape(-1, 1, 2) if len(good_curr) else prev_pts

    matrix, inliers = cv2.estimateAffinePartial2D(
        good_prev.reshape(-1, 1, 2), good_curr.reshape(-1, 1, 2),
        method=cv2.RANSAC, ransacReprojThreshold=3.0,
    )

    if matrix is None:
        return 0.0, 0.0, good_curr.reshape(-1, 1, 2)

    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])

    if inliers is not None:
        surviving = good_curr[inliers.flatten().astype(bool)].reshape(-1, 1, 2)
    else:
        surviving = good_curr.reshape(-1, 1, 2)

    return dx, dy, surviving


def _frame_to_tc(f: int, rate: float) -> str:
    total_secs = f / rate
    hh = int(total_secs // 3600)
    mm = int((total_secs % 3600) // 60)
    ss = int(total_secs % 60)
    ff = int(round((total_secs - int(total_secs)) * rate))
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def compute_motion(
    proxy_path: Path,
    fallback_fps: float = DEFAULT_FPS,
) -> tuple[list[float], float, int] | None:
    """
    Open *proxy_path*, read every frame, and compute per-frame camera
    motion via Lucas-Kanade optical flow.

    Returns ``(motion, fps, total_frames)`` or ``None`` on failure.
    The returned motion list can be passed to ``find_stable_window()``
    repeatedly at different thresholds without re-reading the file.
    """
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        log.error("Cannot open: %s", proxy_path)
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or fallback_fps
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    log.info("  %s  |  %.2f fps  |  %d frames", proxy_path.name, fps, total_frames)

    ret, frame0 = cap.read()
    if not ret:
        log.error("  Could not read first frame of %s", proxy_path.name)
        cap.release()
        return None

    prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
    prev_pts = _seed_features(prev_gray)

    if prev_pts is None or len(prev_pts) < 4:
        log.warning("  Too few features in first frame — skipping %s", proxy_path.name)
        cap.release()
        return None

    motion = [0.0]
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_pts is None or len(prev_pts) < 10:
            log.debug("  Re-seeding features at frame %d", len(motion))
            prev_pts = _seed_features(prev_gray)
            if prev_pts is None:
                motion.append(0.0)
                prev_gray = curr_gray
                continue
        dx, dy, prev_pts = _optical_flow_translation(prev_gray, curr_gray, prev_pts)
        motion.append(math.sqrt(dx * dx + dy * dy))
        prev_gray = curr_gray

    cap.release()
    return motion, fps, total_frames


def find_stable_window(
    motion: list[float],
    fps: float,
    total_frames: int,
    threshold_px: float = DEFAULT_THRESHOLD_PX,
    stable_secs: float = DEFAULT_STABLE_SECS,
) -> dict | None:
    """
    Filter a pre-computed *motion* array for the longest stable window.

    Pure in-memory operation — no disk I/O.  Call this repeatedly on the
    same cached motion array to test different thresholds for free.

    Returns the same dict shape as ``analyze_stability()``, or ``None``.
    """
    stable_needed = max(1, int(round(stable_secs * fps)))

    if len(motion) < stable_needed + 1:
        log.warning("  Clip too short to analyse (%d frames).", len(motion))
        return None

    min_window_frames = int(3.0 * fps)
    stable_windows: list[dict] = []
    current_window_start = None

    for i, m in enumerate(motion):
        if m < threshold_px and current_window_start is None:
            current_window_start = i
        elif m >= threshold_px and current_window_start is not None:
            end_frame = i - 1
            stable_windows.append({
                "start": current_window_start, "end": end_frame,
                "duration": end_frame - current_window_start,
            })
            current_window_start = None

    if current_window_start is not None:
        end_frame = len(motion) - 1
        stable_windows.append({
            "start": current_window_start, "end": end_frame,
            "duration": end_frame - current_window_start,
        })

    log.info("  Found %d raw stable window(s) before filtering.", len(stable_windows))
    stable_windows = [w for w in stable_windows if w["duration"] >= min_window_frames]

    if not stable_windows:
        log.warning("  No stable window >= 3 s found at threshold=%.1f px.", threshold_px)
        return None

    log.info("  %d window(s) remain after filtering (>= 3 s).", len(stable_windows))

    best = max(stable_windows, key=lambda w: w["duration"])
    in_frame, out_frame = best["start"], best["end"]

    log.info("  Selected longest window: start=%d  end=%d  (%.1f s)",
             in_frame, out_frame, best["duration"] / fps)

    # Trim the trailing edge: scan backwards from out_frame to find the last
    # frame where motion was well-settled (< 35% of threshold). Removes the
    # early ramp-up of a gimbal pullaway that stays below threshold but is
    # already in motion — the main cause of out_frames being too late.
    settle_thresh = threshold_px * 0.35
    scan_limit    = max(in_frame, out_frame - max(1, int(fps)))  # max 1 s lookback
    trimmed_out   = out_frame
    for j in range(out_frame, scan_limit, -1):
        if motion[j] < settle_thresh:
            trimmed_out = j
            break
    if trimmed_out != out_frame and (trimmed_out - in_frame) >= min_window_frames:
        log.info("  Trailing edge trimmed: frame %d → %d (%.2f s removed)",
                 out_frame, trimmed_out, (out_frame - trimmed_out) / fps)
        out_frame = trimmed_out

    result = {
        "in_frame": in_frame, "out_frame": out_frame,
        "in_tc": _frame_to_tc(in_frame, fps), "out_tc": _frame_to_tc(out_frame, fps),
        "fps": fps, "total_frames": total_frames,
    }

    log.info("  In=%s (frame %d)  Out=%s (frame %d)  |  stable: %ds",
             result["in_tc"], in_frame, result["out_tc"], out_frame,
             (out_frame - in_frame) / fps)

    return result


def analyze_stability(
    proxy_path: Path,
    threshold_px: float = DEFAULT_THRESHOLD_PX,
    stable_secs: float = DEFAULT_STABLE_SECS,
    fallback_fps: float = DEFAULT_FPS,
) -> dict | None:
    """
    Analyse a proxy video for camera stability. Returns dict with
    in_frame, out_frame, in_tc, out_tc, fps, total_frames — or None.

    Thin wrapper around compute_motion() + find_stable_window().
    Use those two functions directly when you need to re-test multiple
    thresholds on the same clip without re-reading from disk.
    """
    cached = compute_motion(proxy_path, fallback_fps)
    if cached is None:
        return None
    motion, fps, total_frames = cached
    return find_stable_window(motion, fps, total_frames, threshold_px, stable_secs)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2b — Cut on Action Detection
# ═══════════════════════════════════════════════════════════════════════════════

def detect_cut_frame(
    proxy_path: Path,
    in_frame: int,
    out_frame: int,
    fps: float,
    sensitivity: float = 0.02,
    min_rise_secs: float = 0.3,
) -> "int | None":
    """
    Within the stable window [in_frame, out_frame], find the frame where
    subject motion peaks — the 'cut on action' point.

    Uses frame differencing: when the camera is stable, pixel changes between
    consecutive frames are caused by subject motion only (background is static).
    Detects a motion arc (rise → peak → fall) and returns the peak frame.

    Returns the absolute frame index (relative to clip start), or None if no
    qualifying motion arc is found above the sensitivity threshold.
    """
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        log.warning("  detect_cut_frame: cannot open %s", proxy_path.name)
        return None

    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(in_frame))
        frames: list[np.ndarray] = []
        for _ in range(out_frame - in_frame):
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
    finally:
        cap.release()

    if len(frames) < 3:
        log.debug("  detect_cut_frame: too few frames in stable window (%d)", len(frames))
        return None

    # Per-frame diff score: mean absolute pixel change normalised to [0, 1].
    # Stable background → near-zero diff. Moving subject → non-zero region.
    diff_scores = np.array([
        np.mean(cv2.absdiff(frames[i], frames[i + 1])) / 255.0
        for i in range(len(frames) - 1)
    ], dtype=np.float32)

    # Rolling mean smoothing (~5 frames at 25 fps) to suppress noise spikes.
    win = max(1, int(fps) // 5)
    kernel = np.ones(win, dtype=np.float32) / win
    smoothed = np.convolve(diff_scores, kernel, mode="same")

    # Find the highest local maximum where:
    #   1. Score exceeds sensitivity threshold
    #   2. Preceded by a sustained rise of >= min_rise_secs
    #   3. Is a local maximum (higher than both neighbours)
    min_rise_frames = max(1, int(min_rise_secs * fps))
    best_idx: "int | None" = None
    best_score: float = sensitivity  # candidate must beat this

    for i in range(min_rise_frames, len(smoothed) - 1):
        if smoothed[i] <= best_score:
            continue
        # Preceding window must average above sensitivity / 2 (sustained rise)
        preceding = smoothed[max(0, i - min_rise_frames):i]
        if len(preceding) == 0 or float(np.mean(preceding)) <= sensitivity / 2.0:
            continue
        # Must be a local maximum
        if smoothed[i] > smoothed[i - 1] and smoothed[i] >= smoothed[i + 1]:
            best_idx = i
            best_score = float(smoothed[i])

    if best_idx is None:
        log.debug(
            "  detect_cut_frame: no motion arc found (sensitivity=%.3f) in %s",
            sensitivity, proxy_path.name,
        )
        return None

    abs_frame = in_frame + best_idx
    log.info(
        "  detect_cut_frame: peak at frame %d (score=%.4f) in %s",
        abs_frame, best_score, proxy_path.name,
    )
    return abs_frame


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3 — FCP7 XML Generation
# ═══════════════════════════════════════════════════════════════════════════════

def build_fcp7_xml(
    clip_data: list[dict],
    sequence_name: str = "Auto-Trimmed Sequence",
    fps: float = DEFAULT_FPS,
) -> str:
    """
    Build an FCP7 XML sequence that mirrors Premiere's "New Sequence From Clip":
      - Sequence rate, frame size, and pixel aspect ratio are derived from the
        first (representative) clip rather than hardcoded defaults.
      - NTSC / drop-frame flags are set correctly for 29.97 / 23.976 fps clips.
      - Both a video track AND stereo audio track(s) are written so that audio
        is preserved on import without needing to re-link.
    """
    # ── Derive sequence settings from the first clip ──────────────────────────
    first       = clip_data[0] if clip_data else {}
    seq_fps     = first.get("fps", fps)
    timebase    = int(round(seq_fps))
    ntsc_flag   = "TRUE" if _is_ntsc(seq_fps) else "FALSE"
    df_format   = _tc_display_format(seq_fps)
    seq_width   = str(first.get("width",       1920))
    seq_height  = str(first.get("height",      1080))
    sample_rate = str(first.get("sample_rate", 48000))
    channels    = first.get("channels", 2)

    # Pixel aspect ratio: square for 16:9 HD/UHD; flag anamorphic 1440x1080
    w, h = int(seq_width), int(seq_height)
    if w == 1440 and h == 1080:
        pixel_ar    = "HD (1440x1080)"
        anamorphic  = "TRUE"
    else:
        pixel_ar    = "square"
        anamorphic  = "FALSE"

    total_duration = sum(c["out_frame"] - c["in_frame"] for c in clip_data)

    # ── Root structure ────────────────────────────────────────────────────────
    root     = ET.Element("xmeml", version="5")
    project  = ET.SubElement(root, "project")
    ET.SubElement(project, "name").text = sequence_name

    children = ET.SubElement(project, "children")
    sequence = ET.SubElement(children, "sequence")
    ET.SubElement(sequence, "name").text     = sequence_name
    ET.SubElement(sequence, "duration").text = str(total_duration)

    # Sequence frame rate
    seq_rate = ET.SubElement(sequence, "rate")
    ET.SubElement(seq_rate, "timebase").text = str(timebase)
    ET.SubElement(seq_rate, "ntsc").text     = ntsc_flag

    # Timecode
    tc_el   = ET.SubElement(sequence, "timecode")
    tc_rate = ET.SubElement(tc_el, "rate")
    ET.SubElement(tc_rate, "timebase").text    = str(timebase)
    ET.SubElement(tc_rate, "ntsc").text        = ntsc_flag
    ET.SubElement(tc_el, "string").text        = "00:00:00:00"
    ET.SubElement(tc_el, "frame").text         = "0"
    ET.SubElement(tc_el, "displayformat").text = df_format

    # Sequence format — this is what Premiere reads to set Sequence Settings
    fmt_el   = ET.SubElement(sequence, "format")
    fmt_char = ET.SubElement(fmt_el, "samplecharacteristics")
    fmt_rate = ET.SubElement(fmt_char, "rate")
    ET.SubElement(fmt_rate, "timebase").text      = str(timebase)
    ET.SubElement(fmt_rate, "ntsc").text          = ntsc_flag
    ET.SubElement(fmt_char, "width").text         = seq_width
    ET.SubElement(fmt_char, "height").text        = seq_height
    ET.SubElement(fmt_char, "anamorphic").text    = anamorphic
    ET.SubElement(fmt_char, "pixelaspectratio").text = pixel_ar
    ET.SubElement(fmt_char, "fielddominance").text   = "none"
    ET.SubElement(fmt_char, "colordepth").text       = "32"

    # ── Media ─────────────────────────────────────────────────────────────────
    media = ET.SubElement(sequence, "media")

    # ── Video — track-level format block ─────────────────────────────────────
    # Premiere reads sequence settings from TWO places and needs both to agree:
    #   1. <sequence><format>         — sets project-level metadata
    #   2. <media><video><format>     — drives the Editing Mode preset picker
    # Without this second block Premiere ignores the first and falls back to
    # its default DV NTSC preset regardless of what <sequence><format> says.
    video = ET.SubElement(media, "video")

    vid_fmt      = ET.SubElement(video, "format")
    vid_fmt_char = ET.SubElement(vid_fmt, "samplecharacteristics")
    vid_fmt_rate = ET.SubElement(vid_fmt_char, "rate")
    ET.SubElement(vid_fmt_rate, "timebase").text         = str(timebase)
    ET.SubElement(vid_fmt_rate, "ntsc").text             = ntsc_flag
    ET.SubElement(vid_fmt_char, "width").text            = seq_width
    ET.SubElement(vid_fmt_char, "height").text           = seq_height
    ET.SubElement(vid_fmt_char, "anamorphic").text       = anamorphic
    ET.SubElement(vid_fmt_char, "pixelaspectratio").text = pixel_ar
    ET.SubElement(vid_fmt_char, "fielddominance").text   = "none"
    ET.SubElement(vid_fmt_char, "colordepth").text       = "32"

    video_track = ET.SubElement(video, "track")

    timeline_offset = 0

    for clip_idx, clip in enumerate(clip_data, start=1):
        clip_duration = clip["out_frame"] - clip["in_frame"]
        clip_fps      = clip.get("fps", fps)
        clip_tb       = int(round(clip_fps))
        clip_ntsc     = "TRUE" if _is_ntsc(clip_fps) else "FALSE"
        clip_w        = str(clip.get("width",  1920))
        clip_h        = str(clip.get("height", 1080))

        vid_id = f"clipitem-{clip['name']}-v"
        aud_id = f"clipitem-{clip['name']}-a"

        # --- Video clipitem --------------------------------------------------
        vi = ET.SubElement(video_track, "clipitem", id=vid_id)
        ET.SubElement(vi, "name").text     = clip["name"]
        ET.SubElement(vi, "enabled").text  = "TRUE"
        ET.SubElement(vi, "duration").text = str(clip_duration)

        vi_rate = ET.SubElement(vi, "rate")
        ET.SubElement(vi_rate, "timebase").text = str(clip_tb)
        ET.SubElement(vi_rate, "ntsc").text     = clip_ntsc

        ET.SubElement(vi, "start").text = str(timeline_offset)
        ET.SubElement(vi, "end").text   = str(timeline_offset + clip_duration)
        ET.SubElement(vi, "in").text    = str(clip["in_frame"])
        ET.SubElement(vi, "out").text   = str(clip["out_frame"])

        # <file> — full definition here; audio clipitem references by id only
        file_el = ET.SubElement(vi, "file", id=f"file-{clip['name']}")
        ET.SubElement(file_el, "name").text     = clip["name"]
        ET.SubElement(file_el, "pathurl").text  = Path(clip["src_path"]).as_uri()
        ET.SubElement(file_el, "duration").text = str(clip.get("total_frames", clip_duration))

        f_rate = ET.SubElement(file_el, "rate")
        ET.SubElement(f_rate, "timebase").text = str(clip_tb)
        ET.SubElement(f_rate, "ntsc").text     = clip_ntsc

        f_media = ET.SubElement(file_el, "media")
        f_video = ET.SubElement(f_media, "video")
        f_char  = ET.SubElement(f_video, "samplecharacteristics")
        fsc_rate = ET.SubElement(f_char, "rate")
        ET.SubElement(fsc_rate, "timebase").text = str(clip_tb)
        ET.SubElement(fsc_rate, "ntsc").text     = clip_ntsc
        ET.SubElement(f_char, "width").text      = clip_w
        ET.SubElement(f_char, "height").text     = clip_h

        f_audio = ET.SubElement(f_media, "audio")
        f_achar = ET.SubElement(f_audio, "samplecharacteristics")
        ET.SubElement(f_achar, "depth").text      = "16"
        ET.SubElement(f_achar, "samplerate").text = str(clip.get("sample_rate", 48000))
        ET.SubElement(f_audio, "channelcount").text = str(clip.get("channels", 2))

        # Link video → its own audio (makes clicking the clip select both tracks)
        vlink_v = ET.SubElement(vi, "link")
        ET.SubElement(vlink_v, "linkclipref").text  = vid_id
        ET.SubElement(vlink_v, "mediatype").text    = "video"
        ET.SubElement(vlink_v, "trackindex").text   = "1"
        ET.SubElement(vlink_v, "clipindex").text    = str(clip_idx)

        vlink_a = ET.SubElement(vi, "link")
        ET.SubElement(vlink_a, "linkclipref").text  = aud_id
        ET.SubElement(vlink_a, "mediatype").text    = "audio"
        ET.SubElement(vlink_a, "trackindex").text   = "1"
        ET.SubElement(vlink_a, "clipindex").text    = str(clip_idx)

        timeline_offset += clip_duration

    # ── Single stereo audio track ─────────────────────────────────────────────
    # One track with stereo clipitems — eliminates the duplicate A1/A2 layout
    # and keeps audio linked to its video counterpart via <link> elements.
    audio_el = ET.SubElement(media, "audio")

    aud_char = ET.SubElement(audio_el, "samplecharacteristics")
    ET.SubElement(aud_char, "depth").text      = "16"
    ET.SubElement(aud_char, "samplerate").text = sample_rate
    ET.SubElement(audio_el, "channelcount").text = str(channels)

    aud_track = ET.SubElement(audio_el, "track")

    aud_offset = 0
    for clip_idx, clip in enumerate(clip_data, start=1):
        clip_duration = clip["out_frame"] - clip["in_frame"]
        clip_fps      = clip.get("fps", fps)
        clip_tb       = int(round(clip_fps))
        clip_ntsc     = "TRUE" if _is_ntsc(clip_fps) else "FALSE"

        vid_id = f"clipitem-{clip['name']}-v"
        aud_id = f"clipitem-{clip['name']}-a"

        ai = ET.SubElement(aud_track, "clipitem", id=aud_id)
        ET.SubElement(ai, "name").text     = clip["name"]
        ET.SubElement(ai, "enabled").text  = "TRUE"
        ET.SubElement(ai, "duration").text = str(clip_duration)

        ai_rate = ET.SubElement(ai, "rate")
        ET.SubElement(ai_rate, "timebase").text = str(clip_tb)
        ET.SubElement(ai_rate, "ntsc").text     = clip_ntsc

        ET.SubElement(ai, "start").text = str(aud_offset)
        ET.SubElement(ai, "end").text   = str(aud_offset + clip_duration)
        ET.SubElement(ai, "in").text    = str(clip["in_frame"])
        ET.SubElement(ai, "out").text   = str(clip["out_frame"])

        # Reference the file already defined in the video clipitem
        ET.SubElement(ai, "file", id=f"file-{clip['name']}")

        # Stereo — carry both channels from source track 1
        src_track = ET.SubElement(ai, "sourcetrack")
        ET.SubElement(src_track, "mediatype").text  = "audio"
        ET.SubElement(src_track, "trackindex").text = "1"

        # Link audio → its video counterpart
        alink_v = ET.SubElement(ai, "link")
        ET.SubElement(alink_v, "linkclipref").text  = vid_id
        ET.SubElement(alink_v, "mediatype").text    = "video"
        ET.SubElement(alink_v, "trackindex").text   = "1"
        ET.SubElement(alink_v, "clipindex").text    = str(clip_idx)

        alink_a = ET.SubElement(ai, "link")
        ET.SubElement(alink_a, "linkclipref").text  = aud_id
        ET.SubElement(alink_a, "mediatype").text    = "audio"
        ET.SubElement(alink_a, "trackindex").text   = "1"
        ET.SubElement(alink_a, "clipindex").text    = str(clip_idx)

        aud_offset += clip_duration

    # ── Pretty-print ─────────────────────────────────────────────────────────
    raw_xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    dom     = minidom.parseString(raw_xml)
    pretty  = dom.toprettyxml(indent="  ", encoding=None)

    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'

    return "\n".join(lines)


def save_xml(xml_string: str, output_path: Path) -> None:
    """Write the XML string to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_string, encoding="utf-8")
    log.info("Saved sequence XML → %s", output_path)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _prompt_path(label: str, default: Path, must_exist: bool = False) -> Path:
    """Prompt for a path, looping until valid if must_exist is True."""
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        chosen = Path(raw) if raw else default
        if must_exist and not chosen.exists():
            print(f"  ✗ Path not found: {chosen}  — please try again.")
            continue
        return chosen


def _prompt_float(label: str, default: float, lo: float, hi: float) -> float:
    """Prompt for a float within [lo, hi]. Enter accepts default."""
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            val = float(raw)
            if lo <= val <= hi:
                return val
            print(f"  Value must be between {lo} and {hi} — try again.")
        except ValueError:
            print("  Please enter a number.")


def _resolve_raw_path(
    proxy_path: Path, raw_files_by_stem: dict[str, Path]
) -> tuple[str, str]:
    """
    Map a proxy file back to its original raw file.
    Returns (clean_name, original_src_path).
    """
    stem = proxy_path.stem

    # Strip proxy suffixes: _proxy, _Proxy, _PROXY
    clean = stem
    if clean.lower().endswith("_proxy"):
        clean = clean[:-6]

    if raw_files_by_stem:
        if clean in raw_files_by_stem:
            return clean, str(raw_files_by_stem[clean].resolve())
        if stem in raw_files_by_stem:
            return clean, str(raw_files_by_stem[stem].resolve())
        log.warning("  Could not find raw file for '%s'", stem)

    return clean, str(proxy_path.resolve())


# ═══════════════════════════════════════════════════════════════════════════════
# Main Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Headless video stabilisation pipeline → FCP7 XML",
    )
    parser.add_argument("--input",       type=Path,  default=None,
                        help="Raw video input directory")
    parser.add_argument("--proxies",     type=Path,  default=None,
                        help="Proxy output directory")
    parser.add_argument("--output",      type=Path,  default=None,
                        help="Output XML path")
    parser.add_argument("--threshold",   type=float, default=None, metavar="PX",
                        help="Motion threshold in pixels")
    parser.add_argument("--stable-secs", type=float, default=None, dest="stable_secs",
                        help="Stable seconds to confirm In-Point")
    parser.add_argument("--fps",         type=float, default=None,
                        help="Fallback FPS when unreadable from file")
    parser.add_argument("--export-json", type=Path,  default=None, dest="export_json",
                        metavar="FILE",
                        help="Also export analysis results as JSON")
    parser.add_argument("--skip-proxies", action="store_true", dest="skip_proxies",
                        help="Skip Phase 1 (proxies already exist)")
    parser.add_argument("--debug",       action="store_true",
                        help="Enable DEBUG-level logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Interactive wizard if no CLI flags supplied ───────────────────────────
    cli_supplied = any([args.input, args.proxies, args.output])

    if not cli_supplied:
        print()
        print("╔══════════════════════════════════════════════╗")
        print("║   Headless Video Stabilisation Pipeline      ║")
        print("╚══════════════════════════════════════════════╝")
        print("  Press Enter to accept the [default] for each prompt.\n")

    input_dir = args.input or _prompt_path(
        "Raw footage folder ", Path("input"), must_exist=True)
    proxy_dir = args.proxies or _prompt_path(
        "Proxy output folder", Path("proxies"), must_exist=False)
    output_xml = args.output or _prompt_path(
        "Output XML path    ", Path("sequence.xml"), must_exist=False)

    threshold = args.threshold or DEFAULT_THRESHOLD_PX
    stable_secs = args.stable_secs or DEFAULT_STABLE_SECS
    fallback_fps = args.fps or DEFAULT_FPS

    if not cli_supplied:
        print()
        print("  ── Tuning (Enter to keep defaults) ──")
        threshold = _prompt_float(
            "Motion threshold px  (1.0 – 5.0)", DEFAULT_THRESHOLD_PX, 0.1, 50.0)
        stable_secs = _prompt_float(
            "Stable seconds needed (0.5 – 3.0)", DEFAULT_STABLE_SECS, 0.1, 30.0)
        fallback_fps = _prompt_float(
            "Fallback FPS          (e.g. 25)  ", DEFAULT_FPS, 1.0, 240.0)

    print()
    print("  Running with:")
    print(f"    Input folder  : {input_dir}")
    print(f"    Proxy folder  : {proxy_dir}")
    print(f"    Output XML    : {output_xml}")
    print(f"    Threshold     : {threshold} px")
    print(f"    Stable window : {stable_secs} s")
    print(f"    Fallback FPS  : {fallback_fps}")
    print()

    # =====================================================================
    # PHASE 1 — Proxy Generation
    # =====================================================================
    if args.skip_proxies:
        log.info("Skipping Phase 1 (--skip-proxies)")
        # Build mapping from existing proxies
        proxy_map: dict[Path, Path] = {}
        for p in sorted(proxy_dir.glob("*.mp4")):
            if p.name.startswith("._"):
                continue
            # Reverse-map: strip _proxy suffix to find the original
            raw_stem = p.stem
            if raw_stem.lower().endswith("_proxy"):
                raw_stem = raw_stem[:-6]
            matches = [f for f in input_dir.iterdir()
                       if f.is_file() and f.stem == raw_stem and not f.name.startswith("._")]
            if matches:
                proxy_map[matches[0]] = p
            else:
                proxy_map[p] = p  # fallback
    else:
        log.info("=" * 60)
        log.info("PHASE 1 — Proxy Generation")
        log.info("=" * 60)
        proxy_map = generate_proxies(input_dir, proxy_dir)

    if not proxy_map:
        log.error("No proxies available — aborting.")
        sys.exit(1)

    # =====================================================================
    # PHASE 2 — Stability Analysis
    # =====================================================================
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 2 — Stability Analysis")
    log.info("=" * 60)

    # Pre-index raw files for src_path resolution
    raw_files_by_stem: dict[str, Path] = {}
    for f in input_dir.iterdir():
        if f.is_file() and not f.name.startswith("._"):
            raw_files_by_stem[f.stem] = f

    clip_data: list[dict] = []
    skipped: list[str] = []

    for raw_path, proxy_path in proxy_map.items():
        log.info("[analysis] %s", proxy_path.name)
        result = analyze_stability(
            proxy_path,
            threshold_px=threshold,
            stable_secs=stable_secs,
            fallback_fps=fallback_fps,
        )

        if result is None:
            log.warning("  → Skipped (no stable window): %s", raw_path.name)
            skipped.append(raw_path.name)
            continue

        # Resolve back to original high-quality clip
        clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)

        # Probe the ORIGINAL clip for real resolution
        src_info = probe_video_info(Path(src_path))

        clip_data.append({
            "name":         clean_name,
            "src_path":     src_path,
            "in_frame":     result["in_frame"],
            "out_frame":    result["out_frame"],
            "fps":          result["fps"],
            "total_frames": result["total_frames"],
            "in_tc":        result["in_tc"],
            "out_tc":       result["out_tc"],
            "width":        src_info["width"],
            "height":       src_info["height"],
            "sample_rate":  src_info["sample_rate"],
            "channels":     src_info["channels"],
        })

    if skipped:
        log.warning("\nSkipped %d clip(s): %s", len(skipped), ", ".join(skipped))

    if not clip_data:
        log.error("No usable clips after analysis — aborting XML generation.")
        sys.exit(1)

    # ── Optional JSON export ─────────────────────────────────────────────
    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(
            json.dumps(clip_data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Analysis results exported → %s", args.export_json)

    # =====================================================================
    # PHASE 3 — FCP7 XML Generation
    # =====================================================================
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 3 — FCP7 XML Generation")
    log.info("=" * 60)

    fps_counts: dict[float, int] = {}
    for c in clip_data:
        fps_counts[c["fps"]] = fps_counts.get(c["fps"], 0) + 1
    sequence_fps = max(fps_counts, key=fps_counts.get)

    xml_string = build_fcp7_xml(clip_data, fps=sequence_fps)
    save_xml(xml_string, output_xml)

    # =====================================================================
    # Summary
    # =====================================================================
    log.info("")
    log.info("=" * 60)
    log.info("Pipeline complete!")
    log.info("  Clips processed : %d", len(clip_data))
    log.info("  Clips skipped   : %d", len(skipped))
    log.info("  Output XML      : %s", output_xml)
    log.info("=" * 60)

    print()
    print(f"  {'Clip':<35} {'In':<14} {'Out':<14} {'Duration':>10}  Source")
    print("  " + "-" * 100)
    for c in clip_data:
        dur_secs = (c["out_frame"] - c["in_frame"]) / c["fps"]
        src_name = Path(c["src_path"]).name
        print(f"  {c['name']:<35} {c['in_tc']:<14} {c['out_tc']:<14} {dur_secs:>8.1f}s  {src_name}")
    print()


if __name__ == "__main__":
    main()