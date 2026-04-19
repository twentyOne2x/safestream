#!/usr/bin/env python3
"""
obs_face_blur_toolkit.py

Real-time face blurring for OBS using a Python-generated virtual camera, plus
offline post-processing for recorded videos.

Why this exists
---------------
OBS does not ship a built-in "auto blur faces" filter. The simplest OBS-native
solution is the "Detect" plugin (obs-detect), which adds a "Detect" filter and can
mask faces directly in OBS.

If you want a solution you fully control (no OBS plugins), this script can:
1) Read frames from a webcam.
2) Detect faces.
3) Blur/pixelate/black-box them.
4) Output the result to a virtual camera (pyvirtualcam) that OBS can capture.

It can also post-process a recorded video file and blur faces into a new file.

Privacy
-------
All video processing is local. The only optional network activity is a one-time
download of the YuNet face detection model from OpenCV Zoo (can be disabled).

Requirements (Python)
---------------------
- Python 3.9+
- opencv-contrib-python (for YuNet via FaceDetectorYN)
- numpy
- pyvirtualcam (optional but recommended for OBS integration)

Examples
--------
Live virtual camera (default webcam index 0), show preview:
  python obs_face_blur_toolkit.py virtualcam --preview

Live, force pixelation, run detection at 12 fps:
  python obs_face_blur_toolkit.py virtualcam --blur pixelate --detect-fps 12 --preview

Offline: blur faces in recorded video:
  python obs_face_blur_toolkit.py blur-video input.mp4 output.mp4 --blur gaussian

"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:
    import pyvirtualcam  # type: ignore
except Exception:
    pyvirtualcam = None  # type: ignore

try:
    import mss  # type: ignore
except Exception:
    mss = None  # type: ignore

try:
    import pytesseract  # type: ignore
except Exception:
    pytesseract = None  # type: ignore

try:
    import regex as re  # type: ignore
except Exception:
    re = None  # type: ignore


# -------------------------
# Defaults
# -------------------------

DEFAULT_OUTPUT_FPS = 30
DEFAULT_DETECT_FPS = 15
DEFAULT_DETECT_WIDTH = 640

DEFAULT_TRACK_IOU = 0.2
DEFAULT_TRACK_MAX_MISSES = 10
DEFAULT_SMOOTHING = 0.7  # 0..1, higher = smoother (slower response)

DEFAULT_MIN_FACE_PX = 24
DEFAULT_EXPAND = 0.20  # expand box by 20% on each side

# OCR defaults (screen redaction)
DEFAULT_OCR_FPS = 3
DEFAULT_OCR_WIDTH = 1280
DEFAULT_MIN_WORD_CONF = 60
DEFAULT_MAX_TEXT_BOXES = 60
DEFAULT_TEXT_PAD_PX = 4
DEFAULT_TEXT_MERGE_IOU = 0.25
DEFAULT_OCR_LANG = "eng"

# Context/block defaults (screen capture)
DEFAULT_CONTEXT_OCR_FPS = 2
DEFAULT_CONTEXT_TOP_FRAC = 0.18  # OCR only the top part of the frame by default for URL/app hints.
DEFAULT_BLOCK_HOLD_MS = 1200

# Foreground face keep (opt-in): blur everyone except N "foreground" faces (usually the streamer).
DEFAULT_KEEP_N_LARGEST = 0
DEFAULT_KEEP_MIN_AREA_FRAC = 0.03  # Kept faces must be reasonably large; otherwise fail closed (blur all).
DEFAULT_KEEP_CENTER_FRAC = 0.90    # Kept face centers must be near the middle; otherwise fail closed (blur all).

# YuNet model
YUNET_MODEL_NAME = "face_detection_yunet_2023mar.onnx"
YUNET_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
# SHA256 published by KDE mirrorlist (and matches other mirrors)
YUNET_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
DEFAULT_YUNET_CACHE_DIR = Path.home() / ".cache" / "obs_face_blur"

# Sensitive patterns (best-effort). This is intentionally conservative: better to over-redact than miss.
_TEXT_PATTERNS = [
    # Private key blocks
    r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----",
    # 32-byte hex keys (common crypto private key encoding)
    r"\b0x[a-fA-F0-9]{64}\b",
    r"\b[a-fA-F0-9]{64}\b",
    # BIP32 extended private keys (xprv...)
    r"\bxprv[1-9A-HJ-NP-Za-km-z]{80,}\b",
    # JWTs
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    # Stripe
    r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b",
    # GitHub tokens
    r"\bgh[pousr]_[A-Za-z0-9]{36}\b",
    # Google API keys
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
    # Slack tokens
    r"\bxox[baprs]-[0-9A-Za-z-]{10,48}\b",
    # AWS access keys
    r"\bAKIA[0-9A-Z]{16}\b",
    # AWS secret keys (heuristic: 40 chars base64-ish)
    r"\b[0-9A-Za-z/+]{40}\b",
    # Generic high-entropy tokens
    r"\b[A-Za-z0-9_\-]{22,}\b",
    # Emails
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    # Phone numbers (basic)
    r"\b(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}\b",
    # .env style
    r"\b[A-Z0-9_]{2,32}\s*=\s*[^ \t\r\n#]{1,}\b",
    # Street address structures
    r"(?i)\b\d{1,6}\s+(?:[A-Za-z0-9]+\.?\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Circle|Cir|Way|Trail|Trl|Parkway|Pkwy|Place|Pl|Terrace|Ter)\b",
    # City, state, ZIP formats
    r"(?i)\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
    # Apartment/unit formats
    r"(?i)\b(?:apt|apartment|suite|ste|unit|floor|fl)\s*[#\-\s]?\s*\w{1,6}\b",
    # Credit card numbers (grouped 4s or flexible digits)
    r"(?i)\b(?:\d{4}[- ]?){3}\d{4}\b",
    r"(?i)\b(?:\d[ -]?){13,19}\d\b",
    # Card expiry formats
    r"(?i)\b(0[1-9]|1[0-2])[\/\-](?:\d{2}|\d{4})\b",
    # CVV/CVC numbers when labeled
    r"(?i)\b(?:cvv|cvc|cid)[^\d]{0,3}\d{3,4}\b",
]

_BILLING_KEYWORDS = {
    "billing",
    "payment",
    "invoice",
    "checkout",
    "subscription",
    "order",
    "credit",
    "debit",
    "card",
    "statement",
    "receipt",
    "customer",
    "account",
}

_NAME_FIELD_KEYWORDS = {
    "first name",
    "last name",
    "cardholder name",
    "card holder",
    "card holder name",
    "name on card",
    "full name",
}

_AUTOFILL_KEYWORDS = {
    "autofill",
    "auto-fill",
    "auto fill",
    "suggested",
    "suggestion",
    "suggestions",
    "saved info",
    "saved information",
    "saved address",
    "saved addresses",
    "saved payment",
    "fill form",
    "form fill",
    # Password managers commonly show up as autofill sources.
    "bitwarden",
    "lastpass",
    "1password",
    "onepassword",
    "dashlane",
    "keeper",
    "keepass",
}

_GENERIC_SAFE_WORDS = {
    "privacy",
    "policy",
    "terms",
    "conditions",
    "help",
    "support",
    "continue",
    "cancel",
    "learn",
    "more",
    "settings",
    "options",
    "password",
}

_BILLING_NAME_PATTERN = None
_BILLING_SINGLE_NAME_PATTERN = None
_PATTERNS_COMPILED = None

_DEFAULT_BLOCK_KEYWORDS = [
    # Chat apps / messaging
    "telegram",
    "whatsapp",
    "signal",
    "messenger",
    "discord",
    "slack",
    # Password managers / autofill surfaces (best-effort)
    "bitwarden",
    "lastpass",
    "1password",
    "onepassword",
    "dashlane",
    "keeper",
    "keepass",
    "proton pass",
    "protonpass",
    # Apple/Chrome/Safari UI often contains this word, and it is frequently sensitive.
    "passwords",
    # Generic “this is sensitive” UI
    "password",
    "passcode",
    "one-time",
    "one time",
    "verification code",
    "2fa",
    "otp",
    "cvv",
    "cvc",
    "security code",
    # Banking-ish surfaces (heuristic)
    "bank",
    "online banking",
    "sign in",
    "log in",
    "login",
    "account",
    "routing",
    "iban",
    "swift",
    "wire",
    "secure",
]

_DEFAULT_BLOCK_URL_REGEX = [
    # Google Docs / Sheets / Drive
    r"docs\.google\.com",
    r"sheets\.google\.com",
    r"drive\.google\.com",
    r"docs\.new",
    r"sheets\.new",
]

_FIELD_LABEL_KEYWORDS = [
    "password",
    "passphrase",
    "passcode",
    "2fa",
    "otp",
    "verification code",
    "security code",
    "address",
    "street",
    "billing",
    "card",
    "card number",
    "cvv",
    "cvc",
    "routing",
    "account number",
    "iban",
    "swift",
    "name",
    "first name",
    "last name",
    "full name",
    "email",
    "e-mail",
    "username",
    "user name",
    "phone",
    # Crypto / keys
    "private key",
    "secret key",
    "seed phrase",
    "recovery phrase",
    "mnemonic",
    "api key",
    "access key",
]

_FIELD_LABEL_STRONG = [
    # These should almost always be considered sensitive if they appear as labels.
    "password",
    "passphrase",
    "passcode",
    "2fa",
    "otp",
    "verification code",
    "security code",
    "cvv",
    "cvc",
    "routing",
    "account number",
    "iban",
    "swift",
    "card number",
    "billing address",
    "shipping address",
    "address",
    "street",
    # Crypto / keys
    "private key",
    "secret key",
    "seed phrase",
    "recovery phrase",
    "mnemonic",
    "api key",
    "access key",
]

_FIELD_COVER_EXTRA_KEYWORDS = [
    # If these show up in/near an input area, it is safer to cover the dropdown/field region.
    "autofill",
    "auto-fill",
    "suggested",
    "suggestions",
    "saved password",
    "saved passwords",
    "bitwarden",
    "lastpass",
    "1password",
    "onepassword",
]


# -------------------------
# Data types
# -------------------------

@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0

    def as_int(self) -> Tuple[int, int, int, int]:
        return int(round(self.x)), int(round(self.y)), int(round(self.w)), int(round(self.h))

    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)


# -------------------------
# Geometry + tracking helpers
# -------------------------

def clamp_box(b: Box, width: int, height: int) -> Optional[Box]:
    x1 = max(0.0, b.x)
    y1 = max(0.0, b.y)
    x2 = min(float(width), b.x + b.w)
    y2 = min(float(height), b.y + b.h)
    w = max(0.0, x2 - x1)
    h = max(0.0, y2 - y1)
    if w <= 1 or h <= 1:
        return None
    return Box(x1, y1, w, h, b.score)


def expand_box(b: Box, expand: float) -> Box:
    # expand is fraction of width/height added on each side
    dx = b.w * expand
    dy = b.h * expand
    return Box(b.x - dx, b.y - dy, b.w + 2 * dx, b.h + 2 * dy, b.score)


def iou(a: Box, b: Box) -> float:
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.area() + b.area() - inter + 1e-6
    return float(inter / union)


@dataclass
class Track:
    track_id: int
    box: Box
    misses: int = 0


class SimpleTracker:
    """
    Minimal IoU tracker:
    - Matches detections to existing tracks by IoU (greedy).
    - Applies exponential smoothing to reduce jitter.
    - Keeps tracks alive for `max_misses` frames when detection fails.
    """

    def __init__(self, iou_threshold: float, max_misses: int, smoothing: float):
        self.iou_threshold = float(iou_threshold)
        self.max_misses = int(max_misses)
        self.smoothing = float(smoothing)
        self._tracks: List[Track] = []
        self._next_id = 1

    def reset(self) -> None:
        self._tracks = []
        self._next_id = 1

    def boxes(self) -> List[Box]:
        return [t.box for t in self._tracks if t.misses <= self.max_misses]

    def update(self, detections: Sequence[Box]) -> List[Box]:
        dets = list(detections)

        # No tracks -> spawn from detections
        if not self._tracks:
            for d in dets:
                self._tracks.append(Track(self._next_id, d, 0))
                self._next_id += 1
            return self.boxes()

        # Compute IoU matrix
        pairs: List[Tuple[float, int, int]] = []
        for ti, t in enumerate(self._tracks):
            for di, d in enumerate(dets):
                pairs.append((iou(t.box, d), ti, di))
        pairs.sort(key=lambda x: x[0], reverse=True)

        matched_tracks = set()
        matched_dets = set()

        # Greedy matching
        for score, ti, di in pairs:
            if score < self.iou_threshold:
                break
            if ti in matched_tracks or di in matched_dets:
                continue
            matched_tracks.add(ti)
            matched_dets.add(di)

            # Smooth update
            old = self._tracks[ti].box
            new = dets[di]
            a = self.smoothing
            blended = Box(
                x=a * old.x + (1 - a) * new.x,
                y=a * old.y + (1 - a) * new.y,
                w=a * old.w + (1 - a) * new.w,
                h=a * old.h + (1 - a) * new.h,
                score=new.score,
            )
            self._tracks[ti].box = blended
            self._tracks[ti].misses = 0

        # Unmatched tracks: increase misses
        for ti, t in enumerate(self._tracks):
            if ti not in matched_tracks:
                t.misses += 1

        # Drop dead tracks
        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]

        # Add new tracks for unmatched detections
        for di, d in enumerate(dets):
            if di not in matched_dets:
                self._tracks.append(Track(self._next_id, d, 0))
                self._next_id += 1

        return self.boxes()

def _ensure_text_regex_compiled() -> None:
    global _BILLING_NAME_PATTERN, _BILLING_SINGLE_NAME_PATTERN, _PATTERNS_COMPILED
    if _PATTERNS_COMPILED is not None:
        return
    if re is None:
        raise RuntimeError("regex is required for OCR text redaction. Install it (pip install regex).")
    _PATTERNS_COMPILED = [re.compile(p) for p in _TEXT_PATTERNS]
    _BILLING_NAME_PATTERN = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}) (?:[A-Z][a-z]+|[A-Z]{2,})(?: (?:[A-Z][a-z]+|[A-Z]{2,}))?\b")
    _BILLING_SINGLE_NAME_PATTERN = re.compile(r"^(?:[A-Z][a-z]+|[A-Z]{2,})(?:[-'][A-Z][a-z]+|[A-Z]{2,})?$")

def _normalize_ws(s: str) -> str:
    return " ".join((s or "").strip().split())


def ocr_text_fast(
    frame_bgr: np.ndarray,
    *,
    ocr_lang: str,
    min_word_conf: int,
    ocr_width: int,
) -> str:
    """
    OCR a frame and return a normalized joined token string.
    Used for context detection (URL/app keywords), not for precise bounding boxes.
    """
    if pytesseract is None:
        raise RuntimeError("pytesseract is required for OCR. Install it (pip install pytesseract).")

    h, w = frame_bgr.shape[:2]
    if ocr_width > 0 and ocr_width < w:
        scale = ocr_width / float(w)
        oh = max(1, int(round(h * scale)))
        img = cv2.resize(frame_bgr, (ocr_width, oh), interpolation=cv2.INTER_AREA)
    else:
        img = frame_bgr

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    data = pytesseract.image_to_data(
        gray,
        lang=ocr_lang,
        output_type=pytesseract.Output.DICT,
        config="--oem 1 --psm 6",
    )
    n = len(data.get("text", []))
    toks = []
    for i in range(n):
        t = (data["text"][i] or "").strip()
        if not t:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except Exception:
            conf = -1
        if conf < int(min_word_conf):
            continue
        toks.append(t)
    return _normalize_ws(" ".join(toks)).lower()


def _compile_user_regexes(patterns: Sequence[str]) -> List["re.Pattern"]:
    _ensure_text_regex_compiled()
    out = []
    for p in patterns:
        if not p:
            continue
        out.append(re.compile(p, re.IGNORECASE))  # type: ignore[union-attr]
    return out


def context_should_block(
    text: str,
    *,
    block_keywords: Sequence[str],
    block_url_regexes: Sequence[str],
) -> bool:
    """
    Decide whether to block the entire frame based on OCR'd context text.
    """
    t = (text or "").lower()
    if not t:
        return False
    for kw in block_keywords:
        if kw and kw.lower() in t:
            return True
    if block_url_regexes:
        rx = _compile_user_regexes(block_url_regexes)
        return any(r.search(t) for r in rx)
    return False


def _merge_boxes(boxes: List[Box], iou_thresh: float) -> List[Box]:
    if not boxes:
        return []
    boxes_sorted = sorted(boxes, key=lambda b: b.area(), reverse=True)
    kept: List[Box] = []
    for b in boxes_sorted:
        merged = False
        for i, k in enumerate(kept):
            if iou(b, k) >= iou_thresh:
                x1 = min(b.x, k.x)
                y1 = min(b.y, k.y)
                x2 = max(b.x + b.w, k.x + k.w)
                y2 = max(b.y + b.h, k.y + k.h)
                kept[i] = Box(x1, y1, x2 - x1, y2 - y1, max(b.score, k.score))
                merged = True
                break
        if not merged:
            kept.append(b)
    return kept


def find_sensitive_text_boxes(
    frame_bgr: np.ndarray,
    *,
    mode: str,
    ocr_lang: str,
    min_word_conf: int,
    ocr_width: int,
    pad_px: int,
    merge_iou_thresh: float,
    max_boxes: int,
    field_cover: bool,
    field_band_px: int,
    field_band_px_strong: int,
) -> List[Box]:
    """
    Best-effort OCR -> pattern match -> bounding boxes.

    Returns boxes in the coordinate space of `frame_bgr`.
    """
    _ensure_text_regex_compiled()
    if pytesseract is None:
        raise RuntimeError("pytesseract is required for OCR text redaction. Install it (pip install pytesseract).")

    h, w = frame_bgr.shape[:2]
    if ocr_width > 0 and ocr_width < w:
        scale = ocr_width / float(w)
        oh = max(1, int(round(h * scale)))
        img = cv2.resize(frame_bgr, (ocr_width, oh), interpolation=cv2.INTER_AREA)
        sx = w / float(ocr_width)
        sy = h / float(oh)
    else:
        img = frame_bgr
        sx = 1.0
        sy = 1.0

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    data = pytesseract.image_to_data(
        gray,
        lang=ocr_lang,
        output_type=pytesseract.Output.DICT,
        config="--oem 1 --psm 6",
    )

    n = len(data.get("text", []))
    tokens = []
    for i in range(n):
        t = (data["text"][i] or "").strip()
        if not t:
            continue
        try:
            conf = int(float(data["conf"][i]))
        except Exception:
            conf = -1
        if conf < int(min_word_conf):
            continue
        tokens.append((i, t))

    tokens_lower = [t.lower() for _, t in tokens]
    joined = " ".join(tokens_lower)
    billing_context = any(k in joined for k in _BILLING_KEYWORDS)
    name_field_context = any(k in joined for k in _NAME_FIELD_KEYWORDS)
    autofill_context = any(k in joined for k in _AUTOFILL_KEYWORDS)

    def word_rect(idx: int) -> Box:
        x = float(int(data["left"][idx])) * sx
        y = float(int(data["top"][idx])) * sy
        bw = float(int(data["width"][idx])) * sx
        bh = float(int(data["height"][idx])) * sy
        return Box(x, y, bw, bh, 1.0)

    boxes: List[Box] = []

    if mode not in {"sensitive", "all"}:
        raise ValueError("mode must be 'sensitive' or 'all'")

    # Word-level match (sensitive mode only).
    if mode == "sensitive":
        for idx, token in tokens:
            if len(token) <= 3:
                continue
            if any(p.search(token) for p in _PATTERNS_COMPILED):  # type: ignore[arg-type]
                b = word_rect(idx)
                boxes.append(Box(b.x - pad_px, b.y - pad_px, b.w + 2 * pad_px, b.h + 2 * pad_px, 1.0))

    # Line-level aggregation
    by_line = {}
    for idx, _ in tokens:
        key = (int(data.get("block_num", [0])[idx]), int(data.get("line_num", [0])[idx]))
        by_line.setdefault(key, []).append(idx)

    for _, idxs in by_line.items():
        line_text = " ".join((data["text"][i] or "").strip() for i in idxs)
        if not line_text:
            continue
        lt = line_text.lower()
        line_sensitive = (mode == "all")

        if mode == "sensitive":
            # Strong field labels should always trigger redaction.
            if any(k in lt for k in _FIELD_LABEL_STRONG):
                line_sensitive = True

            if any(k in lt for k in _BILLING_KEYWORDS):
                billing_context = True
            if any(k in lt for k in _NAME_FIELD_KEYWORDS):
                name_field_context = True
                line_sensitive = True
            if any(k in lt for k in _AUTOFILL_KEYWORDS):
                autofill_context = True
                line_sensitive = True

            if any(p.search(line_text) for p in _PATTERNS_COMPILED):  # type: ignore[arg-type]
                line_sensitive = True

            # Heuristic: if we're in billing/autofill context and the line looks like a name, redact it.
            if (billing_context or name_field_context or autofill_context) and _BILLING_NAME_PATTERN.search(line_text):  # type: ignore[union-attr]
                line_sensitive = True

            # "Title case" lines can be names; try to avoid nuking generic headers unless we're in a sensitive context.
            if not line_sensitive:
                words = [w.strip(".,'\"") for w in line_text.split() if w.strip(".,'\"")]
                nameish = [w for w in words if _BILLING_SINGLE_NAME_PATTERN.match(w)]  # type: ignore[union-attr]
                meaningful = [w for w in nameish if w.lower() not in _GENERIC_SAFE_WORDS]
                if len(nameish) >= 2 and meaningful and (billing_context or name_field_context or autofill_context or len(nameish) >= 3):
                    line_sensitive = True

        if not line_sensitive:
            continue

        xs: List[float] = []
        ys: List[float] = []
        xe: List[float] = []
        ye: List[float] = []
        for i in idxs:
            b = word_rect(i)
            xs.append(b.x)
            ys.append(b.y)
            xe.append(b.x + b.w)
            ye.append(b.y + b.h)
        x1, y1, x2, y2 = min(xs), min(ys), max(xe), max(ye)
        boxes.append(Box(x1 - pad_px, y1 - pad_px, (x2 - x1) + 2 * pad_px, (y2 - y1) + 2 * pad_px, 1.0))

        # Field band cover: if the line looks like it labels an input field, cover the whole width for a bit below.
        if field_cover:
            strong_hit = any(k in lt for k in _FIELD_LABEL_STRONG)
            extra_hit = any(k in lt for k in _FIELD_COVER_EXTRA_KEYWORDS)
            band_hit = strong_hit or extra_hit or any(k in lt for k in _FIELD_LABEL_KEYWORDS)
            if band_hit:
                # Full-width band starting at label y, extending down to catch the input row.
                band_y = max(0.0, (y1 - pad_px))
                base_h = int(field_band_px_strong) if (strong_hit or extra_hit) else int(field_band_px)
                band_h = float(max(8, base_h))
                boxes.append(Box(0.0, band_y, float(w), band_h, 1.0))

    merged = _merge_boxes(boxes, float(merge_iou_thresh))
    merged = [b for b in merged if clamp_box(b, w, h) is not None]
    if len(merged) > int(max_boxes):
        merged = sorted(merged, key=lambda b: b.area(), reverse=True)[: int(max_boxes)]
    return merged


# -------------------------
# Face detectors
# -------------------------

class FaceDetector:
    def detect(self, frame_bgr: np.ndarray) -> List[Box]:
        raise NotImplementedError


class YuNetFaceDetector(FaceDetector):
    """
    YuNet face detector via OpenCV's FaceDetectorYN.

    Uses the ONNX model from OpenCV Zoo and OpenCV's DNN backend.
    """

    def __init__(
        self,
        model_path: str,
        score_thresh: float = 0.9,
        nms_thresh: float = 0.3,
        top_k: int = 5000,
        backend_id: Optional[int] = None,
        target_id: Optional[int] = None,
    ):
        self.model_path = model_path
        self.score_thresh = float(score_thresh)
        self.nms_thresh = float(nms_thresh)
        self.top_k = int(top_k)

        # The "network input size" is set later via setInputSize for each frame,
        # but OpenCV requires an initial size at creation.
        initial_size = (320, 320)

        create_fn = None
        if hasattr(cv2, "FaceDetectorYN_create"):
            create_fn = cv2.FaceDetectorYN_create
        elif hasattr(cv2, "FaceDetectorYN") and hasattr(cv2.FaceDetectorYN, "create"):
            create_fn = cv2.FaceDetectorYN.create

        if create_fn is None:
            raise RuntimeError(
                "OpenCV was built without FaceDetectorYN. Install opencv-contrib-python."
            )

        kwargs = {}
        # Newer OpenCV versions support backend_id/target_id as arguments.
        if backend_id is not None and target_id is not None:
            kwargs["backend_id"] = int(backend_id)
            kwargs["target_id"] = int(target_id)

        try:
            self._fd = create_fn(
                self.model_path,
                "",
                initial_size,
                self.score_thresh,
                self.nms_thresh,
                self.top_k,
                **kwargs,
            )
        except TypeError:
            # Older signature without backend/target/top_k
            self._fd = create_fn(self.model_path, "", initial_size)

    def detect(self, frame_bgr: np.ndarray) -> List[Box]:
        h, w = frame_bgr.shape[:2]
        # Important: set input size to the actual frame size we're running detect() on.
        try:
            self._fd.setInputSize((w, h))
        except Exception:
            pass

        faces = None
        try:
            _, faces = self._fd.detect(frame_bgr)
        except Exception:
            # Some OpenCV versions return just faces
            try:
                faces = self._fd.detect(frame_bgr)
            except Exception:
                faces = None

        if faces is None:
            return []

        out: List[Box] = []
        for f in faces:
            # Common format: [x, y, w, h, ... , score] (score is often last element)
            x, y, bw, bh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            score = float(f[-1]) if len(f) >= 5 else 1.0
            out.append(Box(x, y, bw, bh, score))
        return out


class HaarFaceDetector(FaceDetector):
    """
    Haar cascade fallback (fast, but less accurate than YuNet).
    """

    def __init__(
        self,
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
    ):
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        self._cc = cv2.CascadeClassifier(cascade_path)
        if self._cc.empty():
            raise RuntimeError(f"Failed to load Haar cascade: {cascade_path}")
        self.scale_factor = float(scale_factor)
        self.min_neighbors = int(min_neighbors)

    def detect(self, frame_bgr: np.ndarray) -> List[Box]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        rects = self._cc.detectMultiScale(
            gray,
            scaleFactor=self.scale_factor,
            minNeighbors=self.min_neighbors,
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        out: List[Box] = []
        for (x, y, w, h) in rects:
            out.append(Box(float(x), float(y), float(w), float(h), 1.0))
        return out


# -------------------------
# Blur / pixelate
# -------------------------

def _odd(k: int) -> int:
    if k <= 1:
        return 1
    return k if (k % 2 == 1) else (k + 1)


def blur_gaussian(frame_bgr: np.ndarray, box: Box, kernel: int) -> None:
    h, w = frame_bgr.shape[:2]
    b = clamp_box(box, w, h)
    if b is None:
        return
    x, y, bw, bh = b.as_int()
    roi = frame_bgr[y : y + bh, x : x + bw]
    if roi.size == 0:
        return
    k = _odd(int(kernel))
    if k <= 1:
        # auto kernel: proportional to face size
        k = _odd(max(15, int(min(bw, bh) * 0.45)))
    # kernel must be <= ROI size to avoid OpenCV errors on tiny ROIs
    k = min(k, _odd(max(1, bw - (1 - bw % 2))), _odd(max(1, bh - (1 - bh % 2))))
    if k <= 1:
        return
    frame_bgr[y : y + bh, x : x + bw] = cv2.GaussianBlur(roi, (k, k), 0)


def blur_pixelate(frame_bgr: np.ndarray, box: Box, pixel_size: int) -> None:
    h, w = frame_bgr.shape[:2]
    b = clamp_box(box, w, h)
    if b is None:
        return
    x, y, bw, bh = b.as_int()
    roi = frame_bgr[y : y + bh, x : x + bw]
    if roi.size == 0:
        return
    ps = max(2, int(pixel_size))
    # Compute downsample size
    dw = max(1, bw // ps)
    dh = max(1, bh // ps)
    small = cv2.resize(roi, (dw, dh), interpolation=cv2.INTER_LINEAR)
    pix = cv2.resize(small, (bw, bh), interpolation=cv2.INTER_NEAREST)
    frame_bgr[y : y + bh, x : x + bw] = pix


def blur_blackbox(frame_bgr: np.ndarray, box: Box) -> None:
    h, w = frame_bgr.shape[:2]
    b = clamp_box(box, w, h)
    if b is None:
        return
    x, y, bw, bh = b.as_int()
    frame_bgr[y : y + bh, x : x + bw] = 0


def apply_blur(frame_bgr: np.ndarray, boxes: Sequence[Box], method: str, gaussian_kernel: int, pixel_size: int) -> None:
    for b in boxes:
        if method == "gaussian":
            blur_gaussian(frame_bgr, b, gaussian_kernel)
        elif method == "pixelate":
            blur_pixelate(frame_bgr, b, pixel_size)
        elif method == "blackbox":
            blur_blackbox(frame_bgr, b)
        else:
            raise ValueError(f"Unknown blur method: {method}")


def _center_in_frac(b: Box, w: int, h: int, frac: float) -> bool:
    if frac <= 0.0 or frac >= 1.0:
        return True
    cx = b.x + (b.w / 2.0)
    cy = b.y + (b.h / 2.0)
    fw = float(w) * float(frac)
    fh = float(h) * float(frac)
    x1 = (float(w) - fw) / 2.0
    y1 = (float(h) - fh) / 2.0
    x2 = x1 + fw
    y2 = y1 + fh
    return (x1 <= cx <= x2) and (y1 <= cy <= y2)


def select_faces_to_blur(
    faces: Sequence[Box],
    *,
    frame_w: int,
    frame_h: int,
    keep_n_largest: int,
    keep_min_area_frac: float,
    keep_center_frac: float,
) -> List[Box]:
    """
    Return the subset of faces that should be blurred.

    Opt-in "keep foreground" behavior:
    - Keep the N largest faces unblurred (assumed foreground/streamer).
    - Fail-closed: if any kept face is too small or not centered (likely background),
      keep none (blur all faces).
    """
    boxes = list(faces)
    if not boxes:
        return []

    k = int(keep_n_largest)
    if k <= 0:
        return boxes

    boxes_sorted = sorted(boxes, key=lambda b: b.area(), reverse=True)
    keep = boxes_sorted[: min(k, len(boxes_sorted))]

    frame_area = float(max(1, int(frame_w)) * max(1, int(frame_h)))
    min_area = max(0.0, float(keep_min_area_frac)) * frame_area
    center_frac = float(keep_center_frac)

    for b in keep:
        if b.area() < min_area:
            return boxes_sorted  # blur all (fail closed)
        if not _center_in_frac(b, int(frame_w), int(frame_h), center_frac):
            return boxes_sorted  # blur all (fail closed)

    keep_ids = {id(b) for b in keep}
    return [b for b in boxes_sorted if id(b) not in keep_ids]


# -------------------------
# Model download
# -------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_with_sha256(url: str, dest: Path, expected_sha256: Optional[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    with urllib.request.urlopen(url) as r, tmp.open("wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    if expected_sha256:
        actual = sha256_file(tmp)
        if actual.lower() != expected_sha256.lower():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Downloaded model checksum mismatch.\n"
                f"Expected: {expected_sha256}\n"
                f"Actual:   {actual}\n"
                f"URL:      {url}"
            )

    tmp.replace(dest)


def ensure_yunet_model(model_path: Optional[str], allow_download: bool) -> str:
    if model_path:
        p = Path(model_path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"YuNet model not found: {p}")
        return str(p)

    # Default cache location
    p = DEFAULT_YUNET_CACHE_DIR / YUNET_MODEL_NAME
    if p.exists():
        # Verify checksum if possible; if it fails, re-download
        try:
            if sha256_file(p).lower() == YUNET_MODEL_SHA256.lower():
                return str(p)
        except Exception:
            pass
        # If checksum mismatch, attempt re-download if allowed
        if not allow_download:
            return str(p)
        p.unlink(missing_ok=True)

    if not allow_download:
        raise FileNotFoundError(
            f"YuNet model missing at default path: {p}\n"
            f"Provide --model-path or enable download."
        )

    print(f"Downloading YuNet model to: {p}")
    download_with_sha256(YUNET_MODEL_URL, p, YUNET_MODEL_SHA256)
    return str(p)


# -------------------------
# Video IO helpers
# -------------------------

def open_capture(source: str, width: Optional[int], height: Optional[int], fps: Optional[int]) -> cv2.VideoCapture:
    """
    source:
      - integer string -> camera index
      - otherwise -> treated as a VideoCapture URL/path (file, RTSP, etc.)
    """
    cap: cv2.VideoCapture
    if source.isdigit():
        idx = int(source)
        cap = cv2.VideoCapture(idx)
    else:
        cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video source: {source}")

    if width is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
    if height is not None:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
    if fps is not None:
        cap.set(cv2.CAP_PROP_FPS, int(fps))

    return cap


def read_capture_props(cap: cv2.VideoCapture, fallback_fps: int) -> Tuple[int, int, int]:
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    fps_i = int(round(fps)) if fps and fps > 1e-3 else int(fallback_fps)
    if w <= 0 or h <= 0:
        # Some backends don't provide size until first frame
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError("Could not read an initial frame to determine capture size.")
        h, w = frame.shape[:2]
        # push frame back by reopening is not possible; caller should handle
        raise RuntimeError("Capture did not report size until first frame; reopen with explicit --width/--height.")
    return w, h, fps_i


# -------------------------
# Virtual camera mode
# -------------------------

def run_virtualcam(args: argparse.Namespace) -> int:
    if args.virtualcam_disable and not args.preview:
        print("Nothing to do: --virtualcam-disable and preview disabled.")
        return 2

    detector: FaceDetector
    if args.detector == "yunet":
        model_path = ensure_yunet_model(args.model_path, allow_download=(not args.no_download))
        detector = YuNetFaceDetector(
            model_path=model_path,
            score_thresh=args.yunet_score_thresh,
            nms_thresh=args.yunet_nms_thresh,
            top_k=args.yunet_top_k,
            backend_id=args.dnn_backend_id,
            target_id=args.dnn_target_id,
        )
    elif args.detector == "haar":
        detector = HaarFaceDetector()
    else:
        raise ValueError(f"Unknown detector: {args.detector}")

    tracker = SimpleTracker(
        iou_threshold=args.track_iou,
        max_misses=args.track_max_misses,
        smoothing=args.track_smoothing,
    )

    cap = open_capture(args.source, args.width, args.height, args.capture_fps)
    cap_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    cap_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap_fps_raw = cap.get(cv2.CAP_PROP_FPS) or 0.0

    if cap_w <= 0 or cap_h <= 0:
        # Determine from first frame
        ret, frame0 = cap.read()
        if not ret or frame0 is None:
            raise RuntimeError("Could not read from capture device.")
        cap_h, cap_w = frame0.shape[:2]
        # Re-open to avoid losing first frame for downstream apps
        cap.release()
        cap = open_capture(args.source, cap_w, cap_h, args.capture_fps)

    out_w = int(args.output_width or cap_w)
    out_h = int(args.output_height or cap_h)
    out_fps = int(args.output_fps or (int(round(cap_fps_raw)) if cap_fps_raw else DEFAULT_OUTPUT_FPS))

    detect_fps = float(args.detect_fps)
    detect_width = int(args.detect_width)
    min_face_px = int(args.min_face_px)
    expand = float(args.expand)

    if args.preview:
        cv2.namedWindow("obs_face_blur_toolkit (preview)", cv2.WINDOW_NORMAL)

    vcam = None
    if not args.virtualcam_disable:
        if pyvirtualcam is None:
            raise RuntimeError(
                "pyvirtualcam is not installed, but virtual camera output was requested.\n"
                "Install it (pip install pyvirtualcam) or run with --virtualcam-disable."
            )

        try:
            vcam = pyvirtualcam.Camera(
                width=out_w,
                height=out_h,
                fps=out_fps,
                device=args.device,
                backend=(None if args.backend == "auto" else args.backend),
                fmt=pyvirtualcam.PixelFormat.RGB,
                print_fps=args.print_fps,
            )
        except TypeError:
            # Older pyvirtualcam: may not support print_fps or named args
            vcam = pyvirtualcam.Camera(out_w, out_h, out_fps, device=args.device, backend=(None if args.backend == "auto" else args.backend))

        print(f"Virtual camera active: {getattr(vcam, 'device', '(unknown device)')}")
        print(f"Output: {out_w}x{out_h} @ {out_fps} fps")

    running = True

    def _stop(_sig: int, _frm) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    next_detect_time = 0.0
    last_boxes: List[Box] = []

    # In preview-only mode, we still want a steady loop rate.
    frame_interval = 1.0 / max(1.0, float(out_fps))
    last_frame_time = time.perf_counter()

    while running:
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        # Resize to output size (virtual cam expects constant size)
        if frame.shape[1] != out_w or frame.shape[0] != out_h:
            frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)

        # Optional transforms
        if args.flip:
            frame = cv2.flip(frame, 1)
        if args.rotate != 0:
            if args.rotate == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif args.rotate == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif args.rotate == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        now = time.perf_counter()
        if now >= next_detect_time:
            # Prepare frame for detection (downscale for speed)
            if detect_width > 0 and detect_width < out_w:
                scale = detect_width / float(out_w)
                dh = max(1, int(round(out_h * scale)))
                det_frame = cv2.resize(frame, (detect_width, dh), interpolation=cv2.INTER_AREA)
                sx = out_w / float(detect_width)
                sy = out_h / float(dh)
            else:
                det_frame = frame
                sx = 1.0
                sy = 1.0

            dets = detector.detect(det_frame)

            # Scale back to output coordinates + filter small faces
            scaled: List[Box] = []
            for d in dets:
                b = Box(d.x * sx, d.y * sy, d.w * sx, d.h * sy, d.score)
                if b.w < min_face_px or b.h < min_face_px:
                    continue
                scaled.append(b)

            # Expand and clamp later (after tracking, to keep tracking stable)
            last_boxes = tracker.update(scaled)

            next_detect_time = now + (1.0 / max(1.0, detect_fps))

        # Blur using last known boxes (optionally keep foreground face(s) visible).
        blur_candidates = select_faces_to_blur(
            last_boxes,
            frame_w=out_w,
            frame_h=out_h,
            keep_n_largest=args.keep_n_largest,
            keep_min_area_frac=args.keep_min_area_frac,
            keep_center_frac=args.keep_center_frac,
        )

        to_blur: List[Box] = []
        for b in blur_candidates:
            eb = expand_box(b, expand)
            cb = clamp_box(eb, out_w, out_h)
            if cb is not None:
                to_blur.append(cb)

        apply_blur(frame, to_blur, args.blur, args.gaussian_kernel, args.pixel_size)

        if args.preview:
            disp = frame.copy()
            if args.draw_boxes:
                for b in to_blur:
                    x, y, bw, bh = b.as_int()
                    cv2.rectangle(disp, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
            cv2.imshow("obs_face_blur_toolkit (preview)", disp)
            if cv2.waitKey(1) & 0xFF == 27:  # ESC
                break

        if vcam is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            vcam.send(rgb)
            try:
                vcam.sleep_until_next_frame()
            except Exception:
                # Fallback timing control
                pass
        else:
            # Preview-only pacing
            elapsed = now - last_frame_time
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
            last_frame_time = time.perf_counter()

    cap.release()
    if args.preview:
        cv2.destroyAllWindows()
    return 0


# -------------------------
# Offline video mode
# -------------------------

def open_video_writer(path: str, width: int, height: int, fps: float, fourcc: str) -> cv2.VideoWriter:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    codec = cv2.VideoWriter_fourcc(*fourcc)
    writer = cv2.VideoWriter(str(out_path), codec, float(fps), (int(width), int(height)))
    if not writer.isOpened():
        raise RuntimeError(
            f"Failed to open VideoWriter for {path}. Try a different --fourcc (e.g. mp4v, avc1, XVID)."
        )
    return writer


def run_blur_video(args: argparse.Namespace) -> int:
    detector: FaceDetector
    if args.detector == "yunet":
        model_path = ensure_yunet_model(args.model_path, allow_download=(not args.no_download))
        detector = YuNetFaceDetector(
            model_path=model_path,
            score_thresh=args.yunet_score_thresh,
            nms_thresh=args.yunet_nms_thresh,
            top_k=args.yunet_top_k,
            backend_id=args.dnn_backend_id,
            target_id=args.dnn_target_id,
        )
    elif args.detector == "haar":
        detector = HaarFaceDetector()
    else:
        raise ValueError(f"Unknown detector: {args.detector}")

    tracker = SimpleTracker(
        iou_threshold=args.track_iou,
        max_misses=args.track_max_misses,
        smoothing=args.track_smoothing,
    )

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open input video: {args.input}")

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 1e-3:
        fps = 30.0

    if w <= 0 or h <= 0:
        raise RuntimeError("Could not read input video dimensions.")

    writer = open_video_writer(args.output, w, h, fps, args.fourcc)

    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    detect_every = max(1, int(args.detect_every))
    detect_width = int(args.detect_width)
    min_face_px = int(args.min_face_px)
    expand = float(args.expand)

    frame_idx = 0
    last_boxes: List[Box] = []

    t0 = time.perf_counter()
    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        frame_idx += 1

        if (frame_idx % detect_every) == 1:
            # Detection frame (downscaled for speed)
            if detect_width > 0 and detect_width < w:
                scale = detect_width / float(w)
                dh = max(1, int(round(h * scale)))
                det_frame = cv2.resize(frame, (detect_width, dh), interpolation=cv2.INTER_AREA)
                sx = w / float(detect_width)
                sy = h / float(dh)
            else:
                det_frame = frame
                sx = 1.0
                sy = 1.0

            dets = detector.detect(det_frame)
            scaled: List[Box] = []
            for d in dets:
                b = Box(d.x * sx, d.y * sy, d.w * sx, d.h * sy, d.score)
                if b.w < min_face_px or b.h < min_face_px:
                    continue
                scaled.append(b)
            last_boxes = tracker.update(scaled)

        # Expand + clamp and blur (optionally keep foreground face(s) visible).
        blur_candidates = select_faces_to_blur(
            last_boxes,
            frame_w=w,
            frame_h=h,
            keep_n_largest=args.keep_n_largest,
            keep_min_area_frac=args.keep_min_area_frac,
            keep_center_frac=args.keep_center_frac,
        )

        to_blur: List[Box] = []
        for b in blur_candidates:
            eb = expand_box(b, expand)
            cb = clamp_box(eb, w, h)
            if cb is not None:
                to_blur.append(cb)

        apply_blur(frame, to_blur, args.blur, args.gaussian_kernel, args.pixel_size)
        writer.write(frame)

        # Progress
        if args.progress and (frame_idx % 60 == 0):
            elapsed = time.perf_counter() - t0
            if total > 0:
                pct = 100.0 * frame_idx / float(total)
                print(f"{frame_idx}/{total} frames ({pct:.1f}%), elapsed {elapsed:.1f}s")
            else:
                print(f"{frame_idx} frames, elapsed {elapsed:.1f}s")

    cap.release()
    writer.release()
    return 0


# -------------------------
# Screen capture -> virtual camera mode
# -------------------------

def _parse_region(s: str) -> Tuple[int, int, int, int]:
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError("Region must be x,y,w,h")
    x, y, w, h = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    if w <= 0 or h <= 0:
        raise ValueError("Region width/height must be > 0")
    return x, y, w, h


def run_screen_virtualcam(args: argparse.Namespace) -> int:
    if mss is None:
        raise RuntimeError("mss is required for screen capture. Install it (pip install mss).")

    if args.virtualcam_disable and not args.preview:
        print("Nothing to do: --virtualcam-disable and preview disabled.")
        return 2

    detector: Optional[FaceDetector] = None
    tracker: Optional[SimpleTracker] = None
    if not args.faces_disable:
        if args.detector == "yunet":
            model_path = ensure_yunet_model(args.model_path, allow_download=(not args.no_download))
            detector = YuNetFaceDetector(
                model_path=model_path,
                score_thresh=args.yunet_score_thresh,
                nms_thresh=args.yunet_nms_thresh,
                top_k=args.yunet_top_k,
                backend_id=args.dnn_backend_id,
                target_id=args.dnn_target_id,
            )
        elif args.detector == "haar":
            detector = HaarFaceDetector()
        else:
            raise ValueError(f"Unknown detector: {args.detector}")
        tracker = SimpleTracker(
            iou_threshold=args.track_iou,
            max_misses=args.track_max_misses,
            smoothing=args.track_smoothing,
        )

    if args.tesseract_cmd:
        if pytesseract is None:
            raise RuntimeError("pytesseract is required for OCR. Install it (pip install pytesseract).")
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd

    out_w = int(args.output_width)
    out_h = int(args.output_height)
    out_fps = int(args.output_fps)

    if args.preview:
        cv2.namedWindow("obs_face_blur_toolkit (screen preview)", cv2.WINDOW_NORMAL)

    vcam = None
    if not args.virtualcam_disable:
        if pyvirtualcam is None:
            raise RuntimeError(
                "pyvirtualcam is not installed, but virtual camera output was requested.\n"
                "Install it (pip install pyvirtualcam) or run with --virtualcam-disable."
            )
        try:
            vcam = pyvirtualcam.Camera(
                width=out_w,
                height=out_h,
                fps=out_fps,
                device=args.device,
                backend=(None if args.backend == "auto" else args.backend),
                fmt=pyvirtualcam.PixelFormat.RGB,
                print_fps=args.print_fps,
            )
        except TypeError:
            vcam = pyvirtualcam.Camera(out_w, out_h, out_fps, device=args.device, backend=(None if args.backend == "auto" else args.backend))

        print(f"Virtual camera active: {getattr(vcam, 'device', '(unknown device)')}")
        print(f"Output: {out_w}x{out_h} @ {out_fps} fps")

    running = True

    def _stop(_sig: int, _frm) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    frame_interval = 1.0 / max(1.0, float(out_fps))
    next_face_time = 0.0
    next_ocr_time = 0.0
    next_context_time = 0.0
    last_face_boxes: List[Box] = []
    last_text_boxes: List[Box] = []
    block_until = 0.0

    # Pre-compile regexes once (if any). This will also validate `regex` availability early.
    user_block_url_regexes = list(_DEFAULT_BLOCK_URL_REGEX)
    user_block_keywords = list(_DEFAULT_BLOCK_KEYWORDS)
    if getattr(args, "block_url_regex", None):
        user_block_url_regexes.extend(args.block_url_regex)
    if getattr(args, "block_keyword", None):
        user_block_keywords.extend(args.block_keyword)

    with mss.mss() as sct:
        monitors = sct.monitors  # 0 = all, 1..n individual
        mon_idx = int(args.monitor)
        if mon_idx < 1 or mon_idx >= len(monitors):
            raise ValueError(f"--monitor must be in 1..{len(monitors)-1}")
        mon = monitors[mon_idx]
        base = {"left": int(mon["left"]), "top": int(mon["top"]), "width": int(mon["width"]), "height": int(mon["height"])}
        if args.region:
            rx, ry, rw, rh = _parse_region(args.region)
            base = {"left": base["left"] + rx, "top": base["top"] + ry, "width": rw, "height": rh}

        last_frame_time = time.perf_counter()
        while running:
            t0 = time.perf_counter()
            img = np.array(sct.grab(base))  # BGRA
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

            # Resize to output
            if frame.shape[1] != out_w or frame.shape[0] != out_h:
                frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)

            # Optional transforms
            if args.flip:
                frame = cv2.flip(frame, 1)
            if args.rotate != 0:
                if args.rotate == 90:
                    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                elif args.rotate == 180:
                    frame = cv2.rotate(frame, cv2.ROTATE_180)
                elif args.rotate == 270:
                    frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            now = time.perf_counter()

            # Context OCR + full-frame block trigger (to avoid leaking whole sensitive surfaces).
            if args.block_on_context and now >= next_context_time:
                try:
                    ch = int(max(1, int(round(out_h * float(args.context_top_frac)))))
                    ctx = frame[0:ch, :, :]
                    ctx_text = ocr_text_fast(
                        ctx,
                        ocr_lang=args.ocr_lang,
                        min_word_conf=args.min_word_conf,
                        ocr_width=min(int(args.ocr_width), out_w),
                    )
                    if context_should_block(ctx_text, block_keywords=user_block_keywords, block_url_regexes=user_block_url_regexes):
                        block_until = max(block_until, now + (float(args.block_hold_ms) / 1000.0))
                except Exception as e:
                    if args.debug:
                        print(f"context OCR failed: {e}")
                next_context_time = now + (1.0 / max(0.5, float(args.context_ocr_fps)))

            if args.block_on_context and now < block_until:
                # Hide everything. Keep it deterministic and hard to reverse: blackbox by default.
                apply_blur(frame, [Box(0.0, 0.0, float(out_w), float(out_h), 1.0)], args.block_method, args.block_gaussian_kernel, args.block_pixel_size)
                if args.preview:
                    disp = frame.copy()
                    cv2.imshow("obs_face_blur_toolkit (screen preview)", disp)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
                if vcam is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    vcam.send(rgb)
                    try:
                        vcam.sleep_until_next_frame()
                    except Exception:
                        pass
                else:
                    elapsed = now - last_frame_time
                    sleep_time = frame_interval - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    last_frame_time = time.perf_counter()
                continue

            # Face detection
            if detector is not None and tracker is not None and now >= next_face_time:
                det_width = int(args.detect_width)
                if det_width > 0 and det_width < out_w:
                    scale = det_width / float(out_w)
                    dh = max(1, int(round(out_h * scale)))
                    det_frame = cv2.resize(frame, (det_width, dh), interpolation=cv2.INTER_AREA)
                    sx = out_w / float(det_width)
                    sy = out_h / float(dh)
                else:
                    det_frame = frame
                    sx = 1.0
                    sy = 1.0

                dets = detector.detect(det_frame)
                scaled: List[Box] = []
                for d in dets:
                    b = Box(d.x * sx, d.y * sy, d.w * sx, d.h * sy, d.score)
                    if b.w < args.min_face_px or b.h < args.min_face_px:
                        continue
                    scaled.append(b)
                last_face_boxes = tracker.update(scaled)
                next_face_time = now + (1.0 / max(1.0, float(args.detect_fps)))

            # OCR detection
            text_enabled = not bool(getattr(args, "text_disable", False))
            if text_enabled and now >= next_ocr_time:
                try:
                    last_text_boxes = find_sensitive_text_boxes(
                        frame,
                        mode=args.text_mode,
                        ocr_lang=args.ocr_lang,
                        min_word_conf=args.min_word_conf,
                        ocr_width=args.ocr_width,
                        pad_px=args.text_pad_px,
                        merge_iou_thresh=args.text_merge_iou,
                        max_boxes=args.max_text_boxes,
                        field_cover=bool(args.field_cover),
                        field_band_px=int(args.field_band_px),
                        field_band_px_strong=int(args.field_band_px_strong),
                    )
                except Exception as e:
                    if args.debug:
                        print(f"OCR failed: {e}")
                    last_text_boxes = []
                next_ocr_time = now + (1.0 / max(0.5, float(args.ocr_fps)))

            # Apply redactions
            face_to_blur: List[Box] = []
            if detector is not None:
                blur_candidates = select_faces_to_blur(
                    last_face_boxes,
                    frame_w=out_w,
                    frame_h=out_h,
                    keep_n_largest=args.keep_n_largest,
                    keep_min_area_frac=args.keep_min_area_frac,
                    keep_center_frac=args.keep_center_frac,
                )
                for b in blur_candidates:
                    eb = expand_box(b, float(args.expand))
                    cb = clamp_box(eb, out_w, out_h)
                    if cb is not None:
                        face_to_blur.append(cb)

            text_to_redact: List[Box] = []
            for b in last_text_boxes:
                cb = clamp_box(b, out_w, out_h)
                if cb is not None:
                    text_to_redact.append(cb)

            apply_blur(frame, face_to_blur, args.face_method, args.gaussian_kernel, args.pixel_size)
            apply_blur(frame, text_to_redact, args.text_method, args.text_gaussian_kernel, args.text_pixel_size)

            # Preview
            if args.preview:
                disp = frame.copy()
                if args.draw_boxes:
                    for b in face_to_blur:
                        x, y, bw, bh = b.as_int()
                        cv2.rectangle(disp, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
                    for b in text_to_redact:
                        x, y, bw, bh = b.as_int()
                        cv2.rectangle(disp, (x, y), (x + bw, y + bh), (0, 165, 255), 2)
                cv2.imshow("obs_face_blur_toolkit (screen preview)", disp)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

            if vcam is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                vcam.send(rgb)
                try:
                    vcam.sleep_until_next_frame()
                except Exception:
                    pass

            # In preview-only mode, pace the loop.
            if vcam is None:
                elapsed = now - last_frame_time
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_frame_time = time.perf_counter()

            if args.debug and (time.perf_counter() - t0) > frame_interval * 2:
                print(f"slow frame: {(time.perf_counter() - t0)*1000:.1f}ms")

    if args.preview:
        cv2.destroyAllWindows()
    return 0


# -------------------------
# CLI
# -------------------------

def parse_dnn_backend_target(backend: str, target: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Map human-friendly strings to OpenCV DNN backend/target constants.
    If unknown, return (None, None) and let OpenCV choose defaults.
    """
    if not backend and not target:
        return None, None

    backend_map = {
        "default": cv2.dnn.DNN_BACKEND_DEFAULT,
        "opencv": cv2.dnn.DNN_BACKEND_OPENCV,
        "cuda": getattr(cv2.dnn, "DNN_BACKEND_CUDA", None),
    }
    target_map = {
        "cpu": cv2.dnn.DNN_TARGET_CPU,
        "cuda": getattr(cv2.dnn, "DNN_TARGET_CUDA", None),
        "cuda_fp16": getattr(cv2.dnn, "DNN_TARGET_CUDA_FP16", None),
    }

    b = backend_map.get(backend.lower())
    t = target_map.get(target.lower())
    if b is None or t is None:
        return None, None
    return int(b), int(t)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="obs_face_blur_toolkit.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common_detector_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--detector", choices=["yunet", "haar"], default="yunet", help="Face detector to use.")
        sp.add_argument("--model-path", default=None, help="Path to YuNet ONNX model. If omitted, uses cache + auto-download.")
        sp.add_argument("--no-download", action="store_true", help="Disable automatic model download for YuNet.")
        sp.add_argument("--yunet-score-thresh", type=float, default=0.9, help="YuNet score threshold.")
        sp.add_argument("--yunet-nms-thresh", type=float, default=0.3, help="YuNet NMS threshold.")
        sp.add_argument("--yunet-top-k", type=int, default=5000, help="YuNet top_k.")
        sp.add_argument("--dnn-backend", default="", help="OpenCV DNN backend: default|opencv|cuda (optional).")
        sp.add_argument("--dnn-target", default="", help="OpenCV DNN target: cpu|cuda|cuda_fp16 (optional).")

        sp.add_argument("--detect-width", type=int, default=DEFAULT_DETECT_WIDTH, help="Downscale width for detection (0 disables).")
        sp.add_argument("--min-face-px", type=int, default=DEFAULT_MIN_FACE_PX, help="Ignore faces smaller than this (pixels).")
        sp.add_argument("--expand", type=float, default=DEFAULT_EXPAND, help="Expand detected boxes by this fraction per side (e.g., 0.2).")
        sp.add_argument("--keep-n-largest", type=int, default=DEFAULT_KEEP_N_LARGEST, help="Keep N largest faces unblurred (blur everyone else). 0 disables (blur all).")
        sp.add_argument("--keep-min-area-frac", type=float, default=DEFAULT_KEEP_MIN_AREA_FRAC, help="Safety gate for --keep-n-largest: kept faces must be at least this fraction of the frame area (else blur all).")
        sp.add_argument("--keep-center-frac", type=float, default=DEFAULT_KEEP_CENTER_FRAC, help="Safety gate for --keep-n-largest: kept face centers must be within the central fraction of the frame (else blur all).")

        sp.add_argument("--track-iou", type=float, default=DEFAULT_TRACK_IOU, help="IoU threshold for associating detections to tracks.")
        sp.add_argument("--track-max-misses", type=int, default=DEFAULT_TRACK_MAX_MISSES, help="How many missed detections before dropping a track.")
        sp.add_argument("--track-smoothing", type=float, default=DEFAULT_SMOOTHING, help="Exponential smoothing factor (0..1).")

        sp.add_argument("--blur", choices=["gaussian", "pixelate", "blackbox"], default="pixelate", help="Anonymization method.")
        sp.add_argument("--gaussian-kernel", type=int, default=0, help="Gaussian kernel size (odd). 0 => auto.")
        sp.add_argument("--pixel-size", type=int, default=12, help="Pixel size for pixelation (bigger => more blocky).")

    # virtualcam
    vc = sub.add_parser("virtualcam", help="Blur faces live and output to a virtual camera for OBS.")
    add_common_detector_args(vc)
    vc.add_argument("--source", default="0", help="Video source: camera index (e.g. 0) or file/URL.")
    vc.add_argument("--width", type=int, default=None, help="Requested capture width.")
    vc.add_argument("--height", type=int, default=None, help="Requested capture height.")
    vc.add_argument("--capture-fps", type=int, default=None, help="Requested capture FPS.")
    vc.add_argument("--output-width", type=int, default=None, help="Virtual camera output width (defaults to capture width).")
    vc.add_argument("--output-height", type=int, default=None, help="Virtual camera output height (defaults to capture height).")
    vc.add_argument("--output-fps", type=int, default=DEFAULT_OUTPUT_FPS, help="Virtual camera output FPS.")
    vc.add_argument("--detect-fps", type=int, default=DEFAULT_DETECT_FPS, help="How many times per second to run detection.")
    vc.add_argument("--flip", action="store_true", help="Mirror horizontally (like a selfie camera).")
    vc.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="Rotate frames.")
    vc.add_argument("--preview", action="store_true", help="Show a local preview window.")
    vc.add_argument("--draw-boxes", action="store_true", help="Draw face boxes in the preview window (debug).")
    vc.add_argument("--virtualcam-disable", action="store_true", help="Do not output to a virtual cam (preview only).")

    vc.add_argument("--backend", default="auto", help="pyvirtualcam backend: auto|obs|unitycapture|v4l2loopback")
    vc.add_argument("--device", default=None, help="pyvirtualcam device name/path (optional).")
    vc.add_argument("--print-fps", action="store_true", help="pyvirtualcam prints actual FPS stats (if supported).")

    # screen-virtualcam
    sc = sub.add_parser("screen-virtualcam", help="Capture screen, redact text/PII/secrets, optional face blur, output to a virtual camera for OBS.")
    add_common_detector_args(sc)
    sc.add_argument("--monitor", type=int, default=1, help="Monitor index for capture (1..N).")
    sc.add_argument("--region", default="", help="Optional crop within monitor: x,y,w,h.")
    sc.add_argument("--output-width", type=int, default=1280, help="Virtual camera output width.")
    sc.add_argument("--output-height", type=int, default=720, help="Virtual camera output height.")
    sc.add_argument("--output-fps", type=int, default=DEFAULT_OUTPUT_FPS, help="Virtual camera output FPS.")
    sc.add_argument("--detect-fps", type=int, default=DEFAULT_DETECT_FPS, help="How many times per second to run face detection.")
    sc.add_argument("--flip", action="store_true", help="Mirror horizontally.")
    sc.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="Rotate frames.")
    sc.add_argument("--preview", action="store_true", help="Show a local preview window.")
    sc.add_argument("--draw-boxes", action="store_true", help="Draw face/text boxes in the preview window (debug).")
    sc.add_argument("--virtualcam-disable", action="store_true", help="Do not output to a virtual cam (preview only).")
    sc.add_argument("--backend", default="auto", help="pyvirtualcam backend: auto|obs|unitycapture|v4l2loopback")
    sc.add_argument("--device", default=None, help="pyvirtualcam device name/path (optional).")
    sc.add_argument("--print-fps", action="store_true", help="pyvirtualcam prints actual FPS stats (if supported).")

    sc.add_argument("--faces-disable", action="store_true", help="Disable face detection/blurring (text-only).")

    sc.add_argument("--text-disable", action="store_true", help="Disable OCR-based text redaction (enabled by default).")
    sc.add_argument("--text-mode", choices=["sensitive", "all"], default="sensitive", help="Text redaction mode: sensitive (pattern-based) or all (redact all detected text).")
    sc.add_argument("--text-method", choices=["gaussian", "pixelate", "blackbox"], default="blackbox", help="Redaction method for text regions.")
    sc.add_argument("--text-gaussian-kernel", type=int, default=0, help="Gaussian kernel for text regions (odd). 0 => auto.")
    sc.add_argument("--text-pixel-size", type=int, default=12, help="Pixel size for text pixelation.")
    sc.add_argument("--face-method", choices=["gaussian", "pixelate", "blackbox"], default="pixelate", help="Anonymization method for faces.")
    sc.add_argument("--field-cover", action=argparse.BooleanOptionalAction, default=True, help="When OCR sees labels like 'Password'/'Address', cover a full-width band below to catch input fields.")
    sc.add_argument("--field-band-px", type=int, default=140, help="Height in pixels of the full-width band used by --field-cover.")
    sc.add_argument("--field-band-px-strong", type=int, default=240, help="Height in pixels of the full-width band used by --field-cover when a strong label is detected (Password/2FA/private key/autofill dropdowns).")

    sc.add_argument("--block-on-context", action="store_true", help="If OCR detects sensitive context (Telegram/WhatsApp/Google Docs/banking-ish), block the entire frame.")
    sc.add_argument("--context-ocr-fps", type=int, default=DEFAULT_CONTEXT_OCR_FPS, help="How many times per second to OCR context region for block detection.")
    sc.add_argument("--context-top-frac", type=float, default=DEFAULT_CONTEXT_TOP_FRAC, help="Top fraction (0..1) of frame to OCR for context (URL/app hints).")
    sc.add_argument("--block-hold-ms", type=int, default=DEFAULT_BLOCK_HOLD_MS, help="How long to keep blocking after a trigger (reduces flicker).")
    sc.add_argument("--block-keyword", action="append", default=[], help="Additional keyword (repeatable) that triggers full-frame block when seen in context OCR.")
    sc.add_argument("--block-url-regex", action="append", default=[], help="Additional regex (repeatable) that triggers full-frame block when matched in context OCR (e.g. banking subdomains).")
    sc.add_argument("--block-method", choices=["gaussian", "pixelate", "blackbox"], default="blackbox", help="Method used when full-frame block is active.")
    sc.add_argument("--block-gaussian-kernel", type=int, default=0, help="Gaussian kernel for full-frame block (odd). 0 => auto.")
    sc.add_argument("--block-pixel-size", type=int, default=18, help="Pixel size for full-frame pixelation block.")

    sc.add_argument("--ocr-fps", type=int, default=DEFAULT_OCR_FPS, help="How many times per second to run OCR (lower saves CPU).")
    sc.add_argument("--ocr-width", type=int, default=DEFAULT_OCR_WIDTH, help="Downscale width used for OCR (lower saves CPU).")
    sc.add_argument("--ocr-lang", default=DEFAULT_OCR_LANG, help="Tesseract language code (e.g., eng).")
    sc.add_argument("--min-word-conf", type=int, default=DEFAULT_MIN_WORD_CONF, help="Tesseract min confidence (0..100).")
    sc.add_argument("--max-text-boxes", type=int, default=DEFAULT_MAX_TEXT_BOXES, help="Max text redaction boxes per OCR pass.")
    sc.add_argument("--text-pad-px", type=int, default=DEFAULT_TEXT_PAD_PX, help="Padding around matched text regions (pixels).")
    sc.add_argument("--text-merge-iou", type=float, default=DEFAULT_TEXT_MERGE_IOU, help="Merge overlapping text boxes if IoU >= this.")
    sc.add_argument("--tesseract-cmd", default="", help="Optional path to tesseract binary (if not on PATH).")
    sc.add_argument("--debug", action="store_true", help="Print debug logs (OCR failures, slow frames).")

    # blur-video
    bv = sub.add_parser("blur-video", help="Blur faces in a recorded video file.")
    add_common_detector_args(bv)
    bv.add_argument("input", help="Input video path.")
    bv.add_argument("output", help="Output video path.")
    bv.add_argument("--fourcc", default="mp4v", help="OpenCV fourcc for output (e.g., mp4v, avc1, XVID).")
    bv.add_argument("--detect-every", type=int, default=1, help="Detect every N frames (speed vs. accuracy).")
    bv.add_argument("--progress", action="store_true", help="Print progress every ~60 frames.")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    backend_id, target_id = parse_dnn_backend_target(getattr(args, "dnn_backend", ""), getattr(args, "dnn_target", ""))
    args.dnn_backend_id = backend_id
    args.dnn_target_id = target_id

    if args.cmd == "virtualcam":
        return run_virtualcam(args)
    if args.cmd == "screen-virtualcam":
        return run_screen_virtualcam(args)
    if args.cmd == "blur-video":
        return run_blur_video(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
