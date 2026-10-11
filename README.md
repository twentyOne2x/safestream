# Safestream

Your desktop stays intact. OBS receives a separate view made from frames that
have already been redacted on your Mac. Captured frames and recognized text are
processed in memory; they are not sent to a cloud service or written to disk.
On this Mac, recognition uses Apple's local Vision framework for English, French
and German, and screen capture uses ScreenCaptureKit. The launcher builds the
small helpers using Apple's Swift toolchain. Capture is bounded to three seconds.
Protective mode uses fast text-region recognition; selective mode uses accurate
recognition to match private words. Both retain the same publication expiry.

Double-click **Safestream.command**. It opens OBS, starts the local redactor, and
selects the **Safestream** scene. Use that scene when you stream or record. The
launcher itself never starts a broadcast, recording or virtual camera. Keep its
Terminal window open; Control-C stops it. OBS then shows **Privacy paused**.

The initial **protective** mode covers every text line OCR recognizes. For a
more readable stream, create `local-policy.json` from `policy.example.json`, set
`"mode": "selective"`, and add sensitive names, addresses and private project
names to `private-terms.txt`, one per line. Keep those details in these local
files. Both are excluded from Git. Restart the launcher after editing them.
Selective mode also applies conservative name, address, phone, email, card,
expiry, security-code and token patterns. It can hide ordinary headings too.

For an area that must always be hidden, add normalized rectangles to the local
policy, for example `{"x": 0, "y": 0, "w": 0.2, "h": 1}` to cover the left
20% of the screen. Use these for a project sidebar, family photos or other
identifying imagery. The coordinates apply to the selected display.

OCR can miss small, stylized, rotated, unfamiliar or partially visible text.
Selective rules do not recognize every person's name or private project. These
are real limits, including in protective mode. Check the redacted preview and
cover known sensitive areas before sharing. Privacy applies to this visual
source; other OBS scenes and audio sources retain their own behavior.

Capture and OCR run up to twice a second, depending on screen contents and Mac
load. This first version is intended for desktop demonstrations, not smooth
gameplay. Each published frame is processed in full before it becomes visible.
Frames older than five seconds are replaced by a slate. The browser separately
blanks its output on server failure, including abrupt process death. No raw
screen-capture source is added to OBS.

For the external display, launch with:

```sh
.venv/bin/python safestream.py --monitor 2 --setup-obs
```

Default display 1 is the Mac's primary display. Closing/reopening
OBS preserves the safe source and its local URL. To roll back the scene selection,
select the original **Scene** in OBS; the redactor can then be stopped with
Control-C. No existing scene or audio device is deleted or reconfigured.

Setup on another Mac running macOS 14+: install Python 3.11+, uv, Apple's command line developer
tools, Tesseract for fallback/tests, and OBS 28+. Run `uv sync --frozen --group dev`, enable OBS's authenticated
websocket server, and grant screen recording access to the launcher host when
macOS asks. The Mac launcher reads the existing OBS connection configuration
locally without printing or passing its password on the command line.

Verification: `uv run --frozen pytest -q`. Focused tests cover category matching,
low-confidence text, more than 40 masks, manual regions, OCR failure, no mutation
of the private frame, publication expiry, loopback restrictions, active-output
preservation and exact OBS ownership before configuration changes. Local OBS
synthetic readback also verifies full opaque coverage, unchanged public text,
startup/restart and a privacy slate after abrupt process death.

The old `obs_secret_redactor.py` overlay entry point is disabled because masking
already-broadcast frames could leak newly appearing information.

References: [OBS Browser Source](https://obsproject.com/kb/browser-source),
[OBS websocket protocol](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md),
and [obsws-python SDK example](https://github.com/aatikturk/obsws-python#how-to-use).
Native recognition follows [Apple's Vision example](https://developer.apple.com/documentation/vision/recognizing-text-in-images).
