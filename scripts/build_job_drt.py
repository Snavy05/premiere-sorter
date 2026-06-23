#!/usr/bin/env python3
"""Build a .drt for an arbitrary folder of uniform HEVC clips (gate-tested clone path)."""

from __future__ import annotations

import argparse
import binascii
import copy
import json
import struct
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GOLDEN_NAME = "Hoang.dv8629.MP4"
GOLDEN_DIR = b"/Users/mac/Downloads/STEADY"
GOLDEN_FILE = b"Hoang.dv8629.MP4"
SEQ_FILENAME = "d3bf0b9d-06a1-4a71-afda-555e696ea720.xml"
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "samples" / "drt_template"


@dataclass
class ClipInfo:
    src_path: Path
    name: str
    frames: int
    fps: float
    start: int
    duration: int


def _varint_read(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while i < len(data):
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, i
        shift += 7
    raise ValueError("truncated varint")


def _varint_write(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _proto_parse(data: bytes) -> list[tuple[int, bytes]]:
    fields: list[tuple[int, bytes]] = []
    i = 0
    while i < len(data):
        tag, i = _varint_read(data, i)
        field_num = tag >> 3
        wire = tag & 7
        if wire != 2:
            raise ValueError(f"unsupported wire type {wire} at offset {i}")
        length, i = _varint_read(data, i)
        fields.append((field_num, data[i : i + length]))
        i += length
    return fields


def _proto_build(fields: list[tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for field_num, val in fields:
        tag = (field_num << 3) | 2
        out.extend(_varint_write(tag))
        out.extend(_varint_write(len(val)))
        out.extend(val)
    return bytes(out)


def _proto_parse_all(data: bytes) -> list[tuple[int, int, bytes | int]]:
    """Return (field_num, wire, value) where value is bytes for wire 2 else int."""
    fields: list[tuple[int, int, bytes | int]] = []
    i = 0
    while i < len(data):
        tag, i = _varint_read(data, i)
        field_num = tag >> 3
        wire = tag & 7
        if wire == 2:
            length, i = _varint_read(data, i)
            fields.append((field_num, wire, data[i : i + length]))
            i += length
        elif wire == 0:
            value, i = _varint_read(data, i)
            fields.append((field_num, wire, value))
        else:
            raise ValueError(f"unsupported wire type {wire} at offset {i}")
    return fields


def _proto_build_all(fields: list[tuple[int, int, bytes | int]]) -> bytes:
    out = bytearray()
    for field_num, wire, val in fields:
        tag = (field_num << 3) | wire
        out.extend(_varint_write(tag))
        if wire == 2:
            assert isinstance(val, (bytes, bytearray))
            out.extend(_varint_write(len(val)))
            out.extend(val)
        else:
            out.extend(_varint_write(int(val)))
    return bytes(out)


def _rewrite_clip_blob(hexstr: str, src_path: Path) -> str:
    raw = binascii.unhexlify(hexstr)
    if struct.unpack(">I", raw[:4])[0] != 2:
        raise ValueError("expected v2 FieldsBlob")
    idx = raw.find(ZSTD_MAGIC)
    if idx < 0:
        raise ValueError("zstd frame not found")
    prefix = raw[:idx]
    payload = subprocess.run(
        ["zstd", "-d", "--stdout"], input=raw[idx:], capture_output=True, check=True
    ).stdout

    new_dir = str(src_path.parent).encode("utf-8")
    new_file = src_path.name.encode("utf-8")
    rebuilt: list[tuple[int, int, bytes | int]] = []
    for field_num, wire, val in _proto_parse_all(payload):
        if wire == 2:
            if field_num == 1:
                rebuilt.append((1, 2, new_dir))
            elif field_num in (2, 6) and val == GOLDEN_FILE:
                rebuilt.append((field_num, 2, new_file))
            else:
                rebuilt.append((field_num, 2, val))
        else:
            rebuilt.append((field_num, wire, val))
    new_payload = _proto_build_all(rebuilt)

    frame = subprocess.run(
        ["zstd", "--single-thread", "-q", "-1", "--stdout"],
        input=new_payload,
        capture_output=True,
        check=True,
    ).stdout
    body = b"\x81" + frame
    header = struct.pack(">II", 2, len(body))
    return binascii.hexlify(prefix + header + body).decode()


def _rewrite_pool_blobs(pool: etree._Element, src_path: Path) -> None:
    for el in pool.xpath('.//*[local-name()="Clip"]'):
        if el.text and el.text.startswith("00000002"):
            el.text = _rewrite_clip_blob(el.text, src_path)


def _find_child(parent: etree._Element, tag: str) -> etree._Element | None:
    for child in parent:
        if etree.QName(child).localname == tag:
            return child
    return None


def _set_text(parent: etree._Element, tag: str, value: str) -> None:
    el = _find_child(parent, tag)
    if el is None:
        raise ValueError(f"Missing <{tag}>")
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


def _serialize(root: etree._Element, original_path: Path) -> bytes:
    raw = etree.tostring(root, xml_declaration=False, encoding="UTF-8", pretty_print=False)
    decl = b'<?xml version="1.0" encoding="UTF-8"?>'
    comment = original_path.read_text(encoding="utf-8").split("\n", 2)[1]
    if comment.startswith("<!--"):
        return decl + b"\n" + comment.encode("utf-8") + b"\n" + raw
    return decl + b"\n" + raw


def _probe_clip(path: Path) -> tuple[int, float]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    fps_text = video.get("r_frame_rate", "50/1")
    num, den = map(int, fps_text.split("/"))
    fps = num / den
    nb = video.get("nb_frames")
    if nb is not None:
        frames = int(nb)
    else:
        frames = int(round(float(data["format"]["duration"]) * fps))
    return frames, fps


def _collect_clips(media_dir: Path) -> list[ClipInfo]:
    paths = sorted(media_dir.glob("*.MP4")) + sorted(media_dir.glob("*.mp4"))
    if not paths:
        raise SystemExit(f"No .MP4 clips in {media_dir}")
    clips: list[ClipInfo] = []
    timeline = 0
    for path in paths:
        frames, fps = _probe_clip(path)
        clips.append(
            ClipInfo(
                src_path=path.resolve(),
                name=path.name,
                frames=frames,
                fps=fps,
                start=timeline,
                duration=frames,
            )
        )
        timeline += frames
    return clips


def _clone_clip_pair(
    golden_video: etree._Element,
    golden_audio: etree._Element,
    golden_pool: etree._Element,
    clip: ClipInfo,
    pool_db_id: str,
    pool_item_id: str,
    video_db_id: str,
    audio_db_id: str,
) -> tuple[etree._Element, etree._Element, etree._Element]:
    pool = copy.deepcopy(golden_pool)
    pool.set("DbId", pool_db_id)
    _set_text(pool, "Name", clip.name)
    _set_text(pool, "UniqueMediaPoolItemId", pool_item_id)
    _rewrite_pool_blobs(pool, clip.src_path)

    src = str(clip.src_path)
    media_start = "0"

    video = copy.deepcopy(golden_video)
    video.set("DbId", video_db_id)
    _set_text(video, "Name", clip.name)
    _set_text(video, "Start", str(clip.start))
    _set_text(video, "Duration", str(clip.duration))
    _set_text(video, "MediaRef", pool_db_id)
    _set_text(video, "MediaStartTime", media_start)
    _set_text(video, "MediaFilePath", src)

    audio = copy.deepcopy(golden_audio)
    audio.set("DbId", audio_db_id)
    _set_text(audio, "Name", clip.name)
    _set_text(audio, "Start", str(clip.start))
    _set_text(audio, "Duration", str(clip.duration))
    _set_text(audio, "MediaRef", pool_db_id)
    _set_text(audio, "MediaStartTime", media_start)
    _set_text(audio, "MediaFilePath", src)

    return pool, video, audio


def build_drt(media_dir: Path, out_path: Path | None = None) -> Path:
    media_dir = media_dir.resolve()
    clips = _collect_clips(media_dir)
    if out_path is None:
        out_path = media_dir / f"{media_dir.name}.drt"
    out_path = out_path.resolve()

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

    for element_wrapper in list(media_vec):
        for child in element_wrapper:
            if etree.QName(child).localname == "Sm2MpVideoClip":
                media_vec.remove(element_wrapper)
                break

    pool_clips: list[etree._Element] = []
    video_clips: list[etree._Element] = []
    audio_clips: list[etree._Element] = []

    for clip in clips:
        pool, video, audio = _clone_clip_pair(
            golden_video,
            golden_audio,
            golden_pool,
            clip,
            pool_db_id=str(uuid.uuid4()),
            pool_item_id=str(uuid.uuid4()),
            video_db_id=str(uuid.uuid4()),
            audio_db_id=str(uuid.uuid4()),
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

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.xml", project_bytes)
        zf.writestr("MediaPool/Master/MpFolder.xml", mp_bytes)
        zf.writestr(f"SeqContainer/{SEQ_FILENAME}", seq_bytes)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Resolve .drt from a media folder")
    parser.add_argument("media_dir", type=Path, help="Folder containing source .MP4 clips")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output .drt path")
    args = parser.parse_args()

    out = build_drt(args.media_dir, args.output)
    print(f"Wrote {out}")
    for line in subprocess.run(["unzip", "-l", str(out)], capture_output=True, text=True).stdout.splitlines():
        print(line)


if __name__ == "__main__":
    main()
