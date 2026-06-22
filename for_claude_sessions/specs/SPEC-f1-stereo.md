# SPEC — F1: one stereo audio track (REWRITE v2, reference-backed)

**Goal:** the camera's original audio imports as **ONE stereo track** in Premiere, not two
mono tracks. All changes in `xml_assembler.py`. Touch no other file.

## Why v1 of this spec failed (the architecture was inverted)
v1 told you to COLLAPSE the audio to one `<track>` + one clipitem + a one-group output bus.
That broke import (audio vanished) because Premiere does NOT represent a single stereo track
that way. Confirmed against a real Premiere-authored file that imports as one stereo track
(`Sample XML FCP7.xml`): a single stereo track is the **exploded-stereo** model —

- the **sequence** element carries `explodedTracks="true"`,
- the sequence `<audio>` opens with an **output bus**: `numOutputChannels`2 + `format` +
  `outputs` with **TWO groups, each `numchannels` 1** (group1→channel1, group2→channel2),
- then **TWO** `<track premiereTrackType="Stereo">` elements (an exploded pair):
  `currentExplodedTrackIndex` 0 and 1, both `totalExplodedTrackCount` 2,
- each clip appears as **two audio clipitems** (one per exploded track),
  `premiereChannelType="stereo"`, `sourcetrack/trackindex` 1 on track A and 2 on track B,
- every clipitem (video + both audio) carries **3 `<link>`s** (video, audio-trk1, audio-trk2),
- each track ends with `enabled`/`locked`/`outputchannelindex` (1 on track A, 2 on track B).

**main already builds the two-track / two-audio-clipitem / cross-link skeleton.** Do NOT
collapse it. This spec only ADDS the stereo-grouping metadata main omits. Net: 6 small edits.

Reference target (sequence audio, abridged from the real file):
```xml
<sequence ... explodedTracks="true">
 <media>
  <audio>
   <numOutputChannels>2</numOutputChannels>
   <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
   <outputs>
     <group><index>1</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>1</index></channel></group>
     <group><index>2</index><numchannels>1</numchannels><downmix>0</downmix><channel><index>2</index></channel></group>
   </outputs>
   <track premiereTrackType="Stereo" currentExplodedTrackIndex="0" totalExplodedTrackCount="2">
     <clipitem id="clipitem-audio-1-ch1" premiereChannelType="stereo"> ... <sourcetrack><trackindex>1</trackindex></sourcetrack> ... </clipitem>
     <enabled>TRUE</enabled><locked>FALSE</locked><outputchannelindex>1</outputchannelindex>
   </track>
   <track premiereTrackType="Stereo" currentExplodedTrackIndex="1" totalExplodedTrackCount="2">
     <clipitem id="clipitem-audio-1-ch2" premiereChannelType="stereo"> ... <sourcetrack><trackindex>2</trackindex></sourcetrack> ... </clipitem>
     <enabled>TRUE</enabled><locked>FALSE</locked><outputchannelindex>2</outputchannelindex>
   </track>
  </audio>
 </media>
</sequence>
```

---

## Task 1 — file media: ONE 2-channel `<audio>` block (`_make_file_elem`, ~line 245-256)
Working refs declare the source as a single 2-channel stream. Replace the `for ch_idx,
ch_label ...` loop (two channelcount-1 blocks) with a SINGLE minimal block — no `layout`,
no `audiochannel` children (the real file has neither):
```python
    # One 2-channel audio stream. Premiere reads channelcount=2 as a stereo source;
    # the sequence-level output bus (see _build_sequence) routes it to one stereo track.
    a = ET.SubElement(media_elem, "audio")
    sc_a = ET.SubElement(a, "samplecharacteristics")
    ET.SubElement(sc_a, "depth").text      = "16"
    ET.SubElement(sc_a, "samplerate").text = str(sample_rate)
    ET.SubElement(a, "channelcount").text  = str(channels)
```
**Check:** `<media>` has exactly one `<audio>` child, `<channelcount>2`, no `<layout>`/`<audiochannel>`.

## Task 2 — sequence element: exploded flag (`_build_sequence`, ~line 438)
The sequence must advertise exploded audio tracks. Change:
```python
    seq = ET.Element("sequence")
```
to:
```python
    seq = ET.Element("sequence", explodedTracks="true")
```
**Check:** the root `<sequence>` tag carries `explodedTracks="true"`.

## Task 3 — audio output bus + TWO exploded stereo tracks (`_build_sequence`, ~line 479-482)
Replace:
```python
    # ── Audio branch (two mono tracks = stereo pair in Premiere) ─────────────
    audio_branch = ET.SubElement(media, "audio")
    a_track_L = ET.SubElement(audio_branch, "track")   # Left  (ch 1)
    a_track_R = ET.SubElement(audio_branch, "track")   # Right (ch 2)
```
with:
```python
    # ── Audio branch: stereo output bus + exploded stereo track pair ─────────
    # Premiere represents ONE stereo timeline track as two "exploded" tracks
    # bound by the output bus below. numOutputChannels/format/outputs is the bus;
    # without it (or with a single-group bus) Premiere drops the audio on import.
    audio_branch = ET.SubElement(media, "audio")
    ET.SubElement(audio_branch, "numOutputChannels").text = "2"
    a_fmt = ET.SubElement(audio_branch, "format")
    a_sc  = ET.SubElement(a_fmt, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text      = "16"
    ET.SubElement(a_sc, "samplerate").text = str(DEFAULT_SAMPLE_RATE)
    a_outputs = ET.SubElement(audio_branch, "outputs")
    for grp in (1, 2):                       # ONE group per channel, numchannels 1 each
        g = ET.SubElement(a_outputs, "group")
        ET.SubElement(g, "index").text       = str(grp)
        ET.SubElement(g, "numchannels").text = "1"
        ET.SubElement(g, "downmix").text     = "0"
        ch = ET.SubElement(g, "channel")
        ET.SubElement(ch, "index").text      = str(grp)
    # Exploded stereo pair: track A = channel 1, track B = channel 2.
    a_track_L = ET.SubElement(audio_branch, "track",
        premiereTrackType="Stereo", currentExplodedTrackIndex="0", totalExplodedTrackCount="2")
    a_track_R = ET.SubElement(audio_branch, "track",
        premiereTrackType="Stereo", currentExplodedTrackIndex="1", totalExplodedTrackCount="2")
```
**Check:** `<audio>` contains, in order, `numOutputChannels`, `format`, `outputs` (two
`group`s, each `numchannels`1), then two `track premiereTrackType="Stereo"` elements.

## Task 4 — audio clipitem: mark stereo (`_make_audio_clipitem`, ~line 365)
The clipitem stays mono-per-track structurally (one channel each, sourcetrack trackindex =
`channel`), but Premiere tags exploded-stereo clipitems `premiereChannelType="stereo"`.
Change:
```python
    item = ET.Element("clipitem", id=item_id, premiereChannelType="mono")
```
to:
```python
    item = ET.Element("clipitem", id=item_id, premiereChannelType="stereo")
```
Leave the id (`clipitem-audio-{clip_index}-ch{channel}`), the `sourcetrack/trackindex =
channel`, and the existing 3 links (video + ch1 + ch2) UNCHANGED — they already match the
reference.
**Check:** both audio clipitems per clip are `premiereChannelType="stereo"`; ids keep `-ch1/-ch2`.

## Task 5 — track trailers (`_build_sequence`, after the clip loop, before `return seq`)
Premiere emits per-track flags after the clipitems. Append to each exploded track once,
after the loop finishes (outputchannelindex distinguishes the pair):
```python
    for trk, oci in ((a_track_L, "1"), (a_track_R, "2")):
        ET.SubElement(trk, "enabled").text            = "TRUE"
        ET.SubElement(trk, "locked").text             = "FALSE"
        ET.SubElement(trk, "outputchannelindex").text = oci
```
**Check:** each audio `<track>` ends with `enabled`/`locked`/`outputchannelindex`, AFTER all
its clipitems; track A → 1, track B → 2.

## Task 6 — leave the clip loop as-is (no change, stated for safety)
The `a_track_L.append(... channel=1)` / `a_track_R.append(... channel=2)` loop and the video
clipitem's two audio links are ALREADY correct — do not touch them. The whole fix is metadata
(Tasks 1-5). Do NOT remove `a_track_L` / `a_track_R` / the `channel=` arg.
**Check:** the clip loop still appends one audio clipitem to each of the two tracks per clip.

---

## Acceptance
1. `python xml_assembler.py` → writes `Automated_Sequence.xml`. Confirm: `<sequence
   explodedTracks="true">`, an `<outputs>` with TWO `<group>`s (`numchannels`1 each), TWO
   `<track premiereTrackType="Stereo">` with `currentExplodedTrackIndex` 0 and 1, audio
   clipitems `premiereChannelType="stereo"`, each track trailer `outputchannelindex` 1/2.
2. Parses: `python -c "import xml.dom.minidom,sys; xml.dom.minidom.parse('Automated_Sequence.xml')"`.
3. **Premiere import (primary, USER does this):** timeline shows ONE stereo track, audio
   PRESENT and in sync, in/out unchanged. The agent does NOT mark this done.

## Escalation
This spec is reference-backed (every shape taken from a real single-stereo-track Premiere
file). If Premiere STILL drops audio after Tasks 1-5, leave `# TODO(claude): F1 v2 — exploded
model applied, Premiere did <X>` and bounce to Claude. Do not invent other shapes.

## Out of scope
Pipeline logic, video side, in/out math, labels, adaptive/CLI work, the empty extra exploded
track pairs Premiere adds for unused A2/A3 (not needed for one populated stereo track).
