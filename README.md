# SteadyCut

Automatically find the steadiest portion of every clip, classify shots by person count, and export a colour-coded, ready-to-import sequence for Premiere Pro — all from a double-clickable app or a single command.

---

## Download

| Platform | File | Notes |
|----------|------|-------|
| macOS (M1/M2/M3/M4) | `SteadyCut-macOS-arm64.zip` | Right-click → Open on first launch (Gatekeeper) |
| macOS (Intel) | `SteadyCut-macOS-x64.zip` | Right-click → Open on first launch (Gatekeeper) |
| Windows 10/11 | `SteadyCut-Windows-x64.zip` | Click "More info → Run anyway" on first launch (SmartScreen) |

**No Python, no FFmpeg, no setup.** Double-click the app — the browser opens automatically. FFmpeg downloads itself on first launch (≈ 80 MB, one time only).

---

## What it does

SteadyCut runs four phases back-to-back on a folder of raw footage:

| Phase | What happens |
|-------|-------------|
| **1 — Proxy Generation** | Transcodes each clip to a lightweight 720p H.264 proxy via FFmpeg. Originals are never touched. Already-existing proxies are skipped. |
| **2 — Stability Analysis** | Runs Lucas-Kanade optical flow (OpenCV) on each proxy to measure per-frame camera motion. Finds all stable windows ≥ N seconds, selects the longest. If a clip fails the starting motion threshold, the threshold relaxes +0.1 px per retry. Clips that exceed the max threshold are added to the timeline uncut rather than dropped. |
| **3 — Shot Classification** | Extracts frames at 25 %, 50 %, and 75 % of each stable window and runs YOLOv8 person detection to classify the clip. |
| **4 — FCP7 XML Assembly** | Builds a Premiere Pro sequence where every clip is trimmed to its stable window, colour-coded by shot type, with stereo audio linked to video. |

### Shot classification labels

| Colour in Premiere | Tag | Meaning |
|---|---|---|
| Cerulean | `[<2 People]` | 1–2 persons detected |
| Mango | `[Multiple Subjects]` | 3+ persons detected |
| Rose | `[BRolls]` | No person / background figure |

---

## Web UI (packaged app)

Launch the app — a browser tab opens at `http://localhost:8000`.

- Set **Input folder** (your raw footage) and **Output XML** path.
- Choose a proxy mode and YOLO model.
- Tune **Threshold** and **Max threshold** as needed.
- Click **Run Pipeline** — progress updates live.

The FFmpeg setup banner at the top dismisses automatically once FFmpeg is ready (first launch only).

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

### 2. Run the web UI

```bash
python3 run.py
```

Browser opens automatically at `http://localhost:8000`.

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
  --skip-proxies          # proxies already exist
  --no-proxies            # skip proxies, analyse originals directly
  --skip-classification   # skip Phase 3, all clips labelled Rose
```

### Proxy mode

```
[1] Generate proxies from raw footage          (default)
[2] Skip generation — proxies already exist
[3] No proxies — analyse original files directly
```

Option 3 works for footage already at 1080p H.264 or lower. For high-resolution or RAW formats (4K, R3D, BRAW) option 1 is significantly faster overall because stability analysis runs on lightweight 720p files.

### YOLO model selection

```
[1] yolov8n.pt  Nano    — fastest, good for clear shots         (default)
[2] yolov8s.pt  Small   — better for overlapping/crowd footage
[3] yolov8m.pt  Medium  — complex scenes, strong occlusion
[4] yolov8l.pt  Large   — high accuracy, speed not a concern
[5] yolov8x.pt  XLarge  — maximum accuracy, dense crowds
```

### Output

```
  Clip                  In             Out             Dur    Tags
  ──────────────────────────────────────────────────────────────────
  RHYC02026.MP4         00:00:12:14    00:00:45:02    32.6s  [<2 People]
  RHYC02031.MP4         00:00:03:01    00:00:41:18    38.7s  [Multiple Subjects]

  ✓  Drag 'Automated_Sequence.xml' into Premiere Pro's Project Panel.
  ⏱  Total pipeline time: 2m 14s
```

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

CI builds run automatically on GitHub Actions for every `v*.*.*` tag — see `.github/workflows/build.yml`.

---

## File structure

```
steadycut/
├── run.py                  ← web UI server (FastAPI + uvicorn)
├── steadycut_pipeline.py   ← CLI entry point + run_pipeline() callable
├── pipelinev3.py           ← Phase 1 + 2: proxy generation and stability analysis
├── shot_classifier.py      ← Phase 3: YOLO person-count classification
├── xml_assembler.py        ← Phase 4: FCP7 XML generation
├── ffmpeg_helper.py        ← FFmpeg/ffprobe resolver and auto-downloader
├── steadycut.spec          ← PyInstaller build spec
├── hooks/
│   └── hook-ultralytics.py ← custom PyInstaller hook for ultralytics data files
├── .github/workflows/
│   └── build.yml           ← CI: macOS arm64 + Intel + Windows x64
├── build.sh / build.bat    ← local build scripts
├── static/
│   └── index.html          ← web dashboard
├── requirements.txt
├── README.md
└── samples/
    └── Finalv3.xml         ← example output sequence
```

---

## Performance

The pipeline is fully parallelised:

- **Phase 1** — up to 4 FFmpeg proxy jobs run concurrently.
- **Phase 2** — optical flow computed for all clips in parallel; motion arrays cached in memory so threshold-relaxation retries cost microseconds, not seconds.
- **Phase 3** — clips classified concurrently (4 workers); frame extraction runs 3 FFmpeg processes in parallel per clip; all 3 frames batched into a single YOLO forward pass.
- **FFprobe results** cached to avoid redundant disk probes.

---

## Troubleshooting

**Clip is too shaky** — pipeline relaxes the threshold +0.1 px per retry up to `--max-threshold`. Clips that hit the ceiling are included in the timeline uncut. Raise `--stable-secs` for a stricter stable-window requirement rather than lowering `--threshold`.

**Shot classifier tags everything as `[BRolls]`** — switch to a larger YOLO model (`yolov8s.pt` or above). The Nano model underestimates confidence on group shots, wide angles, and non-standard clothing.

**Frame extraction fails / blank preview** — verify the FPS value matches the actual clip frame rate and the stable window does not extend beyond the clip's total frame count.

**FFmpeg not found (CLI/dev mode)** — ensure both `ffmpeg` and `ffprobe` are installed and on your system PATH (see Installation above). The packaged app handles this automatically.

**Out of memory on large model + many clips** — reduce `max_workers` in `annotate_clip_list()` inside `shot_classifier.py` from 4 to 2, or switch to a smaller YOLO model.

**macOS Gatekeeper blocks the app** — right-click the `.app` and choose Open; click Open again in the dialog. Only needed on first launch.

**Windows SmartScreen blocks the `.exe`** — click "More info" then "Run anyway" on first launch.

**Logs** — if the app misbehaves, check:
- macOS: `~/Library/Logs/SteadyCut/steadycut.log`
- Windows: `%APPDATA%\SteadyCut\logs\steadycut.log`
