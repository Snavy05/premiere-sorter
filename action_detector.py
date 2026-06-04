"""
action_detector.py — Pose-based action segment detector
========================================================
Uses YOLOv8-pose to track body keypoint velocities frame by frame
and find where meaningful physical actions start and end.

Each detected action becomes a clip entry in the pipeline:
    in_frame  = action start
    out_frame = action end
    peak_frame = frame of maximum joint velocity (used as marker in mark mode)

Public API
----------
    detect_actions(proxy_path, fps, ...) -> list[dict]

Each returned dict:
    start_frame   : int
    end_frame     : int
    peak_frame    : int
    peak_velocity : float  (px/frame)
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("action_detector")

# ── Model config ──────────────────────────────────────────────────────────────

YOLO_POSE_WEIGHTS: str = "yolov8n-pose.pt"

_POSE_MODELS: list[tuple[str, str]] = [
    ("yolov8n-pose.pt", "Nano   — fastest, good for clear shots"),
    ("yolov8s-pose.pt", "Small  — balanced speed / accuracy"),
    ("yolov8m-pose.pt", "Medium — higher accuracy"),
]

# COCO 17-point keypoint indices tracked for action detection.
# Limb extremities move most during physical actions.
#   7=l_elbow  8=r_elbow  9=l_wrist  10=r_wrist
#  13=l_knee  14=r_knee  15=l_ankle 16=r_ankle
ACTION_KP_INDICES: list[int] = [7, 8, 9, 10, 13, 14, 15, 16]

# Minimum keypoint confidence to accept a point (below → treat as missing).
KP_CONF_THRESHOLD: float = 0.3

# ── Lazy model loader ─────────────────────────────────────────────────────────

_pose_model = None
_pose_lock  = threading.Lock()


def _get_pose_model():
    global _pose_model
    if _pose_model is None:
        with _pose_lock:
            if _pose_model is None:
                from ultralytics import YOLO
                from shot_classifier import _resolve_yolo_model_path
                path = _resolve_yolo_model_path(YOLO_POSE_WEIGHTS)
                log.info("[pose] Loading model: %s", path)
                _pose_model = YOLO(path)
                log.info("[pose] Model loaded.")
    return _pose_model


# ── Keypoint helpers ──────────────────────────────────────────────────────────

def _primary_keypoints(
    result,
    person_bbox: "tuple[int,int,int,int] | None",
) -> "np.ndarray | None":
    """
    Extract action keypoints for the primary person in a YOLO-pose result.

    If person_bbox is given → pick the person whose box centre is nearest
    to the bbox centre.  Otherwise → pick the largest bounding box.

    Returns shape (len(ACTION_KP_INDICES), 2) or None if no detection.
    """
    if result.keypoints is None or result.boxes is None:
        return None

    boxes = result.boxes.xyxy.cpu().numpy()     # (P, 4)
    kps   = result.keypoints.xy.cpu().numpy()   # (P, 17, 2)
    confs = (
        result.keypoints.conf.cpu().numpy()
        if result.keypoints.conf is not None
        else np.ones((len(boxes), 17), dtype=np.float32)
    )

    if len(boxes) == 0:
        return None

    if person_bbox is not None:
        bx1, by1, bx2, by2 = person_bbox
        bcx = (bx1 + bx2) / 2.0
        bcy = (by1 + by2) / 2.0
        centres = np.stack(
            [(boxes[:, 0] + boxes[:, 2]) / 2,
             (boxes[:, 1] + boxes[:, 3]) / 2], axis=1,
        )
        idx = int(np.argmin(np.linalg.norm(centres - [bcx, bcy], axis=1)))
    else:
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        idx   = int(np.argmax(areas))

    person_kps   = kps[idx]    # (17, 2)
    person_confs = confs[idx]  # (17,)

    action_kp = person_kps[ACTION_KP_INDICES].copy()
    low_conf  = person_confs[ACTION_KP_INDICES] < KP_CONF_THRESHOLD
    action_kp[low_conf] = 0.0  # zero out unreliable keypoints
    return action_kp


def _compute_velocity(
    kp_prev: np.ndarray,
    kp_curr: np.ndarray,
    frame_step: int,
) -> float:
    """
    Max per-keypoint displacement between two frames, normalised to px/frame.
    Keypoints that are (0, 0) in either frame are ignored.
    """
    valid = kp_prev.any(axis=1) & kp_curr.any(axis=1)
    if not valid.any():
        return 0.0
    disp = np.linalg.norm(kp_curr[valid] - kp_prev[valid], axis=1)
    return float(disp.max()) / max(1, frame_step)


# ── Public API ────────────────────────────────────────────────────────────────

def detect_actions(
    proxy_path: "str | Path",
    fps: float,
    person_bbox: "tuple[int,int,int,int] | None" = None,
    velocity_threshold: float = 3.0,
    min_action_secs: float = 0.25,
    merge_gap_secs: float = 0.4,
    frame_step: int = 2,
) -> list[dict]:
    """
    Find action segments by tracking pose keypoint velocity.

    Parameters
    ----------
    proxy_path         : path to the proxy video file.
    fps                : clip frame rate.
    person_bbox        : (x1,y1,x2,y2) pixel bbox of target person.
                         None → use the largest detected person.
    velocity_threshold : min px/frame to count as "in action".
    min_action_secs    : discard segments shorter than this.
    merge_gap_secs     : merge segments separated by less than this gap.
    frame_step         : analyse every Nth frame (2 = 50 % sampling for speed).

    Returns
    -------
    list of {"start_frame", "end_frame", "peak_frame", "peak_velocity"}
    Empty list if no actions found or inference fails.
    """
    proxy_path = Path(proxy_path)
    model = _get_pose_model()

    # ── Read sampled frames ───────────────────────────────────────────────────
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        log.warning("[action] Cannot open %s", proxy_path.name)
        return []

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    log.info("[action] %s  fps=%.2f  frames=%d  step=%d",
             proxy_path.name, fps, total, frame_step)

    sampled_frames:  list[np.ndarray] = []
    sampled_indices: list[int]        = []
    fi = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if fi % frame_step == 0:
            sampled_frames.append(frame)
            sampled_indices.append(fi)
        fi += 1
    cap.release()

    if len(sampled_frames) < 2:
        log.warning("[action] Too few frames to analyse: %s", proxy_path.name)
        return []

    # ── Batch pose inference ──────────────────────────────────────────────────
    log.info("[action] Pose inference on %d frames…", len(sampled_frames))
    try:
        results = model(sampled_frames, verbose=False)
    except Exception as exc:
        log.error("[action] Inference failed for %s: %s", proxy_path.name, exc)
        return []

    kp_seq: list["np.ndarray | None"] = [
        _primary_keypoints(r, person_bbox) for r in results
    ]

    # ── Velocity signal: one value per interval between consecutive samples ───
    velocity: list[float] = []
    for i in range(len(kp_seq) - 1):
        if kp_seq[i] is None or kp_seq[i + 1] is None:
            velocity.append(0.0)
        else:
            velocity.append(_compute_velocity(kp_seq[i], kp_seq[i + 1], frame_step))

    # Rolling-mean smoothing (~5 samples at typical fps)
    win     = max(1, int(fps / frame_step) // 5)
    kernel  = np.ones(win, dtype=np.float32) / win
    smoothed = np.convolve(np.array(velocity, dtype=np.float32), kernel, mode="same")

    # ── Find contiguous active regions ────────────────────────────────────────
    active              = smoothed >= velocity_threshold
    min_samp            = max(1, int(min_action_secs * fps / frame_step))
    merge_samp          = max(1, int(merge_gap_secs  * fps / frame_step))

    raw_segs: list[list[int]] = []
    in_seg, seg_start = False, 0
    for i, a in enumerate(active):
        if a and not in_seg:
            in_seg, seg_start = True, i
        elif not a and in_seg:
            in_seg = False
            raw_segs.append([seg_start, i - 1])
    if in_seg:
        raw_segs.append([seg_start, len(active) - 1])

    # Merge nearby segments
    merged: list[list[int]] = []
    for seg in raw_segs:
        if merged and seg[0] - merged[-1][1] <= merge_samp:
            merged[-1][1] = seg[1]
        else:
            merged.append(list(seg))

    # Discard segments shorter than min_samp
    merged = [s for s in merged if (s[1] - s[0] + 1) >= min_samp]

    if not merged:
        log.info("[action] No actions found in %s", proxy_path.name)
        return []

    # ── Convert sample indices → video frame numbers ──────────────────────────
    actions: list[dict] = []
    for si_start, si_end in merged:
        start_frame   = sampled_indices[si_start]
        end_si        = min(si_end + 1, len(sampled_indices) - 1)
        end_frame     = sampled_indices[end_si]

        seg_vel       = smoothed[si_start:si_end + 1]
        peak_si       = si_start + int(np.argmax(seg_vel))
        peak_frame    = sampled_indices[min(peak_si, len(sampled_indices) - 1)]
        peak_velocity = float(seg_vel.max())

        actions.append({
            "start_frame":   start_frame,
            "end_frame":     end_frame,
            "peak_frame":    peak_frame,
            "peak_velocity": round(peak_velocity, 3),
        })
        log.info(
            "[action]  %d–%d  peak=%d  vel=%.2f px/frame",
            start_frame, end_frame, peak_frame, peak_velocity,
        )

    log.info("[action] %d segment(s) in %s", len(actions), proxy_path.name)
    return actions
