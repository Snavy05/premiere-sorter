# SPEC — F1: one stereo audio track, not two

**Problem:** `xml_assembler.py` emits TWO mono audio entries per clip (`:245-254`, two
`<audio>` SubElements, `channelcount=1` each). Premiere/Resolve shows two separate audio
tracks. Tester wants ONE stereo track (L/R linked) on the timeline.

**Heads-up:** a prior attempt at this FAILED. The hard part is the exact FCP7 XML shape for
a single 2-channel linked clip. This is a known trap — see escalation rule below.

## Task
In `xml_assembler.py`, change the audio emission so one source clip produces a single
**stereo** audio item (2 channels in one track) instead of two mono tracks. The video side
and clip in/out points must not change.

Reference shape to match (FCP7 / Premiere stereo linked clip): one `<audio>` media def with
`<channelcount>2</channelcount>`, and the timeline audio represented as a single track whose
`<clipitem>` links both channels (Premiere uses `premiereChannelType` / `sourcetrack`
+ `trackindex` 1 and 2 under one linked group). The existing `premiereChannelType="stereo"`
on the clipitem (`:290`) is a starting clue but is clearly not sufficient alone.

## Acceptance (MUST verify by real import — XML that parses is not enough)
1. Generate XML for a 2-channel source clip.
2. Import into Premiere Pro.
3. Timeline shows the clip's audio as **ONE stereo track** (L+R), audio in sync with video,
   in/out points unchanged vs current output.
4. Also sanity-import into DaVinci Resolve (it's stricter on per-channel layout).

## Escalation
If you can't get Premiere to show a single stereo track after ~one focused attempt, STOP —
leave a `# TODO(claude): F1 stereo XML shape — <what you tried, what Premiere did>` and
bounce it back. This is a reasoning/format-reverse-engineering task, not pure mechanical
wiring; don't burn a day guessing XML permutations.
