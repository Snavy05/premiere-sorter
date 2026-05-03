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
import sys
import time
from pathlib import Path

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
        probe_video_info,
        _resolve_raw_path,
        _prompt_path,
        _prompt_float,
        DEFAULT_THRESHOLD_PX,
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
# Main Pipeline
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
    parser.add_argument("--threshold",   type=float, default=None, metavar="PX",
                        help=f"Starting motion threshold in pixels (default: {DEFAULT_THRESHOLD_PX})")
    parser.add_argument("--stable-secs", type=float, default=None, dest="stable_secs",
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

    threshold    = args.threshold    or DEFAULT_THRESHOLD_PX
    stable_secs  = args.stable_secs  or DEFAULT_STABLE_SECS
    fallback_fps = args.fps          or DEFAULT_FPS

    if not cli_supplied:
        print()
        print("  ── Stability Tuning (Enter to keep defaults) ──────────────")
        threshold    = _prompt_float(
            "Motion threshold px   (starting, e.g. 2.0)", DEFAULT_THRESHOLD_PX, 0.1, 50.0)
        stable_secs  = _prompt_float(
            "Stable seconds needed (0.5 – 3.0)         ", DEFAULT_STABLE_SECS, 0.1, 30.0)
        fallback_fps = _prompt_float(
            "Fallback FPS          (e.g. 25)            ", DEFAULT_FPS,          1.0, 240.0)

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
    print(f"    Stable window  : {stable_secs} s")
    print(f"    Fallback FPS   : {fallback_fps}")
    print(f"    YOLO model     : {yolo_model or '(skipped)'}")
    print()

    pipeline_start = time.perf_counter()

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1 — Proxy Generation
    # ─────────────────────────────────────────────────────────────────────────
    if no_proxies:
        log.info("Skipping Phase 1 (--no-proxies: analysing original files directly)")
        raw_files = sorted(
            f for f in input_dir.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            and not f.name.startswith("._")
        )
        if not raw_files:
            log.error("No supported video files found in %s", input_dir)
            sys.exit(1)
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
        proxy_map = generate_proxies(input_dir, proxy_dir)

    if not proxy_map:
        log.error("No clips available — aborting.")
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 — Stability Analysis (progressive threshold relaxation)
    # ─────────────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 2 — Stability Analysis")
    log.info("=" * 60)

    # Index raw files by stem for reverse-mapping proxies → originals
    raw_files_by_stem: dict[str, Path] = {
        f.stem: f
        for f in input_dir.iterdir()
        if f.is_file() and not f.name.startswith("._")
    }

    clip_data: list[dict] = []
    skipped: list[str]   = []

    # ── First pass: run at the user's starting threshold ──────────────────
    remaining: dict[Path, Path] = {}   # clips that still need a stable window

    for raw_path, proxy_path in proxy_map.items():
        log.info("[analysis] %s", proxy_path.name)
        result = analyze_stability(
            proxy_path,
            threshold_px=threshold,
            stable_secs=stable_secs,
            fallback_fps=fallback_fps,
        )

        if result is None:
            log.warning("  → No stable window at threshold %.1f px: %s",
                        threshold, raw_path.name)
            remaining[raw_path] = proxy_path
            continue

        # Resolve back to original high-quality clip
        clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
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

    # ── Progressive relaxation: retry failed clips at higher thresholds ───
    # Increment by 0.1 px each round until every clip has a stable window.
    current_threshold = threshold

    while remaining:
        current_threshold = round(current_threshold + 0.1, 1)
        log.info("")
        log.info("── Relaxing threshold → %.1f px  (%d clip(s) remaining) ──",
                 current_threshold, len(remaining))

        still_remaining: dict[Path, Path] = {}
        for raw_path, proxy_path in remaining.items():
            log.info("[retry] %s at %.1f px", proxy_path.name, current_threshold)
            result = analyze_stability(
                proxy_path,
                threshold_px=current_threshold,
                stable_secs=stable_secs,
                fallback_fps=fallback_fps,
            )

            if result is None:
                still_remaining[raw_path] = proxy_path
                continue

            clean_name, src_path = _resolve_raw_path(proxy_path, raw_files_by_stem)
            src_info = probe_video_info(Path(src_path))

            log.info("  ✓ Recovered %s at threshold %.1f px", raw_path.name, current_threshold)
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

        remaining = still_remaining

    if not clip_data:
        log.error("No usable clips after stability analysis — aborting.")
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 — YOLO Shot Classification
    # ─────────────────────────────────────────────────────────────────────────
    if yolo_model:
        log.info("")
        log.info("=" * 60)
        log.info("PHASE 3 — YOLO Shot Classification  [model: %s]", yolo_model)
        log.info("=" * 60)

        # Apply the chosen model to the shared module-level variable so that
        # the lazy loader in shot_classifier picks it up on first inference.
        shot_classifier.YOLO_MODEL_WEIGHTS = yolo_model

        # annotate_clip_list() mutates each dict in-place, adding shot_tags.
        # e.g. clip["shot_tags"] = ['[<2 People]'] / ['[Multiple Subjects]'] / ['[BRolls]']
        clip_data = annotate_clip_list(clip_data)

        # Tally classification results for the summary
        few   = sum(1 for c in clip_data if "[<2 People]"        in c.get("shot_tags", []))
        crowd = sum(1 for c in clip_data if "[Multiple Subjects]" in c.get("shot_tags", []))
        broll = sum(1 for c in clip_data if "[BRolls]"            in c.get("shot_tags", []))

        log.info(
            "Classification complete — <2 People: %d  Multiple Subjects: %d  B-Roll: %d",
            few, crowd, broll,
        )
    else:
        # No classification: seed each clip with an empty shot_tags list so
        # xml_assembler labels them Rose (unclassified / review needed).
        log.info("Skipping Phase 3 — all clips will receive Rose label in XML.")
        for clip in clip_data:
            clip.setdefault("shot_tags", [])

    # ── Optional JSON export (includes shot_tags) ─────────────────────────────
    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        args.export_json.write_text(
            json.dumps(clip_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("Full analysis exported → %s", args.export_json)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4 — FCP7 XML Assembly
    # ─────────────────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 4 — FCP7 XML Assembly")
    log.info("=" * 60)

    try:
        written_path = assemble_xml(clip_data, output_path=output_xml)
    except ValueError as exc:
        log.error("XML assembly failed: %s", exc)
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────────────────
    # Pipeline Summary
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


if __name__ == "__main__":
    main()
