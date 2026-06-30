# Gumroad Listing — SteadyCut

Copy/paste each block into the matching Gumroad field. Audience: wedding/event videographers. Price **$19** one-time + maintenance support. Positioning: **stable-window finder, Premiere-supported.**

---

## Product name
SteadyCut — find the steadiest, most usable window of every clip

## Price
$19 — one-time, single payment.

## Summary / subtitle (one line)
Stop scrubbing 100–300 raw clips by hand. SteadyCut finds the steadiest, most usable window of every clip automatically and builds a ready-to-edit sequence for Premiere Pro.

---

## Description (Gumroad rich text)

**You shoot the wedding. Then you lose an evening just *finding* the usable parts.**

If you cut weddings or events, the worst part of every job isn't the edit — it's the sift. A hundred-plus raw clips, scrubbing each one to find where the shot actually holds steady, before you can start cutting.

SteadyCut does that pass for you.

Point it at a folder of footage. It:

- **Finds the steadiest window in every clip** — optical-flow analysis locates the longest stretch where the camera holds, and trims to it.
- **Splits a clip into multiple windows when it needs to** — and names them so you always know what came from where (`RHYC02026 (1/3)`, `(2/3)`, `(3/3)`).
- **Builds a ready-to-import sequence** — every clip trimmed to its steadiest window, colour-coded so keepers and "take a look" clips are obvious. Drag the XML straight into **Premiere Pro**.

One folder in. One clean, colour-coded timeline out. You start editing instead of sifting.

### No setup
Double-click the app. A native window opens. **FFmpeg ships inside** — no Python, no installs, nothing to download. Your originals are never touched (it works on lightweight proxies).

### If your camera already shot proxies
Select the folder that contains your **original footage**. If you already have camera-generated proxy files, put them in a subfolder named exactly `proxies` inside that footage folder:

```
Wedding Job/
├── RHYC02026.MP4
├── RHYC02027.MP4
└── proxies/
    ├── RHYC02026.MP4
    └── RHYC02027.MP4
```

SteadyCut will detect the `proxies` folder and still export the Premiere XML back to the original clips.

### Built for the way you work
- Workflow presets for events, group/yearbook shoots, and a balanced default
- Tune the stable-window length, threshold, and head/tail trim — or just use a preset
- Adaptive threshold sets each clip's steady cutoff from its own motion

### What you get
- macOS (Apple Silicon — M1/M2/M3/M4) build
- *(Windows build — see note / coming if not in launch)*
- One-time purchase, yours to keep
- **Maintenance support** — hit a bug, email me, I fix it

> **Note:** Premiere Pro is the supported export. DaVinci Resolve export and the AI action-detection / shot-classification features are included but **experimental** — clearly marked in-app, off by default. SteadyCut's job today is finding your stable windows, and it's good at it.

---

## Requirements
- **macOS:** Apple Silicon (M1 or newer). First launch: right-click the app → **Open** (not yet notarized by Apple).
- **Windows:** Windows 10/11 64-bit. First launch: "More info → Run anyway" (SmartScreen).
- Works with **Adobe Premiere Pro** (FCP7 XML import).

## Refund policy
> It's a $19 tool and it either saves you the sift or it doesn't. Not for you? Email within 14 days, full refund, no questions.

## CTA (end of description)
Built by a wedding videographer who got tired of the sift. Try it on your next job — if it doesn't save you an evening, refund it.

---

## Receipt / post-purchase content
Thanks — you've got SteadyCut.

1. Download the zip for your OS.
2. **macOS:** unzip → right-click `SteadyCut.app` → **Open** → Open again. (First launch only — Gatekeeper.)
   **Windows:** unzip → run `SteadyCut.exe` → "More info" → "Run anyway". (First launch only — SmartScreen.)
3. Open the app → set your footage folder + where to save the XML → pick a preset → **Run pipeline**.
4. Drag the output XML into Premiere Pro's Project panel.

If your camera already made proxies: select the folder with your **original** clips, then put the proxy clips in a subfolder named exactly `proxies` inside that folder. Don't select the `proxies` folder itself.

Bug or question? Email **snavy.works@gmail.com** — I read everything and I fix reported bugs.

— Snavy05
