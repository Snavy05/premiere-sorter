# steadycut.spec — PyInstaller build spec for SteadyCut
# Run with:  pyinstaller steadycut.spec
#
# Produces:
#   macOS  → dist/SteadyCut.app
#   Windows → dist/SteadyCut/SteadyCut.exe

import os
import sys
from pathlib import Path

# PyInstaller 6.x execs this spec in its own namespace without the spec's
# directory on sys.path, so a bare `from _version import …` raises
# ModuleNotFoundError. SPECPATH is injected by PyInstaller (dir of this spec);
# fall back to cwd (build.sh runs from the repo root).
sys.path.insert(0, globals().get("SPECPATH", os.getcwd()))

from _version import __bundle_version__

block_cipher = None
IS_MACOS = sys.platform == "darwin"
IS_WIN   = sys.platform == "win32"

# ── Locate package roots for data collection ──────────────────────────────────
import ultralytics as _ul
import cv2 as _cv2

_ul_root  = Path(_ul.__file__).parent
_cv2_root = Path(_cv2.__file__).parent

# ── Bundled FFmpeg/FFprobe binaries ──────────────────────────────────────────
# CI fetches static ffmpeg/ffprobe into ./bin before the build so they ship
# inside the app — no fragile runtime download on first launch. Resolved at
# runtime via ffmpeg_helper._bundle_dir() (sys._MEIPASS/bin). If the binaries
# aren't present at build time, the app still falls back to download/PATH.
_bin_dir = Path("bin")
_ff_ext  = ".exe" if IS_WIN else ""
binaries = []
for _name in ("ffmpeg", "ffprobe"):
    _p = _bin_dir / f"{_name}{_ff_ext}"
    if _p.exists():
        binaries.append((str(_p), "bin"))
if not binaries:
    print("WARNING: no ffmpeg/ffprobe in ./bin — app will download on first run.")

# ── Data files bundled into the package ──────────────────────────────────────
datas = [
    # Web dashboard
    ("static",                         "static"),
    # Bundled YOLO nano model (copied to user weights dir on first run)
    ("yolov8n.pt",                      "models"),
    # ultralytics runtime data (YAML model configs, class names, assets)
    (str(_ul_root / "cfg"),             "ultralytics/cfg"),
    (str(_ul_root / "assets"),          "ultralytics/assets"),
    # OpenCV data files (haar cascades etc.)
    (str(_cv2_root / "data"),           "cv2/data"),
]

# ── Hidden imports PyInstaller misses via static analysis ─────────────────────
hiddenimports = [
    # uvicorn transports & protocols
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # fastapi / starlette internals
    "fastapi",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.base",
    # ultralytics (hook-ultralytics.py also collects submodules)
    "ultralytics",
    "ultralytics.models",
    "ultralytics.models.yolo",
    "ultralytics.models.yolo.detect",
    "ultralytics.models.yolo.detect.predict",
    "ultralytics.engine.results",
    "ultralytics.utils",
    "ultralytics.utils.ops",
    "ultralytics.nn.tasks",
    "ultralytics.nn.modules",
    "ultralytics.nn.modules.conv",
    "ultralytics.nn.modules.block",
    "ultralytics.nn.modules.head",
    # PyTorch internals loaded dynamically
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torch.jit",
    "torch.distributions",
    "torch.distributions.constraints",
    "torch.distributions.transforms",
    "torchvision",
    "torchvision.transforms",
    "torchvision.ops",
    # PIL / Pillow (ultralytics image I/O)
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # scipy (ultralytics NMS ops)
    "scipy",
    "scipy.spatial",
    "scipy.special",
    "scipy.ndimage",
    # Other runtime deps
    "numpy",
    "cv2",
    "packaging",
    "packaging.version",
    "yaml",
    "requests",
    "tqdm",
    "psutil",
    "h11",
    "anyio",
    "anyio.streams",
    "anyio.streams.memory",
    "sniffio",
    # ultralytics imports matplotlib eagerly (models/yolo/semantic/train.py).
    # Must be bundled — do NOT add it back to excludes.
    "matplotlib",
    "matplotlib.backends.backend_agg",
]

# Modules that are definitely not needed — strip to reduce bundle size
excludes = [
    "triton",
    "caffe2",
    "onnxruntime",
    "tensorboard",
    "tensorflow",
    "IPython",
    "jupyter",
    "notebook",
    "pytest",
    "setuptools",
    "pkg_resources._vendor",
    "distutils",
    "pydoc",
    "xmlrpc",
    "tkinter",
    "wx",
    "PyQt5",
    "PyQt6",
]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["hooks"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_icon_file = ("assets/steadycut.icns" if IS_MACOS else "assets/steadycut.ico")
_icon = _icon_file if Path(_icon_file).exists() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SteadyCut",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no terminal window for end users
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SteadyCut",
)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name="SteadyCut.app",
        icon=_icon,
        bundle_identifier="com.steadycut.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": __bundle_version__,
            "CFBundleVersion": __bundle_version__,
            "NSAppleEventsUsageDescription":
                "SteadyCut opens your browser to display the pipeline dashboard.",
            "LSMinimumSystemVersion": "12.0",
        },
    )
