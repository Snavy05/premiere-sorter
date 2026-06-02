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
LABEL_FEW      = "Cerulean"   # 1-2 people
LABEL_CROWD    = "Mango"      # 3+ people
LABEL_BROLL    = "Rose"       # no people / background


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
    Convert an absolute file-system path to a file:// URL that Premiere
    can resolve on macOS, Windows, or Linux.

    Examples
    --------
    /Volumes/MEDIA/RHYC02026.MP4  ->  file:///Volumes/MEDIA/RHYC02026.MP4
    C:\\Users\\User\\clip.mp4      ->  file:///C:/Users/User/clip.mp4
    """
    p = Path(file_path).resolve()
    # On Windows, Path.as_posix() starts with a drive letter (e.g. C:/...)
    # so we need three slashes: file:///C:/...
    posix = p.as_posix()
    # quote() preserves forward-slashes and colons (safe=" :/")
    encoded = quote(posix, safe="/:@")
    if not encoded.startswith("/"):
        # Windows absolute path: C:/... needs an extra leading slash
        encoded = "/" + encoded
    return f"file://{encoded}"


def _get_label2(shot_tags: list[str]) -> str:
    """
    Map the YOLO classifier's shot_tags output to a Premiere Pro label.

    Priority rules (first match wins):
      1. '[<2 People]'         -> Cerulean  (1-2 persons)
      2. '[Multiple Subjects]' -> Mango     (3+ persons / crowd)
      3. anything else         -> Rose      (B-roll / no people)
    """
    if "[<2 People]" in shot_tags:
        return LABEL_FEW
    if "[Multiple Subjects]" in shot_tags:
        return LABEL_CROWD
    return LABEL_BROLL   # catches both [BRolls] and any empty list


def _format_tags(shot_tags: list[str]) -> str:
    """
    Return the tag list as a space-separated string, or '' if empty.
    Used to suffix clip names in the Project Bin.
    """
    return " ".join(shot_tags)


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

    # ── Timecode block (start = 00:00:00:00) ─────────────────────────────────
    tc = ET.SubElement(file_elem, "timecode")
    tc.append(_make_rate_elem(fps))
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text  = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

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

    # Audio stream description (one entry per channel pair)
    audio_elem = ET.SubElement(media_elem, "audio")
    sc_a = ET.SubElement(audio_elem, "samplecharacteristics")
    ET.SubElement(sc_a, "depth").text      = "16"
    ET.SubElement(sc_a, "samplerate").text = str(sample_rate)
    ET.SubElement(audio_elem, "channelcount").text = str(channels)

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
    shot_tags  = clip.get("shot_tags", [])
    clip_name  = Path(src_path).name

    # Keep display_name as the original filename so Premiere can auto-link
    # media.  The shot classification is conveyed via <label2> colour codes.
    display_name = clip_name

    # Clip duration in frames (used by several child elements)
    duration = out_frame - in_frame

    # Unique XML id for this clipitem element
    item_id = f"clipitem-{clip_index}"

    item = ET.Element("clipitem", id=item_id, premiereChannelType="stereo")

    # ── Core identity ─────────────────────────────────────────────────────────
    ET.SubElement(item, "masterclipid").text = f"masterclip-{clip_index}"
    ET.SubElement(item, "name").text         = display_name
    ET.SubElement(item, "enabled").text      = "TRUE"
    ET.SubElement(item, "duration").text     = str(duration)
    item.append(_make_rate_elem(fps))

    # ── Timeline placement ────────────────────────────────────────────────────
    ET.SubElement(item, "start").text = str(timeline_start)
    ET.SubElement(item, "end").text   = str(timeline_end)

    # ── Source trim points (the Physical Trimmer's output) ────────────────────
    ET.SubElement(item, "in").text  = str(in_frame)
    ET.SubElement(item, "out").text = str(out_frame)

    # ── Colour label (the Semantic Classifier's output) ───────────────────────
    # <labels> is the FCP7 container; Premiere reads <label2> for bin colours.
    labels = ET.SubElement(item, "labels")
    ET.SubElement(labels, "label2").text = _get_label2(shot_tags)

    # ── Source file definition (embedded inside the clipitem) ─────────────────
    # FCP7 XML requires the full <file> definition to be nested inside the
    # first <clipitem> that references it.  Subsequent clipitems on other
    # tracks use the short-form <file id="..."/> reference.
    item.append(file_elem)

    # ── Links — makes Premiere select+move video and both audio tracks together
    link_self = ET.SubElement(item, "link")
    ET.SubElement(link_self, "linkclipref").text = item_id
    ET.SubElement(link_self, "mediatype").text   = "video"
    ET.SubElement(link_self, "trackindex").text  = "1"
    ET.SubElement(link_self, "clipindex").text   = str(clip_index)

    for ach_track in (1, 2):
        link_a = ET.SubElement(item, "link")
        ET.SubElement(link_a, "linkclipref").text = f"clipitem-audio-{clip_index}-ch{ach_track}"
        ET.SubElement(link_a, "mediatype").text   = "audio"
        ET.SubElement(link_a, "trackindex").text  = str(ach_track)
        ET.SubElement(link_a, "clipindex").text   = str(clip_index)

    # ── Logging for the console summary ──────────────────────────────────────
    log.info(
        "  [%02d] %-35s  in=%-6d out=%-6d  dur=%-5d  label=%-10s  tags=%s",
        clip_index,
        display_name[:35],
        in_frame,
        out_frame,
        duration,
        _get_label2(shot_tags),
        shot_tags or "[]",
    )

    return item


def _make_audio_clipitem(
    clip: dict,
    clip_index: int,
    file_id: str,
    timeline_start: int,
    timeline_end: int,
    channel: int,
) -> ET.Element:
    """
    Build a mono <clipitem> for one audio channel track (channel=1 -> L, 2 -> R).

    Two of these (one per channel) are placed on two separate tracks, which is
    how Premiere Pro represents stereo from FCP7 XML.  All three clipitems
    (video + both audio) carry matching <link> elements so that clicking any
    one of them in the timeline selects and moves all three together.
    """
    src_path  = clip["src_path"]
    fps       = clip["fps"]
    in_frame  = clip["in_frame"]
    out_frame = clip["out_frame"]
    shot_tags = clip.get("shot_tags", [])
    duration  = out_frame - in_frame

    video_id = f"clipitem-{clip_index}"
    item_id  = f"clipitem-audio-{clip_index}-ch{channel}"
    item = ET.Element("clipitem", id=item_id)

    ET.SubElement(item, "masterclipid").text = f"masterclip-{clip_index}"
    ET.SubElement(item, "name").text         = Path(src_path).name
    ET.SubElement(item, "enabled").text      = "TRUE"
    ET.SubElement(item, "duration").text     = str(duration)
    item.append(_make_rate_elem(fps))

    ET.SubElement(item, "start").text = str(timeline_start)
    ET.SubElement(item, "end").text   = str(timeline_end)
    ET.SubElement(item, "in").text    = str(in_frame)
    ET.SubElement(item, "out").text   = str(out_frame)

    labels = ET.SubElement(item, "labels")
    ET.SubElement(labels, "label2").text = _get_label2(shot_tags)

    ET.SubElement(item, "file", id=file_id)

    # Which source channel this track carries
    src_track = ET.SubElement(item, "sourcetrack")
    ET.SubElement(src_track, "mediatype").text  = "audio"
    ET.SubElement(src_track, "trackindex").text = str(channel)

    # Link to video
    link_v = ET.SubElement(item, "link")
    ET.SubElement(link_v, "linkclipref").text = video_id
    ET.SubElement(link_v, "mediatype").text   = "video"
    ET.SubElement(link_v, "trackindex").text  = "1"
    ET.SubElement(link_v, "clipindex").text   = str(clip_index)

    # Link to both audio tracks (self + sibling channel)
    for ach_track in (1, 2):
        link_a = ET.SubElement(item, "link")
        ET.SubElement(link_a, "linkclipref").text = f"clipitem-audio-{clip_index}-ch{ach_track}"
        ET.SubElement(link_a, "mediatype").text   = "audio"
        ET.SubElement(link_a, "trackindex").text  = str(ach_track)
        ET.SubElement(link_a, "clipindex").text   = str(clip_index)

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

    # ── Audio branch (two mono tracks = stereo pair in Premiere) ─────────────
    audio_branch = ET.SubElement(media, "audio")
    a_track_L = ET.SubElement(audio_branch, "track")   # Left  (ch 1)
    a_track_R = ET.SubElement(audio_branch, "track")   # Right (ch 2)

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

        # ── Two mono audio clipitems (L + R) for proper stereo ───────────────
        a_track_L.append(
            _make_audio_clipitem(
                clip           = active_clip,
                clip_index     = idx,
                file_id        = file_id,
                timeline_start = timeline_cursor,
                timeline_end   = timeline_cursor + duration,
                channel        = 1,
            )
        )
        a_track_R.append(
            _make_audio_clipitem(
                clip           = active_clip,
                clip_index     = idx,
                file_id        = file_id,
                timeline_start = timeline_cursor,
                timeline_end   = timeline_cursor + duration,
                channel        = 2,
            )
        )

        # Advance the playhead to the end of this clip
        timeline_cursor += duration

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

    # Log the colour-label breakdown before building the XML
    labels = [_get_label2(c.get("shot_tags", [])) for c in clip_data]
    log.info(
        "Label summary  : %s Cerulean (<2 People) · %s Mango (Crowd) · %s Rose (B-roll)",
        labels.count(LABEL_FEW),
        labels.count(LABEL_CROWD),
        labels.count(LABEL_BROLL),
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
        # 1-2 people detected — Cerulean label.
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
        "shot_tags":    ["[<2 People]"],
    },
    {
        # 3+ people (crowd / group) — Mango label.
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
        "shot_tags":    ["[Multiple Subjects]"],
    },
    {
        # No person detected / B-roll — Rose label.
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
        "shot_tags":    ["[BRolls]"],
    },
    {
        # Another 1-2 people clip at 29.97 fps.
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
        "shot_tags":    ["[<2 People]"],
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
