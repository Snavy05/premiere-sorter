"""
analysis.py — Phase 2: Stability Analysis
==========================================
Uses OpenCV Lucas-Kanade optical flow to detect the stable In-Point and
Out-Point of a proxy video clip. Entirely headless — no GUI calls.

Can be run standalone to test analysis in isolation:

    python analysis.py --proxy ./proxies/clip_proxy.mp4
    python analysis.py --proxy ./proxies/clip_proxy.mp4 --threshold 3.0 --stable-secs 1.5
"""

import json
import math
import logging
import argparse
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLD_PX  = 2.0    # Euclidean motion magnitude must stay below this …
DEFAULT_STABLE_SECS   = 1.0    # … for this many seconds to confirm a stable In-Point
DEFAULT_FPS           = 25.0   # Fallback when FPS is not readable from the file

FEATURE_MAX_CORNERS   = 200    # goodFeaturesToTrack — max corners to seed per frame
FEATURE_QUALITY       = 0.01   # Minimum corner quality ratio (0–1)
FEATURE_MIN_DIST      = 10.0   # Minimum pixel distance between detected corners
LK_WIN_SIZE           = (21, 21)  # Lucas-Kanade pyramid search window size
LK_MAX_LEVEL          = 3        # Pyramid depth for optical flow


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _seed_features(gray_frame: np.ndarray) -> np.ndarray | None:
    """
    Detect strong Shi-Tomasi corners in a greyscale frame.
    Returns an (N, 1, 2) float32 array of point coordinates, or None if the
    frame is too featureless (e.g. a black leader).
    """
    pts = cv2.goodFeaturesToTrack(
        gray_frame,
        maxCorners=FEATURE_MAX_CORNERS,
        qualityLevel=FEATURE_QUALITY,
        minDistance=FEATURE_MIN_DIST,
        blockSize=7,
    )
    return pts  # None when no corners found


def _optical_flow_translation(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    prev_pts:  np.ndarray,
) -> tuple[float, float, np.ndarray]:
    """
    Track `prev_pts` from `prev_gray` into `curr_gray` using pyramid
    Lucas-Kanade optical flow, then fit a partial affine transform to
    extract the dominant camera translation (dx, dy).

    The RANSAC inside estimateAffinePartial2D automatically discards
    independently-moving foreground objects, so only global camera motion
    contributes to the returned values.

    Returns:
        dx          — horizontal translation in pixels
        dy          — vertical translation in pixels
        surviving   — successfully tracked points in `curr_gray` (for chaining
                      into the next frame without re-seeding)
    """
    curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray, curr_gray, prev_pts, None,
        winSize=LK_WIN_SIZE,
        maxLevel=LK_MAX_LEVEL,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )

    if status is None:
        return 0.0, 0.0, prev_pts

    good_prev = prev_pts[status == 1]
    good_curr = curr_pts[status == 1]

    # Need at least 4 point pairs to fit a 2D affine transform
    if len(good_prev) < 4:
        return 0.0, 0.0, good_curr.reshape(-1, 1, 2) if len(good_curr) else prev_pts

    matrix, inliers = cv2.estimateAffinePartial2D(
        good_prev.reshape(-1, 1, 2),
        good_curr.reshape(-1, 1, 2),
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
    )

    if matrix is None:
        return 0.0, 0.0, good_curr.reshape(-1, 1, 2)

    dx = float(matrix[0, 2])   # column 2 = translation x
    dy = float(matrix[1, 2])   # column 2 = translation y

    # Carry forward only RANSAC inliers for next iteration
    if inliers is not None:
        surviving = good_curr[inliers.flatten().astype(bool)].reshape(-1, 1, 2)
    else:
        surviving = good_curr.reshape(-1, 1, 2)

    return dx, dy, surviving


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_stability(
    proxy_path:   Path,
    threshold_px: float = DEFAULT_THRESHOLD_PX,
    stable_secs:  float = DEFAULT_STABLE_SECS,
    fallback_fps: float = DEFAULT_FPS,
) -> dict | None:
    """
    Analyse a proxy video clip for camera stability and return In/Out points.

    Algorithm
    ---------
    1. Seed Shi-Tomasi features on frame 0.
    2. For every subsequent frame, track features and compute the affine
       translation (dx, dy) via Lucas-Kanade + RANSAC.
    3. Compute per-frame motion magnitude: m = sqrt(dx² + dy²).
    4. In-Point  = start of first run where m < threshold_px for
                   `stable_secs` consecutive seconds.
    5. Out-Point = first frame after In-Point where m ≥ threshold_px.
                   Defaults to the last frame if the clip never goes jittery
                   again.

    Parameters
    ----------
    proxy_path    : Path to the proxy MP4 file.
    threshold_px  : Motion magnitude (pixels) considered "stable".
    stable_secs   : Duration (seconds) of continuous stability required to
                    confirm an In-Point.
    fallback_fps  : FPS to use when the container metadata is missing.

    Returns
    -------
    A dict with keys:
        in_frame, out_frame  — 0-based frame indices
        in_tc, out_tc        — "HH:MM:SS:FF" timecodes
        fps, total_frames
    or None if no stable window is found or the file cannot be opened.
    """
    cap = cv2.VideoCapture(str(proxy_path))
    if not cap.isOpened():
        log.error("Cannot open: %s", proxy_path)
        return None

    fps           = cap.get(cv2.CAP_PROP_FPS) or fallback_fps
    total_frames  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    stable_needed = max(1, int(round(stable_secs * fps)))

    log.info("  %s  |  %.2f fps  |  %d frames  |  need %d stable frames",
             proxy_path.name, fps, total_frames, stable_needed)

    # --- Read frame 0 and seed feature points --------------------------------
    ret, frame0 = cap.read()
    if not ret:
        log.error("  Could not read first frame of %s", proxy_path.name)
        cap.release()
        return None

    prev_gray = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
    prev_pts  = _seed_features(prev_gray)

    if prev_pts is None or len(prev_pts) < 4:
        log.warning("  Too few features in first frame — skipping %s", proxy_path.name)
        cap.release()
        return None

    # --- Build per-frame motion magnitude list --------------------------------
    motion = [0.0]   # frame 0 has no predecessor; magnitude is 0 by definition

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        curr_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Re-seed tracker if it has dropped too few surviving points
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

    if len(motion) < stable_needed + 1:
        log.warning("  Clip too short to analyse: %s", proxy_path.name)
        return None

    # -------------------------------------------------------------------------
    # State machine: scan the ENTIRE clip and collect every stable window.
    # This prevents false positives from a brief pause at the very start of
    # the recording — we pick the longest window, not the first one found.
    # -------------------------------------------------------------------------

    # Each entry: {"start": int, "end": int, "duration": int}  (all in frames)
    stable_windows: list[dict] = []

    # Minimum frames a window must span to be kept (filters micro-pauses)
    min_window_frames = int(3.0 * fps)

    current_window_start = None   # None = currently outside a stable run

    for i, m in enumerate(motion):

        # --- Entering a stable zone ------------------------------------------
        if m < threshold_px and current_window_start is None:
            current_window_start = i

        # --- Leaving a stable zone (motion spike detected) -------------------
        elif m >= threshold_px and current_window_start is not None:
            end_frame = i - 1   # last quiet frame before the spike
            duration  = end_frame - current_window_start
            stable_windows.append({
                "start":    current_window_start,
                "end":      end_frame,
                "duration": duration,
            })
            log.debug("  Window closed at frame %d  (start=%d, duration=%d frames)",
                      end_frame, current_window_start, duration)
            current_window_start = None

    # --- Edge case: clip ended while still inside a stable window ------------
    if current_window_start is not None:
        end_frame = len(motion) - 1
        duration  = end_frame - current_window_start
        stable_windows.append({
            "start":    current_window_start,
            "end":      end_frame,
            "duration": duration,
        })
        log.debug("  Window closed at final frame %d  (start=%d, duration=%d frames)",
                  end_frame, current_window_start, duration)

    log.info("  Found %d raw stable window(s) before filtering.", len(stable_windows))

    # --- Filter out micro-pauses shorter than 3 seconds ----------------------
    stable_windows = [w for w in stable_windows if w["duration"] >= min_window_frames]

    if not stable_windows:
        log.warning("  No stable window >= 3 s found — clip entirely jittery: %s",
                    proxy_path.name)
        return None

    log.info("  %d window(s) remain after filtering (>= 3 s).", len(stable_windows))

    # --- Select the longest stable window ------------------------------------
    best = max(stable_windows, key=lambda w: w["duration"])
    in_frame  = best["start"]
    out_frame = best["end"]

    log.info("  Selected longest window: start=%d  end=%d  (%.1f s)",
             in_frame, out_frame, best["duration"] / fps)

    # --- Convert frame indices to HH:MM:SS:FF timecodes ----------------------
    def frame_to_tc(f: int, rate: float) -> str:
        total_secs = f / rate
        hh = int(total_secs // 3600)
        mm = int((total_secs % 3600) // 60)
        ss = int(total_secs % 60)
        ff = int(round((total_secs - int(total_secs)) * rate))
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    result = {
        "in_frame":     in_frame,
        "out_frame":    out_frame,
        "in_tc":        frame_to_tc(in_frame, fps),
        "out_tc":       frame_to_tc(out_frame, fps),
        "fps":          fps,
        "total_frames": total_frames,
    }

    log.info("  In=%s (frame %d)  Out=%s (frame %d)  |  stable: %ds",
             result["in_tc"], in_frame,
             result["out_tc"], out_frame,
             (out_frame - in_frame) / fps)

    return result


# ---------------------------------------------------------------------------
# Standalone entry-point for isolated testing
# ---------------------------------------------------------------------------
def _prompt_float(label: str, default: float, lo: float, hi: float) -> float:
    """
    Prompt for a float value, validating it falls within [lo, hi].
    Pressing Enter accepts the default.
    """
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-8s  %(message)s",
                        datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(description="Phase 2 — Stability Analysis")
    parser.add_argument("--proxy",       type=Path,  default=None,
                        help="Path to a single proxy .mp4 file OR a folder of proxies")
    parser.add_argument("--raw-dir",     type=Path,  default=None,
                        help="Original raw footage folder (to resolve source paths for XML)")
    parser.add_argument("--export-json", type=Path,  default=None,
                        metavar="FILE",
                        help="Export results to a JSON file for xml_builder.py")
    parser.add_argument("--threshold",   type=float, default=None,
                        metavar="PIXELS",
                        help=f"Motion threshold in pixels (default: {DEFAULT_THRESHOLD_PX})")
    parser.add_argument("--stable-secs", type=float, default=None,
                        help=f"Seconds of stability required (default: {DEFAULT_STABLE_SECS})")
    parser.add_argument("--fps",         type=float, default=None,
                        help=f"Fallback FPS if unreadable (default: {DEFAULT_FPS})")
    args = parser.parse_args()

    # ── Input: single file or folder ─────────────────────────────────────────
    if not args.proxy:
        print()
        print("  Analyse a single proxy file or an entire folder?")
        print("  [1] Single file")
        print("  [2] Folder of proxies")
        while True:
            mode = input("  Choice [1/2]: ").strip()
            if mode in ("1", "2"):
                break
            print("  Please enter 1 or 2.")

        print()
        if mode == "1":
            while True:
                raw = input("  Proxy file to analyse [proxies/clip_proxy.mp4]: ").strip()
                args.proxy = Path(raw) if raw else Path("proxies/clip_proxy.mp4")
                if args.proxy.exists() and args.proxy.is_file():
                    break
                print(f"  File not found: {args.proxy}  — please try again.")
        else:
            while True:
                raw = input("  Proxy folder [proxies]: ").strip()
                args.proxy = Path(raw) if raw else Path("proxies")
                if args.proxy.exists() and args.proxy.is_dir():
                    break
                print(f"  Folder not found: {args.proxy}  — please try again.")

    # ── Resolve to a list of proxy files to process ──────────────────────────
    if args.proxy.is_dir():
        proxy_files = sorted(args.proxy.glob("*.mp4"))
        if not proxy_files:
            print(f"\n  No .mp4 files found in {args.proxy}. Exiting.")
            raise SystemExit(1)
        print(f"\n  Found {len(proxy_files)} proxy file(s) in '{args.proxy}'.")
    else:
        proxy_files = [args.proxy]

    # ── Tuning parameters (prompt only for values not passed as CLI flags) ───
    print()
    print("  ── Stability tuning (Enter to keep defaults) ──")
    print("  Tip: lower threshold = stricter stability, higher = more tolerant")
    print()

    if args.threshold is None:
        args.threshold = _prompt_float(
            label   = "Motion threshold px  (recommended: 1.0 – 5.0)",
            default = DEFAULT_THRESHOLD_PX,
            lo      = 0.1,
            hi      = 50.0,
        )

    if args.stable_secs is None:
        args.stable_secs = _prompt_float(
            label   = "Stable seconds needed (recommended: 0.5 – 3.0)",
            default = DEFAULT_STABLE_SECS,
            lo      = 0.1,
            hi      = 30.0,
        )

    if args.fps is None:
        args.fps = _prompt_float(
            label   = "Fallback FPS          (e.g. 24, 25, 29.97, 30)",
            default = DEFAULT_FPS,
            lo      = 1.0,
            hi      = 240.0,
        )

    print()
    print(f"  Running with: threshold={args.threshold} px  |  "
          f"stable={args.stable_secs} s  |  fps={args.fps}")
    print()

    # ── Resolve raw footage directory (for src_path in export) ────────────────
    raw_dir = args.raw_dir
    if not raw_dir:
        print()
        raw = input("  Raw footage folder (to link originals in XML) [skip]: ").strip()
        if raw:
            p = Path(raw)
            if p.is_dir():
                raw_dir = p
            else:
                print(f"  ⚠ Folder not found: {p} — will use proxy paths instead.")

    # ── Run analysis over every file and print a summary table ───────────────
    results  = []
    skipped  = []

    # Pre-index raw files by stem for fast lookup
    raw_files_by_stem: dict[str, Path] = {}
    if raw_dir:
        for f in raw_dir.iterdir():
            if f.is_file() and not f.name.startswith("._"):
                raw_files_by_stem[f.stem] = f

    def _resolve_raw_path(proxy_path: Path) -> tuple[str, str]:
        """
        Given a proxy file path, return (clean_name, original_src_path).
        Strips '_proxy' suffix (case-insensitive) to find the original.
        """
        stem = proxy_path.stem

        # Strip proxy suffixes:  _proxy, _Proxy, _PROXY
        clean = stem
        if clean.lower().endswith("_proxy"):
            clean = clean[:-6]

        if raw_dir:
            # Try exact stem match first (cleaned name)
            if clean in raw_files_by_stem:
                return clean, str(raw_files_by_stem[clean].resolve())
            # Try original stem (in case raw files also have _Proxy in name)
            if stem in raw_files_by_stem:
                return clean, str(raw_files_by_stem[stem].resolve())
            # Fallback: best-effort path construction
            log.warning("  Could not find raw file for '%s' in %s", stem, raw_dir)
            return clean, str((raw_dir / proxy_path.name).resolve())
        else:
            return clean, str(proxy_path.resolve())

    for proxy_path in proxy_files:
        log.info("Analysing: %s", proxy_path.name)
        result = analyze_stability(
            proxy_path,
            threshold_px=args.threshold,
            stable_secs=args.stable_secs,
            fallback_fps=args.fps,
        )
        if result:
            name, src_path = _resolve_raw_path(proxy_path)
            result["name"]     = name
            result["src_path"] = src_path
            results.append(result)
        else:
            skipped.append(proxy_path.name)

    # ── Summary table ─────────────────────────────────────────────────────────
    print()
    print(f"  {'Clip':<35} {'In':<14} {'Out':<14} {'Duration':>10}")
    print("  " + "-" * 75)
    for r in results:
        dur_secs = (r["out_frame"] - r["in_frame"]) / r["fps"]
        print(f"  {r['name']:<35} {r['in_tc']:<14} {r['out_tc']:<14} {dur_secs:>8.1f}s")

    if skipped:
        print()
        print(f"  Skipped ({len(skipped)} clip(s) — no stable window found):")
        for name in skipped:
            print(f"    • {name}")

    if not results:
        print("  No stable windows found in any clip.")
        print("  Try raising --threshold or lowering --stable-secs.")

    # ── Export to JSON ────────────────────────────────────────────────────────
    export_path = args.export_json
    if results and export_path is None:
        print()
        raw = input("  Export results to JSON? [analysis_results.json] (Enter to skip): ").strip()
        if raw:
            export_path = Path(raw)
        elif raw == "":
            # User pressed Enter — use default
            export_path = Path("analysis_results.json")

    if results and export_path:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n  ✓ Results exported → {export_path}")
        print(f"    Feed into xml_builder:  python xml_builder.py --json {export_path}")