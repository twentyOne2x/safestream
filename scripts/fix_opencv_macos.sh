#!/usr/bin/env bash
set -euo pipefail

# macOS can block dlopen() of some pip wheels' bundled dylibs (e.g. OpenCV/FFmpeg)
# when they are quarantined or have an invalid code signature. This script:
# - removes quarantine xattrs
# - applies an ad-hoc signature to the cv2 extension and its bundled dylibs
# so `import cv2` works in the venv.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$repo_root/obs_face_blur_toolkit"
venv_py="${1:-$tool_dir/.venv/bin/python3}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 0
fi

if [[ ! -x "$venv_py" ]]; then
  echo "Python not found/executable: $venv_py" >&2
  exit 1
fi

cv2_dir="$("$venv_py" -c 'import site,sys; import pathlib; p=pathlib.Path(site.getsitepackages()[0])/"cv2"; print(str(p))' 2>/dev/null || true)"
if [[ -z "$cv2_dir" || ! -d "$cv2_dir" ]]; then
  # opencv not installed (or import path differs); nothing to do
  exit 0
fi

# If the known-problematic bundled dylibs verify, skip re-signing.
# Avoid doing `import cv2` here: on some macOS setups, a blocked dylib load can SIGKILL python.
probe=""
if [[ -d "$cv2_dir/.dylibs" ]]; then
  probe="$(ls -1 "$cv2_dir"/.dylibs/libavcodec*.dylib "$cv2_dir"/.dylibs/libswscale*.dylib 2>/dev/null | head -n 1 || true)"
fi
if [[ -n "$probe" ]] && codesign --verify --verbose=0 "$probe" >/dev/null 2>&1; then
  exit 0
fi

echo "Fixing OpenCV dylib signing/quarantine under: $cv2_dir"

# Remove quarantine attributes if present (common for downloaded wheels).
if command -v xattr >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$cv2_dir" >/dev/null 2>&1 || true
fi

if ! command -v codesign >/dev/null 2>&1; then
  echo "codesign not available; cannot fix OpenCV dylib signature issues." >&2
  exit 1
fi

sign_one() {
  local f="$1"
  # Ad-hoc signing is sufficient for local dylib loading.
  codesign --force --sign - "$f" >/dev/null 2>&1 || true
}

export -f sign_one

# Sign the cv2 extension and any bundled dylibs.
while IFS= read -r -d '' f; do
  sign_one "$f"
done < <(find "$cv2_dir" -type f \( -name "*.dylib" -o -name "*.so" \) -print0 2>/dev/null || true)

# Quick smoke: ensure import works.
"$venv_py" -c 'import cv2; print("cv2 import ok:", cv2.__version__)' >/dev/null
