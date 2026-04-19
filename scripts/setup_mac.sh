#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$repo_root/obs_face_blur_toolkit"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3 first." >&2
  exit 1
fi

if command -v brew >/dev/null 2>&1; then
  if ! command -v tesseract >/dev/null 2>&1; then
    echo "Installing tesseract via Homebrew..."
    brew install tesseract
  fi
else
  echo "Homebrew not found. Install tesseract manually, then rerun." >&2
  echo "Tesseract is required for OCR-based screen redaction." >&2
fi

mkdir -p "$tool_dir"
if [ ! -d "$tool_dir/.venv" ]; then
  echo "Creating venv at: $tool_dir/.venv"
  python3 -m venv "$tool_dir/.venv"
fi

echo "Installing Python deps..."
"$tool_dir/.venv/bin/python3" -m pip install --upgrade pip >/dev/null
"$tool_dir/.venv/bin/python3" -m pip install -r "$tool_dir/requirements.txt"

if [[ "$(uname -s)" == "Darwin" ]]; then
  # macOS may block dlopen of some pip wheels' bundled dylibs unless they are
  # de-quarantined and ad-hoc signed.
  # Some macOS security setups block executing helper scripts directly (SIGKILL),
  # but allow them to be interpreted by bash.
  bash "$repo_root/scripts/fix_opencv_macos.sh" "$tool_dir/.venv/bin/python3" || true
fi

cat <<'TXT'

Next manual steps (macOS permissions):
1) System Settings -> Privacy & Security -> Screen Recording
   Enable the app you run this from (Terminal/iTerm/PyCharm).
2) For webcam face blur: Privacy & Security -> Camera (enable the same app).

Run screen redaction (recommended):
  bash ./scripts/run_screen_safe.sh

If macOS prints "Killed: 9" when you run scripts directly, run them via bash:
  bash ./scripts/setup_mac.sh

TXT
