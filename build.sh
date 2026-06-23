#!/usr/bin/env bash
# build.sh — Local macOS build script for SteadyCut
# Usage: bash build.sh

set -euo pipefail

echo "═══════════════════════════════════════════"
echo "  SteadyCut — macOS Build"
echo "═══════════════════════════════════════════"
echo

# 1. Ensure build tools are present
echo "→ Installing/upgrading build tools…"
pip install --upgrade pyinstaller py7zr

# 1b. Ensure the bundled YOLO model is present. *.pt is gitignored, so a fresh
#     clone won't contain it; the spec (steadycut.spec) requires yolov8n.pt at
#     the repo root. Fetch it the same way CI does. Needs ultralytics installed
#     (pip install -r requirements.txt first).
if [ ! -f "yolov8n.pt" ]; then
    echo "→ yolov8n.pt missing — fetching (one time)…"
    python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
        || { echo "✗  Could not fetch yolov8n.pt — run 'pip install -r requirements.txt' first."; exit 1; }
fi

# 1c. Ensure ffmpeg/ffprobe are in ./bin so the spec bundles them inside the
#     app (the bundled binary must win over the runtime download). CI fetches
#     these; a local build must too, or the .app ships hollow and a clean machine
#     silently falls back to downloading. Static builds from evermeet.cx.
if [ ! -x "bin/ffmpeg" ] || [ ! -x "bin/ffprobe" ]; then
    echo "→ ffmpeg/ffprobe missing from ./bin — fetching (one time)…"
    mkdir -p bin
    for name in ffmpeg ffprobe; do
        curl -fL "https://evermeet.cx/ffmpeg/getrelease/$name/zip" -o "$name.zip" \
            || { echo "✗  Could not download $name from evermeet.cx"; exit 1; }
        unzip -o "$name.zip" "$name" -d bin >/dev/null
        chmod +x "bin/$name"
        rm -f "$name.zip"
    done
    echo "   $(bin/ffmpeg -version | head -1)"
fi

# 2. Clean previous artifacts
echo "→ Cleaning previous build…"
rm -rf build/ dist/

# 3. Run PyInstaller
echo "→ Building with PyInstaller…"
pyinstaller steadycut.spec

# 4. Verify
if [ -d "dist/SteadyCut.app" ]; then
    SIZE=$(du -sh dist/SteadyCut.app | cut -f1)
    echo
    echo "✓  Build succeeded: dist/SteadyCut.app  (${SIZE})"
else
    echo "✗  Build failed — dist/SteadyCut.app not found"
    exit 1
fi

# 5. Quick smoke test (open + immediately quit to check it launches)
echo
echo "→ Smoke test: opening app (will close in 3 s)…"
open dist/SteadyCut.app
sleep 3
pkill -f "SteadyCut" 2>/dev/null || true
echo "   Done."

# 6. Zip for distribution
echo
read -rp "Zip for distribution? [y/N] " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    cd dist
    zip -r "SteadyCut-macOS-$(uname -m).zip" SteadyCut.app
    echo "✓  Zipped: dist/SteadyCut-macOS-$(uname -m).zip"
fi

echo
echo "═══════════════════════════════════════════"
echo "  Done!"
echo "═══════════════════════════════════════════"
