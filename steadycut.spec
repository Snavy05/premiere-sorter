# steadycut.spec — PyInstaller build spec for SteadyCut
# Run with:  pyinstaller steadycut.spec
#
# Produces:
#   macOS  → dist/SteadyCut.app
#   Windows → dist/SteadyCut/SteadyCut.exe

import sys
from pathlib import Path

block_cipher = None
IS_MACOS = sys.platform == "darwin"
IS_WIN   = sys.platform == "win32"

# ── Locate package roots for data collection ──────────────────────────────────
import ultralytics as _ul
import cv2 as _cv2

_ul_root  = Path(_ul.__file__).parent
_cv2_root = Path(_cv2.__file__).parent

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
    "py7zr",
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
]

# Modules that are definitely not needed — strip to reduce bundle size
excludes = [
    "triton",
    "caffe2",
    "onnxruntime",
    "tensorboard",
    "tensorflow",
    "matplotlib",
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
    binaries=[],
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
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSAppleEventsUsageDescription":
                "SteadyCut opens your browser to display the pipeline dashboard.",
            "LSMinimumSystemVersion": "12.0",
        },
    )
