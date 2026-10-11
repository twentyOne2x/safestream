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

The default **selective** mode keeps ordinary public text readable. Telegram,
LastPass, 1Password, Apple Passwords and OBS's own preview are covered as whole
windows. Browser/window titles matching Challenger, Instagram, Gmail, login,
password and verification surfaces are also covered. A local text check can
recognize these scopes inside a generic editor or chat window; that window then
stays private while it moves or scrolls, including a move to another display.
Public windows in front remain visible.

Emails, handles, addresses, phone numbers, card details, six-digit codes and
recognizable private/public/API keys are covered. Recognized password, 2FA and
recovery fields make their owning window private. Ordinary capitalization does
not hide a heading. Unknown unlabelled personal names and unusual secret formats
need your local terms or a whole-window rule.

Add sensitive family names, addresses and private project names to
`private-terms.txt`, one per line. Copy `policy.example.json` to `local-policy.json`
to add `private_apps` (owner/bundle substrings) or `private_titles` (title
substrings). Both local files are excluded from Git. Restart after editing them.

For an area that must always be hidden, add normalized rectangles to the local
policy, for example `{"x": 0, "y": 0, "w": 0.2, "h": 1}` to cover the left
20% of the screen. Use these for a project sidebar, family photos or other
identifying imagery. The coordinates apply to the selected display.

OCR can miss small, stylized, rotated, unfamiliar or partially visible text.
Selective rules do not recognize every person's name or private project. These
are real limits, including in protective mode. Check the redacted preview and
cover known sensitive areas before sharing. Privacy applies to this visual
source; other OBS scenes and audio sources retain their own behavior.

Continuous capture targets 15 frames per second. Window masks are applied
before frames leave the native capture helper. Local OCR runs separately: pixels
in a changed window remain opaque until a matching whole-window context has been
checked. This can briefly cover public content while it changes; sensitive pixels
never use a previous mask at a new location. Capture rate and classification delay
depend on screen contents and Mac load.

The output pauses when capture or recognition cannot qualify new content. Frames
older than five seconds are replaced by a slate, and the browser independently
blanks on server failure or abrupt process death. No raw display source is added
to OBS. Captured pixels, recognized text and private terms stay local.

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
