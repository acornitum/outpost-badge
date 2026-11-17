#!/usr/bin/env bash
# Copy the firmware onto a mounted CIRCUITPY drive.
#
# Usage:
#   ./firmware/tools/deploy.sh flappy                       # epaper/examples/
#   ./firmware/tools/deploy.sh epaper/examples/gradient.py   # explicit path
#   ./firmware/tools/deploy.sh recondition                   # epaper/tools/
#   ./firmware/tools/deploy.sh flappy /Volumes/OTHER
#   ./firmware/tools/deploy.sh --menu                        # launcher + all apps
#
# --menu installs menu/menu.py as code.py and every app listed in
# menu/apps.txt into CIRCUITPY/apps/. Deploying a single script afterwards
# replaces code.py and removes apps/, so the board is no longer a menu.
#
# Everything in lib/ goes to CIRCUITPY/lib/, which CircuitPython already has on
# sys.path. The chosen script is copied to CIRCUITPY/code.py, which is what the
# board runs. Scripts therefore keep their own names in the repo instead of one
# file being overwritten in place.
set -euo pipefail

FW="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--menu" ]; then
  DEST="${2:-/Volumes/CIRCUITPY}"
  if [ ! -d "$DEST" ]; then
    echo "error: $DEST not found. Is the board in CircuitPython mode?" >&2
    exit 1
  fi
  mkdir -p "$DEST/lib" "$DEST/apps"
  cp -X "$FW"/lib/*.py "$DEST/lib/"
  rm -f "$DEST"/apps/*.py
  N=0
  while IFS= read -r APP; do
    case "$APP" in ""|\#*) continue ;; esac
    if [ ! -f "$FW/$APP" ]; then echo "error: $APP (from menu/apps.txt) not found" >&2; exit 1; fi
    cp -X "$FW/$APP" "$DEST/apps/$(basename "$APP")"
    N=$((N + 1))
  done < "$FW/menu/apps.txt"
  cp -X "$FW/menu/menu.py" "$DEST/code.py"
  rm -f "$DEST/ssd1680.py" "$DEST/._ssd1680.py" "$DEST/._code.py"
  sync
  echo "deployed menu + $N apps -> $DEST"
  exit 0
fi

ENTRY="${1:-epaper/examples/flappy.py}"
DEST="${2:-/Volumes/CIRCUITPY}"

# A bare name resolves against each subsystem's examples/, then its tools/,
# then its experiments/. So `deploy.sh flappy` and `deploy.sh nfc-read` both
# work without naming the subsystem.
if [ ! -f "$FW/$ENTRY" ]; then
  for CAND in \
      "epaper/examples/$ENTRY.py" "epaper/tools/$ENTRY.py" "tools/$ENTRY.py" \
      "epaper/experiments/$ENTRY.py" "nfc/examples/$ENTRY.py" \
      "nfc/experiments/$ENTRY.py" "menu/$ENTRY.py" "$ENTRY.py"; do
    if [ -f "$FW/$CAND" ]; then ENTRY="$CAND"; break; fi
  done
fi
if [ ! -f "$FW/$ENTRY" ]; then
  echo "error: '$1' not found under $FW" >&2
  echo "available:" >&2
  (cd "$FW" && ls epaper/examples epaper/tools epaper/experiments nfc/examples nfc/experiments tools) >&2
  exit 1
fi
if [ ! -d "$DEST" ]; then
  echo "error: $DEST not found. Is the board in CircuitPython mode?" >&2
  exit 1
fi

mkdir -p "$DEST/lib"
# -X avoids copying macOS extended attributes onto the FAT volume.
cp -X "$FW"/lib/*.py "$DEST/lib/"
cp -X "$FW/$ENTRY" "$DEST/code.py"
# An older layout kept the driver at the drive root, where it shadows lib/.
rm -f "$DEST/ssd1680.py" "$DEST/._ssd1680.py" "$DEST/._code.py"
# A standalone script replaces the menu, so the menu's apps are stale.
rm -f "$DEST"/apps/*.py && rmdir "$DEST/apps" 2>/dev/null || true
sync
echo "deployed lib/ + $ENTRY (as code.py) -> $DEST"
