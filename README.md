<p align="center">
  <img src="assets/logo.png" alt="SteadyCut" width="140" />
</p>

<h1 align="center">SteadyCut</h1>

<p align="center">
  Find the steadiest, most usable window of every clip — automatically.
</p>

Automatically find the best portion of every clip — the steadiest window, the on-camera action, or both — classify shots by person count, and export a colour-coded, ready-to-import sequence for Premiere Pro or DaVinci Resolve. All from a double-clickable desktop app or a single command.

---

## Download

| Platform | File | Notes |
|----------|------|-------|
| macOS (M1/M2/M3/M4) | `SteadyCut-macOS-arm64.zip` | Right-click → Open on first launch (Gatekeeper) |
| Windows 10/11 | `SteadyCut-Windows-x64.zip` | Click "More info → Run anyway" on first launch (SmartScreen) |

**No Python, no FFmpeg, no setup.** Double-click the app — it opens a native window (WKWebView on macOS, WebView2 on Windows). FFmpeg downloads itself on first launch (≈ 80 MB, one time only).

---

## What's new in v1.2.1-beta

Cleaner windows — fewer split clips:

- **Glues back falsely-split shots** — a gentle direction wobble inside one continuous shot used to chop it into two adjacent clips. SteadyCut now checks the gap between them: if the camera stayed steady across it, the two are merged back into one window. Genuine cuts (a real motion spike or a longer gap) still split as before, and back-to-back retakes stay separate.

Validated against a 16-clip ground-truth set: no good shots lost, redundant split-clips removed.

## What's new in v1.2.0-beta

Smarter shot detection — it now reads the **direction** the camera travels, not just how much it moves:

- **Catches pull-backs and recalibrations** — when you settle a shot, then gently bring the camera back to re-frame, then go again, SteadyCut used to swallow the whole thing into one window. It now sees the camera reverse direction and splits each settle into its own clip, so the clean take before the re-frame stands on its own.
- **Separates retakes in one file** — shot the same setup twice back-to-back (take it, reset, take it again)? Those now surface as separate clips instead of one merged span, so you can pick the take you wanted.

This works even when the move is *gentle* — a slow drift that never spiked the old motion meter. Single pans, tilts, and steady moves are untouched; only genuine direction reversals split.

## What's new in v1.1.2-beta

Accuracy + progress polish from the second field test:

- **Progress bar no longer looks stuck** — the long pause after "analysing all clips" was the cut-detection stage running silently. Motion analysis, **stable-window detection**, and recovery are now three labelled phases that each advance the bar, so you can always see it's still working.
- **Fewer false splits on busy shots** — when a subject swamps the frame (a cheering crowd filling the shot, a dolly-in obscured by foreground), the motion spike used to be mistaken for a camera cut and split one shot into two. The cut detector now ignores motion that isn't *coherent* camera movement, so those shots stay whole. Real cuts and whip-pans still split as before.

## What's new in v1.1.1-beta

Fixes from the first Windows field test:

- **No more flashing command-prompt windows** — ffmpeg/ffprobe now run hidden on Windows (they popped up console windows during recovery).
- **Audio is back** — the v1.0.3 "single stereo track" change made the audio track vanish on import; reverted to two linked L/R tracks (audio present and synced). A proper single-track version will return once verified against Premiere.
- **Windows relink fixed** — file paths used a `//`-prefixed form Premiere read as a network path, forcing a manual relink. Windows drive paths now use the correct `file://localhost/C:/…` form.
- **Whole-pipeline progress bar** — the bar no longer freezes at 50% during the recovery phase; it now advances continuously across every phase (proxy → analysis → recovery → classification → export).
- **Multi-window clips stand out** — when one source clip yields several stable windows, those clips get a distinct **Caribbean** label colour so you can spot multi-window sources at a glance.

## What's new in v1.1.0-beta

- **Adaptive threshold (per-clip)** — new optional mode under Stability Settings. Instead of one fixed pixel threshold for every clip, each clip's "steady" cutoff is computed from its own motion (median + k·MAD), so a tripod shot and a handheld shot both get sensible windows from a single **Sensitivity** knob — no per-clip tuning. Off by default; the fixed threshold remains the baseline.

## What's new in v1.0.3-beta

- **Multi-shot splitting** — files that hold several shots (continuous recording with whip-pans between setups, or concatenated clips) are now split per shot, so each shot gets its own selected window instead of only the first one or two being kept.
- **Shorter steady windows respected** — the steady-window finder now honours your **Stable seconds** setting instead of silently requiring 3 s, so brief but usable settles are no longer dropped.
- **Nothing silently dropped** — clips that fail analysis stay on the timeline, flagged **Lavender** ("review"), so your clip count in matches the count out. (No more separate rejects file by default.)
- **Single stereo audio track** — audio now imports as one linked L/R stereo clip per shot instead of two separate mono tracks.
- **Live recovery progress** — the progress bar keeps moving during the threshold-recovery phase instead of looking frozen at 100%.
- **Done popup + chime** — a completion notification with a short ping when a run finishes.

---

## What it does

SteadyCut runs four phases back-to-back on a folder of raw footage:

| Phase | What happens |
|-------|-------------|
| **1 — Proxy Generation** | Transcodes each clip to a lightweight 720p H.264 proxy via FFmpeg. Originals are never touched. Existing proxies are skipped. CPU preset and hardware (GPU) encoding are selectable. |
| **2 — Analysis** | Finds the part of each clip to keep. Three modes: **Stability** (Lucas-Kanade optical flow finds the longest steady window), **Action** (YOLOv8-pose tracks body-keypoint velocity to find where movement starts and ends), or **Both** (detect actions inside each stable window). |
| **3 — Shot Classification** | Extracts frames across the selected window and runs YOLOv8 person detection to classify the clip by person count. |
| **4 — XML Assembly** | Builds an FCP7 XML sequence — every clip trimmed to its selected window, colour-coded, stereo audio linked to video. Compatible with **Premiere Pro** and **DaVinci Resolve**. |

### Analysis modes

| Mode | Method | Best for |
|------|--------|----------|
| **Stability** | Optical flow finds the longest steady camera window | Event, documentary, b-roll |
| **Action** | YOLOv8-pose finds where body movement starts/ends | Ceremony, performance, action |
| **Both** | Stability first, then detect actions within each stable window | Mixed footage |

### Cut on Action (COA)

When action analysis is active, COA controls how detected movement turns into cuts:

| COA mode | Behaviour |
|----------|-----------|
| `off` | Output the analysed window only — no cut points added |
| `mark` | Add a marker at the action's peak-velocity frame |
| `cut` | Trim the clip to the detected action segment |

Clips where COA ran but found no action peak are tagged **Lavender** so you can review them.

### Shot classification labels

| Colour | `label_reason` | Meaning |
|--------|----------------|---------|
| Cerulean | `person` / `action` | 1–2 persons, or a pose-detected action |
| Mango | `crowd` | 3+ persons detected |
| Rose | `broll` | No person / background figure |
| Lavender | `no_coa_peak` | COA active but no action peak found — review these |

---

## Workflow presets

The app ships with one-click presets that set every parameter for common jobs. Pick one, then fine-tune if needed.

| Preset | Mode | Tuned for |
|--------|------|-----------|
| 💍 **Wedding / Ceremony** | Action + cut | Cuts on body movements; head/tail trim; classification off |
| 🎉 **Event Recap** | Stability + mark | Finds steady shots, marks action peaks |
| 🎓 **Yearbook / Groups** | Stability | Stable shots, handles multi-person scenes (yolov8s) |
| ✦ **Recommended** | Both + mark | Stability + action, balanced for most footage |
| Custom | — | Set everything yourself |

---

## Desktop app

Launch the app — a native window opens (no browser needed). The web UI is served locally on `http://127.0.0.1:8765`.

- Set **Input folder** (raw footage) and **Output XML** path using the native file pickers.
- Pick a **workflow preset**, then choose **analysis mode** and **COA mode**.
- Tune proxy preset/GPU, motion thresholds, stable seconds, velocity threshold, head/tail trim.
- Click **Run Pipeline** — progress and per-phase timings update live.
- **Stop** mid-run at any time; **View logs** on error.

The FFmpeg setup banner dismisses automatically once FFmpeg is ready (first launch only).

---

## Developer / CLI usage

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

FFmpeg must also be available for CLI use:

| OS | Command |
|----|---------|
| macOS | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |
| Windows | [ffmpeg.org/download](https://ffmpeg.org/download.html) — add to PATH |

### 2. Run the desktop app

```bash
python3 run.py
```

Opens the native SteadyCut window.

### 3. Run the CLI pipeline

```bash
python3 steadycut_pipeline.py
```

An interactive wizard walks through every setting. Press Enter to accept the default for each prompt.

### CLI flags (skip the wizard)

```bash
python3 steadycut_pipeline.py \
  --input  ./footage \
  --proxies ./proxies \
  --output  ./MySequence.xml \
  --threshold 2.0 \
  --max-threshold 100.0 \
  --stable-secs 1.0 \
  --fps 25.0 \
  --yolo-model yolov8n.pt \
  --skip-proxies          # proxies already exist \
  --no-proxies            # skip proxies, analyse originals directly \
  --skip-classification   # skip Phase 3, all clips labelled Rose \
  --export-json report.json   # also dump full clip analysis \
  --debug                 # DEBUG-level logging
```

> Action mode, COA, head/tail trim, and proxy GPU/CPU presets are exposed through the desktop UI (and the `run_pipeline()` callable in `steadycut_pipeline.py`). The CLI flags above cover the stability-based workflow.

### Proxy mode

```
[1] Generate proxies from raw footage          (default)
[2] Skip generation — proxies already exist
[3] No proxies — analyse original files directly
```

Option 3 works for footage already at 1080p H.264 or lower. For high-resolution or RAW formats (4K, R3D, BRAW) option 1 is significantly faster overall because analysis runs on lightweight 720p proxies.

### YOLO model selection

```
[1] yolov8n.pt  Nano    — fastest, good for clear shots         (default)
[2] yolov8s.pt  Small   — better for overlapping/crowd footage
[3] yolov8m.pt  Medium  — complex scenes, strong occlusion
[4] yolov8l.pt  Large   — high accuracy, speed not a concern
[5] yolov8x.pt  XLarge  — maximum accuracy, dense crowds
```

Action mode uses the matching `yolov8*-pose.pt` weights (Nano/Small/Medium pose models).

### Output

```
  Clip                  In             Out             Dur    Tags
  ──────────────────────────────────────────────────────────────────
  RHYC02026.MP4         00:00:12:14    00:00:45:02    32.6s  [<2 People]
  RHYC02031.MP4         00:00:03:01    00:00:41:18    38.7s  [Multiple Subjects]

  ✓  Drag 'Automated_Sequence.xml' into Premiere Pro or DaVinci Resolve.
  ⏱  Total pipeline time: 2m 14s
```

Clips too shaky to find a stable window are written to a separate `*_rejects.xml` for review rather than dropped silently.

---

## Building from source

### macOS

```bash
bash build.sh
```

### Windows

```bat
build.bat
```

Both scripts install PyInstaller, clean previous artifacts, run `pyinstaller steadycut.spec`, and optionally zip the output for distribution.

CI builds run automatically on GitHub Actions for every `v*.*.*` tag — see `.github/workflows/build.yml` (macOS arm64 + Intel + Windows x64).

---

## File structure

```
steadycut/
├── run.py                  ← desktop app (FastAPI + uvicorn + pywebview window)
├── steadycut_pipeline.py   ← CLI entry point + run_pipeline() callable
├── pipelinev3.py           ← Phase 1 + 2: proxy generation and stability analysis
├── action_detector.py      ← pose-based action detection (YOLOv8-pose)
├── shot_classifier.py      ← Phase 3: YOLO person-count classification
├── xml_assembler.py        ← Phase 4: FCP7 XML (Premiere + DaVinci Resolve)
├── ffmpeg_helper.py        ← FFmpeg/ffprobe resolver and auto-downloader
├── steadycut.spec          ← PyInstaller build spec
├── hooks/
│   └── hook-ultralytics.py ← custom PyInstaller hook for ultralytics data files
├── .github/workflows/
│   └── build.yml           ← CI: macOS arm64 + Intel + Windows x64
├── build.sh / build.bat    ← local build scripts
├── static/
│   └── index.html          ← web dashboard (presets, modes, live progress)
├── requirements.txt
├── README.md
└── samples/
    └── Finalv3.xml         ← example output sequence
```

---

## Performance

The pipeline is fully parallelised:

- **Phase 1** — up to 4 FFmpeg proxy jobs run concurrently; optional hardware (GPU) H.264 encoder.
- **Phase 2** — optical flow computed for all clips in parallel; motion arrays cached so threshold-relaxation retries cost microseconds. Action analysis runs a single batched YOLO-pose pass with CPU thread limits to avoid oversubscription.
- **Phase 3** — clips classified concurrently; frames extracted in parallel per clip and batched into one YOLO forward pass.
- **FFprobe results** cached to avoid redundant disk probes.

---

## Troubleshooting

**Clip is too shaky** — the pipeline relaxes the threshold +0.1 px per retry up to `--max-threshold`. Clips that hit the ceiling are written to `*_rejects.xml`. Raise `--stable-secs` for a stricter stable-window requirement rather than lowering `--threshold`.

**Shot classifier tags everything as `[BRolls]`** — switch to a larger YOLO model (`yolov8s.pt` or above). The Nano model underestimates confidence on group shots, wide angles, and non-standard clothing.

**Action mode misses movement / over-cuts** — adjust the velocity threshold. Lower = more sensitive (more cuts), higher = only large movements. Use a larger pose model for subtle motion.

**Lots of Lavender clips** — COA found no action peak in those clips. Either the footage is static (use Stability mode) or the velocity threshold is too high.

**Frame extraction fails / blank preview** — verify the FPS value matches the actual clip frame rate and the selected window does not extend beyond the clip's total frame count.

**FFmpeg not found (CLI/dev mode)** — ensure both `ffmpeg` and `ffprobe` are installed and on your PATH (see above). The packaged app handles this automatically.

**Out of memory on large model + many clips** — reduce `max_workers` in `annotate_clip_list()` inside `shot_classifier.py` from 4 to 2, or switch to a smaller YOLO model.

**macOS Gatekeeper blocks the app** — right-click the `.app` and choose Open; click Open again in the dialog. First launch only.

**Windows SmartScreen blocks the `.exe`** — click "More info" then "Run anyway" on first launch.

**Logs** — written to a `logs/` folder next to the app, timestamped per run (`logs/steadycut_YYYYMMDD_HHMMSS.txt`). Use **View logs** in the UI on error, or open the file directly.
