"""
ffmpeg_helper.py — FFmpeg/FFprobe binary resolver for SteadyCut
===============================================================
Finds ffmpeg and ffprobe via:
  1. Platform app-data cache  (~/.../SteadyCut/bin/)
  2. System PATH
  3. Auto-download on first run (BtbN on Windows, evermeet.cx on macOS)

Public API
----------
  ensure_ffmpeg(status_callback=None) -> tuple[Path, Path]
  get_ffmpeg()  -> str   (raises if unavailable)
  get_ffprobe() -> str   (raises if unavailable)
  is_ffmpeg_available() -> bool   (non-blocking, no download)
"""
from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional


# ── Platform-aware app-data directory ────────────────────────────────────────

def _app_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / "SteadyCut"


BIN_DIR: Path = _app_data_dir() / "bin"
_EXE = ".exe" if platform.system() == "Windows" else ""

_FFMPEG_PATH:  Optional[Path] = None
_FFPROBE_PATH: Optional[Path] = None


# ── Resolution helpers ────────────────────────────────────────────────────────

def _find_in_cache() -> tuple[Optional[Path], Optional[Path]]:
    ff = BIN_DIR / f"ffmpeg{_EXE}"
    fp = BIN_DIR / f"ffprobe{_EXE}"
    return (ff if ff.exists() else None, fp if fp.exists() else None)


def _find_in_path_bins() -> tuple[Optional[str], Optional[str]]:
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def _cache_from_path(ff_str: str, fp_str: str) -> tuple[Path, Path]:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    ff_dest = BIN_DIR / f"ffmpeg{_EXE}"
    fp_dest = BIN_DIR / f"ffprobe{_EXE}"
    if not ff_dest.exists():
        shutil.copy2(ff_str, ff_dest)
    if not fp_dest.exists():
        shutil.copy2(fp_str, fp_dest)
    return ff_dest, fp_dest


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ── Download helpers ──────────────────────────────────────────────────────────

def _report(cb: Optional[Callable[[str], None]], msg: str) -> None:
    if cb:
        cb(msg)


def _download_windows(cb: Optional[Callable[[str], None]]) -> tuple[Path, Path]:
    url = (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
        "ffmpeg-master-latest-win64-gpl.zip"
    )
    _report(cb, "Downloading FFmpeg for Windows…")
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"
        _report(cb, "Fetching FFmpeg archive (this may take a moment)…")
        urllib.request.urlretrieve(url, zip_path)
        _report(cb, "Extracting FFmpeg…")
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                # Binaries live at  <root>/bin/ffmpeg.exe  and  .../ffprobe.exe
                name = Path(member).name
                if name in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in member:
                    dest = BIN_DIR / name
                    with zf.open(member) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    ff = BIN_DIR / "ffmpeg.exe"
    fp = BIN_DIR / "ffprobe.exe"
    if not ff.exists() or not fp.exists():
        raise RuntimeError("FFmpeg extraction failed — binaries not found in archive.")
    _report(cb, "FFmpeg ready.")
    return ff, fp


def _download_macos(cb: Optional[Callable[[str], None]]) -> tuple[Path, Path]:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError(
            "py7zr is required for macOS FFmpeg extraction. "
            "Install it with:  pip install py7zr"
        ) from exc

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}

    for name in ("ffmpeg", "ffprobe"):
        url = f"https://evermeet.cx/ffmpeg/getrelease/{name}/7z"
        _report(cb, f"Downloading {name} for macOS…")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"{name}.7z"
            urllib.request.urlretrieve(url, archive)
            _report(cb, f"Extracting {name}…")
            with py7zr.SevenZipFile(archive, mode="r") as z:
                z.extractall(path=BIN_DIR)

        dest = BIN_DIR / name
        if not dest.exists():
            raise RuntimeError(
                f"FFmpeg extraction failed — {name} not found after extraction."
            )
        _make_executable(dest)
        results[name] = dest

    _report(cb, "FFmpeg ready.")
    return results["ffmpeg"], results["ffprobe"]


def _download_ffmpeg(cb: Optional[Callable[[str], None]]) -> tuple[Path, Path]:
    system = platform.system()
    if system == "Windows":
        return _download_windows(cb)
    elif system == "Darwin":
        return _download_macos(cb)
    else:
        raise RuntimeError(
            f"Auto-download is not supported on {system}. "
            "Install ffmpeg manually (e.g. sudo apt install ffmpeg)."
        )


# ── Public API ────────────────────────────────────────────────────────────────

def ensure_ffmpeg(
    status_callback: Optional[Callable[[str], None]] = None,
) -> tuple[Path, Path]:
    """
    Blocking call. Returns (ffmpeg_path, ffprobe_path).
    Downloads and caches binaries on first call if not already present.
    """
    global _FFMPEG_PATH, _FFPROBE_PATH

    # 1. Already cached on disk
    ff, fp = _find_in_cache()
    if ff and fp:
        _FFMPEG_PATH, _FFPROBE_PATH = ff, fp
        _report(status_callback, "FFmpeg is available.")
        return ff, fp

    # 2. On system PATH — copy into cache so future calls skip PATH lookup
    ff_str, fp_str = _find_in_path_bins()
    if ff_str and fp_str:
        ff, fp = _cache_from_path(ff_str, fp_str)
        _FFMPEG_PATH, _FFPROBE_PATH = ff, fp
        _report(status_callback, "FFmpeg is available.")
        return ff, fp

    # 3. Auto-download
    ff, fp = _download_ffmpeg(status_callback)
    _FFMPEG_PATH, _FFPROBE_PATH = ff, fp
    return ff, fp


def is_ffmpeg_available() -> bool:
    """Non-blocking. True if ffmpeg+ffprobe are in the cache or on PATH."""
    ff, fp = _find_in_cache()
    if ff and fp:
        return True
    ff_str, fp_str = _find_in_path_bins()
    return bool(ff_str and fp_str)


def get_ffmpeg() -> str:
    """Return the ffmpeg binary path as a string. Lazy-resolves from cache/PATH."""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        ff, _ = _find_in_cache()
        if ff:
            _FFMPEG_PATH = ff
        else:
            ff_str, _ = _find_in_path_bins()
            if ff_str:
                _FFMPEG_PATH = Path(ff_str)
    if _FFMPEG_PATH is None:
        raise RuntimeError(
            "FFmpeg is not available. The app should have downloaded it on startup — "
            "check the setup banner or install ffmpeg manually."
        )
    return str(_FFMPEG_PATH)


def get_ffprobe() -> str:
    """Return the ffprobe binary path as a string. Lazy-resolves from cache/PATH."""
    global _FFPROBE_PATH
    if _FFPROBE_PATH is None:
        _, fp = _find_in_cache()
        if fp:
            _FFPROBE_PATH = fp
        else:
            _, fp_str = _find_in_path_bins()
            if fp_str:
                _FFPROBE_PATH = Path(fp_str)
    if _FFPROBE_PATH is None:
        raise RuntimeError(
            "FFprobe is not available. The app should have downloaded it on startup — "
            "check the setup banner or install ffmpeg manually."
        )
    return str(_FFPROBE_PATH)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"Platform : {platform.system()}")
    print(f"Cache dir: {BIN_DIR}")
    print()

    if is_ffmpeg_available():
        print("FFmpeg already available — skipping download.")
    else:
        print("FFmpeg not found — downloading…")

    try:
        ff, fp = ensure_ffmpeg(status_callback=print)
        print(f"\nffmpeg  : {ff}")
        print(f"ffprobe : {fp}")

        r = subprocess.run(
            [str(ff), "-version"], capture_output=True, text=True, timeout=10
        )
        first_line = (r.stdout or r.stderr or "").splitlines()[0]
        print(f"Version : {first_line}")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
