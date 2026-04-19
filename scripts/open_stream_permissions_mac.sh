#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper is macOS-only." >&2
  exit 1
fi

# macOS requires *user* approval for these. This script just opens the right panes.
open -a "System Settings" || true

# OBS + safestream commonly need these toggles:
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone" || true
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera" || true
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent" || true
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true

cat <<'TXT'

Enable these apps (as needed):
- Screen Recording: OBS and the app running the safe-screen script (Terminal/iTerm/PyCharm).
- Microphone/Camera: OBS (and Terminal/PyCharm if you use the Python webcam tools).
- Input Monitoring / Accessibility: only if you need OBS global hotkeys.

Then quit + reopen OBS after toggling.

TXT

