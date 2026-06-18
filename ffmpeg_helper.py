"""
ffmpeg_helper.py — FFmpeg/FFprobe binary resolver for SteadyCut
===============================================================
Finds ffmpeg and ffprobe via:
  0. Binaries bundled inside the packaged app  (sys._MEIPASS/bin/)
  1. Platform app-data cache  (~/.../SteadyCut/bin/)
  2. System PATH
  3. Auto-download on first run (BtbN on Windows, evermeet.cx zip on macOS)

Every resolved binary is made executable and validated by actually running
`<bin> -version`. A binary that exists but cannot execute is treated as missing,
so a broken cache self-heals by falling through to the next source.

Public API
----------
  ensure_ffmpeg(status_callback=None) -> tuple[Path, Path]
  get_ffmpeg()  -> str   (raises if unavailable)
  get_ffprobe() -> str   (raises if unavailable)
  is_ffmpeg_available() -> bool   (non-blocking, no download)
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("ffmpeg_helper")


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


# ── Validation & permissions ──────────────────────────────────────────────────

def _make_executable(path: Path) -> None:
    """chmod +x (no-op on Windows where exec bit is implied by .exe)."""
    if platform.system() == "Windows":
        return
    try:
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        log.warning("Could not chmod +x %s: %s", path, exc)


def _runs_ok(path: Path) -> bool:
    """True if the binary actually executes. Guards against non-exec / corrupt files."""
    if not path or not path.exists():
        return False
    _make_executable(path)
    try:
        proc = subprocess.run(
            [str(path), "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("FFmpeg binary at %s failed to execute: %s", path, exc)
        return False


# ── Resolution helpers ────────────────────────────────────────────────────────

def _bundle_dir() -> Optional[Path]:
    """Binaries shipped inside the packaged app (PyInstaller _MEIPASS/bin)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    d = Path(base) / "bin"
    return d if d.exists() else None


def _find_bundled() -> tuple[Optional[Path], Optional[Path]]:
    d = _bundle_dir()
    if not d:
        return None, None
    ff = d / f"ffmpeg{_EXE}"
    fp = d / f"ffprobe{_EXE}"
    return (ff if ff.exists() else None, fp if fp.exists() else None)


def _find_in_cache() -> tuple[Optional[Path], Optional[Path]]:
    ff = BIN_DIR / f"ffmpeg{_EXE}"
    fp = BIN_DIR / f"ffprobe{_EXE}"
    return (ff if ff.exists() else None, fp if fp.exists() else None)


def _find_in_path_bins() -> tuple[Optional[str], Optional[str]]:
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


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
    if not _runs_ok(ff):
        raise RuntimeError("FFmpeg was downloaded but failed to execute.")
    _report(cb, "FFmpeg ready.")
    return ff, fp


def _download_macos(cb: Optional[Callable[[str], None]]) -> tuple[Path, Path]:
    # evermeet.cx serves a `zip` variant alongside `7z`. We use zip so extraction
    # goes through stdlib zipfile — the 7z builds use the BCJ2 filter, which
    # py7zr cannot decode (it raised "BCJ2 filter is not supported by py7zr").
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}

    for name in ("ffmpeg", "ffprobe"):
        url = f"https://evermeet.cx/ffmpeg/getrelease/{name}/zip"
        _report(cb, f"Downloading {name} for macOS…")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"{name}.zip"
            urllib.request.urlretrieve(url, archive)
            _report(cb, f"Extracting {name}…")
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    if Path(member).name == name:
                        dest = BIN_DIR / name
                        with zf.open(member) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        break

        dest = BIN_DIR / name
        if not dest.exists():
            raise RuntimeError(
                f"FFmpeg extraction failed — {name} not found after extraction."
            )
        _make_executable(dest)
        results[name] = dest

    if not _runs_ok(results["ffmpeg"]):
        raise RuntimeError(
            "FFmpeg was downloaded but failed to execute. "
            "On macOS this can be Gatekeeper quarantine — try installing ffmpeg "
            "manually (brew install ffmpeg) and restart."
        )
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

def _resolve(validate: bool) -> tuple[Optional[Path], Optional[Path]]:
    """
    Resolve (ffmpeg, ffprobe) without downloading, trying each source in order:
    bundled → cache → PATH. When validate=True each candidate must actually run
    (`-version`); a present-but-broken binary is skipped so resolution self-heals.
    """
    check = _runs_ok if validate else (lambda p: bool(p and p.exists()))

    # 0. Bundled inside the packaged app
    ff, fp = _find_bundled()
    if ff and fp and check(ff) and check(fp):
        return ff, fp

    # 1. Writable cache
    ff, fp = _find_in_cache()
    if ff and fp and check(ff) and check(fp):
        return ff, fp

    # 2. System PATH
    ff_str, fp_str = _find_in_path_bins()
    if ff_str and fp_str:
        ff, fp = Path(ff_str), Path(fp_str)
        if check(ff) and check(fp):
            return ff, fp

    return None, None


def ensure_ffmpeg(
    status_callback: Optional[Callable[[str], None]] = None,
) -> tuple[Path, Path]:
    """
    Blocking call. Returns (ffmpeg_path, ffprobe_path).
    Resolves a *working* binary (bundled → cache → PATH), validating that it
    actually executes. Downloads and caches on first call if none is usable.
    """
    global _FFMPEG_PATH, _FFPROBE_PATH

    ff, fp = _resolve(validate=True)
    if ff and fp:
        _FFMPEG_PATH, _FFPROBE_PATH = ff, fp
        _report(status_callback, "FFmpeg is available.")
        return ff, fp

    # Nothing usable — auto-download into the cache.
    ff, fp = _download_ffmpeg(status_callback)
    _FFMPEG_PATH, _FFPROBE_PATH = ff, fp
    return ff, fp


def is_ffmpeg_available() -> bool:
    """Non-blocking. True if a *working* ffmpeg+ffprobe is bundled/cached/on PATH."""
    ff, fp = _resolve(validate=True)
    return bool(ff and fp)


def get_ffmpeg() -> str:
    """Return the ffmpeg binary path as a string. Lazy-resolves from bundle/cache/PATH."""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        _FFMPEG_PATH, _ = _resolve(validate=False)
    if _FFMPEG_PATH is None:
        raise RuntimeError(
            "FFmpeg is not available. The app should have set it up on startup — "
            "check the setup banner or install ffmpeg manually."
        )
    _make_executable(_FFMPEG_PATH)
    return str(_FFMPEG_PATH)


def get_ffprobe() -> str:
    """Return the ffprobe binary path as a string. Lazy-resolves from bundle/cache/PATH."""
    global _FFPROBE_PATH
    if _FFPROBE_PATH is None:
        _, _FFPROBE_PATH = _resolve(validate=False)
    if _FFPROBE_PATH is None:
        raise RuntimeError(
            "FFprobe is not available. The app should have set it up on startup — "
            "check the setup banner or install ffmpeg manually."
        )
    _make_executable(_FFPROBE_PATH)
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
