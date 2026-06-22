# SteadyCut — Punch List

Source: tester feedback from **Hoàng** (event videographer, 17 Jun 2026) + run logs in
`SteadyCut_Feedback/`. Hardware: a7S III on RS4 Pro gimbal, 4K60 10-bit, M1 Pro 16GB.

Tester verdict: **"Tiếp tục, có lực kéo"** (continue, has traction) · **"rất cần cực cần"**
(strong demand) · willing to pay **$50 one-time** with fixes + updates + warranty.

---

## Boot work-stream (BLOCKERS — app does not run for end users)

These four bugs form one death spiral: ffmpeg never installs cleanly, the app trusts a
broken binary, every transcode fails, and the UI reports the wrong cause.

### B1 — macOS ffmpeg auto-download is broken by design
- **Symptom:** Red banner — `FFmpeg setup failed: BCJ2 filter is not supported by py7zr`.
- **Cause:** `ffmpeg_helper.py:127` downloads from `evermeet.cx/.../7z`. Those archives use
  the BCJ2 filter, which `py7zr` cannot decode → `extractall()` throws. Download path fails
  100% of the time on macOS.
- **Fix:** Download the `zip` variant and extract with stdlib `zipfile` (already used on
  Windows). Drop `py7zr` entirely.

### B2 — cache trusts a non-executable binary forever
- **Symptom:** `[Errno 13] Permission denied: '.../SteadyCut/bin/ffmpeg'` on every clip.
- **Cause:** `_find_in_cache()` checks only `.exists()`, never executability.
  `_make_executable()` runs only in the download path, never on cache hits or
  copy-from-PATH. A non-`+x` binary in `bin/` is returned forever. `is_ffmpeg_available()`
  inherits the bug → app declares ffmpeg "ready" then dies.
- **Fix:** Validate by actually running `<bin> -version`; `chmod +x` on every resolve;
  self-heal (re-download) if a cached binary fails validation.

### B3 — ffmpeg should ship inside the app
- **Cause:** Runtime download is fragile (network, mirrors, archive formats, Gatekeeper).
- **Fix:** Bundle static `ffmpeg`/`ffprobe` into the build; resolver prefers the bundled
  copy (`sys._MEIPASS/bin`). Runtime download stays as fallback only.

### B4 — pipeline reports "No clips" when the real error is ffmpeg
- **Symptom:** "No clips available after Phase 1" with a folder full of clips.
- **Cause:** All proxy jobs fail (`PermissionError`), swallowed as `WARNING`
  (`pipelinev3.py:316`); `generate_proxy` only catches `FileNotFoundError` (`:257`).
  Then `steadycut_pipeline.py:360` raises the generic "No clips" message.
- **Fix:** Catch `PermissionError`/`OSError` explicitly; abort message must distinguish
  "0 files found" from "N files found, all failed to transcode — check FFmpeg".

### B5 — packaged .app vanishes on first boot with no logs
- **Symptom:** App window flashes and disappears; nothing written to disk.
- **Cause:** Crash before logging is initialized (likely a missing bundled dep). Log
  `151836` shows one `starting` line then nothing.
- **Fix:** Install `faulthandler` + `sys.excepthook` that writes a traceback to a crash
  file before UI init, so first-boot failures are always diagnosable.

---

## Accuracy work-stream (CORE — converts "maybe" to "yes")

Tester: accuracy ~50% across two batches; half of 60 clips off.

### A1 — multi-shot files not split *(highest impact)*
One source file often holds several camera shots. App extracts only 1–2 and ignores the
rest. Needs per-file shot-boundary detection, emitting every shot as its own clip.

### A2 — only one stable window picked per clip
Long clips have many stable regions; app picks a single one (usually the middle). Should
surface all qualifying stable windows.

### A3 — over-trims head and tail
Cuts too aggressively at both ends of the usable region.

### A4 — full moving shots mishandled
A continuous gimbal move gets cut mid-move and is not flagged as a "full shot".

---

## Tuning work-stream (open experiments)

### T1 — adaptive sensitivity sweep: pick batch-optimal k *(CLOSED — winner k=3.0)*
**Decision (2026-06-22):** sweep closed, **default locked at k=3.0**. k=2.5 and k=2.0 triaged
in Premiere and rejected. The eval scorecard *preferred* 2.5 (junk 2% vs 9%, usable-after-trim
98% vs 91%) but that metric **undercounts the real failures** — it tags over-merge and
cut-on-action clips as V3 "boundaries" (near-success) when they're structurally wrong. Human
triage is ground truth: at k=2.5, 8580 merged everything into 1 window, 8635 merged all the way
through (user wants stable-window cuts, not one blob), and 8619/20/21 cut *on* the action and
lost the shot. User satisfied with k=3.0. The pain points below are **not k-tunable** in the
helpful direction — logged as separate work:
  - **Over-merge of high-motion clips (8580, 8635):** threshold swallows the whole motion range
    → one window. Fix = per-clip **threshold cap** or forced internal-shot split, NOT lower k
    (lower k splits more but in the wrong places → V3 boundaries rose 51%→59%). See DEV-PLAN §3.
  - **Cut-on-action pans (8619/20/21):** intent-level shot-preservation problem = **A4** (full
    moving shots mishandled), a separate algorithm, not a threshold knob.
- **Possible later probe:** k=2.75/2.8 is a valid float but interpolates *toward* the rejected
  2.5 failures, away from the satisfying 3.0 — low odds, costs a triage cycle. Not before launch.

**Sweep record (historical):**
- **What:** `adaptive_threshold = median + k·MAD`. s3.0 was a net win (junk 17%→9%) but too
  loose on motion-heavy clips. Bracketed by re-running the Hoàng batch (same proxies) at
  lower k to find the floor. CLI-unreachable; driven via `for_claude_sessions/run_adaptive_batch.py <k>`.
- **Artifacts to triage** (`/Users/mac/Downloads/STEADY/v1.2.1/`, import → triage tracks
  V1 usable / V2 junk / V3 boundaries → `scripts/eval_xml.py`):
  - `ADAPTIVE FCP7.xml` — **k=3.0**, baseline, already triaged (junk 9%, usable-after-trim 91%).
  - `adaptive_s2.5.xml` — **k=2.5**, candidate. Fixed 8580 over-merge (1→3 windows), kept 8635
    (thr 9.82px, w1=20.5s). 8603 still merged (low-MAD, k is wrong knob).
  - `adaptive_s2.0.xml` — **k=2.0**, floor probe. 232 clips (vs 216 @2.5). 8635 thr 8.71px,
    did NOT collapse (w1=11.8s+w7=8.4s) → floor not reached at 2.0. 8603 finally split
    (w1+w2). Pan cluster (8619-21) ~same as 2.5. More splitting overall = triage must check
    if extra windows are real or junk slivers.
- **Threshold deltas (px), s3.0 → s2.5 → s2.0:** 8580 10.4→9.60→8.78 · 8635 10.93→9.82→8.71 ·
  8603 4.6→4.29→3.96 · 8619 5.6→5.01→4.42 · 8620 3.1→2.77→2.44 · 8621 2.8→2.46→2.15 ·
  8602 —→1.92→1.70.
- **Decision rule:** lowest k that holds junk ≤9% AND recovers multi-windowing. 8635 survives
  all three (robust), so the deciding axis is junk-rate: does the extra splitting at lower k
  add usable windows or junk slivers? If 2.0 junk > 2.5 junk, pick 2.5. Don't probe below 2.0
  (walks back into fixed-threshold junk regime).
- **Done:** winning k=3.0 is the `--sensitivity` default (CLI + UI both wired, commit b8ab74e).

---

## Feature work-stream

### F1 — stereo: one track, two channels
Currently exports **two** audio tracks. Should be a single stereo track (L/R) on the
timeline. Prior fix attempt failed; lives in `xml_assembler.py`. Needs the exact
Premiere/FCP7 XML shape for a 2-channel linked clip.

### F2 — keep failed clips in the timeline, color-coded
Don't silently drop fails. Leave them on the timeline with a distinct label colour so the
editor can redo them by hand.

### F3 — multi-shot color grouping
When one source splits into N pieces, give all N the same label colour so siblings are
recognizable.

### F4 — people labeler
Classify by head-count (0 / 1 / many, or pick exact N) via a dropdown, colour per choice.

### F5 — version label at top of UI is stale/hardcoded
- **Symptom:** Top of app shows `v2` — not the real release (e.g. `v1.2.1-beta`), so a
  tester can't tell which build they're running.
- **Fix:** Source the label from the actual version string (single source of truth — git
  tag / `__version__`) and render it at the top so the running version is always visible.

### F6 — export per-run settings + results to a sidecar file
- **Goal:** After each run, dump a reference file (JSON or XML) capturing the full run
  config + results, so runs can be compared for performance over time.
- **Capture:** app version, timestamp, all settings/params used (e.g. `merge_gap_frames`,
  direction on/off, thresholds), input batch (file count/names), and per-run results
  (clips emitted, windows, timings, fail counts). Enough to reproduce + benchmark.
- **Why:** field-test loop currently re-derives numbers by hand; a machine-readable
  sidecar per run makes A/B comparison across versions trivial.

---

## Working positives (do not regress)
- Speed: 12m48s for 50 clips — "nhanh".
- Premiere import: clean.
- Audio lands on the timeline (just needs collapsing to one stereo track — see F1).

---

## Priority order
1. **Boot work-stream B1–B5** — nothing else is seen until the app runs.
2. **A1 multi-shot splitting** — the single biggest accuracy lever.
3. **F1 stereo / F2 keep-fails** — cheap, high-value workflow wins.
4. **A2–A4** — accuracy polish.
5. **F3 / F4** — labeling niceties.
