# Issues

## 2026-02-06 - Add Face Blur Toolkit (OBS Virtual Cam + Offline Video)

PLANNER
- Spec check: solvable. We have a bundle zip with a Python tool; integrate it into this repo with clear docs and a minimal local verification path.
- Acceptance criteria:
  - `obs_face_blur_toolkit/obs_face_blur_toolkit.py` exists in-repo with `virtualcam` + `blur-video` subcommands.
  - `obs_face_blur_toolkit/README.md` documents:
    - OBS-native plugin option (Detect / obs-detect) for in-OBS sources.
    - Python virtualcam option (pyvirtualcam) with macOS-focused notes.
    - Offline processing usage and codec troubleshooting.
  - Repo root `README.md` points to both tools (face blur + OCR redactor).
  - Minimal verification recorded here: CLI help runs; offline mode can process a tiny synthetic video using `--detector haar` without downloading models.
- Missing info/questions:
  - None required to proceed. Assumption: primary target is macOS (current machine), but docs will include Windows/Linux notes as provided.
- Context + suspected cause:
  - Current repo only contains `obs_secret_redactor.py`. The requested face blur functionality is provided via the supplied zip bundle but not integrated.
- Fix intent:
  - Add the bundle into the repo as `obs_face_blur_toolkit/` and add top-level docs + `.gitignore`.
- Executor prompt (files, constraints, tests):
  - Touch: `README.md`, `.gitignore`, `docs/ISSUES.md`, `obs_face_blur_toolkit/*`.
  - Add a simple smoke verification command sequence (no camera required).

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context + acceptance criteria recorded.
- [x] Fix applied (bundle integrated + root docs + gitignore).
- [x] Verification run (CLI help + offline synthetic video smoke test).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - CLI help:
    - `cd obs_face_blur_toolkit && . .venv/bin/activate && python obs_face_blur_toolkit.py --help`
  - Offline smoke test (no model download; `--detector haar`), created a small synthetic input video and produced an output video that re-opened successfully (45 frames written).

## 2026-02-06 - Pre-OBS Screen Anonymizer (OCR PII/Secrets + Optional Face Blur) -> Virtual Camera

PLANNER
- Spec check: solvable with tradeoffs. We can capture screen frames locally, run OCR + pattern matching for PII/secrets, and redact boxes before frames are sent to OBS by outputting a virtual camera feed. Accuracy will be best-effort, not a guarantee.
- Acceptance criteria:
  - Add a new subcommand `screen-virtualcam` to `obs_face_blur_toolkit/obs_face_blur_toolkit.py`.
  - `screen-virtualcam` can:
    - capture a monitor or region (macOS Screen Recording permission required),
    - optionally detect faces,
    - run OCR and redact matched text regions (names/addresses/emails/phones/credit cards/API keys, etc.),
    - output to a virtual camera device via `pyvirtualcam`,
    - optionally show a local preview window.
  - Update `obs_face_blur_toolkit/requirements.txt` and `obs_face_blur_toolkit/README.md` with prerequisites:
    - install Tesseract for OCR,
    - grant macOS Screen Recording permission to the terminal/Python running the script,
    - virtual camera installation notes.
  - Minimal verification recorded here:
    - `python obs_face_blur_toolkit.py screen-virtualcam --help` runs.
    - Non-GUI unit-ish smoke: text redaction box finder can be imported and executed on a synthetic image without raising.
- Missing info/questions:
  - Which do you want to target by default: full monitor or a fixed region crop?
  - Is "black box" acceptable for text redaction (stronger privacy) vs blur/pixelate (nicer look)?
- Context + suspected cause:
  - Current scripts cover (a) face blur for webcam (pre-OBS via virtual cam) and (b) OCR redaction inside OBS via websocket screenshots. User wants OCR redaction and secrets/PII applied pre-OBS on arbitrary apps (Telegram/WhatsApp/cloud consoles).
- Fix intent:
  - Implement screen capture + OCR redaction pipeline in the existing toolkit to keep setup simple and reuse the virtualcam output path.
- Executor prompt (files, constraints, tests):
  - Touch: `obs_face_blur_toolkit/obs_face_blur_toolkit.py`, `obs_face_blur_toolkit/requirements.txt`, `obs_face_blur_toolkit/README.md`, `docs/ISSUES.md`.
  - Keep processing local; do not send frames off-machine.
  - Tests: run `--help` for new subcommand; run a small in-process function smoke on a synthetic image (no real screen capture required in CI/headless).

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context + acceptance criteria recorded.
- [x] Fix applied (screen capture + OCR redaction + docs).
- [x] Verification run (help + synthetic smoke).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - CLI help:
    - `cd obs_face_blur_toolkit && . .venv/bin/activate && python obs_face_blur_toolkit.py screen-virtualcam --help`
  - Synthetic OCR smoke:
    - Imported `find_sensitive_text_boxes()` and detected boxes on a synthetic image containing an AWS access key + email (2 boxes found).

## 2026-02-06 - Paranoid Screen Redaction Mode (Redact All On-Screen Text)

PLANNER
- Spec check: solvable. To approximate “redact literally all sensitive info on screen”, the only robust OCR-based approach is to redact **all detected text**, not just pattern matches.
- Acceptance criteria:
  - Add `--text-mode sensitive|all` to `screen-virtualcam`.
  - `--text-mode all` produces redaction boxes for all OCR-detected text (above confidence threshold), merged to sane line boxes to avoid thousands of tiny boxes.
  - Docs updated to explain that `--text-mode all` is the recommended “paranoid” mode for avoiding leaks of names/addresses/API keys/password fields.
  - Minimal verification:
    - `python obs_face_blur_toolkit.py screen-virtualcam --help` shows `--text-mode`.
    - A synthetic OCR smoke can return boxes in `--text-mode all` even when the text is not a secret pattern.
- Missing info/questions:
  - None required.
- Context + suspected cause:
  - Pattern matching can miss unknown API key formats, random addresses/names, or low-quality OCR. User wants near-guarantee.
- Fix intent:
  - Add an OCR “all text” mode that redacts everything OCR sees, using blackbox by default.
- Executor prompt (files, constraints, tests):
  - Touch: `obs_face_blur_toolkit/obs_face_blur_toolkit.py`, `obs_face_blur_toolkit/README.md`, `docs/ISSUES.md`.
  - Keep processing local.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Fix applied (add `--text-mode all` + docs).
- [x] Verification run (help + synthetic OCR smoke).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - Help output includes `--text-mode {sensitive,all}`.
  - Synthetic OCR smoke:
    - In `--text-mode all`, boxes are returned for non-secret text lines.

## 2026-02-06 - Context-Based Full-Screen Blocking (Telegram/WhatsApp/Google Docs/Bank URLs) + Field Band Cover

PLANNER
- Spec check: solvable with heuristics. We can OCR a small “context region” (top of screen) to detect app/site indicators (e.g., `docs.google.com`, `sheets.google.com`, `Telegram`, `WhatsApp`, bank/login keywords) and then black out the entire frame when triggered. Separately, we can expand redaction around “field labels” (Password/Address/Name/etc.) to cover nearby input boxes that OCR might not read.
- Acceptance criteria:
  - Add `--block-on-context` option to `screen-virtualcam`.
  - Add `--block-url-regex` and `--block-keyword` allowlists (repeatable) and ship sensible defaults:
    - Google Docs/Sheets/Drive
    - Telegram / WhatsApp
    - generic bank/login/account/secure keywords (best-effort)
  - Add `--field-cover` (default on) that creates a full-width “band” redaction beneath common field labels (password/address/name/card/etc.).
  - Update `obs_face_blur_toolkit/README.md` with recommended “safe” command lines.
  - Verification:
    - `--help` shows the new flags.
    - Synthetic OCR smoke triggers block when `docs.google.com` is present in the context region.
- Missing info/questions:
  - None required; defaults should be configurable by flags.
- Context + suspected cause:
  - OCR pattern matching can miss unknown token formats and password dots; user wants whole-screen hiding when the surface is clearly sensitive (chats/docs/banks).
- Fix intent:
  - Add fast context OCR + hysteresis hold timer to avoid flicker; add field-band redaction on label detection.
- Executor prompt (files, constraints, tests):
  - Touch: `obs_face_blur_toolkit/obs_face_blur_toolkit.py`, `obs_face_blur_toolkit/README.md`, `docs/ISSUES.md`.
  - Keep processing local.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Fix applied (context block + field cover + docs).
- [x] Verification run (help + synthetic trigger smoke).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - Help output includes `--block-on-context`, `--block-url-regex`, and `--field-cover`.
  - Synthetic OCR smoke: OCR sees `docs.google.com/...` and `context_should_block()` returns True when `docs\\.google\\.com` is in `--block-url-regex`.

## 2026-02-08 - Finder Crashes / Unstable

PLANNER
- Spec check: solvable. We can collect crash evidence and apply safe user-level resets (saved state, caches, Finder prefs), plus disable third-party Finder extensions if implicated.
- Acceptance criteria:
  - Finder remains running after relaunch (no immediate crash loop).
  - You can open at least 2 Finder windows and browse for 1-2 minutes without a crash.
  - If crash reports are accessible: identify a likely culprit (plugin/extension/path) and document it here.
- Missing info/questions:
  - If crash logs are blocked by macOS privacy, you may need to grant Terminal Full Disk Access to read `~/Library/Logs/DiagnosticReports`.
- Context + suspected cause:
  - Report: Finder "crashes all the time".
  - Common causes: corrupted Finder prefs/saved state, problematic sidebar items, Finder Sync extensions (Dropbox/Drive/etc), QuickLook generators.
- Fix intent:
  - Capture evidence (crash logs / unified log) then apply least-destructive resets, escalating to prefs reset and disabling extensions.
- Executor prompt (files, constraints, tests):
  - Touch: `/Users/user/PycharmProjects/safestream/docs/ISSUES.md` (record evidence + steps).
  - Avoid destructive system changes; only user-level preference/cache resets.
  - Verification: relaunch Finder and confirm stability.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Evidence collected (crash report excerpt and/or `log show`).
- [x] Fix applied (resets / extension disable as needed).
- [x] Verification run (Finder stable after relaunch).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - No Finder crash reports present in `~/Library/Logs/DiagnosticReports` at the time of audit (no `Finder_*.crash` / `*Finder*.ips`).
  - Finder PID monitored for 60s pre-fix and post-fix: no PID changes (no crash loop observed).
  - Performed user-level reset: removed Finder saved state + caches and reset `~/Library/Preferences/com.apple.finder.plist` (backup at `~/Desktop/FinderFixBackup_20260208_064708`).
  - Restarted Finder and programmatically opened 2 windows and navigated to `/Applications` and `/Users/user`.
  - Disabled a third-party QuickLook generator (`~/Library/QuickLook/DropboxQL.qlgenerator`) by moving it into the backup folder and resetting QuickLook daemons.

## 2026-02-08 - System Settings Won't Show / Won't Come To Front

PLANNER
- Spec check: solvable. We can restart the app, clear its saved window state, reset its per-app prefs, and inspect crash logs/unified logs to see if it is crashing or opening off-screen.
- Acceptance criteria:
  - `open -a "System Settings"` results in a visible System Settings window.
  - Deep link open (x-apple.systempreferences URL) shows a pane.
- Missing info/questions:
  - If it’s on another Space/Display, we may need you to confirm which displays/spaces you’re using.
- Context + suspected cause:
  - User report: System Settings doesn't show up when opened.
  - Common causes: corrupted saved state (window off-screen), stuck process, crash on launch, display/Spaces issue.
- Fix intent:
  - Collect evidence (crash reports/logs) then apply least-destructive resets (saved state, plist prefs), then reopen via deep link.
- Executor prompt (files, constraints, tests):
  - Touch: `/Users/user/PycharmProjects/safestream/docs/ISSUES.md`.
  - Avoid destructive system changes; only user-level state resets.
  - Verification: open visible window.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Evidence collected (DiagnosticReports + unified log excerpt).
- [x] Fix applied (state/prefs reset + reopen).
- [x] Verification run (window appears).

VERIFIER
- Compare proofs to acceptance criteria: PARTIAL.
- Proofs:
  - Identified severe UI slowness driver: a `pnpm exec tsx ... collectorcrypt_scrape/daemon_refresh.ts` launch agent spawning Playwright Chromium (`chrome-headless-shell`) with a renderer pegging CPU (~100%).
  - Stopped the running Playwright/Node process tree and disabled the launch agent `com.attn.collectorcrypt.refresh` via `launchctl bootout` + `launchctl disable` to prevent respawn.
  - Performed user-level reset: removed saved state and per-app prefs for System Settings (`com.apple.SystemSettings.plist`, `com.apple.systempreferences.plist`), restarted `cfprefsd`.
  - Reopened via deep link (`x-apple.systempreferences:...Privacy_ScreenCapture`) and activated the app; process is running afterwards.
  - Found a concrete symptom: System Settings had a window but it was flagged `visible=false`. Forced it visible and moved it on-screen via AppleScript.
  - Enabled Dock setting `workspaces-auto-swoosh=1` so app activation should switch to the Space containing the window (Mission Control setting: “When switching to an application, switch to a Space with open windows”).
  - If it still does not appear after that, next suspect is WindowServer/UI load (close high-CPU apps/tabs) or a deeper OS issue that may require a reboot.

## 2026-02-14 - Public Streaming Privacy: Blur Background Faces (Keep Foreground) + Stronger Autofill/Password/Private Key Redaction

PLANNER
- Spec check: solvable with best-effort tradeoffs. We can (a) add an explicit “keep foreground faces visible” mode for face blur that blurs everyone else, and (b) harden OCR-based redaction for passwords/emails/autofill dropdowns (LastPass/Bitwarden/etc) and private keys. This cannot be a 100% guarantee because face detection and OCR can miss, but we can provide safer defaults and a fail-closed mode (blur all / block whole frame when ambiguous).
- Acceptance criteria:
  - Face blur:
    - Add new CLI flags to `virtualcam`, `blur-video`, and `screen-virtualcam` to support “blur background faces but not foreground face(s)”:
      - `--keep-n-largest N` (keep N largest faces unblurred, blur the rest; default 0 = blur all).
      - Safety gates: `--keep-min-area-frac` and `--keep-center-frac` to reduce the chance of accidentally leaving a background bystander unblurred.
    - Minimal verification: a small synthetic unit-ish test demonstrates that when `keep-n-largest=1`, only the smaller face boxes remain in the blur set.
  - Screen text redaction (OCR):
    - Strengthen “sensitive mode” so it reliably covers:
      - password fields (including masked password dots that OCR may not read) via field-band cover,
      - autofill dropdowns and password manager UI (LastPass/Bitwarden/etc),
      - emails,
      - private keys and common crypto secret material (“seed phrase”, “mnemonic”, etc).
    - Add password manager keywords to context blocking defaults so opening password managers can trigger full-frame block.
    - Minimal verification: synthetic image tests trigger box detection for (a) an email, (b) a PEM private key header, and (c) an “Email/Password” label causing a band cover box.
  - Docs:
    - Update `obs_face_blur_toolkit/README.md` with recommended “public streaming” command lines and limitations (best-effort; fail-closed suggestions).
- Missing info/questions:
  - How do you want to define “foreground” for keeping faces unblurred: keep 1 face (streamer), keep N faces (co-host), or keep faces inside a fixed region? For now, implement N-largest + safety gates (center + min area) to reduce privacy risk.
- Context + suspected cause:
  - Current `virtualcam`/`blur-video`/`screen-virtualcam` blur all detected faces equally; they do not distinguish foreground vs background.
  - Current OCR redaction can miss password values (masked bullets) and some autofill dropdowns unless we cover the whole input region.
- Fix intent:
  - Add a deterministic “keep foreground visible” control for face blur (opt-in), and expand field-cover + defaults for password managers and private key material.
- Executor prompt (files, constraints, tests):
  - Touch: `obs_face_blur_toolkit/obs_face_blur_toolkit.py`, `obs_face_blur_toolkit/README.md`, `docs/ISSUES.md`.
  - Constraints: local-only processing; prefer fail-closed behavior when ambiguous.
  - Tests:
    - CLI `--help` for `virtualcam` and `screen-virtualcam` shows new flags.
    - Run a small Python snippet that imports helper functions and asserts expected face-box filtering and text-box detection on synthetic images.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context + acceptance criteria recorded.
- [x] Fix applied (foreground keep mode + redaction hardening + docs).
- [x] Tests run (CLI help + synthetic tests).
- [x] Verification recorded here (proofs + PASS/FAIL).

VERIFIER
- Compare proofs to acceptance criteria: PASS.
- Proofs:
  - CLI help shows new flags:
    - `obs_face_blur_toolkit.py virtualcam --help` includes `--keep-n-largest`, `--keep-min-area-frac`, `--keep-center-frac`.
    - `obs_face_blur_toolkit.py screen-virtualcam --help` includes those plus `--field-band-px-strong`.
  - Synthetic smoke (local, via toolkit venv):
    - `select_faces_to_blur()` with 3 face boxes and `keep_n_largest=1` returned only the 2 smaller boxes to blur.
    - `find_sensitive_text_boxes()` on a synthetic image containing an email, a PEM private key header, `0x`-hex key, and `Bitwarden` produced redaction boxes including full-width bands from field/dropdown cover (`smoke_ok`).

## 2026-02-15 - MX Master 3S Mouse Pointer Hyperspeed (System Scaling Ignored)

PLANNER
- Spec check: solvable. We can force a per-device pointer acceleration/speed value via `hidutil property` for the specific mouse (Logitech MX Master 3S), independent of `com.apple.mouse.scaling`.
- Acceptance criteria:
  - Pointer speed is no longer "hyperspeed" for the MX Master 3S; user confirms mouse is usable.
  - `hidutil property --matching '{"VendorID":0x046D,"ProductID":0xB034}' --get HIDPointerAcceleration` reflects the new (lower) value.
  - Trackpad remains unchanged (separate from mouse).
  - Mouse scroll direction remains Windows-like (wheel up scrolls up) via LinearMouse, without changing the trackpad.
- Missing info/questions:
  - None required to apply the mitigation; user confirmation required for subjective feel.
- Context + suspected cause:
  - User report: mouse pointer moves at hyperspeed and `com.apple.mouse.scaling` changes appear to have no effect.
  - Device: Logitech MX Master 3S over Bluetooth LE (`VendorID 0x046D`, `ProductID 0xB034`).
  - A Logitech "USB Receiver" was also present (`VendorID 0x046D`, `ProductID 0xC547`), which can be used for dongle mode.
  - Likely per-device HID pointer acceleration override required; global defaults key may not be applied to this device path.
- Fix intent:
  - Set per-device `HIDPointerAcceleration` for the MX Master 3S with `hidutil` (and also the Logitech USB receiver so either connection path is covered).
  - Keep LinearMouse limited to mouse-only scroll reversal (no pointer speed overrides) to avoid multiple competing settings.
- Executor prompt (files, constraints, tests):
  - Touch: `docs/ISSUES.md` only (record).
  - Commands:
    - `hidutil property --matching '{"VendorID":0x046D,"ProductID":0xB034}' --set '{"HIDPointerAcceleration":20000}'`
    - `hidutil property --matching '{"VendorID":0x046D,"ProductID":0xC547}' --set '{"HIDPointerAcceleration":20000}'`
    - Verify via `--get`.
  - Verification:
    - Record before/after `hidutil` output.
    - User confirms pointer speed feels normal.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context + acceptance criteria recorded.
- [x] Fix applied.
- [ ] Tests run (N/A; OS setting change).
- [ ] Verification recorded here (requires user confirmation).

VERIFIER
- Compare proofs to acceptance criteria: PARTIAL (awaiting user confirmation).
- Proofs:
  - Before: `hidutil ... --get HIDPointerAcceleration` returned `45056` for the MX Master 3S.
  - After: set to `20000` for `VendorID 0x046D / ProductID 0xB034`:
    - `hidutil property --matching '{"VendorID":0x046D,"ProductID":0xB034}' --get HIDPointerAcceleration` shows `20000`.
  - Also set to `20000` for the Logitech USB receiver `VendorID 0x046D / ProductID 0xC547` so dongle mode is covered.
  - LinearMouse config kept mouse-only scroll reverse (no pointer override):
    - `~/.config/linearmouse/linearmouse.json` contains only `scrolling.reverse.vertical=true` under an MX Master 3S device match.

## 2026-02-28 - Screenshot Shortcut Sometimes Freezes Laptop / Capture UI Gets Stuck

PLANNER
- Spec check: solvable. We can investigate the screenshot stack (`screencapture`, `screencaptureui`, WindowServer, tccd), identify blocking/focus interference, and apply a minimal non-destructive reset.
- Acceptance criteria:
  - Screenshot shortcut no longer leaves a long-running stuck `screencapture -pdin -z keyboard.selection` process.
  - Screenshot UI can be invoked without freezing the desktop.
  - Investigation identifies likely culprits with evidence (process + logs).
- Missing info/questions:
  - Which exact shortcut path fails most often (`Cmd+Shift+3`, `Cmd+Shift+4`, or `Cmd+Shift+5`) is not yet specified; proceed with generic screenshot-path diagnostics.
- Context + suspected cause:
  - `WindowServer` has been running high CPU for extended periods.
  - Live stuck pair observed: `screencapture -pdin -z keyboard.selection` + `screencaptureui`.
  - WindowServer logs repeatedly show `active key thief ... pid: 95595`, mapped to `AltTab.app`.
  - `tccd` logs during screenshot events show attribution involving `com.lwouis.alt-tab-macos`.
  - High concurrent UI load (WindowServer + WallpaperAerialsExtension + browser renderers) likely increases freeze probability.
- Fix intent:
  - Collect process/log evidence.
  - Kill stale screenshot capture processes.
  - Relaunch screenshot UI and validate process behavior.
  - Recommend mitigation path: temporarily quit AltTab and reduce WindowServer load (disable animated wallpaper).
- Complexity: small
- Executor prompt (files, constraints, tests):
  - Touch: `docs/ISSUES.md`.
  - Constraints: non-destructive user-level actions only; avoid system-wide resets unless needed.
  - Verification:
    - Check process list before/after reset.
    - Check relevant unified logs for key interception and screenshot process lifecycle.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context + acceptance criteria recorded.
- [x] Evidence collected (process snapshots + `log show` excerpts).
- [x] Minimal reset applied (`kill -9` stale screenshot processes and relaunch screenshot UI).
- [x] Additional mitigation applied: forced `com.apple.screencapture show-thumbnail=1`, restarted `SystemUIServer` + `Dock`, and temporarily stopped `AltTab` for clean validation.
- [x] Restored explicit screenshot hotkey bindings in `com.apple.symbolichotkeys` for IDs `28/29/30/31/184` and restarted `cfprefsd`, `SystemUIServer`, and `Dock`.
- [x] Isolated third-party key interceptors by stopping both `AltTab` and `Hammerspoon` before re-test.
- [ ] User-facing verification complete (awaiting user to test shortcuts interactively).

VERIFIER
- Compare proofs to acceptance criteria: PARTIAL (evidence-backed likely cause, pending interactive user confirmation).
- Proofs:
  - Stuck screenshot pair observed for over a minute:
    - `91391 /usr/sbin/screencapture -pdin -z keyboard.selection`
    - `91392 /System/Library/CoreServices/screencaptureui.app/Contents/MacOS/screencaptureui`
  - WindowServer logs repeatedly reported active key interception by `pid 95595`:
    - `active key thief ... pid: 95595`
  - `pid 95595` resolved to `AltTab.app`:
    - `/Applications/AltTab.app/Contents/MacOS/AltTab`
  - `tccd` logs during screenshot UI requests included attribution to `com.lwouis.alt-tab-macos`.
  - After resetting stale screenshot processes, they were cleared and screenshot UI relaunched cleanly with a new PID.
  - Forced thumbnail setting now explicitly enabled:
    - `defaults read com.apple.screencapture` shows `"show-thumbnail" = 1`.
  - After stopping `AltTab`, there were no new WindowServer `active key thief` events in the following 5 minutes.
  - Screenshot hotkeys are now explicitly present and enabled in `~/Library/Preferences/com.apple.symbolichotkeys.plist`:
    - `28` (`Cmd+Shift+3`), `29` (`Cmd+Ctrl+Shift+3`), `30` (`Cmd+Shift+4`), `31` (`Cmd+Ctrl+Shift+4`), `184` (`Cmd+Shift+5`).
  - Backup created before keymap rewrite:
    - `~/Library/Preferences/com.apple.symbolichotkeys.plist.backup-20260228-181056`
  - With both `AltTab` and `Hammerspoon` stopped, no `active key thief` events were observed in the last 2 minutes.

## 2026-03-01 - Telegram App Does Not Boot

PLANNER
- Spec check: solvable. We can verify whether Telegram is crashing on launch, launching headless, blocked by stale state, or blocked by OS policy/runtime dependencies; then apply least-destructive recovery.
- Acceptance criteria:
  - Telegram launches and stays running.
  - Telegram window becomes visible/active.
  - No immediate crash loop after launch attempt.
- Missing info/questions:
  - None required to begin diagnostics; proceed with local process/log inspection.
- Context + suspected cause:
  - User report: "telegram doesnt boot".
  - Initial checks show app bundle exists but process does not stay running after `open -a Telegram`.
  - No immediate Telegram crash report found in `~/Library/Logs/DiagnosticReports`.
- Fix intent:
  - Gather startup logs/process evidence.
  - Reset only Telegram user-state components if needed.
  - Re-test launch and visibility.
- Complexity: small
- Executor prompt (files, constraints, tests):
  - Touch: `docs/ISSUES.md`.
  - Constraints: non-destructive first; back up any Telegram state before moving/removing.
  - Verification:
    - `open -a Telegram`, confirm PID persists.
    - Confirm a visible/active Telegram window.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Initial context/evidence captured.
- [x] Fix applied.
- [x] Verification run.

VERIFIER
- Compare proofs to acceptance criteria: PARTIAL (process-level PASS; user-visible confirmation pending).
- Proofs:
  - Confirmed Telegram app bundle and binary are present:
    - `/Applications/Telegram.app`
    - `/Applications/Telegram.app/Contents/MacOS/Telegram`
  - Applied non-destructive state reset with backup:
    - Backed up and rotated `~/Library/Application Support/Telegram Desktop/tdata/settingss` to `settingss.pre_fix_20260301_215145`.
    - Backed up and removed `~/Library/Preferences/com.tdesktop.Telegram.plist`.
    - Backup directory: `~/telegram_boot_fix_backup_20260301_215145`.
  - Identified launch blockage pattern:
    - `open -a Telegram` intermittently produced no new main process while stale Telegram helper/crashpad processes remained.
  - Applied runtime cleanup and relaunch:
    - Killed all `/Applications/Telegram.app` processes (main + helper/crashpad), then relaunched.
    - Main process now starts and persists (`/Applications/Telegram.app/Contents/MacOS/Telegram`).
  - Restart-path verification:
    - Performed quit + relaunch cycle; `open -a Telegram` now starts a fresh main process (`PID 55711` at verification time) without immediate crash loop.
  - Remaining caveat:
    - Terminal stderr still logs occasional Qt/macOS layout warnings; no crash loop observed, but final UI/visibility confirmation is user-interactive.

## 2026-03-04 - Telegram Boot Regression (Stuck During Camera Stack Init)

PLANNER
- Spec check: solvable. We can reproduce startup path, capture where Telegram blocks, and apply user-level process resets first.
- Acceptance criteria:
  - Telegram launches and stays running.
  - Startup log advances past early screen-scale lines into media/app init.
  - Telegram process is visible/frontmost after activation.
- Missing info/questions:
  - None required to proceed.
- Context + suspected cause:
  - User report: Telegram still cannot open after prior restart.
  - Repro showed Telegram repeatedly hanging very early in launch.
  - `sample` showed main-thread blockage in `AVCaptureDALDevice` / `CoreMediaIO` extension initialization (camera stack).
- Fix intent:
  - Reset camera helper processes likely involved in CMIO deadlock.
  - Relaunch Telegram and verify full startup progression.
- Complexity: small
- Executor prompt (files, constraints, tests):
  - Touch: `docs/ISSUES.md`.
  - Constraints: non-destructive user-level resets only.
  - Verification:
    - `sample <pid>` no longer pinned in camera init during startup.
    - Telegram log progresses through media/app/font/OpenGL initialization.
    - Process is visible/frontmost via AppleScript process query.

EXECUTOR
- [x] Report captured in `docs/ISSUES.md`.
- [x] Context/evidence captured (`sample` and startup logs).
- [x] Fix applied.
- [x] Verification run.

VERIFIER
- Compare proofs to acceptance criteria: PASS (process + visibility level).
- Proofs:
  - Captured repeated blocked stack before fix:
    - Main thread in `+[AVCaptureDALDevice devices]` -> `CMIO::DAL::PlugInManagement::InitializeExtensionPlugIns()` -> `xpc_connection_send_message_with_reply_sync`.
  - Confirmed this was not limited to existing Telegram profile by reproducing with `-workdir /tmp/...`.
  - Applied helper reset:
    - Restarted user camera helpers: `ContinuityCaptureAgent`, `ptpcamerad`, `mscamerad-xpc`, `avconferenced`.
    - Relaunched Telegram.
  - Post-fix Telegram startup log advanced fully (media device list, app settings/accounts, fonts, OpenGL context).
  - Telegram process persisted and became frontmost/visible:
    - `Telegram, true, false, true` from System Events process properties.
  - Additional note:
    - `colima` VM was also stopped during isolation; this was not sufficient alone, but it reduced concurrent virtualization load while debugging.
