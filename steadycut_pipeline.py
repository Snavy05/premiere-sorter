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
        detect_cut_frame,
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
# Programmatic Pipeline Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    input_dir: Path,
    output_xml: Path,
    *,
    proxy_dir: Path = Path("proxies"),
    no_proxies: bool = False,
    skip_proxies: bool = False,
    threshold: float = DEFAULT_THRESHOLD_PX,
    max_threshold: float = DEFAULT_MAX_THRESHOLD_PX,
    stable_secs: float = DEFAULT_STABLE_SECS,
    fallback_fps: float = DEFAULT_FPS,
    yolo_model: str | None = "yolov8n.pt",
    export_json: Path | None = None,
    state: dict | None = None,
    cut_on_action_mode: str = "off",
    coa_sensitivity: float = 0.02,
) -> list[dict]:
    """Run all four pipeline phases programmatically.

    Pass a mutable ``state`` dict to receive live progress updates:
        {"running": bool, "phase": str, "percent": int, "done": bool, "error": str|None}
    """

    def _st(updates: dict) -> None:
        if state is not None:
            state.update(updates)

    pipeline_start = time.perf_counter()

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

        proxy_map = generate_proxies(input_dir, proxy_dir,
                                     on_proxy_done=_proxy_progress)

    if not proxy_map:
        raise RuntimeError("No clips available after Phase 1 — aborting.")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 — Stability Analysis (progressive threshold relaxation)
    # ─────────────────────────────────────────────────────────────────────────
    _st({"phase": "Analyzing Motion", "percent": 25})
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 2 — Stability Analysis")
    log.info("=" * 60)

    raw_files_by_stem: dict[str, Path] = {
        f.stem: f
        for f in input_dir.iterdir()
        if f.is_file() and not f.name.startswith("._")
    }

    clip_data: list[dict] = []
    skipped: list[str]   = []

    # Stage A: compute optical flow for all clips in parallel
    log.info("Computing optical flow for %d clip(s) in parallel…", len(proxy_map))
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
            log.warning("  → Motion compute failed, skipping: %s", raw_path.name)
            skipped.append(raw_path.name)
            continue

        motion, fps_clip, total_frames = cached
        log.info("[analysis] %s  threshold=%.1f px", proxy_path.name, threshold)
        result = find_stable_window(motion, fps_clip, total_frames,
                                    threshold_px=threshold, stable_secs=stable_secs)

        if result is None:
            # Distinguish permanent failure (too short) from threshold failure.
            stable_needed = max(1, int(round(stable_secs * fps_clip)))
            if len(motion) < stable_needed + 1:
                log.warning("  → Clip too short to analyse (%d frames) — dropping: %s",
                            len(motion), raw_path.name)
                skipped.append(raw_path.name)
            else:
                log.warning("  → No stable window at threshold %.1f px: %s",
                            threshold, raw_path.name)
                remaining[raw_path] = proxy_path
            continue

        clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
        src_info = probe_video_info(Path(src_path))

        cut_frame: "int | None" = None
        if cut_on_action_mode != "off":
            cut_frame = detect_cut_frame(
                proxy_path, result["in_frame"], result["out_frame"], fps_clip,
                sensitivity=coa_sensitivity,
            )

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
            "cut_frame":    cut_frame,
        })

    # Stage C: threshold relaxation — pure in-memory, no disk reads
    # Drop clips that are permanently too short before the retry loop.
    # find_stable_window() returns None for both "too short" (permanent) and
    # "no stable window at this threshold" (temporary). Clips in the first
    # category will never resolve no matter how high the threshold goes, so
    # they must be removed from remaining here or the loop runs forever.
    for raw_path, proxy_path in list(remaining.items()):
        motion, fps_clip, _ = motion_cache[proxy_path]
        stable_needed = max(1, int(round(stable_secs * fps_clip)))
        if len(motion) < stable_needed + 1:
            log.warning("  → Clip permanently too short (%d frames) — dropping: %s",
                        len(motion), raw_path.name)
            skipped.append(raw_path.name)
            remaining.pop(raw_path)

    current_threshold = threshold

    while remaining:
        current_threshold = round(current_threshold + 0.1, 1)

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
                log.info("  → Adding %s uncut (frames 0–%d)", raw_path.name, out_frame)
                clip_data.append({
                    "name":         clean_name,
                    "src_path":     src_path,
                    "in_frame":     0,
                    "out_frame":    out_frame,
                    "fps":          fps_clip,
                    "total_frames": total_frames_clip,
                    "in_tc":        _frame_to_tc(0, fps_clip),
                    "out_tc":       _frame_to_tc(out_frame, fps_clip),
                    "width":        src_info["width"],
                    "height":       src_info["height"],
                    "sample_rate":  src_info["sample_rate"],
                    "channels":     src_info["channels"],
                    "cut_frame":    None,  # too shaky; skip Cut on Action
                })
            break

        log.info("")
        log.info("── Relaxing threshold → %.1f px  (%d clip(s) remaining) ──",
                 current_threshold, len(remaining))

        still_remaining: dict[Path, Path] = {}
        for raw_path, proxy_path in remaining.items():
            cached = motion_cache[proxy_path]
            motion, fps_clip, total_frames = cached
            result = find_stable_window(motion, fps_clip, total_frames,
                                        threshold_px=current_threshold,
                                        stable_secs=stable_secs)

            if result is None:
                still_remaining[raw_path] = proxy_path
                continue

            clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
            src_info = probe_video_info(Path(src_path))

            log.info("  ✓ Recovered %s at threshold %.1f px", raw_path.name, current_threshold)

            cut_frame_r: "int | None" = None
            if cut_on_action_mode != "off":
                cut_frame_r = detect_cut_frame(
                    proxy_path, result["in_frame"], result["out_frame"], fps_clip,
                    sensitivity=coa_sensitivity,
                )

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
                "cut_frame":    cut_frame_r,
            })

        remaining = still_remaining

    if not clip_data:
        raise RuntimeError("No usable clips after stability analysis — aborting.")

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 — YOLO Shot Classification
    # ─────────────────────────────────────────────────────────────────────────
    _st({"phase": "YOLO Classification", "percent": 50})

    if yolo_model:
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 3 — YOLO Shot Classification  [model: %s]", yolo_model)
        log.info("=" * 60)

        shot_classifier.YOLO_MODEL_WEIGHTS = yolo_model

        def _yolo_progress(done: int, total: int) -> None:
            _st({"percent": 50 + int(done / total * 25)})

        clip_data = annotate_clip_list(clip_data, on_clip_done=_yolo_progress)

        few   = sum(1 for c in clip_data if "[<2 People]"        in c.get("shot_tags", []))
        crowd = sum(1 for c in clip_data if "[Multiple Subjects]" in c.get("shot_tags", []))
        broll = sum(1 for c in clip_data if "[BRolls]"            in c.get("shot_tags", []))

        log.info(
            "Classification complete — <2 People: %d  Multiple Subjects: %d  B-Roll: %d",
            few, crowd, broll,
        )
    else:
        log.info("Skipping Phase 3 — all clips will receive Rose label in XML.")
        for clip in clip_data:
            clip.setdefault("shot_tags", [])

    if export_json:
        export_json.parent.mkdir(parents=True, exist_ok=True)
        export_json.write_text(
            json.dumps(clip_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Full analysis exported → %s", export_json)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4 — FCP7 XML Assembly
    # ─────────────────────────────────────────────────────────────────────────
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
    # Summary
    # ─────────────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("Pipeline complete!")
    log.info("  Clips processed  : %d", len(clip_data))
    log.info("  Clips skipped    : %d", len(skipped))
    log.info("  Output XML       : %s", written_path)
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
    print(f"  ✓  Drag '{written_path.name}' into Premiere Pro's Project Panel.")
    print(f"  ⏱  Total pipeline time: {elapsed_str}")
    print()

    _st({"phase": "Complete", "percent": 100, "running": False, "done": True})
    return clip_data


# ─────────────────────────────────────────────────────────────────────────────
# CLI / Wizard Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SteadyCut — 4-phase video stabilisation pipeline → FCP7 XML",
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
