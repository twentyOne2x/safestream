# Pump.fun Streaming (macOS) - OBS + Safe Screen

This repo is built around a "pre-OBS" safety model:
capture your screen, redact sensitive text locally, then feed the already-redacted video into OBS as a virtual camera.

## 1) Install and bootstrap

Run from repo root:

```bash
bash ./scripts/pumpfun_ready_mac.sh
```

This installs OBS (via Homebrew) and sets up the Python venv + OCR dependencies used by `screen-virtualcam`.

## 2) macOS permissions (required)

One-time:

1. System Settings -> Privacy & Security -> Screen Recording:
   enable the app you will run the script from (Terminal/iTerm/PyCharm).
2. If you use webcam face blur:
   Privacy & Security -> Camera -> enable the same app.

## 3) Start the safe screen virtual camera

```bash
bash ./scripts/run_pumpfun_safe.sh
```

Notes:
- Default output is `1280x720 @ 30fps`. Override as needed:
  `./scripts/run_pumpfun_safe.sh --output-width 1920 --output-height 1080`
- For debugging (local preview + box overlays):
  `bash ./scripts/run_screen_safe.sh`

## 4) Use it in OBS

1. Open OBS once and Start/Stop Virtual Camera once (Tools -> Virtual Camera).
   This creates the macOS virtual camera device that Python writes into.
2. Create a Scene (example: `Pumpfun (Safe)`).
3. Add Source -> Video Capture Device -> select the virtual camera device (usually `OBS Virtual Camera`).
4. Add your Mic source (Audio Input Capture).

### System audio (optional, macOS)

If you need browser/system audio in OBS, macOS usually requires a virtual audio device (e.g., BlackHole/Loopback).
This repo does not auto-configure system audio routing; do it only if you need it.

## 5) Stream destination

Pump.fun itself is usually something you *show* on stream (Browser), not the streaming provider.
In OBS, set your streaming destination under Settings -> Stream (Twitch/YouTube/Kick/Custom RTMP).
