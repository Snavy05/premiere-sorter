#!/usr/bin/env python3
"""
run_adaptive_batch.py — local A/B driver: re-run the Hoang field-test batch with
adaptive thresholding ON (the feature is unreachable from the CLI; main() never
passes `adaptive`). Reuses the existing proxies so only the threshold mode
differs from the user's fixed-1.5px run.

Also writes a run-settings sidecar JSON (poor-man's punchlist F6) so this run is
machine-comparable later.
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path("/Users/mac/Documents/premiere-sorter")
os.chdir(REPO)              # yolov8n.pt + relative paths resolve from repo root
sys.path.insert(0, str(REPO))

import steadycut_pipeline as scp

STEADY   = Path("/Users/mac/Downloads/STEADY")
INPUT    = STEADY
PROXIES  = STEADY / "proxies"
OUTDIR   = STEADY / "v1.2.1"

SENSITIVITY = 3.0  # median + k*MAD; 3.0 gave 8635 -> 68% coverage in the probe

settings = dict(
    input_dir=str(INPUT),
    proxy_dir=str(PROXIES),
    skip_proxies=True,
    adaptive=True,
    sensitivity=SENSITIVITY,
    multi_shot=True,
    keep_failed_clips=True,
    stable_secs=scp.DEFAULT_STABLE_SECS,
    note="A/B vs user's fixed-1.5px run; same proxies reused",
)

out_xml  = OUTDIR / f"adaptive_s{SENSITIVITY:g}.xml"
out_json = OUTDIR / f"adaptive_s{SENSITIVITY:g}_analysis.json"
sidecar  = OUTDIR / f"adaptive_s{SENSITIVITY:g}_runsettings.json"

t0 = time.time()
scp.run_pipeline(
    input_dir=INPUT,
    output_xml=out_xml,
    proxy_dir=PROXIES,
    skip_proxies=True,
    adaptive=True,
    sensitivity=SENSITIVITY,
    export_json=out_json,
)
elapsed = time.time() - t0

sidecar.write_text(json.dumps({
    "timestamp": datetime.now().isoformat(timespec="seconds"),
    "elapsed_secs": round(elapsed, 1),
    "settings": settings,
    "output_xml": str(out_xml),
    "analysis_json": str(out_json),
}, indent=2))
print(f"\n=== DONE in {elapsed:.0f}s ===")
print(f"xml:     {out_xml}")
print(f"sidecar: {sidecar}")
