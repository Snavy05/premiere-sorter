# SteadyCut

Automatically find the steadiest portion of every clip, classify shots by person count, and export a colour-coded, ready-to-import sequence for Premiere Pro — all in one command.

---

## What it does

SteadyCut runs four phases back-to-back on a folder of raw footage:

| Phase | What happens |
|-------|-------------|
| **1 — Proxy Generation** | Transcodes each clip to a lightweight 720p H.264 proxy via FFmpeg. Originals are never touched. Already-existing proxies are skipped. |
| **2 — Stability Analysis** | Runs Lucas-Kanade optical flow (OpenCV) on each proxy to measure per-frame camera motion. Finds all stable windows ≥ 3 s, selects the longest. If a clip fails the starting motion threshold, the threshold is automatically relaxed by +0.1 px per retry until a stable window is found — no clip is ever dropped. |
| **3 — Shot Classification** | Extracts frames at 25 %, 50 %, and 75 % of each stable window and runs YOLOv8 person detection to classify the clip. |
| **4 — FCP7 XML Assembly** | Builds a Premiere Pro sequence where every clip is trimmed to its stable window, colour-coded by shot type, with stereo audio linked to video. |

### Shot classification labels

| Colour in Premiere | Tag | Meaning |
|---|---|---|
| Cerulean | `[<2 People]` | 1–2 persons detected |
| Mango | `[Multiple Subjects]` | 3+ persons detected |
| Rose | `[BRolls]` | No person / background figure |

---

## Installation

### 1. Python dependencies

```bash
pip install -r requirements.txt
```

### 2. FFmpeg

| OS | Command |
|----|---------|
| macOS | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |
| Windows | [ffmpeg.org/download](https://ffmpeg.org/download.html) — add to PATH |

### 3. YOLO weights

Downloaded automatically on first run. No manual step needed.

---

## Usage

```bash
python3 steadycut_pipeline.py
```

An interactive wizard walks through every setting. Press Enter to accept the default for each prompt.

### Proxy mode

At startup the wizard asks how to handle proxies:

```
[1] Generate proxies from raw footage          (default)
[2] Skip generation — proxies already exist
[3] No proxies — analyse original files directly
```

Option 3 is useful for footage that is already 1080p H.264 or lower. For high-resolution or RAW formats (4K, R3D, BRAW) option 1 is significantly faster overall because the stability analysis runs on lightweight 720p files.

### YOLO model selection

```
[1] yolov8n.pt  Nano    — fastest, good for clear shots         (default)
[2] yolov8s.pt  Small   — better for overlapping/crowd footage
[3] yolov8m.pt  Medium  — complex scenes, strong occlusion
[4] yolov8l.pt  Large   — high accuracy, speed not a concern
[5] yolov8x.pt  XLarge  — maximum accuracy, dense crowds
```

### CLI flags (skip the wizard)

```bash
python3 steadycut_pipeline.py \
  --input  ./footage \
  --proxies ./proxies \
  --output  ./MySequence.xml \
  --threshold 2.0 \
  --stable-secs 1.0 \
  --fps 25.0 \
  --yolo-model yolov8n.pt \
  --skip-proxies          # proxies already exist
  --no-proxies            # skip proxies entirely, analyse originals
  --skip-classification   # skip Phase 3, all clips labelled Rose
  --export-json clips.json
```

### Output

At the end of the run a summary table is printed:

```
  Clip                                In             Out             Dur    Tags                  Source
  ──────────────────────────────────────────────────────────────────────────────────────────────────────
  RHYC02026.MP4                       00:00:12:14    00:00:45:02    32.6s  [<2 People]           RHYC02026.MP4
  RHYC02031.MP4                       00:00:03:01    00:00:41:18    38.7s  [Multiple Subjects]   RHYC02031.MP4

  ✓  Drag 'Automated_Sequence.xml' into Premiere Pro's Project Panel.
  ⏱  Total pipeline time: 2m 14s
```

Drag the generated `.xml` into Premiere Pro's Project Panel to populate the timeline.

---

## File structure

```
steadycut/
├── steadycut_pipeline.py   ← entry point — run this
├── pipelinev3.py           ← Phase 1 + 2: proxy generation and stability analysis
├── shot_classifier.py      ← Phase 3: YOLO person-count classification
├── xml_assembler.py        ← Phase 4: FCP7 XML generation
├── requirements.txt
├── README.md
├── samples/
│   └── Finalv3.xml         ← example output sequence
├── input/                  ← put your raw footage here (created at runtime)
└── proxies/                ← 720p proxies written here (created at runtime)
```

---

## Performance

The pipeline is fully parallelised:

- **Phase 1** — up to 4 FFmpeg proxy jobs run concurrently.
- **Phase 2** — optical flow is computed for all clips in parallel. Motion arrays are cached in memory so threshold relaxation retries cost microseconds, not seconds.
- **Phase 3** — clips are classified concurrently (4 workers); frame extraction runs 3 FFmpeg processes in parallel per clip; all 3 frames are batched into a single YOLO forward pass.
- **FFprobe results** are cached to avoid redundant disk probes.

---

## Troubleshooting

**Clip is too shaky / threshold keeps relaxing** — the pipeline will keep retrying with a looser threshold until it finds a stable window. If you want a stricter starting point, raise `--stable-secs` rather than `--threshold` — requiring a longer stable run is more meaningful than a lower motion budget.

**Shot classifier tags everything as `[BRolls]`** — switch to a larger YOLO model (`yolov8s.pt` or above). The Nano model underestimates confidence on group shots, wide angles, and non-standard clothing. Run with `--debug` to see every raw detection score.

**Frame extraction fails / blank preview** — verify that the FPS value matches the actual clip frame rate and that the stable window does not extend beyond the clip's total frame count. Run with `--debug` to see the exact FFmpeg commands being executed.

**FFmpeg / ffprobe not found** — ensure both are installed and available on your system PATH (see Installation above).

**Out of memory on large model + many clips** — reduce `max_workers` in `annotate_clip_list()` inside `shot_classifier.py` from 4 to 2, or switch to a smaller YOLO model.
