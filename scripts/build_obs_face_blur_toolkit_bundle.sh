#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_dir="$root_dir/obs_face_blur_toolkit"
dist_dir="$root_dir/dist"
out_zip="$dist_dir/obs_face_blur_toolkit_bundle.zip"

mkdir -p "$dist_dir"

tmp_dir="$(mktemp -d -t obs_face_blur_toolkit_bundle_)"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT

cp "$tool_dir/obs_face_blur_toolkit.py" "$tmp_dir/obs_face_blur_toolkit.py"
cp "$tool_dir/README.md" "$tmp_dir/README.md"
cp "$tool_dir/requirements.txt" "$tmp_dir/requirements.txt"

(cd "$tmp_dir" && /usr/bin/zip -qr "$out_zip" obs_face_blur_toolkit.py README.md requirements.txt)

echo "Wrote: $out_zip"

