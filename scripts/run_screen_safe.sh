#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$repo_root/obs_face_blur_toolkit"
py="$tool_dir/.venv/bin/python3"

if [ ! -x "$py" ]; then
  echo "venv missing. Run: bash ./scripts/setup_mac.sh" >&2
  exit 1
fi

# Defaults: block sensitive surfaces + redact sensitive text (not all text).
# Pass extra args to override.
exec "$py" "$tool_dir/obs_face_blur_toolkit.py" screen-virtualcam \
  --block-on-context \
  --text-mode sensitive --text-method blackbox \
  --preview --draw-boxes \
  "$@"
