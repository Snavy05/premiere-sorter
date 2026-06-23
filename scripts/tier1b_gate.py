#!/usr/bin/env python3
"""Throwaway Tier-1b gate: 2-clip .drt from duplicate golden clip 8629."""

from __future__ import annotations

import copy
import uuid
import zipfile
from pathlib import Path

from lxml import etree

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "samples" / "drt_template"
OUT_PATH = Path(__file__).resolve().parent.parent / "samples" / "tier1b_duplicate_test.drt"
GOLDEN_NAME = "Hoang.dv8629.MP4"
SEQ_FILENAME = "d3bf0b9d-06a1-4a71-afda-555e696ea720.xml"


def _find_child(parent: etree._Element, tag: str) -> etree._Element | None:
    for child in parent:
        if etree.QName(child).localname == tag:
            return child
    return None


def _find_text(parent: etree._Element, tag: str) -> str | None:
    el = _find_child(parent, tag)
    return el.text if el is not None else None


def _set_text(parent: etree._Element, tag: str, value: str) -> None:
    el = _find_child(parent, tag)
    if el is None:
        raise ValueError(f"Missing <{tag}> under {etree.QName(parent).localname}")
    el.text = value


def _find_by_name(items_parent: etree._Element, clip_tag: str, name: str) -> etree._Element:
    for element_wrapper in items_parent:
        for child in element_wrapper:
            if etree.QName(child).localname != clip_tag:
                continue
            name_el = _find_child(child, "Name")
            if name_el is not None and name_el.text == name:
                return child
    raise ValueError(f"No {clip_tag} named {name!r}")


def _clone_clip_pair(
    golden_video: etree._Element,
    golden_audio: etree._Element,
    golden_pool: etree._Element,
    *,
    start: int,
    duration: int,
    pool_db_id: str,
    pool_item_id: str,
    video_db_id: str,
    audio_db_id: str,
) -> tuple[etree._Element, etree._Element, etree._Element]:
    pool = copy.deepcopy(golden_pool)
    pool.set("DbId", pool_db_id)
    _set_text(pool, "UniqueMediaPoolItemId", pool_item_id)

    video = copy.deepcopy(golden_video)
    video.set("DbId", video_db_id)
    _set_text(video, "Start", str(start))
    _set_text(video, "Duration", str(duration))
    _set_text(video, "MediaRef", pool_db_id)

    audio = copy.deepcopy(golden_audio)
    audio.set("DbId", audio_db_id)
    _set_text(audio, "Start", str(start))
    _set_text(audio, "Duration", str(duration))
    _set_text(audio, "MediaRef", pool_db_id)

    return pool, video, audio


def _serialize(root: etree._Element, original_path: Path) -> bytes:
    # Preserve XML declaration style from template (no re-indent of blob text).
    raw = etree.tostring(
        root,
        xml_declaration=False,
        encoding="UTF-8",
        pretty_print=False,
    )
    decl = b'<?xml version="1.0" encoding="UTF-8"?>'
    comment = original_path.read_text(encoding="utf-8").split("\n", 2)[1]
    if comment.startswith("<!--"):
        return decl + b"\n" + comment.encode("utf-8") + b"\n" + raw
    return decl + b"\n" + raw


def build_tier1b() -> Path:
    project_path = TEMPLATE_DIR / "project.xml"
    mp_path = TEMPLATE_DIR / "MediaPool" / "Master" / "MpFolder.xml"
    seq_path = TEMPLATE_DIR / "SeqContainer" / SEQ_FILENAME

    mp_root = etree.parse(str(mp_path), etree.XMLParser(recover=True)).getroot()
    seq_root = etree.parse(str(seq_path)).getroot()
    project_bytes = project_path.read_bytes()

    media_vec = _find_child(mp_root, "MediaVec")
    assert media_vec is not None

    golden_pool = None
    for element_wrapper in media_vec:
        for child in element_wrapper:
            if etree.QName(child).localname == "Sm2MpVideoClip":
                name_el = _find_child(child, "Name")
                if name_el is not None and name_el.text == GOLDEN_NAME:
                    golden_pool = child
                    break
        if golden_pool is not None:
            break
    if golden_pool is None:
        raise RuntimeError(f"Golden pool clip {GOLDEN_NAME!r} not found")

    video_track = _find_child(_find_child(seq_root, "VideoTrackVec"), "Element")
    video_items = _find_child(_find_child(video_track, "Sm2TiTrack"), "Items")
    golden_video = _find_by_name(video_items, "Sm2TiVideoClip", GOLDEN_NAME)

    audio_track = _find_child(_find_child(seq_root, "AudioTrackVec"), "Element")
    audio_items = _find_child(_find_child(audio_track, "Sm2TiTrack"), "Items")
    golden_audio = _find_by_name(audio_items, "Sm2TiAudioClip", GOLDEN_NAME)

    # Remove golden pool clip wrappers; keep Sm2MpTimelineClip (first MediaVec entry).
    for element_wrapper in list(media_vec):
        for child in element_wrapper:
            if etree.QName(child).localname == "Sm2MpVideoClip":
                media_vec.remove(element_wrapper)
                break

    # Two clones of 8629 at different timeline positions; blobs verbatim.
    clip_specs = [
        {"start": 215784, "duration": 390},
        {"start": 216174, "duration": 390},
    ]
    pool_clips: list[etree._Element] = []
    video_clips: list[etree._Element] = []
    audio_clips: list[etree._Element] = []

    for spec in clip_specs:
        pool_db_id = str(uuid.uuid4())
        pool_item_id = str(uuid.uuid4())
        video_db_id = str(uuid.uuid4())
        audio_db_id = str(uuid.uuid4())
        pool, video, audio = _clone_clip_pair(
            golden_video,
            golden_audio,
            golden_pool,
            start=spec["start"],
            duration=spec["duration"],
            pool_db_id=pool_db_id,
            pool_item_id=pool_item_id,
            video_db_id=video_db_id,
            audio_db_id=audio_db_id,
        )
        pool_clips.append(pool)
        video_clips.append(video)
        audio_clips.append(audio)

    for pool in pool_clips:
        wrapper = etree.Element("Element")
        wrapper.append(pool)
        media_vec.append(wrapper)

    for child in list(video_items):
        video_items.remove(child)
    for child in list(audio_items):
        audio_items.remove(child)

    for video in video_clips:
        wrapper = etree.Element("Element")
        wrapper.append(video)
        video_items.append(wrapper)
    for audio in audio_clips:
        wrapper = etree.Element("Element")
        wrapper.append(audio)
        audio_items.append(wrapper)

    mp_bytes = _serialize(mp_root, mp_path)
    seq_bytes = _serialize(seq_root, seq_path)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", project_bytes)
        zf.writestr("MediaPool/Master/MpFolder.xml", mp_bytes)
        zf.writestr(f"SeqContainer/{SEQ_FILENAME}", seq_bytes)

    return OUT_PATH


if __name__ == "__main__":
    out = build_tier1b()
    print(f"Wrote {out}")
    import subprocess

    subprocess.run(["unzip", "-l", str(out)], check=True)
