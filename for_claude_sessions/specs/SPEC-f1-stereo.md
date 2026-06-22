# SPEC — F1: one stereo audio track (root-caused; do tasks 1→6 IN ORDER)

**Goal:** the camera's original audio imports as **ONE stereo track** in Premiere, not two
mono tracks. All changes are in `xml_assembler.py`. Touch no other file.

## Why this failed before (read this — it's why the escalation rule is now lifted)
`08b10a3` already wrote the stereo clipitem + `premiereTrackType="Stereo"` track and **it
broke** — `1076637` reverted it because *"that shape made the audio track disappear on
Premiere import."* Root cause: the sequence declared a stereo clip and a stereo track but
**never declared a sequence-level stereo OUTPUT BUS**. FCP7 routes clip channels to the
timeline via a `<media><audio>` block with `<numOutputChannels>` + `<format>` + `<outputs>`.
Two *mono* tracks survive without it; a 2-channel clip on one track has no stereo bus to route
into, so Premiere drops the audio.

**The fix = re-apply 08b10a3's audio builders PLUS add the output bus (Task 2).** The output
bus is the new part that makes it actually work. See the reverted builders with:
`git show 08b10a3 -- xml_assembler.py`. This is now mechanical — do NOT bounce to Claude
unless real Premiere import STILL drops the audio after all 6 tasks.

Target audio shape (what the final XML must contain, once):
```xml
<audio>
  <numOutputChannels>2</numOutputChannels>
  <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>
  <outputs>
    <group><index>1</index><numchannels>2</numchannels><downmix>0</downmix>
      <channel><index>1</index></channel><channel><index>2</index></channel>
    </group>
  </outputs>
  <track premiereTrackType="Stereo">
    <clipitem id="clipitem-audio-1" premiereChannelType="stereo"> ... <sourcetrack><trackindex>1</trackindex> ... </clipitem>
    <enabled>TRUE</enabled><locked>FALSE</locked><outputchannelindex>1</outputchannelindex>
  </track>
</audio>
```

---

## Task 1 — file media: one 2-channel `<audio>` block (`_make_file_elem`, ~line 245-256)
Currently emits TWO `<audio>` blocks in a `for ch_idx, ch_label ...` loop. Replace that whole
loop with a SINGLE block:
```python
    # Single stereo audio stream: one 2-channel <audio> block. Pairs with the
    # stereo clipitem + the sequence stereo output bus (see _build_sequence) so
    # Premiere imports ONE linked stereo clip, not two mono tracks.
    a = ET.SubElement(media_elem, "audio")
    sc_a = ET.SubElement(a, "samplecharacteristics")
    ET.SubElement(sc_a, "depth").text      = "16"
    ET.SubElement(sc_a, "samplerate").text = str(sample_rate)
    ET.SubElement(a, "channelcount").text  = str(channels)
    ET.SubElement(a, "layout").text        = "stereo"
    for ch_idx, ch_label in enumerate(("left", "right"), start=1):
        ach = ET.SubElement(a, "audiochannel")
        ET.SubElement(ach, "sourcechannel").text = str(ch_idx)
        ET.SubElement(ach, "channellabel").text  = ch_label
```
**Check:** `_make_file_elem` now creates exactly one `<audio>` child of `<media>`.

## Task 2 — sequence audio output bus + ONE stereo track (`_build_sequence`, ~line 479-482)
This is the new part. Currently:
```python
    # ── Audio branch (two mono tracks = stereo pair in Premiere) ─────────────
    audio_branch = ET.SubElement(media, "audio")
    a_track_L = ET.SubElement(audio_branch, "track")   # Left  (ch 1)
    a_track_R = ET.SubElement(audio_branch, "track")   # Right (ch 2)
```
Replace with:
```python
    # ── Audio branch: stereo output bus + ONE stereo track ───────────────────
    # The output bus (numOutputChannels/format/outputs) is mandatory: without a
    # stereo bus, a 2-channel clip has nowhere to route and Premiere drops the
    # audio on import (the bug that sank the previous single-track attempt).
    audio_branch = ET.SubElement(media, "audio")
    ET.SubElement(audio_branch, "numOutputChannels").text = "2"
    a_fmt = ET.SubElement(audio_branch, "format")
    a_sc  = ET.SubElement(a_fmt, "samplecharacteristics")
    ET.SubElement(a_sc, "depth").text      = "16"
    ET.SubElement(a_sc, "samplerate").text = str(DEFAULT_SAMPLE_RATE)
    a_outputs = ET.SubElement(audio_branch, "outputs")
    a_group   = ET.SubElement(a_outputs, "group")
    ET.SubElement(a_group, "index").text       = "1"
    ET.SubElement(a_group, "numchannels").text = "2"
    ET.SubElement(a_group, "downmix").text     = "0"
    for ch in (1, 2):
        a_ch = ET.SubElement(a_group, "channel")
        ET.SubElement(a_ch, "index").text = str(ch)
    a_track = ET.SubElement(audio_branch, "track", premiereTrackType="Stereo")
```
**Check:** `<audio>` now contains, in order, `numOutputChannels`, `format`, `outputs`, then
one `track`. `a_track_L` / `a_track_R` no longer exist (Task 5 removes their use).

## Task 3 — audio clipitem: one stereo clipitem (`_make_audio_clipitem`, ~line 340-401)
- Remove the `channel: int` parameter from the signature.
- Change id + attr:
```python
    item_id  = f"clipitem-audio-{clip_index}"
    item = ET.Element("clipitem", id=item_id, premiereChannelType="stereo")
```
- `<sourcetrack>` trackindex becomes fixed `"1"`:
```python
    ET.SubElement(src_track, "trackindex").text = "1"
```
- Replace the `for ach_track in (1, 2):` audio-link loop with a single audio link:
```python
    link_a = ET.SubElement(item, "link")
    ET.SubElement(link_a, "linkclipref").text = item_id
    ET.SubElement(link_a, "mediatype").text   = "audio"
    ET.SubElement(link_a, "trackindex").text  = "1"
    ET.SubElement(link_a, "clipindex").text   = str(clip_index)
    ET.SubElement(link_a, "groupindex").text  = "1"
```
**Check:** one audio clipitem per clip, id has no `-ch1/-ch2` suffix.

## Task 4 — video clipitem: single audio link (`_make_video_clipitem`, ~line 315-321)
Replace the `for ach_track in (1, 2):` audio-link loop with ONE link to the stereo audio clip:
```python
    link_a = ET.SubElement(item, "link")
    ET.SubElement(link_a, "linkclipref").text = f"clipitem-audio-{clip_index}"
    ET.SubElement(link_a, "mediatype").text   = "audio"
    ET.SubElement(link_a, "trackindex").text  = "1"
    ET.SubElement(link_a, "clipindex").text   = str(clip_index)
    ET.SubElement(link_a, "groupindex").text  = "1"
```
**Check:** video clipitem has exactly two `<link>` elements (self video + one audio).

## Task 5 — clip loop: append one audio clipitem (`_build_sequence`, ~line 543-563)
Currently appends to `a_track_L` (channel=1) and `a_track_R` (channel=2). Replace BOTH appends
with a single append to `a_track`, dropping the `channel=` argument:
```python
        a_track.append(
            _make_audio_clipitem(
                clip           = active_clip,
                clip_index     = idx,
                file_id        = file_id,
                timeline_start = timeline_cursor,
                timeline_end   = timeline_cursor + duration,
            )
        )
```
**Check:** loop references `a_track` only; no `a_track_L` / `a_track_R` / `channel=` remain
anywhere (grep to confirm).

## Task 6 — track trailer elements (`_build_sequence`, after the clip loop, before `return seq`)
Premiere emits per-track flags after the clipitems. Append to the stereo audio track once,
after the loop finishes:
```python
    ET.SubElement(a_track, "enabled").text            = "TRUE"
    ET.SubElement(a_track, "locked").text             = "FALSE"
    ET.SubElement(a_track, "outputchannelindex").text = "1"
```
**Check:** these three are the LAST children of the audio `<track>`, after all clipitems.

---

## Acceptance (XML that parses is NOT enough — real import is the gate)
1. `python xml_assembler.py` → writes `Automated_Sequence.xml` from the built-in 2-channel demo.
   Confirm in the file: one `<track premiereTrackType="Stereo">`, an `<outputs>` group with
   `<numchannels>2`, file `<channelcount>2`, and one `clipitem-audio-N` per clip.
2. Grep clean: `grep -n "a_track_L\|a_track_R\|channel        =" xml_assembler.py` returns nothing.
3. **Premiere import (primary):** timeline shows ONE stereo track, audio PRESENT (not
   disappeared), in sync with video, in/out points unchanged vs the old two-track output.
4. **DaVinci Resolve import (sanity):** audio links as one stereo clip.

## Escalation (narrowed — only after all 6 tasks + real import)
If Premiere STILL drops the audio after tasks 1-6, leave
`# TODO(claude): F1 stereo — output bus added per spec, Premiere did <X>` and bounce to Claude.
Do not guess other XML permutations. If only Resolve regresses (Premiere fine), note it in the
TODO; Premiere is the priority.

## Out of scope
Pipeline logic, video side, in/out math, labels, the adaptive/CLI work.
