#!/usr/bin/env bash
set -euo pipefail

open -a "System Settings" || true
sleep 1
open "x-apple.systempreferences:com.apple.preference.general" || true
sleep 1
osascript <<'APPLESCRIPT'
try
  tell application "System Settings"
    activate
    reopen
    delay 0.5
    if (count of windows) > 0 then
      try
        set visible of window 1 to true
      end try
      try
        set miniaturized of window 1 to false
      end try
      try
        set bounds of window 1 to {80, 60, 1360, 960}
      end try
    end if
  end tell
on error errMsg number errNum
  do shell script "echo " & quoted form of ("System Settings AppleScript error(" & errNum & "): " & errMsg) & " 1>&2"
end try
APPLESCRIPT
