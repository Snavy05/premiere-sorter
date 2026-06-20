#!/usr/bin/env python3
"""
eval_windows.py — triage grader for SteadyCut's stable-window engine.

This is a DEV TOOL, not part of the shipped app. It answers one question:
"is the engine good enough to trust?" — measured the way that actually matters
for footage triage, NOT frame-perfect auto-editing (which is impossible: the
keeper take lives in the shooter's head, not the pixels).

It compares the engine's detected windows against YOUR ground-truth keeper picks
and prints a scorecard built on three triage metrics:

  1. KEEPER RECALL   did a detection overlap your shot at all? (missing a keeper
                     is the worst sin — you lose good footage)
  2. SCRUB DISTANCE  how far from your shot's true start does the cursor land?
                     (small = you nudge; large = you scrub from zero anyway)
  3. JUNK RATE       detections that overlap NO keeper = clutter you delete

IoU (exact-boundary match) is reported too, but only as secondary info. A low
IoU with good recall + short scrub still means the tool saved you time.

USAGE
  python3 scripts/eval_windows.py --labels /path/to/labels.csv \
          --proxy-dir /path/to/proxies [--direction both] [--cover 0.3]

LABELS FILE (CSV, kept OUTSIDE the repo — it's your private footage truth):
  clip,in,out          <- one row per keeper window; clip may repeat
  dv8570,20,175
  dv8600,0,345
  dv8600,647,740
  # lines starting with # are ignored

  Proxy for a clip is found at:  {proxy-dir}/{pattern}  where {pattern}
  defaults to "Hoang.{clip}_Proxy.mp4" (override with --pattern).

Nothing here is imported back into the app. The optional --csv output is just a
run log so you can compare batches over time.
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from pipelinev3 import (  # noqa: E402
    compute_motion, find_stable_windows, segment_shots,
)

# Mirror the program's recovery sweep: start strict, relax until windows appear.
BASE_THR = 2.0
MAX_THR = 8.0
STABLE_SECS = 1.0


def _norm_clip(clip):
    """Normalise a clip id to match the proxy filename: bare digits -> dvNNNN."""
    clip = clip.strip()
    return f"dv{clip}" if clip.isdigit() else clip


def load_labels(path):
    """
    clip -> [(in, out), ...] from a CSV. Uses real CSV parsing so quoted notes
    with commas are safe. Ignores '#' comment lines, a header row, and any
    columns past in/out (e.g. a notes column). Bare-digit clip ids get a 'dv'
    prefix; duplicate (clip,in,out) rows are de-duped.
    """
    gt = defaultdict(list)
    seen = set()
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                continue
            if row[0].strip().lower() in ("clip", "name"):  # header
                continue
            if len(row) < 3 or not row[1].strip() or not row[2].strip():
                print(f"  ! skipping malformed label row: {row!r}")
                continue
            clip = _norm_clip(row[0])
            try:
                a, b = int(row[1]), int(row[2])
            except ValueError:
                print(f"  ! skipping non-numeric in/out: {row!r}")
                continue
            key = (clip, a, b)
            if key in seen:
                continue
            seen.add(key)
            gt[clip].append((a, b))
    return dict(gt)


def sweep_windows(motion, fps, total, direction):
    """Start strict, relax +0.1px until windows appear (mirrors the app)."""
    thr = BASE_THR
    while thr <= MAX_THR + 1e-9:
        w = find_stable_windows(motion, fps, total, threshold_px=round(thr, 1),
                                stable_secs=STABLE_SECS, direction=direction)
        if w:
            return [(d["in_frame"], d["out_frame"]) for d in w], round(thr, 1)
        thr += 0.1
    return [], round(thr, 1)


def build_app_windows(motion, fps, total, coherence, direction):
    """
    Mirror the SHIPPED pipeline's per-clip window set so we grade what a tester
    actually gets, not just one component:

      primary stable windows (recovery sweep, find_stable_windows)
        UNION
      uncovered shots from segment_shots (A1 multi-shot coverage)

    A segment_shots span is added only when no primary window overlaps it —
    exactly the pipeline's rule (steadycut_pipeline.py ~930-941). head/tail trim
    default to 0 in the app, so no boundary shift is applied here.
    """
    primary, thr = sweep_windows(motion, fps, total, direction)
    windows = list(primary)
    shots = segment_shots(motion, fps, coherence=coherence, direction=direction)
    if len(shots) > 1:  # single-shot files are handled by the window finder
        for s_start, s_end in shots:
            if any(w_in <= s_end and w_out >= s_start for w_in, w_out in primary):
                continue
            windows.append((s_start, s_end))
    return windows, thr


def overlap(a, b):
    lo = max(a[0], b[0]); hi = min(a[1], b[1])
    return max(0, hi - lo)


def iou(a, b):
    inter = overlap(a, b)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def grade(detected, gt, cover_thr):
    """
    Triage grading. For each keeper (GT) window find its best-overlapping
    detection. A keeper is RECALLED if a detection covers >= cover_thr of it.
    Returns per-keeper rows + the set of detections that matched something.
    """
    rows = []
    used = set()
    for g in gt:
        glen = max(1, g[1] - g[0])
        best_i, best_ov, best_iou = -1, 0, 0.0
        for k, d in enumerate(detected):
            ov = overlap(d, g)
            if ov > best_ov:
                best_ov, best_i, best_iou = ov, k, iou(d, g)
        covered = best_ov / glen
        recalled = covered >= cover_thr
        if recalled and best_i >= 0:
            used.add(best_i)
        det = detected[best_i] if best_i >= 0 else None
        scrub = abs(det[0] - g[0]) if det else None  # cursor-vs-true-start, frames
        rows.append({"gt": g, "det": det, "covered": covered,
                     "recalled": recalled, "iou": best_iou, "scrub": scrub})
    junk = [d for k, d in enumerate(detected) if k not in used]
    return rows, junk


def fmt(w):
    return f"{w[0]}-{w[1]}" if w else "--"


def _find_proxy(clip, proxy_dirs, pattern):
    """Return the first existing proxy for clip across all given dirs."""
    for d in proxy_dirs:
        p = d / pattern.format(clip=clip)
        if p.exists():
            return p
    return None


def run(gt_all, proxy_dirs, pattern, directions, cover_thr, csv_out):
    log_rows = []
    # agg[direction] -> dict of running totals
    agg = {d: {"keepers": 0, "recalled": 0, "scrub": [], "iou": [],
               "junk": 0, "dets": 0} for d in directions}

    for clip, gt in gt_all.items():
        proxy = _find_proxy(clip, proxy_dirs, pattern)
        if proxy is None:
            print(f"\n{clip}: proxy not found in any --proxy-dir — skipping")
            continue
        res = compute_motion(proxy)
        if res is None:
            print(f"\n{clip}: motion analysis FAILED — skipping")
            continue
        motion, fps, total, coherence, direction = res

        print(f"\n========== {clip}   fps={fps:.0f}  frames={total} ==========")
        print(f"  KEEPERS ({len(gt)}): {', '.join(fmt(g) for g in gt)}")

        for dlabel in directions:
            dir_arg = direction if dlabel == "on" else None
            det, thr = build_app_windows(motion, fps, total, coherence, dir_arg)
            rows, junk = grade(det, gt, cover_thr)

            a = agg[dlabel]
            a["keepers"] += len(gt)
            a["recalled"] += sum(1 for r in rows if r["recalled"])
            a["junk"] += len(junk)
            a["dets"] += len(det)
            a["scrub"] += [r["scrub"] for r in rows if r["recalled"]]
            a["iou"] += [r["iou"] for r in rows if r["recalled"]]

            tag = "direction ON " if dlabel == "on" else "direction OFF"
            print(f"\n  {tag}  @thr={thr}px  ->  {len(det)} window(s): "
                  f"{', '.join(fmt(d) for d in det) or '(none)'}")
            for r in rows:
                mark = "RECALL" if r["recalled"] else " MISS "
                scrub = f"{r['scrub']}f" if r["scrub"] is not None else "  --"
                print(f"      [{mark}] keeper {fmt(r['gt']):>11} ~ det "
                      f"{fmt(r['det']):>11}  cover {r['covered']:.0%}"
                      f"  scrub {scrub:>6}  IoU {r['iou']:.2f}")
            if junk:
                print(f"      junk (overlaps no keeper): "
                      f"{', '.join(fmt(d) for d in junk)}")

            junk_set = set(junk)
            for d in det:
                log_rows.append({"clip": clip, "direction": dlabel,
                                 "threshold": thr, "det_in": d[0],
                                 "det_out": d[1],
                                 "hits_keeper": d not in junk_set})

    # ---- aggregate scorecard ----
    print("\n" + "=" * 60)
    print(f"TRIAGE SCORECARD   ({len(gt_all)} clips, cover>={cover_thr:.0%})")
    print("=" * 60)
    for dlabel in directions:
        a = agg[dlabel]
        if a["keepers"] == 0:
            continue
        recall = a["recalled"] / a["keepers"]
        med_scrub = _median(a["scrub"])
        med_iou = _median(a["iou"])
        junk_rate = a["junk"] / a["dets"] if a["dets"] else 0.0
        tag = "direction ON " if dlabel == "on" else "direction OFF"
        print(f"\n  {tag}")
        print(f"    keeper recall   {a['recalled']}/{a['keepers']}  ({recall:.0%})"
              "   <- did it find your shot at all")
        print(f"    median scrub    {med_scrub}f"
              "   <- frames from true start the cursor lands")
        print(f"    junk rate       {a['junk']}/{a['dets']}  ({junk_rate:.0%})"
              "   <- detections that hit no keeper")
        print(f"    median IoU      {med_iou:.2f}"
              "   (secondary — exact-boundary match)")

    if csv_out:
        with open(csv_out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["clip", "direction", "threshold",
                                              "det_in", "det_out", "hits_keeper"])
            w.writeheader(); w.writerows(log_rows)
        print(f"\nrun log -> {csv_out}")


def _median(xs):
    if not xs:
        return 0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    v = (s[mid - 1] + s[mid]) / 2
    return round(v, 2)


def main():
    p = argparse.ArgumentParser(description="Triage grader for SteadyCut windows.")
    p.add_argument("--labels", required=True, help="CSV of keeper windows (clip,in,out)")
    p.add_argument("--proxy-dir", required=True, action="append",
                   help="folder holding the proxies (repeat to search several)")
    p.add_argument("--pattern", default="Hoang.{clip}_Proxy.mp4",
                   help="proxy filename pattern; {clip} substituted")
    p.add_argument("--direction", choices=["on", "off", "both"], default="both",
                   help="grade with the v1.2.0 direction signal on, off, or both")
    p.add_argument("--cover", type=float, default=0.3,
                   help="min fraction of a keeper a detection must cover to count "
                        "as recalled (default 0.3)")
    p.add_argument("--csv", default=None, help="optional run-log CSV output path")
    args = p.parse_args()

    gt_all = load_labels(args.labels)
    if not gt_all:
        print("No labels loaded — check the file.")
        sys.exit(1)

    directions = ["off", "on"] if args.direction == "both" else [args.direction]
    proxy_dirs = [Path(d) for d in args.proxy_dir]
    run(gt_all, proxy_dirs, args.pattern, directions, args.cover, args.csv)


if __name__ == "__main__":
    main()
