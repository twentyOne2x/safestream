# safestream

Small, local-first privacy tools intended to be used with OBS.

## Tools

### 1) Face blur toolkit (webcam live + offline recordings)

Folder: `obs_face_blur_toolkit/`

What it does:
- `virtualcam`: live face anonymization (gaussian blur, pixelate, or black box) and output to a virtual camera device so OBS can record it.
- `blur-video`: offline post-processing for recordings (e.g., an OBS `.mp4` recording).

Docs:
- See `obs_face_blur_toolkit/README.md`

### 2) Secret/PII redaction inside OBS (text OCR)

File: `obs_secret_redactor.py`

What it does:
- Uses `obs-websocket` screenshots + OCR to detect secret-ish strings and covers them with black rectangles inside OBS.

Notes:
- This script **covers** content with boxes. It does not do “true blur” of underlying pixels.
- If you want “blur faces on any OBS source”, prefer an OBS-native filter plugin such as `obs-detect` (see `obs_face_blur_toolkit/README.md`).

