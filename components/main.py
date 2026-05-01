"""
main.py — Pipeline Orchestrator
=================================
Wires the three phases together. Contains no logic of its own — edit
the individual modules to change behaviour:

    proxy.py        Phase 1 — FFmpeg proxy generation
    analysis.py     Phase 2 — OpenCV stability analysis
    xml_builder.py  Phase 3 — FCP7 XML construction

Usage:
    python main.py                       # interactive prompts for all folders
    python main.py --input DIR ...       # skip prompts by passing flags directly
"""

import sys
import logging
import argparse
from pathlib import Path

from proxy       import generate_proxies
from analysis    import analyze_stability, DEFAULT_THRESHOLD_PX, DEFAULT_STABLE_SECS, DEFAULT_FPS
from xml_builder import build_fcp7_xml, save_xml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def prompt_for_path(label: str, default: Path, must_exist: bool = False) -> Path:
    """
    Interactively ask the user to type a folder/file path.
    - Pressing Enter accepts the `default`.
    - If `must_exist` is True, the loop keeps asking until the path exists.
    """
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        chosen = Path(raw) if raw else default

        if must_exist and not chosen.exists():
            print(f"  ✗ Path not found: {chosen}  — please try again.")
            continue

        return chosen


def prompt_for_folders() -> dict:
    """
    Interactive startup wizard. Runs only when no CLI flags are supplied.
    Returns a dict of resolved config values ready to pass to the pipeline.
    """
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║   Headless Video Stabilisation Pipeline      ║")
    print("╚══════════════════════════════════════════════╝")
    print("  Press Enter to accept the [default] for each prompt.\n")

    input_dir  = prompt_for_path("Raw footage folder ", Path("input"),        must_exist=True)
    proxy_dir  = prompt_for_path("Proxy output folder", Path("proxies"),      must_exist=False)
    output_xml = prompt_for_path("Output XML path    ", Path("sequence.xml"), must_exist=False)

    print()
    print("  ── Advanced settings (Enter to keep defaults) ──")
    raw_thresh = input(f"  Motion threshold px    [{DEFAULT_THRESHOLD_PX}]: ").strip()
    raw_secs   = input(f"  Stable seconds needed  [{DEFAULT_STABLE_SECS}]: ").strip()
    raw_fps    = input(f"  Fallback FPS           [{DEFAULT_FPS}]: ").strip()
    print()

    return {
        "input":       input_dir,
        "proxies":     proxy_dir,
        "output":      output_xml,
        "threshold":   float(raw_thresh) if raw_thresh else DEFAULT_THRESHOLD_PX,
        "stable_secs": float(raw_secs)   if raw_secs   else DEFAULT_STABLE_SECS,
        "fps":         float(raw_fps)    if raw_fps     else DEFAULT_FPS,
        "debug":       False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Headless video stabilisation pipeline → FCP7 XML",
        # Don't show defaults — the interactive wizard handles the no-arg case
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input",       type=Path,  default=None,
                        help="Raw video input directory")
    parser.add_argument("--proxies",     type=Path,  default=None,
                        help="Proxy output directory")
    parser.add_argument("--output",      type=Path,  default=None,
                        help="Output XML path")
    parser.add_argument("--threshold",   type=float, default=None,
                        metavar="PIXELS",
                        help="Motion threshold in pixels")
    parser.add_argument("--stable-secs", type=float, default=None,
                        dest="stable_secs",
                        help="Stable seconds to confirm In-Point")
    parser.add_argument("--fps",         type=float, default=None,
                        help="Fallback FPS when unreadable from file")
    parser.add_argument("--debug",       action="store_true",
                        help="Enable DEBUG-level logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ── If no folder arguments were passed, run the interactive wizard ────────
    cli_supplied = any([args.input, args.proxies, args.output])
    if not cli_supplied:
        cfg = prompt_for_folders()
    else:
        # Fill any missing CLI args with their defaults silently
        cfg = {
            "input":       args.input       or Path("input"),
            "proxies":     args.proxies     or Path("proxies"),
            "output":      args.output      or Path("sequence.xml"),
            "threshold":   args.threshold   or DEFAULT_THRESHOLD_PX,
            "stable_secs": args.stable_secs or DEFAULT_STABLE_SECS,
            "fps":         args.fps         or DEFAULT_FPS,
            "debug":       args.debug,
        }

    if cfg["debug"]:
        logging.getLogger().setLevel(logging.DEBUG)

    # Confirm what we're about to run
    print("  Running with:")
    print(f"    Input folder  : {cfg['input']}")
    print(f"    Proxy folder  : {cfg['proxies']}")
    print(f"    Output XML    : {cfg['output']}")
    print(f"    Threshold     : {cfg['threshold']} px")
    print(f"    Stable window : {cfg['stable_secs']} s")
    print(f"    Fallback FPS  : {cfg['fps']}")
    print()

    # =========================================================================
    # PHASE 1 — Proxy Generation
    # =========================================================================
    log.info("=" * 60)
    log.info("PHASE 1 — Proxy Generation")
    log.info("=" * 60)

    proxy_map = generate_proxies(cfg["input"], cfg["proxies"])
    if not proxy_map:
        log.error("No proxies generated — aborting.")
        sys.exit(1)

    # =========================================================================
    # PHASE 2 — Stability Analysis
    # =========================================================================
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 2 — Stability Analysis")
    log.info("=" * 60)

    clip_data: list[dict] = []
    skipped:   list[str]  = []

    for raw_path, proxy_path in proxy_map.items():
        log.info("[analysis] %s", raw_path.name)
        result = analyze_stability(
            proxy_path,
            threshold_px=cfg["threshold"],
            stable_secs=cfg["stable_secs"],
            fallback_fps=cfg["fps"],
        )

        if result is None:
            log.warning("  → Skipped (no stable window): %s", raw_path.name)
            skipped.append(raw_path.name)
            continue

        clip_data.append({
            "name":         raw_path.stem,
            "src_path":     str(raw_path.resolve()),
            "in_frame":     result["in_frame"],
            "out_frame":    result["out_frame"],
            "fps":          result["fps"],
            "total_frames": result["total_frames"],
            "in_tc":        result["in_tc"],
            "out_tc":       result["out_tc"],
        })

    if skipped:
        log.warning("\nSkipped %d clip(s): %s", len(skipped), ", ".join(skipped))

    if not clip_data:
        log.error("No usable clips after analysis — aborting XML generation.")
        sys.exit(1)

    # =========================================================================
    # PHASE 3 — FCP7 XML Generation
    # =========================================================================
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 3 — FCP7 XML Generation")
    log.info("=" * 60)

    # Use the most common FPS as the sequence timebase
    fps_counts: dict[float, int] = {}
    for c in clip_data:
        fps_counts[c["fps"]] = fps_counts.get(c["fps"], 0) + 1
    sequence_fps = max(fps_counts, key=fps_counts.get)

    xml_string = build_fcp7_xml(clip_data, fps=sequence_fps)
    save_xml(xml_string, cfg["output"])

    # =========================================================================
    # Summary
    # =========================================================================
    log.info("")
    log.info("=" * 60)
    log.info("Pipeline complete!")
    log.info("  Clips processed : %d", len(clip_data))
    log.info("  Clips skipped   : %d", len(skipped))
    log.info("  Output XML      : %s", cfg["output"])
    log.info("=" * 60)

    print(f"\n{'Clip':<35} {'In':<14} {'Out':<14} {'Duration':>10}")
    print("-" * 75)
    for c in clip_data:
        dur_secs = (c["out_frame"] - c["in_frame"]) / c["fps"]
        print(f"{c['name']:<35} {c['in_tc']:<14} {c['out_tc']:<14} {dur_secs:>8.1f}s")


if __name__ == "__main__":
    main()
