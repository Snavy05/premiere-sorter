# Video Pipeline — Stability Analysis & Shot Classification

Two Python scripts that work together to automatically find the steadiest portion of every video clip, classify the shot type using AI, and export a ready-to-import sequence for Premiere Pro / Final Cut Pro 7.

---

## What each script does

### `pipelinev3.py` — Stability Pipeline (3 Phases)

Scans a folder of raw footage and produces an FCP7 XML sequence containing only the most stable window of each clip, linked back to the original high-quality files.

**Phase 1 — Proxy Generation**
Transcodes every raw clip to a lightweight 720p H.264 proxy using FFmpeg. Proxies are what the analysis runs on — the original files are never touched. Already-existing proxies are skipped automatically.

**Phase 2 — Stability Analysis**
Runs Lucas-Kanade optical flow (OpenCV) on each proxy to measure per-frame camera motion in pixels. Uses a state machine to find all stable windows (motion below threshold for at least 3 seconds), then selects the longest one. Clips with no qualifying window are skipped with a warning.

**Phase 3 — FCP7 XML Generation**
Builds an XML sequence where each clip item points to the original high-quality file (not the proxy), trimmed to its stable in/out points. NTSC / drop-frame flags, frame rate, resolution, and stereo audio are all auto-detected from the source files via ffprobe.

---

### `shot_classifier.py` — YOLO Shot Classifier

Classifies the cinematographic shot type of a stable video segment using YOLOv8 person detection. Can be run standalone on a single clip, or integrated into `pipelinev3.py` via `annotate_clip_list()`.

**Cascade Architecture** — three gates are checked in order before any shot tag is assigned. Failure at any gate immediately returns `[Scenery]`.

| Gate | Name | What it checks |
|------|------|----------------|
| 1 | Scenery Area | Primary bounding box must cover ≥ 10% of frame area. Smaller boxes are background figures or artefacts. |
| 2 | Temporal Consistency | Frames sampled at 25%, 50%, and 75% of the clip. Subject must appear in all three, and must not drift more than 30% of frame width horizontally (filters out photobombers). |
| 3 | Focus Check | Laplacian variance of the cropped subject region. Low variance = blurry foreground obstruction, not an intentional subject. |

If all three gates pass, framing maths determines the shot type based on how much of the frame height the subject occupies:

| Tag | Condition |
|-----|-----------|
| `[WS]` — Wide Shot | Subject height < 40% of frame |
| `[MS]` — Medium Shot | Subject height 40–85% of frame |
| `[MCU]` — Medium Close-Up | Subject height > 85% and bottom edge cuts off at frame boundary |
| `[CU]` — Close-Up | Subject height > 85% and fully contained within frame |

Additional tags that can appear alongside the shot type:

| Tag | Meaning |
|-----|---------|
| `[Multi-Subject]` | More than one person detected; framing is based on the largest |
| `[Partial_Frame]` | Subject's bounding box bleeds within 5% of the left or right edge |
| `[Occluded]` | Bounding box aspect ratio suggests the subject is seated behind a desk or partially hidden |
| `[Scenery]` | Failed one of the three cascade gates — no valid subject |

---

## Installation

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install FFmpeg (system tool — required by both scripts)

| OS | Command |
|----|---------|
| macOS | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |
| Windows | Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH |

### 3. YOLO model weights

Downloaded automatically on first run — no manual step needed. The script will prompt you to choose a model size.

---

## How to run

### `pipelinev3.py`

```bash
python3 pipelinev3.py
```

The script opens an interactive wizard and prompts for:

- **Raw footage folder** — directory containing your original clips
- **Proxy output folder** — where the 720p proxies will be written
- **Output XML path** — where to save the finished FCP7 XML file
- **Motion threshold** (default 2.0 px) — how much camera movement is allowed before a frame is considered unstable. Lower = stricter.
- **Stable seconds needed** (default 1.0 s) — minimum run of stable frames required before an in-point is confirmed.
- **Fallback FPS** (default 25.0) — used only if ffprobe cannot read the frame rate from a file.

Once the wizard is complete the three phases run automatically and print a summary table of every clip's in/out timecode and duration.

> **Tip:** If your proxies already exist from a previous run, you can skip Phase 1 by passing `--skip-proxies` as a flag: `python3 pipelinev3.py --skip-proxies`

---

### `shot_classifier.py`

```bash
python3 shot_classifier.py
```

The script opens an interactive prompt and asks for:

1. **YOLO model** — choose from the menu:

   | # | Model | Speed | Best for |
   |---|-------|-------|----------|
   | 1 | `yolov8n.pt` (Nano) | Fastest | Clear, uncluttered shots — default |
   | 2 | `yolov8s.pt` (Small) | ~2× slower | Overlapping or partially-hidden subjects (recommended for group/crowd footage) |
   | 3 | `yolov8m.pt` (Medium) | ~4× slower | Complex scenes, strong occlusion |
   | 4 | `yolov8l.pt` (Large) | ~8× slower | High accuracy, speed not a concern |
   | 5 | `yolov8x.pt` (XLarge) | ~12× slower | Maximum accuracy, dense crowds |

2. **Video file path** — path to the clip to classify
3. **In-frame** — stable window start frame (from pipelinev3.py output)
4. **Out-frame** — stable window end frame
5. **FPS** — frame rate (default 25.0)
6. **Preview PNG** — optionally save a debug image with bounding boxes and tags drawn on the mid-frame
7. **Debug logging** — verbose output showing every YOLO detection and gate decision

**Example output:**
```
─────────────────────────────────────
  Shot tags  : ['[MS]', '[Multi-Subject]']
─────────────────────────────────────
```

---

## Using both scripts together

`shot_classifier.py` exposes an `annotate_clip_list()` function that can be called directly from `pipelinev3.py` to embed shot tags into the XML output:

```python
from shot_classifier import annotate_clip_list

# In pipelinev3.py main(), after Phase 2 builds clip_data:
clip_data = annotate_clip_list(clip_data)
# Each clip dict now has a "shot_tags" key, e.g. ['[MS]', '[Partial_Frame]']
```

---

## File structure

```
your-project/
├── pipelinev3.py
├── shot_classifier.py
├── requirements.txt
├── README.md
├── input/          ← your raw footage goes here
├── proxies/        ← 720p proxies written here by Phase 1
└── sequence.xml    ← FCP7 XML output, ready to import into Premiere Pro
```

---

## Troubleshooting

**"No stable window found" for a clip** — the entire clip is too shaky, or the motion threshold is too strict. Try raising the threshold (e.g. from 2.0 to 3.5 px) in the wizard.

**Shot classifier misses a person in a crowd** — switch from Nano to Small (`yolov8s.pt`) in the model menu. Small is significantly better at overlapping and partially-hidden subjects.

**Preview PNG is blank / frame extraction failed** — check that your `--fps` value matches the actual clip frame rate, and that `--out-frame` does not exceed the clip's total frame count. Run with debug logging enabled to see the exact FFmpeg commands being used.

**FFmpeg not found** — make sure FFmpeg is installed and available on your system PATH (see Installation above).
