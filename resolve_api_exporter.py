"""
DaVinci Resolve scripting-API exporter.

Imports job media into Resolve's Media Pool and builds a timeline from the
cut list via CreateTimelineFromClips — media is online by construction because
Resolve probes each file on ImportMedia.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ResolveUnavailable(Exception):
    """Resolve scripting API is unreachable; caller should fall back to file export."""


class ResolveBuildError(Exception):
    """
    Resolve WAS reachable but the timeline build failed verification (parity
    mismatch, offline pool item, item-count drift). This is a correctness bug,
    NOT an availability problem — caller must surface it, not silently fall
    back to a file export.
    """


def _set_resolve_env() -> None:
    """Set process-local env vars required by DaVinciResolveScript."""
    if platform.system() == "Windows":
        programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        os.environ["RESOLVE_SCRIPT_API"] = os.path.join(
            programdata,
            "Blackmagic Design",
            "DaVinci Resolve",
            "Support",
            "Developer",
            "Scripting",
        )
        os.environ["RESOLVE_SCRIPT_LIB"] = os.path.join(
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
            "fusionscript.dll",
        )
    else:
        os.environ["RESOLVE_SCRIPT_API"] = (
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        )
        os.environ["RESOLVE_SCRIPT_LIB"] = (
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
        )

    modules_path = os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules")
    if modules_path not in sys.path:
        sys.path.insert(0, modules_path)


def _import_resolve_module():
    """Import DaVinciResolveScript after env bootstrap. Returns module or None."""
    _set_resolve_env()
    try:
        import DaVinciResolveScript as dvr  # type: ignore[import-not-found]
    except ImportError:
        return None
    return dvr


def _launch_resolve() -> None:
    """Auto-launch DaVinci Resolve (platform-specific)."""
    if platform.system() == "Windows":
        exe = os.path.join(
            r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
            "Resolve.exe",
        )
        if os.path.isfile(exe):
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            log.warning("Resolve.exe not found at %s", exe)
    else:
        subprocess.Popen(
            ["open", "-a", "DaVinci Resolve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _scriptapp(dvr) -> Any | None:
    """Return the Resolve app object, or None if unreachable."""
    return dvr.scriptapp("Resolve")


def _connect(timeout_s: int = 60) -> Any | None:
    """
    Bootstrap env, import DaVinciResolveScript, and return the Resolve app object.

    If Resolve is not running, auto-launch and poll scriptapp every 2 s up to
    timeout_s. Returns None when Resolve is unavailable or external scripting
    is disabled.
    """
    dvr = _import_resolve_module()
    if dvr is None:
        log.warning("DaVinciResolveScript module not importable — Resolve not installed?")
        return None

    resolve = _scriptapp(dvr)
    if resolve is not None:
        log.info("Connected to DaVinci Resolve.")
        return resolve

    log.info("Resolve not reachable — auto-launching and polling up to %d s …", timeout_s)
    _launch_resolve()

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(2)
        resolve = _scriptapp(dvr)
        if resolve is not None:
            log.info("Connected to DaVinci Resolve after auto-launch.")
            return resolve

    log.warning(
        "Could not connect to DaVinci Resolve within %d s "
        "(not running, scripting disabled, or still starting).",
        timeout_s,
    )
    return None


def _seq_fps(clip_data: list[dict], fps_override: float | None) -> float:
    """Majority-vote sequence fps (same rule as assemble_xml)."""
    if fps_override is not None:
        return float(fps_override)
    if not clip_data:
        raise ResolveUnavailable("clip_data is empty")
    counts = Counter(float(c["fps"]) for c in clip_data)
    return counts.most_common(1)[0][0]


def _xml_duration(clip: dict) -> int:
    """Per-clip duration as xml_assembler emits it."""
    return clip["out_frame"] - clip["in_frame"]


def _resolve_end_frame(clip: dict) -> int:
    """
    Resolve endFrame (exclusive). xml_assembler treats out_frame as exclusive
    (duration = out_frame - in_frame), so no +1 offset is needed.
    """
    return clip["out_frame"]


def _norm_path(p: str) -> str:
    return str(Path(p).expanduser().resolve())


def _map_pool_items_by_path(items: list) -> dict[str, Any]:
    """Map imported MediaPoolItems by normalized File Path property."""
    mapping: dict[str, Any] = {}
    for item in items:
        if item is None:
            continue
        file_path = item.GetClipProperty("File Path")
        if not file_path:
            raise ResolveUnavailable("ImportMedia returned item without File Path")
        mapping[_norm_path(file_path)] = item
    return mapping


def export_to_resolve(
    clip_data: list[dict],
    job_name: str,
    fps_override: float | None = None,
) -> bool:
    """
    Import media and build a timeline in live DaVinci Resolve.

    Returns True on success. Raises ResolveUnavailable when the API cannot
    complete the build (caller should fall back to file export).
    """
    if not clip_data:
        raise ResolveUnavailable("clip_data is empty")

    resolve = _connect()
    if resolve is None:
        raise ResolveUnavailable("Could not connect to DaVinci Resolve")

    pm = resolve.GetProjectManager()
    if pm is None:
        raise ResolveUnavailable("GetProjectManager() returned None")

    pj = pm.LoadProject(job_name) or pm.CreateProject(job_name) or pm.GetCurrentProject()
    if pj is None:
        raise ResolveUnavailable("Could not get or create a Resolve project")

    mp = pj.GetMediaPool()
    if mp is None:
        raise ResolveUnavailable("GetMediaPool() returned None")

    root = mp.GetRootFolder()
    if root is None:
        raise ResolveUnavailable("GetRootFolder() returned None")

    # Reuse an existing job bin if present (re-running the same job should not
    # stack duplicate subfolders); otherwise create it.
    bin_ = next(
        (f for f in (root.GetSubFolderList() or []) if f.GetName() == job_name),
        None,
    )
    if bin_ is None:
        bin_ = mp.AddSubFolder(root, job_name)
    if bin_ is None:
        raise ResolveUnavailable(f"AddSubFolder({job_name!r}) returned None")
    if not mp.SetCurrentFolder(bin_):
        raise ResolveUnavailable("SetCurrentFolder() failed")

    unique_paths = list(dict.fromkeys(_norm_path(c["src_path"]) for c in clip_data))
    imported = mp.ImportMedia(unique_paths)
    if not imported or len(imported) != len(unique_paths):
        raise ResolveUnavailable(
            f"ImportMedia expected {len(unique_paths)} items, got "
            f"{0 if not imported else len(imported)}"
        )

    item_for = _map_pool_items_by_path(imported)
    for path in unique_paths:
        if path not in item_for:
            raise ResolveUnavailable(f"ImportMedia missing pool item for {path}")

    seq_fps = _seq_fps(clip_data, fps_override)
    if not pj.SetSetting("timelineFrameRate", str(seq_fps)):
        raise ResolveUnavailable(f"SetSetting(timelineFrameRate, {seq_fps!r}) failed")

    infos = [
        {
            "mediaPoolItem": item_for[_norm_path(c["src_path"])],
            "startFrame": c["in_frame"],
            "endFrame": _resolve_end_frame(c),
        }
        for c in clip_data
    ]

    # Build via CreateEmptyTimeline + AppendToTimeline, NOT CreateTimelineFromClips.
    # CreateTimelineFromClips cannot place the same mediaPoolItem more than once:
    # a job with multiple stable windows per source (the common case) collapses to
    # one item per file and corrupts every source range past the first. AppendToTimeline
    # supports a pool item repeated with distinct startFrame/endFrame.
    tl = mp.CreateEmptyTimeline(job_name)
    if tl is None:
        raise ResolveUnavailable(f"CreateEmptyTimeline returned None for job {job_name!r}")
    if not pj.SetCurrentTimeline(tl):
        raise ResolveUnavailable("SetCurrentTimeline() failed")

    appended = mp.AppendToTimeline(infos)
    if not appended:
        raise ResolveUnavailable(
            f"AppendToTimeline returned empty/false for {len(infos)} clip(s)"
        )

    v1_items = tl.GetItemListInTrack("video", 1)
    if v1_items is None:
        raise ResolveUnavailable("GetItemListInTrack(video, 1) returned None")

    # Parity + verification (Tasks 2–3)
    offline: list[str] = []
    for path, item in item_for.items():
        resolution = item.GetClipProperty("Resolution")
        if not resolution:
            offline.append(path)

    log.info("Resolve export parity check (%d clips):", len(clip_data))
    log.info("  %-4s  %-8s  %-8s  %-8s  %-8s  %s", "idx", "xml_dur", "api_dur", "in", "left", "src")
    mismatches = 0
    if len(v1_items) != len(clip_data):
        log.error(
            "V1 item count %d != clip_data length %d",
            len(v1_items),
            len(clip_data),
        )
        mismatches += 1

    for idx, (clip, ti) in enumerate(zip(clip_data, v1_items)):
        expected = _xml_duration(clip)
        actual = ti.GetDuration()
        left = ti.GetLeftOffset()
        log.info(
            "  [%02d]  %-8d  %-8d  %-8d  %-8d  %s",
            idx,
            expected,
            actual,
            clip["in_frame"],
            left,
            Path(clip["src_path"]).name,
        )
        if actual != expected:
            mismatches += 1
        if left != clip["in_frame"]:
            mismatches += 1

    if offline:
        log.error("Offline pool items: %s", offline)
        mismatches += 1

    if mismatches:
        raise ResolveBuildError(
            f"Resolve timeline verification failed ({mismatches} mismatch(es)); "
            "see log for parity table. Timeline was built but does NOT match the "
            "cut list — this is a build bug, not a connectivity issue."
        )

    log.info(
        "Resolve export OK: timeline %r, %d clips on V1, all pool items online.",
        job_name,
        len(v1_items),
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
    app = _connect()
    if app:
        print("OK — connected to DaVinci Resolve")
    else:
        print("FAIL — could not connect (Resolve not running / scripting disabled / not installed)")
        raise SystemExit(1)
