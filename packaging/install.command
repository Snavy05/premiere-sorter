#!/bin/bash
# SteadyCut — first-launch helper for macOS.
#
# The app is not code-signed with an Apple Developer certificate, so Gatekeeper
# blocks it with "Apple could not verify SteadyCut". This script strips the
# quarantine flag from the app sitting next to it and launches it.
#
# Usage: double-click this file in Finder (it opens in Terminal and runs).

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/SteadyCut.app"

if [ ! -d "$APP" ]; then
  echo "ERROR: SteadyCut.app not found next to this script."
  echo "Keep install.command in the same folder as SteadyCut.app and try again."
  exit 1
fi

echo "Unblocking SteadyCut..."
xattr -cr "$APP"
echo "Launching SteadyCut..."
open "$APP"
echo "Done. You can close this window."
