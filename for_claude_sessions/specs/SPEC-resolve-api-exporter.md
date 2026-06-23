# SPEC — Resolve scripting-API exporter (DaVinci, online-by-construction)

**Goal:** add a DaVinci export target that drives a **live DaVinci Resolve** via its Python
scripting API — import the job's media into the Media Pool and build a timeline from the cut
list **directly in Resolve**. Because Resolve probes each file on import, the media is **online
by construction** — the "File not found in search directories" / offline-clip failure that
sank both the FCP7-XML and `.drt` file exports **cannot occur** with this path.

**Status of the research: DONE and VERIFIED on this machine** (Resolve Studio 20.0.1.6).
Every API call below was run live against the real RHYCO job and confirmed. This spec hands the
**typing** to Cursor; the **API shape + quirks are settled below** — do **not** let Cursor Auto
redesign the call sequence or guess signatures (same failure class as F1; see
`[[nle-stereo-dialect-divergence]]`). Follow the calls here exactly.

**New file:** `resolve_api_exporter.py`. Wire it into `steadycut_pipeline.py` + `run.py` +
`static/index.html` behind the existing NLE target selector. Do **not** touch `xml_assembler.py`
(the FCP7 path stays as the fallback).

**Relationship to the `.drt` exporter:** the `.drt` work on `feat/drt-exporter` is **DEFERRED —
kept as a backup** file-export route. This API exporter is the **primary** DaVinci path. Do not
work on DRT here.

---

## ⛔ BRANCH DISCIPLINE — HARD RULE (read first, every session)

All work for this feature lives on **`feat/resolve-api-exporter`** and NOWHERE else.

- **Before ANY commit or write:** run `git branch --show-current`. If it is not
  `feat/resolve-api-exporter`, STOP and switch. Never `git checkout <branch> -- <path>` to move
  files onto another branch (that smeared DRT work onto `fix/nle-stereo-paths` once already).
- **Never switch branches mid-task.** One task = one branch, start to finish.
- `static/index.html` on this branch carries unrelated stereo/UI WIP — at Task 5 ADD only the
  new option; leave existing uncommitted lines alone.

---

## Why this works (settled — don't re-derive)

The offline failure in every FILE export (XML, DRT) is the same: Resolve trusts its own encoded
**media fingerprint** (probe metadata: frames, codec, internal UUID, creation date) over the
plain path, and a file we author can't reproduce that fingerprint. The scripting API removes the
problem entirely — **Resolve does the probe itself** on `ImportMedia`, so the pool item is
genuine and always online. We only tell Resolve *which files* and *where to cut them*.

**Verified live (RHYCO job, 6× 4K HEVC 50fps):**
- `ImportMedia([6 paths])` → 6 pool items, all online (`Resolution=3840x2160`, `FPS=50.0`,
  correct `Frames`).
- `CreateTimelineFromClips(name, infos)` → timeline with 6 clips on V1.
- Trim honored: `{"startFrame":100,"endFrame":199}` → timeline item `LeftOffset=100`,
  `GetDuration()=99`. ⇒ **duration = endFrame − startFrame (endFrame EXCLUSIVE).**
- `pj.SetSetting("timelineFrameRate","50.0")` → True; `GetSetting` confirms. Must be set
  **before** creating the timeline.

---

## API reference (settled — exact calls, don't guess)

### Bootstrap / connect
Set env (process-local), then import the module and get the app object:

```python
# macOS
RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
# Windows
#   RESOLVE_SCRIPT_API = %PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
#   RESOLVE_SCRIPT_LIB = C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
# PYTHONPATH must include  <RESOLVE_SCRIPT_API>/Modules/
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")   # -> None if Resolve not running OR external scripting OFF
```

`scriptapp("Resolve")` returns **None** when Resolve isn't running **or** when
Prefs → System → General → **"External scripting using"** is set to None. The API cannot toggle
that pref (it's the gate to the API itself) — treat None-after-launch as "user must enable
scripting", see fallback.

### Object graph (all verified)
```python
pm  = resolve.GetProjectManager()
pj  = pm.GetCurrentProject()                 # or pm.CreateProject(name) / pm.LoadProject(name)
mp  = pj.GetMediaPool()
root = mp.GetRootFolder()
bin_ = mp.AddSubFolder(root, "<job name>")   # isolate the job; returns folder
mp.SetCurrentFolder(bin_)                     # imports land here
items = mp.ImportMedia([abs_path, ...])       # -> [MediaPoolItem], online; order may differ from input
pj.SetSetting("timelineFrameRate", str(fps))  # BEFORE create; string, e.g. "50.0"
tl = mp.CreateTimelineFromClips("<tl name>", infos)   # infos: list[clipInfo]
```

`clipInfo` dict (per timeline clip):
```python
{"mediaPoolItem": <MediaPoolItem>, "startFrame": <int>, "endFrame": <int>}  # endFrame EXCLUSIVE
```

Inspect (for the acceptance check):
```python
its = tl.GetItemListInTrack("video", 1)       # list[TimelineItem]
ti.GetDuration()      # frames; == endFrame-startFrame
ti.GetLeftOffset()    # source in-point; == startFrame
```
NB: timeline default start = frame 180000 (01:00:00:00 @ 50) — `GetStart()` is offset by that;
irrelevant to correctness.

### CreateProject quirk
`pm.CreateProject(name)` returned **None** in testing when an unsaved "Untitled" project was open
and/or the name half-existed. Strategy: `LoadProject(name) or CreateProject(name) or
GetCurrentProject()` — fall through to the current project rather than aborting.

---

## Data model — mapping the pipeline cut list to the API

The pipeline produces `clip_data: list[dict]` (same list the FCP7 exporter consumes). Each entry
= ONE cut (one timeline clip). Required keys used here: `src_path` (str), `in_frame` (int),
`out_frame` (int), `fps` (float).

- **Import once per unique `src_path`** → build `{src_path: MediaPoolItem}`. (`ImportMedia`
  return order is not guaranteed — map by `MediaPoolItem.GetClipProperty("File Path")`, not by
  list position.)
- Build `infos` in `clip_data` order:
  `{"mediaPoolItem": item_for[src_path], "startFrame": in_frame, "endFrame": out_frame + 1}`.
  The `+1` makes the window inclusive of `out_frame` (duration `out_frame-in_frame+1`) — **PARITY
  CHECK (Task 2): the per-clip duration MUST equal what `xml_assembler` emits for the same
  `clip_data`**; if the XML exporter treats `out_frame` as exclusive, drop the `+1`. Match it.
- Sequence fps = majority vote of `clip_data[*].fps` (same rule as `assemble_xml`).

---

## Implementation tasks

### Task 1 — `resolve_api_exporter.py`: connect + auto-launch + fallback
- `def _connect(timeout_s=60) -> resolve|None`: set env vars (platform branch), import module,
  `scriptapp("Resolve")`. If None → auto-launch (`open -a "DaVinci Resolve"` on mac; launch
  `Resolve.exe` on Windows) and **poll** `scriptapp` every 2 s up to `timeout_s`. Return the app
  or None.
- `def export_to_resolve(clip_data, job_name, fps_override=None) -> bool`: orchestrates the API
  build (Tasks 2–3). Returns True on success.
- If module import fails, Resolve not installed, `_connect` returns None, or any API call returns
  None/partial → **raise `ResolveUnavailable`** (custom exception) so the caller can fall back.
- **Check:** with Resolve closed, `_connect` launches it and connects within timeout; with
  scripting disabled, returns None (don't hang).

### Task 2 — build pool + timeline from the cut list
- Connect, get/make project (`LoadProject or CreateProject or GetCurrentProject`), media pool,
  job sub-bin.
- Import unique `src_path`s; map by `File Path` clip property.
- `SetSetting("timelineFrameRate", str(seq_fps))` BEFORE create.
- Assemble `infos` (mapping above), `CreateTimelineFromClips(job_name, infos)`.
- **Check (parity):** for the RHYCO `clip_data`, each timeline item `GetDuration()` equals the
  FCP7-XML clip length for the same entry. Log a per-clip table.

### Task 3 — verify + report
- After build: count items on V1 == len(clip_data); confirm every pool item online
  (`GetClipProperty("Resolution")` non-empty). Return structured result (timeline name, clips,
  any offline). Raise/return-False on mismatch so the caller can fall back.

### Task 4 — pipeline wiring (`steadycut_pipeline.py`)
- `target_nle` gains `"resolve_api"`. When selected, after Phase 3 produces `clip_data`, call
  `export_to_resolve(...)` instead of writing an XML.
- **Fallback chain:** on `ResolveUnavailable` (or success==False), fall back to the existing
  file export — write the `resolve`-dialect FCP7 XML (`assemble_xml(..., target_nle="resolve")`)
  and surface a clear message: "Resolve not reachable — wrote <file>; import manually." (DRT
  fallback can slot in later if/when `feat/drt-exporter` lands; XML is the fallback for now.)

### Task 5 — CLI (`run.py`) + UI (`static/index.html`)
- `run.py`: `ProcessRequest.target_nle` already exists; accept `"resolve_api"`. No new required
  fields.
- `static/index.html`: add the NLE option **"DaVinci Resolve (live — auto-builds timeline)"**
  → `resolve_api`. Keep the existing file-based "DaVinci Resolve (XML)" as a separate choice.
  ADD only; do not disturb existing uncommitted lines.

---

## Acceptance
- With Resolve running + scripting enabled: selecting `resolve_api` on the RHYCO job imports 6
  clips **online** and builds a timeline whose per-clip durations match the cut list (and the
  FCP7-XML parity check). No manual import, no offline clips.
- With Resolve **closed**: export auto-launches Resolve, waits, then builds — same result.
- With Resolve **not installed** or scripting **disabled**: no crash; falls back to writing the
  resolve-dialect FCP7 XML and tells the user.

## Escalation (stop + ask, don't guess)
- `CreateTimelineFromClips` returns None for a valid `infos` → capture the exact `infos` and the
  Resolve version; do not retry blindly.
- Parity check fails (durations differ from XML) → STOP; the `+1` / inclusive-vs-exclusive
  convention is the likely cause — confirm against `xml_assembler` before changing either.
- `_connect` hangs or scripting pref can't be detected → leave a `# TODO(claude):` and fall back;
  do not poll forever.

## Out of scope (MVP)
- Color, effects, transitions, retimes, markers.
- Audio beyond what `CreateTimelineFromClips` places by default (match the XML exporter's audio
  behavior only if trivial; otherwise defer).
- Remote/networked Resolve, headless/CI (Resolve needs a GUI session).
- Project/render settings management beyond timeline frame rate.
