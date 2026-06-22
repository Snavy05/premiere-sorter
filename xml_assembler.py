"""
xml_assembler.py — Phase 4: FCP7 XML Sequence Assembler
=========================================================
Consumes the enriched clip dictionaries produced by Phases 1–3 of the
SteadyCut pipeline and writes a Final Cut Pro 7 XML file that Premiere
Pro can import directly as a fully-populated, colour-coded timeline.

What this script does
---------------------
  • Reads a list of clip dicts, each carrying:
        src_path   — absolute path to the proxy (or original) video file
        in_frame   — stable-window start frame  (Phase 2 output)
        out_frame  — stable-window end frame    (Phase 2 output)
        fps        — clip frame rate
        shot_tags  — YOLO classifier result     (Phase 3 output)
        width, height, sample_rate, channels    (optional; probed by pipeline)

  • Maps shot_tags -> Premiere Pro <label2> colour:
        ['[<2 People]']         ->  Cerulean  (1-2 persons)
        ['[Multiple Subjects]'] ->  Mango     (3+ persons)
        ['[BRolls]'] / []       ->  Rose      (no people / background)

  • Appends the joined tags to each clip's <name> so they are visible
    in the Premiere Project Bin (e.g. "RHYC02026.MP4 [<2 People]").

  • Lays all clips end-to-end on a single video track + two audio tracks,
    using cumulative frame offsets for <start>/<end> (timeline position)
    and in_frame/out_frame for <in>/<out> (source trim points).

  • Writes the result to  Automated_Sequence.xml  (UTF-8, pretty-printed).

Integration
-----------
Drop-in replacement for the Phase 3 XML block inside pipelinev3.py:

    from xml_assembler import assemble_xml
    assemble_xml(clip_data, output_path=Path("Automated_Sequence.xml"))

Or run standalone:

    python xml_assembler.py          # uses the built-in demo dataset

Tested against Premiere Pro 2023 / 2024 FCP7 XML import.
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path
from urllib.parse import quote   # for building safe file:// URLs
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("xml_assembler")

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

OUTPUT_FILENAME = "Automated_Sequence.xml"

# Default frame dimensions used when a clip dict omits width/height.
DEFAULT_WIDTH  = 1280
DEFAULT_HEIGHT = 720

# Default audio spec used when a clip dict omits audio metadata.
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS    = 2

# Premiere Pro <label2> colour names (case-sensitive, must match Premiere's
# internal label set exactly).
LABEL_PERSON   = "Cerulean"   # person detected (1-2 people / action)
LABEL_CROWD    = "Mango"      # 3+ people
LABEL_BROLL    = "Rose"       # no people / background
LABEL_NO_COA   = "Lavender"   # COA active but no action peak detected
LABEL_MULTIWIN = "Caribbean"  # one of several stable windows from the same source clip

# Unified label_reason → Premiere colour map.
# Set by the pipeline on each clip dict; XML assembler reads this only.
_LABEL_MAP: dict[str, str] = {
    "action":      LABEL_PERSON,  # pose detected person doing action
    "person":      LABEL_PERSON,  # YOLO found 1-2 persons in stable window
    "crowd":       LABEL_CROWD,   # YOLO found 3+ persons
    "broll":       LABEL_BROLL,   # no person / background
    "no_coa_peak": LABEL_NO_COA,  # COA ran but found no action peak
    "review":      LABEL_NO_COA,  # failed analysis — kept on timeline, flag for manual redo
}


# ═════════════════════════════════════════════════════════════════════════════
# Helper Utilities
# ═════════════════════════════════════════════════════════════════════════════

def _is_ntsc(fps: float) -> bool:
    """
    Return True for drop-frame NTSC rates (23.976, 29.97, 47.952, 59.94 ...).
    FCP7 XML requires <ntsc>TRUE</ntsc> for these rates so that Premiere
    calculates drop-frame timecode correctly.
    A frame rate is NTSC when it is not very close to an integer value.
    """
    return abs(fps - round(fps)) > 0.005


def _timebase(fps: float) -> int:
    """
    FCP7 <timebase> is always the nearest integer to the frame rate.
    E.g. 29.97 -> 30,  23.976 -> 24,  25.0 -> 25.
    """
    return round(fps)


def _path_to_url(file_path: str) -> str:
    """
    Convert an absolute path to a file:// URL accepted by both Premiere and
    DaVinci Resolve.

    POSIX paths keep the file://localhost/// form Premiere/Resolve expect on
    macOS. Windows drive paths use a SINGLE slash after the host
    (file://localhost/C:/...) — the triple-slash form produced "//C:/..." which
    Premiere on Windows reads as a UNC network path, forcing a manual relink.

    /Volumes/MEDIA/clip.MP4   ->  file://localhost///Volumes/MEDIA/clip.MP4
    C:\\Users\\User\\clip.mp4 ->  file://localhost/C:/Users/User/clip.mp4
    """
    p = Path(file_path).resolve()
    posix = p.as_posix()                          # always forward-slashes
    encoded = quote(posix, safe="/:@")
    if len(posix) >= 2 and posix[1] == ":":
        # Windows drive path (e.g. C:/Users/...): single slash after the host.
        return f"file://localhost/{encoded}"
    # POSIX absolute path: preserve the triple-slash form that already works.
    encoded = encoded.lstrip("/")
    return f"file://localhost///{encoded}"


def _label_from_reason(reason: str) -> str:
    """Return Premiere <label2> colour for a clip's label_reason field."""
    return _LABEL_MAP.get(reason, LABEL_BROLL)


def _label_for_clip(clip: dict) -> str:
    """
    Premiere <label2> colour for a clip. Clips that are one of several stable
    windows extracted from the same source file get a distinct colour
    (LABEL_MULTIWIN) so the editor can spot multi-window sources at a glance;
    this intentionally overrides the classification colour for those clips.
    Everything else colours by its label_reason.
    """
    if clip.get("multi_window"):
        return LABEL_MULTIWIN
    return _label_from_reason(clip.get("label_reason", "broll"))


# ═════════════════════════════════════════════════════════════════════════════
# XML Element Builders
# ═════════════════════════════════════════════════════════════════════════════

def _make_rate_elem(fps: float) -> ET.Element:
    """
    Build a reusable <rate> element:
        <rate>
            <timebase>25</timebase>
            <ntsc>FALSE</ntsc>
        </rate>
    """
    rate = ET.Element("rate")
    ET.SubElement(rate, "timebase").text = str(_timebase(fps))
    ET.SubElement(rate, "ntsc").text = "TRUE" if _is_ntsc(fps) else "FALSE"
    return rate


def _make_file_elem(
    clip: dict,
    file_id: str,
) -> ET.Element:
    """
    Build the <file> element that describes the source media.
    Premiere reads <pathurl> to locate the file on disk and <name> to
    display it in the Project Bin.

        <file id="file-1">
            <name>RHYC02026.MP4</name>
            <pathurl>file:///Volumes/...</pathurl>
            <rate> ... </rate>
            <duration>TOTAL_SOURCE_FRAMES</duration>
            <timecode> ... </timecode>
            <media>
                <video> ... </video>
                <audio> ... </audio>
            </media>
        </file>
    """
    src_path   = clip["src_path"]
    fps        = clip["fps"]
    width      = clip.get("width",  DEFAULT_WIDTH)
    height     = clip.get("height", DEFAULT_HEIGHT)
    sample_rate = clip.get("sample_rate", DEFAULT_SAMPLE_RATE)
    channels   = clip.get("channels",    DEFAULT_CHANNELS)

    # Total frame count of the source file.  Falls back to out_frame when
    # total_frames is absent so the file duration is at least valid.
    total_frames = clip.get("total_frames", clip["out_frame"])

    file_elem = ET.Element("file", id=file_id)

    # ── Identity ─────────────────────────────────────────────────────────────
    ET.SubElement(file_elem, "name").text = Path(src_path).name
    ET.SubElement(file_elem, "pathurl").text = _path_to_url(src_path)
    file_elem.append(_make_rate_elem(fps))
    ET.SubElement(file_elem, "duration").text = str(total_frames)

    # ── Timecode block (actual embedded file TC) ─────────────────────────────
    # DaVinci Resolve validates that the XML's declared TC range overlaps with
    # the file's embedded timecode. Cameras record time-of-day TC (e.g. 03:09:50:44),
    # so we must declare that here rather than 00:00:00:00.
    tc_string = clip.get("tc_string", "00:00:00:00")
    tc_frame  = clip.get("tc_frame",  0)
    df_format = "DF" if _is_ntsc(fps) else "NDF"

    tc = ET.SubElement(file_elem, "timecode")
    tc.append(_make_rate_elem(fps))
    ET.SubElement(tc, "string").text = tc_string
    ET.SubElement(tc, "frame").text  = str(tc_frame)
    ET.SubElement(tc, "displayformat").text = df_format

    # ── Media characteristics ─────────────────────────────────────────────────
    media_elem = ET.SubElement(file_elem, "media")

    # Video stream description
    video_elem = ET.SubElement(media_elem, "video")
    sc_v = ET.SubElement(video_elem, "samplecharacteristics")
    sc_v.append(_make_rate_elem(fps))
    ET.SubElement(sc_v, "width").text          = str(width)
    ET.SubElement(sc_v, "height").text         = str(height)
    ET.SubElement(sc_v, "anamorphic").text     = "FALSE"
    ET.SubElement(sc_v, "pixelaspectratio").text = "square"
    ET.SubElement(sc_v, "fielddominance").text = "none"

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

    return file_elem


def _make_video_clipitem(
    clip: dict,
    clip_index: int,
    file_id: str,
    file_elem: ET.Element,
    timeline_start: int,
    timeline_end: int,
) -> ET.Element:
    """
    Build the <clipitem> element for the video track.

    Key frame arithmetic
    --------------------
    <in>    / <out>   = source trim points  (in_frame / out_frame from Phase 2)
    <start> / <end>   = timeline positions  (cumulative frame offsets)

    This separation is what allows Premiere to show only the stable window
    even though the file itself is much longer.
    """
    src_path   = clip["src_path"]
    fps        = clip["fps"]
    in_frame   = clip["in_frame"]
    out_frame  = clip["out_frame"]
    label_reason = clip.get("label_reason", "broll")
    display_name = clip.get("name") or Path(src_path).name

    duration = out_frame - in_frame
    item_id  = f"clipitem-{clip_index}"

    item = ET.Element("clipitem", id=item_id, premiereChannelType="stereo")

    ET.SubElement(item, "masterclipid").text = f"masterclip-{clip_index}"
    ET.SubElement(item, "name").text         = display_name
    ET.SubElement(item, "enabled").text      = "TRUE"
    ET.SubElement(item, "duration").text     = str(duration)
    item.append(_make_rate_elem(fps))
    ET.SubElement(item, "start").text = str(timeline_start)
    ET.SubElement(item, "end").text   = str(timeline_end)
    ET.SubElement(item, "in").text    = str(in_frame)
    ET.SubElement(item, "out").text   = str(out_frame)
    ET.SubElement(item, "alphatype").text        = "none"
    ET.SubElement(item, "pixelaspectratio").text = "square"
    ET.SubElement(item, "anamorphic").text       = "FALSE"

    # Full <file> definition embedded in the first clipitem per source
    item.append(file_elem)

    # Links — video self + two audio channels with groupindex for Resolve
    link_self = ET.SubElement(item, "link")
    ET.SubElement(link_self, "linkclipref").text = item_id
    ET.SubElement(link_self, "mediatype").text   = "video"
    ET.SubElement(link_self, "trackindex").text  = "1"
    ET.SubElement(link_self, "clipindex").text   = str(clip_index)

    link_a = ET.SubElement(item, "link")
    ET.SubElement(link_a, "linkclipref").text = f"clipitem-audio-{clip_index}"
    ET.SubElement(link_a, "mediatype").text   = "audio"
    ET.SubElement(link_a, "trackindex").text  = "1"
    ET.SubElement(link_a, "clipindex").text   = str(clip_index)
    ET.SubElement(link_a, "groupindex").text  = "1"

    labels = ET.SubElement(item, "labels")
    ET.SubElement(labels, "label2").text = _label_for_clip(clip)

    log.info(
        "  [%02d] %-35s  in=%-6d out=%-6d  dur=%-5d  label=%-10s  reason=%s",
        clip_index,
        display_name[:35],
        in_frame,
        out_frame,
        duration,
        _label_for_clip(clip),
        label_reason,
    )

    return item


def _make_audio_clipitem(
    clip: dict,
    clip_index: int,
    file_id: str,
    timeline_start: int,
    timeline_end: int,
) -> ET.Element:
    """
    Build a stereo <clipitem> for the single audio track.

    One per clip, linked to the video clipitem via matching <link> elements so
    that clicking either in the timeline selects and moves both together.
    """
    src_path     = clip["src_path"]
    fps          = clip["fps"]
    in_frame     = clip["in_frame"]
    out_frame    = clip["out_frame"]
    duration     = out_frame - in_frame

    video_id = f"clipitem-{clip_index}"
    item_id  = f"clipitem-audio-{clip_index}"
    item = ET.Element("clipitem", id=item_id, premiereChannelType="stereo")

    ET.SubElement(item, "masterclipid").text = f"masterclip-{clip_index}"
    ET.SubElement(item, "name").text         = clip.get("name") or Path(src_path).name
    ET.SubElement(item, "enabled").text      = "TRUE"
    ET.SubElement(item, "duration").text     = str(duration)
    item.append(_make_rate_elem(fps))

    ET.SubElement(item, "start").text = str(timeline_start)
    ET.SubElement(item, "end").text   = str(timeline_end)
    ET.SubElement(item, "in").text    = str(in_frame)
    ET.SubElement(item, "out").text   = str(out_frame)

    ET.SubElement(item, "file", id=file_id)

    src_track = ET.SubElement(item, "sourcetrack")
    ET.SubElement(src_track, "mediatype").text  = "audio"
    ET.SubElement(src_track, "trackindex").text = "1"

    link_v = ET.SubElement(item, "link")
    ET.SubElement(link_v, "linkclipref").text = video_id
    ET.SubElement(link_v, "mediatype").text   = "video"
    ET.SubElement(link_v, "trackindex").text  = "1"
    ET.SubElement(link_v, "clipindex").text   = str(clip_index)

    link_a = ET.SubElement(item, "link")
    ET.SubElement(link_a, "linkclipref").text = item_id
    ET.SubElement(link_a, "mediatype").text   = "audio"
    ET.SubElement(link_a, "trackindex").text  = "1"
    ET.SubElement(link_a, "clipindex").text   = str(clip_index)
    ET.SubElement(link_a, "groupindex").text  = "1"

    labels = ET.SubElement(item, "labels")
    ET.SubElement(labels, "label2").text = _label_for_clip(clip)

    return item


# ═════════════════════════════════════════════════════════════════════════════
# Sequence Builder
# ═════════════════════════════════════════════════════════════════════════════

def _build_sequence(
    clip_data: list[dict],
    sequence_fps: float,
    cut_on_action_mode: str = "off",
) -> ET.Element:
    """
    Construct the complete FCP7 <sequence> element tree from clip_data.

    Structure
    ---------
    <sequence>
      <name> ... </name>
      <duration> ... </duration>       ← total timeline length in frames
      <rate> ... </rate>
      <media>
        <video>
          <format> ... </format>       ← sequence video characteristics
          <track>                    ← one video track
            <file .../>               ← <file> blocks declared once per clip
            <clipitem .../>           ← one per clip
          </track>
        </video>
        <audio>
          <track> ... </track>         ← one stereo track (both channels)
        </audio>
      </media>
    </sequence>
    """

    # ── Sequence-level metadata ───────────────────────────────────────────────
    seq = ET.Element("sequence")
    ET.SubElement(seq, "name").text    = "Automated Sequence"
    ET.SubElement(seq, "duration").text = str(
        sum(c["out_frame"] - c["in_frame"] for c in clip_data)
    )
    seq.append(_make_rate_elem(sequence_fps))

    # Pick sequence dimensions by majority vote so a single odd clip doesn't
    # set the canvas for everything. Falls back to the first clip on a tie.
    from collections import Counter
    dim_counts = Counter(
        (c.get("width", DEFAULT_WIDTH), c.get("height", DEFAULT_HEIGHT))
        for c in clip_data
    )
    seq_width, seq_height = dim_counts.most_common(1)[0][0]
    if len(dim_counts) > 1:
        log.warning(
            "Clips have mixed dimensions %s — sequence canvas set to %dx%d "
            "(most common). Other clips will be pillarboxed/letterboxed by Premiere.",
            dict(dim_counts), seq_width, seq_height,
        )

    # ── <media> root ──────────────────────────────────────────────────────────
    media = ET.SubElement(seq, "media")

    # ── Video branch ──────────────────────────────────────────────────────────
    video_branch = ET.SubElement(media, "video")

    # Sequence-level <format> block (tells Premiere the canvas size/rate)
    fmt = ET.SubElement(video_branch, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    sc.append(_make_rate_elem(sequence_fps))
    ET.SubElement(sc, "width").text          = str(seq_width)
    ET.SubElement(sc, "height").text         = str(seq_height)
    ET.SubElement(sc, "anamorphic").text     = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"

    # Single video track
    v_track = ET.SubElement(video_branch, "track")

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

    # ── Clip loop — compute cumulative timeline offsets ───────────────────────
    timeline_cursor = 0   # running frame count; advances after each clip

    for idx, clip in enumerate(clip_data, start=1):
        in_frame    = clip["in_frame"]
        cut_frame   = clip.get("cut_frame")

        # ── Cut on Action: resolve effective out_frame ────────────────────────
        # "cut" mode: trim clip to the action peak (cut_frame replaces out_frame)
        # "mark" mode: keep full stable window; marker added to video clipitem
        # "off" / fallback: use the original out_frame unchanged
        if cut_on_action_mode == "cut" and cut_frame is not None:
            out_frame = cut_frame
        else:
            out_frame = clip["out_frame"]

        duration  = out_frame - in_frame

        if duration <= 0:
            log.warning(
                "  [%02d] Skipping clip with non-positive duration "
                "(in=%d, out=%d): %s",
                idx, in_frame, out_frame, clip["src_path"],
            )
            continue

        # Unique stable id used to cross-reference <file> across v/a tracks
        file_id   = f"file-{idx}"

        # ── Build the full <file> definition block ────────────────────────────
        file_elem = _make_file_elem(clip, file_id)

        # ── Video clipitem (contains the full <file> definition) ─────────────
        # Build first, then conditionally append a marker before adding to track
        active_clip = dict(clip, out_frame=out_frame)  # use resolved out_frame
        vi = _make_video_clipitem(
            clip           = active_clip,
            clip_index     = idx,
            file_id        = file_id,
            file_elem      = file_elem,
            timeline_start = timeline_cursor,
            timeline_end   = timeline_cursor + duration,
        )

        # "mark" mode: add a Premiere marker at the action peak frame.
        # <in> is clip-local (0-indexed from the clip's in_frame).
        # <out> = -1 signals a point marker (no range) — required by Premiere.
        if cut_on_action_mode == "mark" and cut_frame is not None:
            marker_local = cut_frame - in_frame
            marker = ET.SubElement(vi, "marker")
            ET.SubElement(marker, "comment").text = "CutOnAction"
            ET.SubElement(marker, "name").text    = "Suggested Cut"
            ET.SubElement(marker, "in").text      = str(marker_local)
            ET.SubElement(marker, "out").text     = "-1"
            log.info("  [%02d] Cut-on-Action marker at source frame %d (clip-local %d)",
                     idx, cut_frame, marker_local)

        v_track.append(vi)

        # ── One stereo audio clipitem ────────────────────────────────────────
        a_track.append(
            _make_audio_clipitem(
                clip           = active_clip,
                clip_index     = idx,
                file_id        = file_id,
                timeline_start = timeline_cursor,
                timeline_end   = timeline_cursor + duration,
            )
        )

        # Advance the playhead to the end of this clip
        timeline_cursor += duration

    ET.SubElement(a_track, "enabled").text            = "TRUE"
    ET.SubElement(a_track, "locked").text             = "FALSE"
    ET.SubElement(a_track, "outputchannelindex").text = "1"

    return seq


# ═════════════════════════════════════════════════════════════════════════════
# XML Serialisation
# ═════════════════════════════════════════════════════════════════════════════

def _serialise_xml(sequence_elem: ET.Element) -> str:
    """
    Wrap the <sequence> element in the FCP7 <xmeml> root, pretty-print it
    with minidom, and return the complete XML string.

    The DOCTYPE declaration is intentionally omitted: Premiere Pro does not
    require it and some systems raise validation errors if it is present.
    """
    # Build the <xmeml> root (FCP7 wrapper that Premiere Pro recognises)
    root = ET.Element("xmeml", version="5")
    root.append(sequence_elem)

    # Serialise to a raw byte string first
    raw_bytes = ET.tostring(root, encoding="unicode")

    # Pretty-print via minidom for human-readability
    pretty = minidom.parseString(raw_bytes).toprettyxml(indent="  ", encoding=None)

    # minidom adds its own <?xml ...?> declaration; keep it.
    return pretty


def _save_xml(xml_str: str, output_path: Path) -> None:
    """Write the XML string to disk and log confirmation."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_str, encoding="utf-8")
    log.info("XML written -> %s  (%d bytes)", output_path, output_path.stat().st_size)


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════

def assemble_xml(
    clip_data: list[dict],
    output_path: Path | str = OUTPUT_FILENAME,
    fps_override: float | None = None,
    cut_on_action_mode: str = "off",
) -> Path:
    """
    Build and save the FCP7 XML sequence from *clip_data*.

    Parameters
    ----------
    clip_data : list[dict]
        One dict per clip.  Required keys:
            src_path   (str)        — absolute path to the video file
            in_frame   (int)        — stable window start frame
            out_frame  (int)        — stable window end frame
            fps        (float)      — clip frame rate
            shot_tags  (list[str])  — e.g. ['[<2 People]'] / ['[Multiple Subjects]'] / ['[BRolls]']
        Optional keys (default values used when absent):
            width, height (int)     — frame dimensions in pixels
            sample_rate   (int)     — audio sample rate in Hz
            channels      (int)     — audio channel count
            total_frames  (int)     — full source file length in frames
            name          (str)     — display name override

    output_path : Path | str
        Where to write the XML.  Defaults to 'Automated_Sequence.xml' in
        the current working directory.

    fps_override : float | None
        Force a specific sequence frame rate.  When None (default), the
        most common fps value across all clips is used.

    Returns
    -------
    Path
        The resolved path of the written XML file.

    Raises
    ------
    ValueError
        If *clip_data* is empty.
    """

    # ── Guard: abort gracefully on empty input ────────────────────────────────
    if not clip_data:
        log.error(
            "clip_data is empty — nothing to assemble.  "
            "Ensure Phases 1–3 have produced valid output before calling "
            "assemble_xml()."
        )
        raise ValueError(
            "clip_data list is empty.  No XML will be written."
        )

    output_path = Path(output_path)

    log.info("=" * 60)
    log.info("PHASE 4 — FCP7 XML Assembly")
    log.info("=" * 60)
    log.info("Clips received : %d", len(clip_data))

    # ── Determine sequence frame rate ─────────────────────────────────────────
    if fps_override is not None:
        sequence_fps = fps_override
        log.info("Sequence FPS   : %.4f  (override)", sequence_fps)
    else:
        # Vote on the most common fps among all clips
        fps_votes: dict[float, int] = {}
        for c in clip_data:
            fps_votes[c["fps"]] = fps_votes.get(c["fps"], 0) + 1
        sequence_fps = max(fps_votes, key=fps_votes.get)
        log.info(
            "Sequence FPS   : %.4f  (majority vote from %d clips)",
            sequence_fps,
            len(clip_data),
        )

    # Log label breakdown using unified label_reason field
    _labels = [_label_from_reason(c.get("label_reason", "broll")) for c in clip_data]
    log.info(
        "Label summary  : %s Cerulean (person/action) · %s Mango (crowd) · %s Rose (broll/unknown) · %s Lavender (no peak)",
        _labels.count(LABEL_PERSON),
        _labels.count(LABEL_CROWD),
        _labels.count(LABEL_BROLL),
        _labels.count(LABEL_NO_COA),
    )
    log.info("-" * 60)

    # ── Build the element tree ────────────────────────────────────────────────
    sequence_elem = _build_sequence(clip_data, sequence_fps, cut_on_action_mode)

    # ── Serialise and save ────────────────────────────────────────────────────
    xml_string = _serialise_xml(sequence_elem)
    _save_xml(xml_string, output_path)

    log.info("=" * 60)
    log.info("Assembly complete!  Drag '%s' into Premiere Pro.", output_path.name)
    log.info("=" * 60)

    return output_path.resolve()


# ═════════════════════════════════════════════════════════════════════════════
# Standalone Demo (run with:  python xml_assembler.py)
# ═════════════════════════════════════════════════════════════════════════════

_DEMO_CLIP_DATA: list[dict] = [
    {
        "name":         "RHYC02026.MP4",
        "src_path":     "/Volumes/MEDIA/RHYC02026.MP4",
        "in_frame":     312,
        "out_frame":    987,
        "fps":          25.0,
        "total_frames": 1500,
        "width":        1920,
        "height":       1080,
        "sample_rate":  48000,
        "channels":     2,
        "label_reason": "action",
    },
    {
        "name":         "RHYC02031.MP4",
        "src_path":     "/Volumes/MEDIA/RHYC02031.MP4",
        "in_frame":     75,
        "out_frame":    650,
        "fps":          25.0,
        "total_frames": 900,
        "width":        1920,
        "height":       1080,
        "sample_rate":  48000,
        "channels":     2,
        "label_reason": "crowd",
    },
    {
        "name":         "RHYC02044.MP4",
        "src_path":     "/Volumes/MEDIA/RHYC02044.MP4",
        "in_frame":     200,
        "out_frame":    820,
        "fps":          29.97,
        "total_frames": 1200,
        "width":        1920,
        "height":       1080,
        "sample_rate":  48000,
        "channels":     2,
        "label_reason": "broll",
    },
    {
        "name":         "RHYC02051.MP4",
        "src_path":     "/Volumes/MEDIA/RHYC02051.MP4",
        "in_frame":     500,
        "out_frame":    1100,
        "fps":          29.97,
        "total_frames": 1800,
        "width":        1920,
        "height":       1080,
        "sample_rate":  48000,
        "channels":     2,
        "label_reason": "person",
    },
]


def main() -> None:
    """Standalone entry-point: run the assembler on the built-in demo dataset."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Running xml_assembler.py in standalone demo mode.")
    log.info("(Pass your real clip_data list by calling assemble_xml() directly.)")
    log.info("")

    try:
        out = assemble_xml(_DEMO_CLIP_DATA, output_path=OUTPUT_FILENAME)
        print(f"\n  [OK]  XML saved -> {out}")
        print("     Drag it into Premiere Pro's Project Panel to populate the timeline.\n")
    except ValueError as exc:
        # assemble_xml already logged the details; just exit cleanly.
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
