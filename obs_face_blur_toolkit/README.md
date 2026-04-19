# OBS Face Blur Toolkit

This package gives you two complete ways to blur faces when recording/streaming with OBS:

1) **OBS-native (no Python):** install the OBS filter plugin **Detect (obs-detect)** and enable face masking/pixelation directly on a source inside OBS.

2) **Python virtual-camera pipeline (no OBS plugins required):**
   - Read frames from a webcam (or other OpenCV source)
   - Detect faces (YuNet / Haar)
   - Blur/pixelate/black-box faces
   - Output as a **virtual camera** that OBS can capture

It also includes an **offline** mode to blur faces in recorded video files (e.g., an OBS recording).

---

## Repository contents

- `obs_face_blur_toolkit.py`  
  Single-file tool with two subcommands:
  - `virtualcam` → live face blur → virtual camera for OBS
  - `blur-video` → post-process video files

- `requirements.txt`

---

## Option A: OBS-native (recommended for in-OBS sources)

### What you get
- Works on any OBS source (camera, media source, browser, etc.)
- Face detection based on YuNet
- Masking, including pixelate/blur-like masking

### Requirements
- OBS Studio **30.0.0+**
- Install the plugin: **Detect – Object Detection, Tracking built-in OBS**

### Basic setup
1. Add your webcam as **Video Capture Device** in OBS.
2. Right-click the source → **Filters**
3. Add **Detect**
4. Select **Face detection** model
5. Enable **Masking** and choose **Pixelate** (or the blur/pixelate option available)
6. Tune the confidence/size settings to reduce false positives

### When you should prefer this option
Use the OBS-native plugin approach if you want face blur on:
- **Media Source** videos you play inside OBS
- **Browser Source**
- **Display Capture / Window Capture**
- Any other source that is already inside OBS

The Python virtualcam approach below only anonymizes whatever video frames you feed into it (typically a webcam).

---

## Option B: Python virtual camera (works with OBS)

### 1) Install a virtual camera backend

`pyvirtualcam` does not create a device on its own — it writes frames into an existing virtual camera device.

#### Windows
- Backend `obs`: uses **OBS Virtual Camera**
- Backend `unitycapture`: uses **Unity Capture** (DirectShow filter)

If you also want OBS itself to output its own virtual camera at the same time, prefer **Unity Capture** for the Python output.

#### macOS
- Backend `obs`: uses **OBS Virtual Camera**
- One-time setup is usually: start OBS → Start Virtual Camera → Stop Virtual Camera → close OBS

Note: avoid running OBS's own "Start Virtual Camera" output at the same time you are trying to use the same virtual camera device as an input fed by Python.

#### Linux
- Backend `v4l2loopback`
- Install + create a device (Ubuntu/Debian example):
  - `sudo apt install v4l2loopback-dkms`
  - `sudo modprobe v4l2loopback devices=1 exclusive_caps=1 card_label="PyVirtualCam"`

---

### 2) Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2a) Install Tesseract (required for OCR text redaction)

The screen anonymizer mode uses Tesseract via `pytesseract` to find text boxes.

macOS (Homebrew):
- `brew install tesseract`

If `tesseract` is not on your PATH, pass `--tesseract-cmd /path/to/tesseract`.

### 2b) macOS permissions (required for screen capture)

If you use `screen-virtualcam`, macOS will require Screen Recording permission for the app that runs Python (Terminal, iTerm, PyCharm, etc.):
- System Settings -> Privacy & Security -> Screen Recording -> enable your terminal/IDE

---

### 3) Run live face blur → virtual camera

#### Minimal (webcam index 0)
```bash
python3 obs_face_blur_toolkit.py virtualcam
```

#### Blur background faces, keep the foreground face visible (streamer/co-host)
If you want to blur bystanders in the background but *not* blur the main face in the foreground,
use `--keep-n-largest`.

This is heuristic and fail-closed:
- If the “kept” face is not large enough or not centered enough, the tool will blur **all** faces.

Keep 1 foreground face (common):
```bash
python3 obs_face_blur_toolkit.py virtualcam --keep-n-largest 1 --preview
```

Keep 2 foreground faces (e.g., co-host):
```bash
python3 obs_face_blur_toolkit.py virtualcam --keep-n-largest 2 --preview
```

Safety knobs (more strict = less risk of accidentally leaving a bystander unblurred):
- `--keep-min-area-frac 0.03`
- `--keep-center-frac 0.90`

#### With preview window
```bash
python3 obs_face_blur_toolkit.py virtualcam --preview
```

#### Pixelate more strongly (bigger blocks)
```bash
python3 obs_face_blur_toolkit.py virtualcam --blur pixelate --pixel-size 18 --preview
```

#### Gaussian blur instead of pixelate
```bash
python3 obs_face_blur_toolkit.py virtualcam --blur gaussian --gaussian-kernel 0 --preview
```

(When `--gaussian-kernel 0`, the script auto-picks a kernel based on face size.)

#### Run detection less often (CPU saving)
```bash
python3 obs_face_blur_toolkit.py virtualcam --detect-fps 10 --preview
```

#### Pick a specific pyvirtualcam backend
```bash
python3 obs_face_blur_toolkit.py virtualcam --backend unitycapture --preview
```

---

## Screen Anonymizer (pre-OBS): redact names/addresses/secrets on Telegram/WhatsApp/cloud consoles

If you want anonymization **before** OBS sees the pixels (so you never accidentally capture raw names/addresses/API keys):
use `screen-virtualcam` to capture your screen, run OCR + pattern matching, and output an already-redacted virtual camera feed.

### Quick setup (macOS)
From the repo root:
```bash
./scripts/setup_mac.sh
./scripts/run_screen_safe.sh
```

Example (primary monitor, 1280x720 output, preview on):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam --preview --output-width 1280 --output-height 720
```

Paranoid mode (recommended if you want to avoid leaking *any* on-screen names/addresses/API keys/password fields):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam --text-mode all --text-method blackbox --preview
```

Full-screen block when the surface is clearly sensitive (recommended):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam \
  --block-on-context \
  --text-mode all --text-method blackbox \
  --preview --draw-boxes
```

Add your own “bank subdomain” triggers (examples):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam \
  --block-on-context \
  --block-url-regex '(^|\\s)secure\\.[a-z0-9.-]+' \
  --block-url-regex '(^|\\s)[a-z0-9-]+\\.mybank\\.com' \
  --preview
```

Text-only mode (no face detection):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam --faces-disable --preview
```

Disable OCR text redaction (faces only):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam --text-disable --preview
```

Crop to a region within the selected monitor (x,y,w,h):
```bash
python3 obs_face_blur_toolkit.py screen-virtualcam --region 100,80,1400,900 --preview
```

Performance knobs:
- `--ocr-fps 2` (lower is faster but may miss fast popups)
- `--ocr-width 900` (lower is faster but less accurate)
- `--min-word-conf 70` (higher reduces false positives)

Redaction style knobs:
- Faces: `--face-method pixelate|gaussian|blackbox`
- Text: `--text-method blackbox|pixelate|gaussian`
- Field/dropdown cover tuning: `--field-band-px` and `--field-band-px-strong` (strong is used for Password/2FA/private key/autofill dropdowns)

Important limitations:
- OCR is best-effort. It can miss small text, stylized fonts, or fast-moving UI.
- For privacy-critical use, prefer `--text-method blackbox`, increase `--text-pad-px`, and keep `--ocr-fps` reasonably high.

---

### 4) Use it in OBS

1. In OBS, click **+** → **Video Capture Device**
2. Select the virtual camera device:
   - Windows `obs` backend: typically **OBS Virtual Camera**
   - Windows `unitycapture`: typically **Unity Video Capture**
   - Linux: the created `/dev/video*` device label
3. Record/stream as usual.

### "Extend to video from OBS" (what to do)
If what you mean is "blur faces that appear in a video that is already in OBS":
- Use **Option A** (Detect filter) on that source.

If what you mean is "blur faces in the final recording that OBS produced":
- Use the **Offline** mode below (`blur-video`) on the recording file.

---

## Offline: blur faces in an OBS recording

```bash
python3 obs_face_blur_toolkit.py blur-video input.mp4 output.mp4 --blur gaussian --progress
```

Tips:
- If your output video won’t open, change `--fourcc`:
  - `mp4v` (default, common for .mp4)
  - `XVID` (often for .avi)
  - `avc1` (H.264, may require system codecs)

---

## Notes on accuracy

Face detection is not perfect. For privacy-critical use:
- Increase `--expand` so the blur covers more than the face rectangle.
- Prefer `pixelate` or `blackbox` if you need hard guarantees.
- Keep `--detect-fps` high enough to catch quick appearances.

---

## Troubleshooting

### Virtual camera is missing
- Windows: the OBS install includes a helper script to install Virtual Camera (run as Administrator).
- macOS: you may need to approve system software under Privacy & Security, then restart OBS.
- Linux: ensure the `v4l2loopback` module is loaded and you have permissions to access `/dev/video*`.

### `pyvirtualcam` says OBS Virtual Camera is not installed
On macOS/Windows this commonly means you need to start/stop OBS Virtual Camera once in OBS to trigger installation.

---

## License

You can treat this script as a starting point for your own workflow.
