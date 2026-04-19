#!/usr/bin/env bash
set -euo pipefail

# Stream-safe screen capture: OCR redaction + optional full-frame block on sensitive context,
# output to a virtual camera device that OBS can capture as a normal camera.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$repo_root/obs_face_blur_toolkit"
py="$tool_dir/.venv/bin/python3"

if [ ! -x "$py" ]; then
  echo "venv missing. Run: ./scripts/setup_mac.sh" >&2
  echo "If you see 'Killed: 9' running scripts directly on macOS, try: bash ./scripts/setup_mac.sh" >&2
  exit 1
fi

# Defaults are tuned for "show browser/UI while reducing leak risk".
# Pass extra args to override (e.g. --output-width 1920 --output-height 1080).
exec "$py" "$tool_dir/obs_face_blur_toolkit.py" screen-virtualcam \
  --faces-disable \
  --block-on-context \
  --text-mode sensitive --text-method blackbox \
  "$@"
