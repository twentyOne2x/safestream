# Safestream: private desktop, redacted OBS output

Current request, superseding initial defaults (2026-10-11): use selective privacy
on the real screen, preserve ordinary public text, and track moving Telegram,
Challenger, Instagram, Gmail, LastPass and 1Password windows. Hide personal names,
addresses, cards, handles, email, password/2FA fields and private/public/API keys.
The same original Safestream runtime and OBS scene remain owned by this chat.
No messages, sign-in, credential reads or broadcasts are authorized by this task.

Current operator canary: move an actual Telegram window and a harmless public
test window on the selected display; compare each processed OBS frame with the
current privacy rectangles and verify ordinary settled public text is readable.
Done means selective mode is live, tracked private windows stay opaque during
movement, new pixels wait for classification, tested synthetic secret categories
are covered, OBS readback passes, and the capture cadence is measured truthfully.
Unread or failed classification must block new pixels, never reuse masks at old
coordinates. Unknown private facts and arbitrary secret strings remain a stated
recognition limit; explicit private terms/whole-window rules cover known surfaces.

Current slices cut before implementation: (1) selective secret recognition and
regressions, (2) persistent native capture with private window masks, (3) exact
changed-region quarantine and output integration, (4) launcher/policy and live
moving-window proof. Helpers mutate disjoint files, root integrates and verifies.

Working target: Resume twentyone2x/safestream on this Mac, preserving the
user's native view while OBS receives only locally processed, redacted frames.
Operator path: Launch Safestream, capture the chosen display privately, redact
the captured frame, preview the safe output, and use that output as an OBS source.
Done means: Synthetic names, address, card details and private project text are
covered in OBS output; native content remains intact; startup, processing failure,
stale output and shutdown show a privacy slate; a restart resumes the same route.
Not done: Guaranteed recognition of all personal information, cloud processing,
stream publication, other applications' capture sources, or smooth video at 30 FPS.
Required tier: Local operator path and OBS readback with synthetic data. Real screen
capture is authorized; no live broadcast or external publication is requested.

Owner: This chat owns integration, Git, runtime and OBS configuration. Bounded
helpers own only named disjoint files. Starting checkout is clean main at
2c8074ba82409533d7bf75d70723265b60ec71c2; OBS has one empty Scene and no running
process at discovery. Local Mac only. OBS websocket is configured and authenticated.

Canary: A synthetic image containing all requested categories, through production
redaction and local output, then an OBS Browser Source screenshot. Earliest observed
failure in the old path is incompatible SDK import, followed by unsafe post-broadcast
overlay updates, feedback masking, and fail-open errors. Replace publication with
processed frames; never publish captured bytes directly.

Reuse: Original obs_secret_redactor.py regex/PII intent; installed Tesseract;
OBS Browser Source and current obsws-python ReqClient provider examples. LEGOS search
covered attn-legacy, business-and-personal, royale, scratch-and-backups but did not
index safestream; GitHub inventory found the original repository.

Slices, cut before implementation: (1) local redaction engine and focused privacy
regressions; (2) buffered loopback output and capture runner; (3) OBS setup, launcher,
operator documentation and synthetic lifecycle evidence. Each publication concern
stays separately reviewable. Personal terms and captured frames remain untracked.

Privacy limits: OCR can miss small, stylized or unfamiliar text. Protective mode
masks all recognized text. Selective mode needs private terms for family/project
names, conservative name/address rules, and explicit honest coverage limits.
Non-text identifying imagery requires manual regions or whole-screen blackout.
Do not expose or record raw capture, OCR text, OBS credentials or private terms.

2026-10-11 operator proof: Original Tesseract path exceeded publication freshness
on the full desktop. Mac capture now uses ScreenCaptureKit (three-second bound),
with local Vision fast regions for protective mode and accurate text for selective
mode. Five live samples were fresh and redacted; measured capture about 0.79s,
recognition about 0.39s on that sampled screen. These are local observations,
not a frame-rate guarantee. Native selective OBS readback covered seven complete
synthetic private rows and preserved the eighth public row's pixels exactly.
Worker death and bad-image processing produced a slate; same-runtime recovery,
OBS restart, and repeated launcher selection were exercised. No broadcast started.
An OBS crash occurred during setup/reload; normal-mode recovery retained the scene.
Routine reuse now avoids unnecessary browser settings/reload calls. First-setup
ownership is journaled before creation and a retained empty scene can resume.
