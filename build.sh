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
if [[ "${answer,,}" == "y" ]]; then
    cd dist
    zip -r "SteadyCut-macOS-$(uname -m).zip" SteadyCut.app
    echo "✓  Zipped: dist/SteadyCut-macOS-$(uname -m).zip"
fi

echo
echo "═══════════════════════════════════════════"
echo "  Done!"
echo "═══════════════════════════════════════════"
