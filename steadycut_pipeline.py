"""
steadycut_pipeline.py — Final Unified SteadyCut Pipeline
=========================================================
Orchestrates all four phases of the SteadyCut pipeline by importing
from the three constituent modules and wiring them together:

  Phase 1 — FFmpeg proxy generation    (pipelinev3.py)
  Phase 2 — Stability analysis          (pipelinev3.py)
  Phase 3 — YOLO shot classification    (shot_classifier.py)
  Phase 4 — FCP7 XML assembly           (xml_assembler.py)

Usage:
    python steadycut_pipeline.py                              # interactive wizard
    python steadycut_pipeline.py --input DIR --proxies DIR    # CLI flags

The generated XML links back to the ORIGINAL high-quality clips,
colour-coded by shot type in Premiere Pro:
    Cerulean  — [<2 People]         (1-2 persons detected)
    Mango     — [Multiple Subjects] (3+ persons detected)
    Rose      — [BRolls]            (no people / background figure)

Dependencies
------------
All three source files must be importable from the same directory:
    pipelinev3.py
    shot_classifier.py
    xml_assembler.py

External requirements:
    ffmpeg / ffprobe  (on PATH)
    opencv-python-headless
    numpy
    ultralytics  (YOLOv8)
"""

from __future__ import annotations

import json
import logging
import argparse
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────────────────────
# Logging — configure before importing submodules so their loggers inherit it
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("steadycut")

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 + 2 — imported from pipelinev3.py
# ─────────────────────────────────────────────────────────────────────────────

try:
    from pipelinev3 import (
        generate_proxies,
        analyze_stability,
        compute_motion,
        find_stable_window,
        find_stable_windows,
        detect_cut_frame,
        detect_cut_frame_detailed,
        detect_gpu_encoder,
        probe_video_info,
        _resolve_raw_path,
        _frame_to_tc,
        _prompt_path,
        _prompt_float,
        DEFAULT_THRESHOLD_PX,
        DEFAULT_MAX_THRESHOLD_PX,
        DEFAULT_STABLE_SECS,
        DEFAULT_FPS,
        SUPPORTED_EXTENSIONS,
        CPU_PRESETS,
    )
except ImportError as exc:
    log.error(
        "Could not import pipelinev3.py — make sure it is in the same "
        "directory as this script.\n  %s", exc
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — imported from shot_classifier.py
# ─────────────────────────────────────────────────────────────────────────────

try:
    import shot_classifier
    from shot_classifier import annotate_clip_list, _YOLO_MODELS
except ImportError as exc:
    log.error(
        "Could not import shot_classifier.py — make sure it is in the same "
        "directory as this script.\n  %s", exc
    )
    sys.exit(1)

try:
    import action_detector
except ImportError as exc:
    log.error(
        "Could not import action_detector.py — make sure it is in the same "
        "directory as this script.\n  %s", exc
    )
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — imported from xml_assembler.py
# ─────────────────────────────────────────────────────────────────────────────

try:
    from xml_assembler import assemble_xml
except ImportError as exc:
    log.error(
        "Could not import xml_assembler.py — make sure it is in the same "
        "directory as this script.\n  %s", exc
    )
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Interactive Prompt Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prompt_yolo_model() -> str:
    """
    Show the YOLO model selection menu and return the chosen weights filename.
    The user types a number (1–5); pressing Enter alone selects the default
    (1 = Nano, fastest).
    """
    print()
    print("  ┌─ YOLO Model Selection ─────────────────────────────────────────────────┐")
    for i, (weights, desc) in enumerate(_YOLO_MODELS, start=1):
        default_marker = "  ← default" if i == 1 else ""
        print(f"  │  [{i}] {weights:<15}  {desc}{default_marker}")
    print("  └───────────────────────────────────────────────────────────────────────┘")

    while True:
        raw = input("  Choose model [1]: ").strip()
        if raw == "":
            return _YOLO_MODELS[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(_YOLO_MODELS):
            return _YOLO_MODELS[int(raw) - 1][0]
        print(f"  ⚠  Please enter a number between 1 and {len(_YOLO_MODELS)}.")


def _prompt_bool(label: str, default: bool = False) -> bool:
    """Prompt for a yes/no answer. Returns bool."""
    default_str = "Y/n" if default else "y/N"
    while True:
        raw = input(f"  {label} [{default_str}]: ").strip().lower()
        if raw in ("", "y", "yes"):
            return True if raw in ("y", "yes") else default
        if raw in ("n", "no"):
            return False
        print("  ⚠  Please enter y or n.")


# ─────────────────────────────────────────────────────────────────────────────
# COA Helper
# ─────────────────────────────────────────────────────────────────────────────

def _detect_coa(
    proxy_path: Path,
    in_frame: int,
    out_frame: int,
    fps: float,
    coa_sensitivity: float,
    use_yolo_prescreen: bool,
) -> "tuple[int | None, float | None, bool]":
    """
    Run Cut-on-Action detection for one window.

    When *use_yolo_prescreen* is True, a quick YOLO person check runs first:
      - 2+ persons detected → skip COA entirely (returns coa_no_peak=True).
      - 1 person detected   → restrict frame diff to that person's bbox (ROI).
      - 0 persons detected  → fall back to full-frame diff.

    Returns (cut_frame, coa_score, coa_no_peak).
    coa_no_peak=True means either no arc was found OR the clip was skipped
    because it contains multiple people.
    """
    roi: "tuple[int, int, int, int] | None" = None

    if use_yolo_prescreen:
        try:
            pinfo = shot_classifier.get_primary_person_bbox(
                proxy_path, in_frame, out_frame, fps
            )
            if pinfo["person_count"] >= 2:
                log.info(
                    "  -> COA skipped (%d persons detected): %s",
                    pinfo["person_count"], proxy_path.name,
                )
                return None, None, True
            roi = pinfo["bbox"]
        except Exception as exc:
            log.warning("  -> COA prescreen failed (%s) — using full frame.", exc)

    detail = detect_cut_frame_detailed(
        proxy_path, in_frame, out_frame, fps,
        sensitivity=coa_sensitivity,
        roi=roi,
    )
    if detail:
        return detail["frame"], detail["score"], False
    return None, None, True


# ─────────────────────────────────────────────────────────────────────────────
# Dev Report
# ─────────────────────────────────────────────────────────────────────────────

def _write_dev_report(
    dev_report: dict,
    output_xml: Path,
    settings: dict,
) -> Path:
    """Write a JSON dev report alongside the output XML for debugging window detection."""
    import datetime
    import json as _json

    report_path = output_xml.with_name(output_xml.stem + "_dev_report.json")
    payload = {
        "generated":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "settings":   settings,
        "clips":      dev_report,
    }
    report_path.write_text(
        _json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Dev report -> %s", report_path)
    return report_path


# ─────────────────────────────────────────────────────────────────────────────
# Phase Timing Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _record_phase_start(name: str, phase_ts: dict) -> None:
    phase_ts[name] = time.perf_counter()

def _record_phase_end(name: str, state: "dict | None", phase_ts: dict) -> None:
    if name in phase_ts and state is not None:
        elapsed = round(time.perf_counter() - phase_ts[name], 1)
        timings = state.get("phase_timings", {})
        timings[name] = elapsed
        state["phase_timings"] = timings  # dict assignment is GIL-atomic in CPython


# ─────────────────────────────────────────────────────────────────────────────
# Programmatic Pipeline Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    input_dir: Path,
    output_xml: Path,
    *,
    proxy_dir: Path = Path("proxies"),
    no_proxies: bool = False,
    skip_proxies: bool = False,
    skip_stability: bool = False,
    threshold: float = DEFAULT_THRESHOLD_PX,
    max_threshold: float = DEFAULT_MAX_THRESHOLD_PX,
    stable_secs: float = DEFAULT_STABLE_SECS,
    fallback_fps: float = DEFAULT_FPS,
    yolo_model: str | None = "yolov8n.pt",
    export_json: Path | None = None,
    state: dict | None = None,
    cut_on_action_mode: str = "off",
    coa_sensitivity: float = 0.02,
    tail_trim_frames: int = 0,
    head_trim_frames: int = 0,
    proxy_cpu_preset: str = "high",
    proxy_use_gpu: bool = False,
    analysis_mode: str = "stability",        # "stability" | "action" | "both"
    skip_multi_person_action: bool = True,   # skip 2+ person clips in action/both mode
    action_velocity_threshold: float = 3.0,  # px/frame — action detector sensitivity
    pose_model: str = "yolov8n-pose.pt",     # YOLOv8-pose weights for action detection
) -> list[dict]:
    """Run all four pipeline phases programmatically.

    Pass a mutable ``state`` dict to receive live progress updates:
        {"running": bool, "phase": str, "percent": int, "done": bool, "error": str|None}
    """

    def _st(updates: dict) -> None:
        if state is not None:
            state.update(updates)

    pipeline_start = time.perf_counter()
    phase_ts: dict = {}
    if state is not None:
        state["pipeline_start_ts"] = pipeline_start
    _record_phase_start("Proxy Generation", phase_ts)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1 — Proxy Generation
    # ─────────────────────────────────────────────────────────────────────────
    _st({"phase": "Generating Proxies", "percent": 0, "running": True, "done": False, "error": None})

    if no_proxies:
        log.info("Skipping Phase 1 (--no-proxies: analysing original files directly)")
        raw_files = sorted(
            f for f in input_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not f.name.startswith("._")
        )
        if not raw_files:
            raise RuntimeError(f"No supported video files found in {input_dir}")
        proxy_map: dict[Path, Path] = {f: f for f in raw_files}
    elif skip_proxies:
        log.info("Skipping Phase 1 (--skip-proxies)")
        proxy_map: dict[Path, Path] = {}
        for p in sorted(proxy_dir.glob("*.mp4")):
            if p.name.startswith("._"):
                continue
            raw_stem = p.stem
            if raw_stem.lower().endswith("_proxy"):
                raw_stem = raw_stem[:-6]
            matches = [
                f for f in input_dir.iterdir()
                if f.is_file() and f.stem == raw_stem and not f.name.startswith("._")
            ]
            proxy_map[matches[0] if matches else p] = p
    else:
        log.info("=" * 60)
        log.info("PHASE 1 — Proxy Generation")
        log.info("=" * 60)

        def _proxy_progress(done: int, total: int) -> None:
            _st({"percent": int(done / total * 25),
                 "clip_current": done, "clip_total": total})

        max_workers, ffmpeg_threads = CPU_PRESETS.get(proxy_cpu_preset, CPU_PRESETS["high"])
        gpu_encoder = detect_gpu_encoder() if proxy_use_gpu else None
        log.info(
            "Proxy settings — CPU preset: %s (workers=%d, threads=%s)  GPU: %s",
            proxy_cpu_preset, max_workers,
            str(ffmpeg_threads) if ffmpeg_threads > 0 else "auto",
            gpu_encoder or "off",
        )
        proxy_map = generate_proxies(
            input_dir, proxy_dir,
            on_proxy_done=_proxy_progress,
            max_workers=max_workers,
            ffmpeg_threads=ffmpeg_threads,
            gpu_encoder=gpu_encoder,
        )

    if not proxy_map:
        # Distinguish "folder is empty" from "clips were found but every proxy
        # failed". The latter is almost always an FFmpeg problem (not executable,
        # missing, or quarantined) — point the user at the real cause + the log
        # instead of the misleading "no clips" message.
        raw_count = sum(
            1 for f in input_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not f.name.startswith("._")
        )
        if raw_count == 0:
            raise RuntimeError(f"No supported video files found in {input_dir}")
        if skip_proxies:
            raise RuntimeError(
                f"No proxies found in {proxy_dir} (--skip-proxies). "
                "Run once without --skip-proxies to generate them first."
            )
        raise RuntimeError(
            f"FFmpeg failed on all {raw_count} clip(s) — 0 proxies were produced. "
            "This usually means FFmpeg is missing or not executable. "
            "Check the log for the FFmpeg error and re-run the app's FFmpeg setup."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 — Stability Analysis (or full-clip pass-through if skipped)
    # ─────────────────────────────────────────────────────────────────────────
    _record_phase_end("Proxy Generation", state, phase_ts)
    _record_phase_start("Action Analysis", phase_ts)
    _st({"phase": "Analyzing Motion", "percent": 25})

    # Configure model weights early for both detection and pose models.
    if yolo_model:
        shot_classifier.YOLO_MODEL_WEIGHTS = yolo_model
    if pose_model:
        action_detector.YOLO_POSE_WEIGHTS = pose_model

    raw_files_by_stem: dict[str, Path] = {
        f.stem: f
        for f in input_dir.iterdir()
        if f.is_file() and not f.name.startswith("._")
    }

    clip_data:     list[dict] = []
    skipped_clips: list[dict] = []   # full clip dicts for the rejects XML
    dev_report:    dict       = {}

    def _add_skip(rp: Path, reason: str) -> None:
        """Collect a skipped clip's metadata for the rejects sequence."""
        try:
            info = probe_video_info(rp)
            fps_s = info["fps"]
            tot_s = info["total_frames"] or 1
            w, h, sr, ch = info["width"], info["height"], info["sample_rate"], info["channels"]
        except Exception:
            fps_s, tot_s = fallback_fps, 1
            w, h, sr, ch = 1920, 1080, 48000, 2
        skipped_clips.append({
            "name":         rp.stem,
            "src_path":     str(rp.resolve()),
            "in_frame":     0,
            "out_frame":    max(0, tot_s - 1),
            "fps":          fps_s,
            "total_frames": tot_s,
            "in_tc":        _frame_to_tc(0, fps_s),
            "out_tc":       _frame_to_tc(max(0, tot_s - 1), fps_s),
            "width":        w,
            "height":       h,
            "sample_rate":  sr,
            "channels":     ch,
            "shot_tags":    [],
            "cut_frame":    None,
            "coa_no_peak":  False,
            "skip_reason":  reason,
        })

    # Build a src→proxy reverse map (needed for "both" mode second pass)
    src_to_proxy: dict[str, Path] = {str(raw): proxy for raw, proxy in proxy_map.items()}

    if analysis_mode == "action":
        # ─────────────────────────────────────────────────────────────────────
        # PHASE 2 — Action Detection (pose-based, no stability analysis)
        # ─────────────────────────────────────────────────────────────────────
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 2 — Action Detection  [pose: %s]", pose_model)
        log.info("=" * 60)
        _st({"phase": "Detecting Actions", "percent": 25})
        total_clips = len(proxy_map)
        _st({"clip_total": total_clips, "clip_current": 0})

        for idx, (raw_path, proxy_path) in enumerate(proxy_map.items(), 1):
            import cv2 as _cv2
            cap = _cv2.VideoCapture(str(proxy_path))
            fps_clip     = cap.get(_cv2.CAP_PROP_FPS) or fallback_fps
            total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            # Person pre-screen — skip multi-person clips if requested
            pinfo = shot_classifier.get_primary_person_bbox(
                proxy_path, 0, max(0, total_frames - 1), fps_clip,
            )
            if pinfo["person_count"] >= 2 and skip_multi_person_action:
                log.info(
                    "  -> Skipping multi-person clip (%d persons): %s",
                    pinfo["person_count"], raw_path.name,
                )
                _add_skip(raw_path, "multi_person_action")
                _st({"percent": 25 + int(idx / total_clips * 25),
                     "clip_current": idx, "clip_total": total_clips})
                continue

            _st({"percent": 25 + int((idx - 1) / total_clips * 25),
                 "clip_current": idx - 1, "clip_total": total_clips})
            actions = action_detector.detect_actions(
                proxy_path, fps_clip,
                person_bbox=pinfo["bbox"],
                velocity_threshold=action_velocity_threshold,
            )

            if not actions:
                log.info("  -> No actions found: %s", raw_path.name)
                _add_skip(raw_path, "no_actions_detected")
                _st({"percent": 25 + int(idx / total_clips * 25),
                     "clip_current": idx, "clip_total": total_clips})
                continue

            clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
            src_info = probe_video_info(Path(src_path))

            for a_idx, action in enumerate(actions, 1):
                action_name = (
                    f"{clean_name} [a{a_idx}]" if len(actions) > 1 else clean_name
                )
                # Mark mode: marker at action velocity peak
                # Cut / off: clean trim, no marker
                cut_frame = (
                    action["peak_frame"] if cut_on_action_mode == "mark" else None
                )
                clip_data.append({
                    "name":         action_name,
                    "src_path":     src_path,
                    "in_frame":     action["start_frame"],
                    "out_frame":    action["end_frame"],
                    "fps":          fps_clip,
                    "total_frames": total_frames,
                    "in_tc":        _frame_to_tc(action["start_frame"], fps_clip),
                    "out_tc":       _frame_to_tc(action["end_frame"],   fps_clip),
                    "width":        src_info["width"],
                    "height":       src_info["height"],
                    "sample_rate":  src_info["sample_rate"],
                    "channels":     src_info["channels"],
                    "tc_string":    src_info["tc_string"],
                    "tc_frame":     src_info["tc_frame"],
                    "cut_frame":    cut_frame,
                    "coa_no_peak":  False,
                    "label_reason": "action",
                    "shot_tags":    [],
                })

            _st({"percent": 25 + int(idx / total_clips * 25),
                 "clip_current": idx, "clip_total": total_clips})

        if not clip_data:
            raise RuntimeError("No action segments found in any clip — aborting.")

    elif skip_stability:
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 2 — Stability Analysis SKIPPED (full-clip mode)")
        log.info("=" * 60)
        total_clips = len(proxy_map)
        _st({"clip_total": total_clips, "clip_current": 0})
        for idx, (raw_path, proxy_path) in enumerate(proxy_map.items(), 1):
            import cv2 as _cv2
            cap = _cv2.VideoCapture(str(proxy_path))
            fps_clip     = cap.get(_cv2.CAP_PROP_FPS) or fallback_fps
            total_frames = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            out_frame = max(0, total_frames - 1)
            in_frame  = min(head_trim_frames, out_frame - 1)
            out_frame = max(in_frame + 1, out_frame - tail_trim_frames)
            clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
            src_info = probe_video_info(Path(src_path))
            cut_frame: "int | None" = None
            coa_score: "float | None" = None
            coa_no_peak = False
            if cut_on_action_mode != "off":
                cut_frame, coa_score, coa_no_peak = _detect_coa(
                    proxy_path, in_frame, out_frame, fps_clip, coa_sensitivity,
                    use_yolo_prescreen=(yolo_model is not None),
                )
            clip_data.append({
                "name":         clean_name,
                "src_path":     src_path,
                "in_frame":     in_frame,
                "out_frame":    out_frame,
                "fps":          fps_clip,
                "total_frames": total_frames,
                "in_tc":        _frame_to_tc(in_frame, fps_clip),
                "out_tc":       _frame_to_tc(out_frame, fps_clip),
                "width":        src_info["width"],
                "height":       src_info["height"],
                "sample_rate":  src_info["sample_rate"],
                "channels":     src_info["channels"],
                "tc_string":    src_info["tc_string"],
                "tc_frame":     src_info["tc_frame"],
                "cut_frame":    cut_frame,
                "coa_no_peak":  coa_no_peak,
                "label_reason": "no_coa_peak" if coa_no_peak else "broll",
            })
            dev_report[Path(src_path).name] = [{
                "window_index":   1,
                "in_frame":       in_frame,
                "out_frame":      out_frame,
                "in_tc":          _frame_to_tc(in_frame, fps_clip),
                "out_tc":         _frame_to_tc(out_frame, fps_clip),
                "duration_secs":  round((out_frame - in_frame) / fps_clip, 2),
                "coa_frame":      cut_frame,
                "coa_score":      round(coa_score, 6) if coa_score is not None else None,
                "threshold_used": None,
            }]
            _st({"percent": 25 + int(idx / total_clips * 25),
                 "clip_current": idx, "clip_total": total_clips})
    else:
        # stability or both — run optical flow stability analysis
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 2 — Stability Analysis")
        log.info("=" * 60)

        # Stage A: compute optical flow for all clips in parallel
        log.info("Computing optical flow for %d clip(s) in parallel...", len(proxy_map))
        motion_cache: dict[Path, tuple[list[float], float, int] | None] = {}
        total_clips = len(proxy_map)
        clips_done  = 0
        _st({"clip_total": total_clips, "clip_current": 0})

        def _motion_job(proxy_path: Path) -> tuple[Path, tuple | None]:
            return proxy_path, compute_motion(proxy_path, fallback_fps)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(_motion_job, proxy_path): proxy_path
                    for proxy_path in proxy_map.values()}
            for fut in as_completed(futs):
                try:
                    proxy_path, cached = fut.result()
                except Exception as exc:
                    proxy_path = futs[fut]
                    log.warning("[motion] Exception on %s: %s", proxy_path.name, exc)
                    cached = None
                motion_cache[proxy_path] = cached
                clips_done += 1
                _st({"percent": 25 + int(clips_done / total_clips * 25),
                     "clip_current": clips_done, "clip_total": total_clips})

        # Stage B: first-pass window finding at starting threshold (no I/O)
        remaining: dict[Path, Path] = {}

        for raw_path, proxy_path in proxy_map.items():
            cached = motion_cache.get(proxy_path)
            if cached is None:
                log.warning("  -> Motion compute failed, skipping: %s", raw_path.name)
                _add_skip(raw_path, "motion_failed")
                continue

            motion, fps_clip, total_frames = cached
            log.info("[analysis] %s  threshold=%.1f px", proxy_path.name, threshold)
            windows = find_stable_windows(motion, fps_clip, total_frames,
                                          threshold_px=threshold, stable_secs=stable_secs)

            if not windows:
                stable_needed = max(1, int(round(stable_secs * fps_clip)))
                if len(motion) < stable_needed + 1:
                    log.warning("  -> Clip too short to analyse (%d frames) — dropping: %s",
                                len(motion), raw_path.name)
                    _add_skip(raw_path, "too_short")
                else:
                    log.warning("  -> No stable windows at threshold %.1f px: %s",
                                threshold, raw_path.name)
                    remaining[raw_path] = proxy_path
                continue

            clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
            src_info = probe_video_info(Path(src_path))
            log.info("  -> %d stable window(s) found in %s", len(windows), proxy_path.name)

            report_windows = []
            for w_idx, window in enumerate(windows, start=1):
                window_name = f"{clean_name} [w{w_idx}]" if len(windows) > 1 else clean_name

                w_in  = min(window["in_frame"] + head_trim_frames, window["out_frame"] - 1)
                w_out = max(w_in + 1, window["out_frame"] - tail_trim_frames)
                cut_frame: "int | None" = None
                coa_score: "float | None" = None
                coa_no_peak = False
                if cut_on_action_mode != "off":
                    cut_frame, coa_score, coa_no_peak = _detect_coa(
                        proxy_path, w_in, w_out, fps_clip, coa_sensitivity,
                        use_yolo_prescreen=(yolo_model is not None),
                    )

                clip_data.append({
                    "name":         window_name,
                    "src_path":     src_path,
                    "in_frame":     w_in,
                    "out_frame":    w_out,
                    "fps":          window["fps"],
                    "total_frames": window["total_frames"],
                    "in_tc":        _frame_to_tc(w_in, fps_clip),
                    "out_tc":       _frame_to_tc(w_out, fps_clip),
                    "width":        src_info["width"],
                    "height":       src_info["height"],
                    "sample_rate":  src_info["sample_rate"],
                    "channels":     src_info["channels"],
                    "tc_string":    src_info["tc_string"],
                    "tc_frame":     src_info["tc_frame"],
                    "cut_frame":    cut_frame,
                    "coa_no_peak":  coa_no_peak,
                    "label_reason": "no_coa_peak" if coa_no_peak else "broll",
                })
                report_windows.append({
                    "window_index":   w_idx,
                    "in_frame":       w_in,
                    "out_frame":      w_out,
                    "in_tc":          _frame_to_tc(w_in, fps_clip),
                    "out_tc":         _frame_to_tc(w_out, fps_clip),
                    "duration_secs":  round((w_out - w_in) / fps_clip, 2),
                    "coa_frame":      cut_frame,
                    "coa_score":      round(coa_score, 6) if coa_score is not None else None,
                    "threshold_used": threshold,
                })

            dev_report[Path(src_path).name] = report_windows

        # Stage C: threshold relaxation — pure in-memory, no disk reads
        for raw_path, proxy_path in list(remaining.items()):
            motion, fps_clip, _ = motion_cache[proxy_path]
            stable_needed = max(1, int(round(stable_secs * fps_clip)))
            if len(motion) < stable_needed + 1:
                log.warning("  -> Clip permanently too short (%d frames) — dropping: %s",
                            len(motion), raw_path.name)
                _add_skip(raw_path, "too_short")
                remaining.pop(raw_path)

        current_threshold = threshold

        # 4b — recovery progress: surface live progress during the threshold
        # relaxation sweep so the UI keeps moving instead of pinning at the
        # end-of-analysis clip count (which made the app look frozen).
        recovery_total = len(remaining)
        if recovery_total:
            _st({"phase": "Recovering clips",
                 "clip_current": 0, "clip_total": recovery_total})

        while remaining:
            current_threshold = round(current_threshold + 0.1, 1)
            _st({"phase": f"Recovering clips · {current_threshold:.1f}px",
                 "clip_current": recovery_total - len(remaining),
                 "clip_total": recovery_total})

            if current_threshold > max_threshold:
                log.warning(
                    "Maximum threshold %.1f px reached — %d clip(s) are too shaky for "
                    "stability trimming; adding full clip(s) to timeline: %s",
                    max_threshold, len(remaining),
                    ", ".join(raw_path.name for raw_path in remaining),
                )
                for raw_path, proxy_path in remaining.items():
                    motion_r, fps_clip, total_frames_clip = motion_cache[proxy_path]
                    clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
                    src_info = probe_video_info(Path(src_path))
                    out_frame = total_frames_clip - 1 if total_frames_clip > 0 else len(motion_r) - 1
                    in_frame_fb = min(head_trim_frames, out_frame - 1)
                    out_frame   = max(in_frame_fb + 1, out_frame - tail_trim_frames)
                    log.info("  -> Adding %s uncut (frames %d-%d)", raw_path.name, in_frame_fb, out_frame)
                    clip_data.append({
                        "name":         clean_name,
                        "src_path":     src_path,
                        "in_frame":     in_frame_fb,
                        "out_frame":    out_frame,
                        "fps":          fps_clip,
                        "total_frames": total_frames_clip,
                        "in_tc":        _frame_to_tc(in_frame_fb, fps_clip),
                        "out_tc":       _frame_to_tc(out_frame, fps_clip),
                        "width":        src_info["width"],
                        "height":       src_info["height"],
                        "sample_rate":  src_info["sample_rate"],
                        "channels":     src_info["channels"],
                        "tc_string":    src_info["tc_string"],
                        "tc_frame":     src_info["tc_frame"],
                        "cut_frame":    None,
                        "coa_no_peak":  False,
                        "label_reason": "broll",
                    })
                break

            log.info("")
            log.info("── Relaxing threshold -> %.1f px  (%d clip(s) remaining) ──",
                     current_threshold, len(remaining))

            still_remaining: dict[Path, Path] = {}
            for raw_path, proxy_path in remaining.items():
                cached = motion_cache[proxy_path]
                motion, fps_clip, total_frames = cached
                windows = find_stable_windows(motion, fps_clip, total_frames,
                                              threshold_px=current_threshold,
                                              stable_secs=stable_secs)

                if not windows:
                    still_remaining[raw_path] = proxy_path
                    continue

                clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
                src_info = probe_video_info(Path(src_path))

                log.info("  [OK] Recovered %s at threshold %.1f px (%d window(s))",
                         raw_path.name, current_threshold, len(windows))

                report_windows_r = []
                for w_idx, window in enumerate(windows, start=1):
                    window_name = f"{clean_name} [w{w_idx}]" if len(windows) > 1 else clean_name

                    w_in_r  = min(window["in_frame"] + head_trim_frames, window["out_frame"] - 1)
                    w_out_r = max(w_in_r + 1, window["out_frame"] - tail_trim_frames)
                    cut_frame_r: "int | None" = None
                    coa_score_r: "float | None" = None
                    coa_no_peak_r = False
                    if cut_on_action_mode != "off":
                        cut_frame_r, coa_score_r, coa_no_peak_r = _detect_coa(
                            proxy_path, w_in_r, w_out_r, fps_clip, coa_sensitivity,
                            use_yolo_prescreen=(yolo_model is not None),
                        )

                    clip_data.append({
                        "name":         window_name,
                        "src_path":     src_path,
                        "in_frame":     w_in_r,
                        "out_frame":    w_out_r,
                        "fps":          window["fps"],
                        "total_frames": window["total_frames"],
                        "in_tc":        _frame_to_tc(w_in_r, fps_clip),
                        "out_tc":       _frame_to_tc(w_out_r, fps_clip),
                        "width":        src_info["width"],
                        "height":       src_info["height"],
                        "sample_rate":  src_info["sample_rate"],
                        "channels":     src_info["channels"],
                        "tc_string":    src_info["tc_string"],
                        "tc_frame":     src_info["tc_frame"],
                        "cut_frame":    cut_frame_r,
                        "coa_no_peak":  coa_no_peak_r,
                        "label_reason": "no_coa_peak" if coa_no_peak_r else "broll",
                    })
                    report_windows_r.append({
                        "window_index":   w_idx,
                        "in_frame":       w_in_r,
                        "out_frame":      w_out_r,
                        "in_tc":          _frame_to_tc(w_in_r, fps_clip),
                        "out_tc":         _frame_to_tc(w_out_r, fps_clip),
                        "duration_secs":  round((w_out_r - w_in_r) / fps_clip, 2),
                        "coa_frame":      cut_frame_r,
                        "coa_score":      round(coa_score_r, 6) if coa_score_r is not None else None,
                        "threshold_used": current_threshold,
                    })

                dev_report[Path(src_path).name] = report_windows_r

            remaining = still_remaining
            _st({"clip_current": recovery_total - len(remaining),
                 "clip_total": recovery_total})

        if not clip_data:
            raise RuntimeError("No usable clips after stability analysis — aborting.")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2b — Action Detection within stable windows (both mode)
    # ─────────────────────────────────────────────────────────────────────────
    if analysis_mode == "both" and clip_data:
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 2b — Action Detection within stable windows [pose: %s]", pose_model)
        log.info("=" * 60)
        _st({"phase": "Detecting Actions", "percent": 47})

        new_clip_data: list[dict] = []
        total_both = len(clip_data)
        _st({"clip_total": total_both, "clip_current": 0})
        for both_idx, clip in enumerate(clip_data, 1):
            proxy_path = Path(src_to_proxy.get(clip["src_path"], clip["src_path"]))
            pinfo = shot_classifier.get_primary_person_bbox(
                proxy_path, clip["in_frame"], clip["out_frame"], clip["fps"],
            )
            if pinfo["person_count"] >= 2 and skip_multi_person_action:
                log.info(
                    "  -> Keeping clip as-is (multi-person, action skip): %s", clip["name"]
                )
                new_clip_data.append(clip)
                _st({"percent": 47 + int(both_idx / total_both * 3),
                     "clip_current": both_idx, "clip_total": total_both})
                continue

            _st({"percent": 47 + int((both_idx - 1) / total_both * 3),
                 "clip_current": both_idx - 1, "clip_total": total_both})
            actions = action_detector.detect_actions(
                proxy_path, clip["fps"],
                person_bbox=pinfo["bbox"],
                velocity_threshold=action_velocity_threshold,
            )

            if not actions:
                log.info("  -> No actions found within window — keeping as-is: %s", clip["name"])
                new_clip_data.append(clip)
                _st({"percent": 47 + int(both_idx / total_both * 3),
                     "clip_current": both_idx, "clip_total": total_both})
                continue

            base_name = clip["name"]
            for a_idx, action in enumerate(actions, 1):
                action_name = (
                    f"{base_name} [a{a_idx}]" if len(actions) > 1 else base_name
                )
                cut_frame = (
                    action["peak_frame"] if cut_on_action_mode == "mark" else None
                )
                new_clip_data.append({
                    **clip,
                    "name":         action_name,
                    "in_frame":     action["start_frame"],
                    "out_frame":    action["end_frame"],
                    "in_tc":        _frame_to_tc(action["start_frame"], clip["fps"]),
                    "out_tc":       _frame_to_tc(action["end_frame"],   clip["fps"]),
                    "cut_frame":    cut_frame,
                    "coa_no_peak":  False,
                    "label_reason": "action",
                })
            _st({"percent": 47 + int(both_idx / total_both * 3),
                 "clip_current": both_idx, "clip_total": total_both})

        clip_data = new_clip_data
        if not clip_data:
            raise RuntimeError("No clips remain after action detection (both mode) — aborting.")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 — YOLO Shot Classification
    # ─────────────────────────────────────────────────────────────────────────
    _record_phase_end("Action Analysis", state, phase_ts)
    _record_phase_start("COA Detection", phase_ts)
    _st({"phase": "YOLO Classification", "percent": 50})

    if yolo_model:
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 3 — YOLO Shot Classification  [model: %s]", yolo_model)
        log.info("=" * 60)

        def _yolo_progress(done: int, total: int) -> None:
            _st({"percent": 50 + int(done / total * 25)})

        clip_data = annotate_clip_list(clip_data, on_clip_done=_yolo_progress)

        # Update label_reason from YOLO result for clips not already labelled
        # by action detection or COA (those take priority).
        _tag_to_reason = {
            "[<2 People]":        "person",
            "[Multiple Subjects]": "crowd",
        }
        for clip in clip_data:
            if clip.get("label_reason") in ("action", "no_coa_peak"):
                continue
            tags = clip.get("shot_tags", [])
            for tag, reason in _tag_to_reason.items():
                if tag in tags:
                    clip["label_reason"] = reason
                    break
            else:
                clip.setdefault("label_reason", "broll")

        few   = sum(1 for c in clip_data if c.get("label_reason") == "person")
        crowd = sum(1 for c in clip_data if c.get("label_reason") == "crowd")
        broll = sum(1 for c in clip_data if c.get("label_reason") == "broll")

        log.info(
            "Classification complete — person: %d  crowd: %d  broll: %d",
            few, crowd, broll,
        )
    else:
        log.info("Skipping Phase 3 — stability clips labelled broll by default.")
        for clip in clip_data:
            clip.setdefault("shot_tags", [])
            clip.setdefault("label_reason", "broll")

    if export_json:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(
            json.dumps(clip_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Full analysis exported -> %s", export_json)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4 — FCP7 XML Assembly
    # ─────────────────────────────────────────────────────────────────────────
    _record_phase_end("COA Detection", state, phase_ts)
    _record_phase_start("XML Assembly", phase_ts)
    _st({"phase": "Assembling XML", "percent": 75})
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 4 — FCP7 XML Assembly")
    log.info("=" * 60)

    # Sort clips by filename in natural (numeric) order so the Premiere
    # timeline matches the ascending clip numbers from the source folder.
    def _natural_key(clip: dict) -> list:
        return [int(t) if t.isdigit() else t.lower()
                for t in re.split(r"(\d+)", clip["name"])]

    clip_data.sort(key=_natural_key)
    log.info("Clips sorted by filename: %s", ", ".join(c["name"] for c in clip_data))

    try:
        written_path = assemble_xml(
            clip_data,
            output_path=output_xml,
            cut_on_action_mode=cut_on_action_mode,
        )
    except ValueError as exc:
        raise RuntimeError(f"XML assembly failed: {exc}") from exc

    # ─────────────────────────────────────────────────────────────────────────
    # Dev Report
    # ─────────────────────────────────────────────────────────────────────────
    dev_report_path = _write_dev_report(
        dev_report,
        output_xml,
        settings={
            "threshold":          threshold,
            "max_threshold":      max_threshold,
            "stable_secs":        stable_secs,
            "cut_on_action_mode": cut_on_action_mode,
            "coa_sensitivity":    coa_sensitivity,
            "skip_stability":     skip_stability,
            "tail_trim_frames":   tail_trim_frames,
            "head_trim_frames":   head_trim_frames,
        },
    )
    _st({"dev_report": dev_report, "dev_report_path": str(dev_report_path)})

    # ─────────────────────────────────────────────────────────────────────────
    # Rejects XML
    # ─────────────────────────────────────────────────────────────────────────
    rejects_path: "Path | None" = None
    if skipped_clips:
        rejects_xml = output_xml.with_name(output_xml.stem + "_rejects.xml")
        try:
            rejects_path = assemble_xml(
                skipped_clips,
                output_path=rejects_xml,
                cut_on_action_mode="off",
            )
            log.info("Rejects XML (%d clip(s)) -> %s", len(skipped_clips), rejects_path)
        except Exception as exc:
            log.warning("Could not write rejects XML: %s", exc)
    _st({"rejects_xml_path": str(rejects_path) if rejects_path else None})

    # ─────────────────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("Pipeline complete!")
    log.info("  Clips processed  : %d", len(clip_data))
    log.info("  Clips skipped    : %d", len(skipped_clips))
    log.info("  Output XML       : %s", written_path)
    if rejects_path:
        log.info("  Rejects XML      : %s", rejects_path)
    log.info("  Dev report       : %s", dev_report_path)
    log.info("=" * 60)

    print()
    header = f"  {'Clip':<35} {'In':<14} {'Out':<14} {'Dur':>7}  {'Tags':<20}  Source"
    print(header)
    print("  " + "─" * (len(header) + 5))

    for c in clip_data:
        dur_secs  = (c["out_frame"] - c["in_frame"]) / c["fps"]
        tags_str  = " ".join(c.get("shot_tags", [])) or "(unclassified)"
        src_name  = Path(c["src_path"]).name
        print(
            f"  {c['name']:<35} {c['in_tc']:<14} {c['out_tc']:<14} "
            f"{dur_secs:>5.1f}s  {tags_str:<20}  {src_name}"
        )

    elapsed = time.perf_counter() - pipeline_start
    hours, rem = divmod(int(elapsed), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        elapsed_str = f"{hours}h {minutes:02d}m {seconds:02d}s"
    elif minutes:
        elapsed_str = f"{minutes}m {seconds:02d}s"
    else:
        elapsed_str = f"{elapsed:.1f}s"

    print()
    print(f"  [OK]  Drag '{written_path.name}' into Premiere Pro's Project Panel.")
    print(f"  ⏱  Total pipeline time: {elapsed_str}")
    print()

    _record_phase_end("XML Assembly", state, phase_ts)
    if state is not None and state.get("pipeline_start_ts") is not None:
        state["pipeline_total_s"] = round(time.perf_counter() - state["pipeline_start_ts"], 1)

    _st({"phase": "Complete", "percent": 100, "running": False, "done": True})
    return clip_data


# ─────────────────────────────────────────────────────────────────────────────
# CLI / Wizard Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SteadyCut — 4-phase video stabilisation pipeline -> FCP7 XML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Phases\n"
            "  1  Proxy generation      — 720p H.264 via FFmpeg\n"
            "  2  Stability analysis    — Lucas-Kanade optical flow\n"
            "  3  Shot classification   — YOLO person-count (<2 People / Multiple Subjects / B-roll)\n"
            "  4  FCP7 XML assembly     — colour-coded Premiere Pro timeline\n"
        ),
    )

    # ── I/O paths ─────────────────────────────────────────────────────────────
    parser.add_argument("--input",       type=Path, default=None,
                        help="Raw footage input directory")
    parser.add_argument("--proxies",     type=Path, default=None,
                        help="Proxy output directory")
    parser.add_argument("--output",      type=Path, default=None,
                        help="Output XML path (default: Automated_Sequence.xml)")

    # ── Analysis tuning ───────────────────────────────────────────────────────
    parser.add_argument("--threshold",     type=float, default=None, metavar="PX",
                        help=f"Starting motion threshold in pixels (default: {DEFAULT_THRESHOLD_PX})")
    parser.add_argument("--max-threshold", type=float, default=None, metavar="PX",
                        dest="max_threshold",
                        help=f"Maximum threshold before a clip is dropped as too shaky (default: {DEFAULT_MAX_THRESHOLD_PX})")
    parser.add_argument("--stable-secs",   type=float, default=None, dest="stable_secs",
                        help=f"Stable seconds required to confirm an In-Point (default: {DEFAULT_STABLE_SECS})")
    parser.add_argument("--fps",         type=float, default=None,
                        help=f"Fallback FPS when unreadable from file (default: {DEFAULT_FPS})")

    # ── YOLO model ────────────────────────────────────────────────────────────
    parser.add_argument(
        "--yolo-model", type=str, default=None, dest="yolo_model",
        metavar="WEIGHTS",
        help=(
            "YOLO weights to use for Phase 3 shot classification. "
            "Options: yolov8n.pt (default/fastest), yolov8s.pt, yolov8m.pt, "
            "yolov8l.pt, yolov8x.pt (most accurate)"
        ),
    )

    # ── Skip flags ────────────────────────────────────────────────────────────
    parser.add_argument("--skip-proxies",         action="store_true", dest="skip_proxies",
                        help="Skip Phase 1 — proxies already exist in --proxies dir")
    parser.add_argument("--no-proxies",           action="store_true", dest="no_proxies",
                        help="Analyse original files directly — skip proxy generation and proxy-based analysis entirely")
    parser.add_argument("--skip-classification",  action="store_true", dest="skip_classification",
                        help="Skip Phase 3 — omit YOLO shot classification (all clips labelled Rose)")

    # ── Optional exports ──────────────────────────────────────────────────────
    parser.add_argument("--export-json", type=Path, default=None, dest="export_json",
                        metavar="FILE",
                        help="Also export the full clip analysis (including shot_tags) as JSON")

    # ── Logging ───────────────────────────────────────────────────────────────
    parser.add_argument("--debug", action="store_true",
                        help="Enable DEBUG-level logging across all modules")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # ── Interactive wizard when no CLI path flags are given ───────────────────
    cli_supplied = any([args.input, args.proxies, args.output])

    if not cli_supplied:
        print()
        print("╔═══════════════════════════════════════════════════════════╗")
        print("║          SteadyCut — Unified 4-Phase Pipeline             ║")
        print("║  Phase 1: Proxy Gen  │  Phase 2: Stability Analysis       ║")
        print("║  Phase 3: YOLO Shot Classification                        ║")
        print("║  Phase 4: FCP7 XML Assembly (Premiere Pro import)         ║")
        print("╚═══════════════════════════════════════════════════════════╝")
        print("  Press Enter to accept the [default] for each prompt.\n")

    input_dir = args.input or _prompt_path("Raw footage folder ", Path("input"), must_exist=True)

    # ── Proxy mode: resolve before prompting for proxy folder ─────────────────
    no_proxies   = args.no_proxies
    skip_proxies = args.skip_proxies
    if not cli_supplied:
        print()
        print("  ── Phase 1: Proxy Mode ────────────────────────────────────")
        print("  [1] Generate proxies from raw footage          (default)")
        print("  [2] Skip generation — proxies already exist")
        print("  [3] No proxies — analyse original files directly")
        while True:
            raw_mode = input("  Choose [1]: ").strip()
            if raw_mode in ("", "1"):
                break
            if raw_mode == "2":
                skip_proxies = True
                break
            if raw_mode == "3":
                no_proxies = True
                break
            print("  ⚠  Please enter 1, 2, or 3.")

    if no_proxies:
        proxy_dir = args.proxies or Path("proxies")
    else:
        proxy_dir = args.proxies or _prompt_path("Proxy output folder", Path("proxies"), must_exist=False)

    output_xml = args.output or _prompt_path(
        "Output XML path    ", Path("Automated_Sequence.xml"), must_exist=False
    )

    threshold     = args.threshold     or DEFAULT_THRESHOLD_PX
    max_threshold = args.max_threshold or DEFAULT_MAX_THRESHOLD_PX
    stable_secs   = args.stable_secs   or DEFAULT_STABLE_SECS
    fallback_fps  = args.fps           or DEFAULT_FPS

    if not cli_supplied:
        print()
        print("  ── Stability Tuning (Enter to keep defaults) ──────────────")
        threshold     = _prompt_float(
            "Motion threshold px   (starting, e.g. 2.0)", DEFAULT_THRESHOLD_PX,     0.1,  50.0)
        max_threshold = _prompt_float(
            "Max threshold px      (gives up above this)", DEFAULT_MAX_THRESHOLD_PX, 0.1, 500.0)
        stable_secs   = _prompt_float(
            "Stable seconds needed (0.5 – 3.0)         ", DEFAULT_STABLE_SECS,       0.1,  30.0)
        fallback_fps  = _prompt_float(
            "Fallback FPS          (e.g. 25)            ", DEFAULT_FPS,               1.0, 240.0)

    if args.skip_classification:
        yolo_model = None
        log.info("Shot classification will be skipped (--skip-classification).")
    elif args.yolo_model:
        yolo_model = args.yolo_model
    elif not cli_supplied:
        print()
        print("  ── Phase 3: YOLO Shot Classification ──────────────────────")
        skip_clf = _prompt_bool("Skip shot classification?", default=False)
        if skip_clf:
            yolo_model = None
        else:
            yolo_model = _prompt_yolo_model()
    else:
        # CLI mode but no --yolo-model specified: default to Nano
        yolo_model = _YOLO_MODELS[0][0]

    # ── Configuration summary ─────────────────────────────────────────────────
    print()
    print("  Running with:")
    print(f"    Input folder   : {input_dir}")
    if no_proxies:
        print( "    Proxy mode     : disabled — analysing originals directly")
    else:
        print(f"    Proxy folder   : {proxy_dir}")
        print(f"    Skip proxies   : {'yes' if skip_proxies else 'no'}")
    print(f"    Output XML     : {output_xml}")
    print(f"    Threshold      : {threshold} px  (relaxes +0.1 px per retry until stable window found)")
    print(f"    Max threshold  : {max_threshold} px  (clips exceeding this are dropped as too shaky)")
    print(f"    Stable window  : {stable_secs} s")
    print(f"    Fallback FPS   : {fallback_fps}")
    print(f"    YOLO model     : {yolo_model or '(skipped)'}")
    print()

    try:
        run_pipeline(
            input_dir=input_dir,
            output_xml=output_xml,
            proxy_dir=proxy_dir,
            no_proxies=no_proxies,
            skip_proxies=skip_proxies,
            threshold=threshold,
            max_threshold=max_threshold,
            stable_secs=stable_secs,
            fallback_fps=fallback_fps,
            yolo_model=yolo_model,
            export_json=args.export_json,
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
