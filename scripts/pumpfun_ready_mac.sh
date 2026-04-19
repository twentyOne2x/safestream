#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper is macOS-only." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Install Homebrew first, then rerun." >&2
  exit 1
fi

if ! brew list --cask obs >/dev/null 2>&1; then
  echo "Installing OBS..."
  brew install --cask obs
fi

echo "Setting up safestream deps (tesseract + venv)..."
# Some macOS setups may SIGKILL direct execution of scripts; running via bash is more reliable.
bash "$repo_root/scripts/setup_mac.sh"

cat <<'TXT'

Ready for Pump.fun streaming (safe screen capture).

Manual permissions (one-time):
1) System Settings -> Privacy & Security -> Screen Recording:
   enable the app you will run the safe-screen script from (Terminal/iTerm/PyCharm).
2) If you use webcam blur: Privacy & Security -> Camera (enable the same app).

How to use in OBS:
1) Open OBS once. Start/Stop Virtual Camera once (Tools -> Virtual Camera).
2) Create a Scene. Add Source -> Video Capture Device.
3) Choose the virtual camera device (usually "OBS Virtual Camera").

Start safe screen feed:
  bash ./scripts/run_pumpfun_safe.sh

Or (debug preview + boxes):
  bash ./scripts/run_screen_safe.sh

TXT
