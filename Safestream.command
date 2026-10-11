#!/bin/zsh
cd "${0:A:h}" || exit 1
mkdir -p .build
if [[ ! -x .build/native-ocr || native_ocr.swift -nt .build/native-ocr ]]; then
  /usr/bin/swiftc -O native_ocr.swift -o .build/native-ocr || exit 1
fi
if [[ ! -x .build/native-capture || native_capture.swift -nt .build/native-capture ]]; then
  /usr/bin/swiftc -parse-as-library -O native_capture.swift -o .build/native-capture || exit 1
fi
if [[ ! -x .build/native-stream || native_stream.swift -nt .build/native-stream ]]; then
  /usr/bin/swiftc -parse-as-library -O native_stream.swift -o .build/native-stream-launch-next || exit 1
  mv .build/native-stream-launch-next .build/native-stream || exit 1
fi
open -a OBS
exec .venv/bin/python safestream.py --setup-obs
