#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper is macOS-only." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$repo_root/tmp"
log="$repo_root/tmp/pumpfun_safe_screen.log"

echo "1) Opening macOS permission panes (you must click Allow/Enable)..."
bash "$repo_root/scripts/open_stream_permissions_mac.sh" || true

echo
echo "2) Starting safe screen feed (background). Logs: $log"
nohup bash "$repo_root/scripts/run_pumpfun_safe.sh" >"$log" 2>&1 &
echo "   PID: $!"

echo
echo "3) Launching OBS..."
open -a OBS || true

cat <<'TXT'

OBS checklist:
1) Tools -> Virtual Camera: Start once (this may prompt for a Camera Extension).
2) Scene: add Source -> Video Capture Device -> choose "OBS Virtual Camera".
3) Settings -> Stream:
   - If streaming to Pump.fun RTMP: Service = Custom, then paste Server + Stream Key from Pump.fun "Go Live".
   - Otherwise choose Twitch/YouTube/Kick/etc and use that platform's stream key.

If the safe screen feed is black:
- Confirm Screen Recording permission is enabled for the app running the script (Terminal/iTerm/PyCharm).
- Quit and reopen OBS after changing permissions.

TXT

