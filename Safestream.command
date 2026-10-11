#!/bin/zsh
cd "${0:A:h}" || exit 1
open -a OBS
exec .venv/bin/python safestream.py --setup-obs
