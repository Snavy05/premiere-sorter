# SPEC — DRT exporter (DaVinci Resolve native timeline)

**Goal:** add a second exporter that writes a `.drt` (DaVinci Resolve Timeline) file which
**auto-links the original h265 media on import** — no "File not found in search directories",
no Media Storage prefs step. This is the workaround for DaVinci's FCP7-XML conform being
unable to auto-load our footage (see `docs/PUNCHLIST.md` DaVinci-offline thread).

**Status of the format research: DONE.** Every layer of the `.drt` has been reverse-engineered
and a hand-edited clip was confirmed to import + link in Resolve 20 ("the swap test"). This spec
hands the *typing* to Cursor; the *format reasoning is already settled below* — do **not** let
Cursor Auto redesign the byte layout (same failure class as F1; see
`[[nle-stereo-dialect-divergence]]`). Follow the layout here exactly.

**New file:** `drt_exporter.py` (new module). Wire it into `steadycut_pipeline.py` + `run.py`
+ `static/index.html` behind a target selector. Do **not** touch `xml_assembler.py` (the FCP7
path stays as-is).

---

## Architecture decision (read before coding): clone-the-golden, substitute plain fields

A `.drt` is a ZIP of 3 XML files (details below). Clip metadata is partly **plain XML** and
partly **opaque encoded blobs** (zstd + protobuf + UTF-16BE TLV). Synthesizing the blobs from
scratch is possible but unnecessary.

**The footage is uniform** — all clips are the same camera/codec/resolution/frame-rate/sample-
rate (Sony a7S III, 4K HEVC `hvc1` 4:2:2 10-bit, PCM, same fps). Therefore **the only thing
that varies per clip is plain-XML**: file path, name, timeline start, duration, source in-point,
and a handful of GUIDs. **Every encoded blob is format-constant and is copied verbatim from a
golden template clip.** The swap test proved exactly this: it changed only name + path on top of
verbatim blobs and Resolve linked the clip.

So the exporter is: **load the golden `.drt` in `samples/drt_template/`, clone its per-clip
blocks N times, substitute the plain fields + fresh GUIDs, re-zip.** No protobuf/zstd writing in
the MVP.

Golden template committed at: `samples/drt_template/` (extracted from a real Resolve-20 export;
7 clips `Hoang.dv8629`–`8635`, paths under `/Users/mac/Downloads/STEADY/`).

---

## Format reference (settled — don't re-derive)

### The ZIP
`.drt` = ZIP (DEFLATED), entries at archive root, no top-level dir:
```
project.xml
MediaPool/Master/MpFolder.xml
SeqContainer/<sequence-guid>.xml
```
(`.DS_Store` entries in the original are macOS cruft — do NOT emit them.)

### File roles
| File | Root element | Role |
|------|--------------|------|
| `project.xml` | `<SM_Project>` | project wrapper: render prefs, Fusion ver, gallery ref. **Boilerplate.** Clone verbatim, swap `DbId`. No clip data. |
| `MediaPool/Master/MpFolder.xml` | `<Sm2MpFolder Name="Master">` | **the media pool.** `<MediaVec>` holds one `<Sm2MpVideoClip>` per source file. The part FCP7 XML can't produce — embedded paths here drive auto-conform. |
| `SeqContainer/<guid>.xml` | `<Sm2SequenceContainer>` | **the timeline (cut list).** `<VideoTrackVec>`/`<AudioTrackVec>` → `<Sm2TiTrack>` → `<Items>` → `<Sm2TiVideoClip>`/`<Sm2TiAudioClip>`. Each clip references a pool item by GUID. |

### The link chain (MUST stay consistent)
```
SeqContainer clip <MediaRef>  ──►  MpFolder <Sm2MpVideoClip><UniqueMediaPoolItemId>  ──►  file on disk
```
For each clip: generate ONE fresh pool GUID, write it to the MpVideoClip's
`<UniqueMediaPoolItemId>` AND to both the video and audio timeline clips' `<MediaRef>`.

### Per-clip PLAIN-XML fields to substitute (verified present as plain text)

**SeqContainer — both `<Sm2TiVideoClip>` and `<Sm2TiAudioClip>` (one each per clip):**
| Element | Meaning | Source |
|---------|---------|--------|
| element `DbId=` attr | clip instance id | fresh uuid4 |
| `<Name>` | filename | `os.path.basename(src)` |
| `<Start>` | timeline position, frames | from pipeline cut list |
| `<Duration>` | clip length, frames | from pipeline cut list |
| `<MediaRef>` | → pool GUID | the per-clip pool GUID |
| `<MediaStartTime>` | source in-point, **seconds (float)** | `in_frame / fps` |
| `<MediaFilePath>` | absolute path, **plaintext** | dest media path |

**MpFolder — each `<Sm2MpVideoClip>`:**
| Element | Meaning | Source |
|---------|---------|--------|
| element `DbId=` attr | pool item db id | fresh uuid4 |
| `<Name>` | filename | basename |
| `<UniqueMediaPoolItemId>` | pool GUID | the per-clip pool GUID (same as MediaRef above) |
| `<Clip>` blob | contains the path as a literal | see "path inside the Clip blob" below |

**Leave VERBATIM (format-constant for uniform footage):** every `*BA` field
(`MediaTimemapBA`, `PreConformMediaExtents`, `VirtualAudioTrackBA`, `PinsBA`, `TracksBA`, …),
`<MediaFrameRate>`, `<FieldsBlob>`, and all the MpVideoClip property blobs (`Time`, `Geometry`,
`Radiometry`, `Proxy`, `VideoMetadata`, `MediaMetadata`, `Video`, `BtVideoInfo`,
`EmbeddedAudioVec`). These encode resolution/fps/codec/channels, identical across our clips.

### Path inside the `<Clip>` blob (MpFolder)
The pool item's `<Clip>` FieldsBlob is `00000002 <len> 81 <zstd-frame>`. zstd stored the payload
as a **raw (uncompressed) block**, so the absolute path appears as a **literal ASCII run**
inside the blob bytes (protobuf field1: `0a <varint-len> <path-utf8>`). The swap test edited it
with a same-length literal replace and it worked.

- **MVP (Tier 1):** our filenames are constant length (`Hoang.dvNNNN.MP4`) and the dest dir is
  constant → do a **same-length literal byte replace** of the old path with the new path inside
  the blob hex. If lengths match, no re-framing needed (verified by the swap test).
- **Tier 2 (only if Tier 1's length assumption breaks):** use the blob codec in Appendix A to
  decompress → rewrite protobuf field1 → fix the varint length prefixes → re-emit as a zstd raw
  block → fix the `00000002 <len> 81` header length. Pre-written; wire it only if needed.

### Format header recap (for Appendix A / Tier 2)
`FieldsBlob` = hex. First 4 bytes big-endian = **version**:
- `00000001` → uncompressed: `<ver><uint32 fieldCount>` then TLV fields:
  `<uint32 keyLenBytes><key UTF-16BE><uint32 type><value…>`.
- `00000002` → `<ver><uint32 payloadLen = totalBytes-8><0x81 tag><zstd frame>`. zstd frame
  decompresses to **protobuf** (length-delimited field1) wrapping the same UTF-16BE TLV.
- Header length field verified: `payloadLen == len(blob) - 8`.

---

## GUID-duplication risk (the one real gotcha) + the gate

Cloning the SAME golden clip N times duplicates GUIDs **embedded inside the opaque blobs** (e.g.
the internal `MediaRef` key inside MpVideoClip property blobs). The swap test used 7 *distinct*
golden clips, so it never exercised duplicate embedded GUIDs. Resolve may or may not tolerate
duplicates.

**Gate this before building the full N-clip path (cheap, ~10 min):**

1. **Tier-1a quick test:** build a 2-clip DRT by cloning **two different** golden clips
   (8629, 8630) and substituting only name/path/start/duration/outer-GUIDs, blobs verbatim.
   Import → both online + correct positions? If yes, distinct-source cloning works.
2. **Tier-1b duplicate test:** build a 2-clip DRT by cloning the **same** golden clip twice
   (both = 8629 source, two different timeline positions). Import.
   - **Both online** → embedded-GUID duplication is FINE → MVP = clone golden[0] for every clip,
     done. Simplest.
   - **Second offline / error** → embedded GUIDs must be unique → enable **Appendix B** (regen
     every embedded GUID per clone via the TLV codec). Still bounded.

Do 1b FIRST — its result picks the MVP shape. Do not build the full pipeline wiring until 1b is
answered.

---

## Data model

Reuse the pipeline's existing per-clip cut data. The exporter needs, per timeline clip:
```python
@dataclass
class DrtClip:
    src_path: str      # absolute dest path of the media on disk (what Resolve opens)
    name: str          # basename
    start: int         # timeline position, frames
    duration: int      # frames
    in_frame: int      # source in-point, frames  -> MediaStartTime = in_frame / fps
    fps: float         # for MediaStartTime seconds conversion
```
(If the pipeline already carries richer cut objects, adapt — but these 6 are the minimum.)

---

## Implementation tasks

### Task 1 — `drt_exporter.py` skeleton
- `load_template(template_dir="samples/drt_template")` → read the 3 XML strings.
- Parse with **`lxml.etree`** if available else `xml.etree.ElementTree` (the repo already uses
  ElementTree in `xml_assembler.py`). Keep blob text untouched — only edit element `.text`,
  `.attrib`, and clone subtrees. **Never pretty-print/re-indent** the blob-bearing files (it can
  shift bytes Resolve cares about); serialize with the original encoding and no reformatting.
- Identify the golden video-clip element, audio-clip element, and MpVideoClip element by
  matching the golden `Name` (e.g. `Hoang.dv8629.MP4`).

### Task 2 — per-clip clone + substitute (MVP shape chosen by the 1b gate)
For each `DrtClip`:
1. `pool_guid = str(uuid.uuid4())`.
2. Clone golden `<Sm2MpVideoClip>`; set `DbId`=uuid4, `<Name>`, `<UniqueMediaPoolItemId>`=
   `pool_guid`; rewrite the `<Clip>` blob path (Tier-1 same-length literal replace).
3. Clone golden `<Sm2TiVideoClip>` and `<Sm2TiAudioClip>`; set each `DbId`=uuid4, `<Name>`,
   `<Start>`, `<Duration>`, `<MediaRef>`=`pool_guid`, `<MediaStartTime>`=`in_frame/fps`,
   `<MediaFilePath>`=`src_path`.
4. If 1b said "regen needed": run Appendix B GUID-regen on the cloned blobs.
Append clones to `<MediaVec>` (pool) and the track `<Items>` (timeline). Remove the 7 golden
originals from the output. Keep exactly one video track + one audio track for the MVP.

### Task 3 — assemble + zip
- Reserialize the 3 XML strings.
- Write ZIP with `zipfile.ZIP_DEFLATED`, entries exactly:
  `project.xml`, `MediaPool/Master/MpFolder.xml`, `SeqContainer/<seq-guid>.xml`.
  No `.DS_Store`, no directory entries. (`SeqContainer/<seq-guid>.xml` — keep the golden
  sequence filename/guid or regen consistently with `project.xml` + the `<Sequence>` ref inside
  the track; simplest MVP = keep golden seq guid verbatim, it's self-consistent.)
- Output path: alongside the FCP7 XML output, `<project>.drt`.

### Task 4 — pipeline wiring (`steadycut_pipeline.py`)
- Add an export target. Today `target_nle` ∈ {`premiere`, `resolve`} selects FCP7-XML dialect.
  Add `resolve_drt` as a third value that routes to `drt_exporter.export(clips, out_path)`
  instead of `xml_assembler`. Do not disturb the existing two.

### Task 5 — CLI (`run.py`) + UI (`static/index.html`)
- CLI: extend the existing `--target-nle` (or equivalent) choices to include `resolve_drt`.
- UI: add the third option to the existing target selector. Label: **"DaVinci Resolve (.drt —
  auto-links originals)"**. Mirror however `premiere`/`resolve` are presented today; no new UI
  pattern.

---

## Acceptance

1. `python drt_exporter.py` (or via pipeline) on a real cut → writes `<project>.drt`.
2. `unzip -l` shows exactly the 3 entries, no `.DS_Store`.
3. Each XML parses (`xml.dom.minidom.parse` on the extracted files).
4. Link chain holds: every SeqContainer `<MediaRef>` equals some MpFolder
   `<UniqueMediaPoolItemId>`; counts match the cut.
5. **Resolve import (primary, USER does this):** drag the `.drt` into Resolve 20 →
   - timeline rebuilds with all clips at correct positions/durations,
   - **original h265 media is ONLINE with NO Media Storage step**,
   - audio present.
   The agent does NOT mark this done; user confirms in Resolve.

## Escalation
If import fails after Tasks 1–3: the cause is almost certainly the 1b gate (embedded-GUID
duplication) or a Tier-1 path-length break. Leave
`# TODO(claude): DRT export — Tier-1 verbatim applied, Resolve did <X>` and bounce to Claude with
the exact symptom (which clips offline, any error dialog text). Do not invent new byte layouts.

## Out of scope (MVP)
- **Mixed-format footage** (different res/fps/codec/sample-rate across clips). The verbatim-blob
  trick assumes uniform footage. Mixed → needs per-format golden templates or full blob
  synthesis. Defer.
- Multiple video/audio tracks, transitions, effects, retimes, markers, color.
- Regenerating `project.xml` settings beyond `DbId`.

---

## Appendix A — blob codec (Tier 2, pre-written; wire only if Tier 1 path-length breaks)
```python
import struct, subprocess, binascii

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

def blob_decode(hexstr):
    raw = binascii.unhexlify(hexstr)
    ver = struct.unpack(">I", raw[:4])[0]
    if ver == 1:
        return ("v1", raw[4:])                      # uncompressed TLV body (after fieldCount handled by caller)
    # ver 2: <ver><uint32 payloadLen><0x81><zstd frame>
    idx = raw.find(ZSTD_MAGIC)
    frame = raw[idx:]
    out = subprocess.run(["zstd", "-d", "--stdout"], input=frame,
                         capture_output=True, check=True).stdout
    return ("v2", out)                              # decompressed protobuf bytes

def blob_encode_v2(payload_bytes):
    # Re-emit as a zstd frame (any level; Resolve decodes regardless) + header.
    frame = subprocess.run(["zstd", "-q", "-1", "--stdout"], input=payload_bytes,
                           capture_output=True, check=True).stdout
    body = b"\x81" + frame
    return binascii.hexlify(struct.pack(">II", 2, len(body)) + body).decode()
```

## Appendix B — embedded-GUID regen (only if the 1b duplicate test fails)
Inside a decoded v2 protobuf payload the embedded GUIDs are UTF-16BE strings of the form
`H<guid>` (e.g. `H98ea95d4-0e43-406f-a85f-a8f49f10ef2f`) stored as a TLV value. Regen:
1. `blob_decode` the property blob → protobuf bytes.
2. Find each `00480048…`-style UTF-16BE `H<36-char-guid>` run; for each, generate a fresh uuid4,
   re-encode as UTF-16BE with the same `H` prefix (**same byte length** — uuid4 string is always
   36 chars → no length fixup needed), splice back.
3. `blob_encode_v2` → new hex. Keep a map so the same source GUID maps consistently within one
   clip's blobs.
Because the replacement is fixed-length, no protobuf varint or header-length recomputation is
required — same trick that made the swap test safe.
