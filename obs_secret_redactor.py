#!/usr/bin/env python3
"""
obs_secret_redactor.py
Real-time visual redaction inside OBS via obs-websocket 5.x.

Usage:
  python3 obs_secret_redactor.py --host 127.0.0.1 --port 4455 --password "<pwd>"

Notes:
- Creates and reuses a pool of Color Source rectangles named REDACT_BOX_<n>.
- Captures Program Scene frames via GetSourceScreenshot, runs OCR, matches secrets/PII,
  maps boxes back to scene base resolution, and positions overlays accordingly.
- All processing is local. No network calls.
"""

if __name__ == "__main__":
    # Retire the post-broadcast overlay entry point. Never parse old credentials.
    print("The old overlay prototype is disabled. Open Safestream.command for buffered redaction.")
    raise SystemExit(1)

import argparse
import base64
import signal
import sys
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np
import pytesseract
import regex as re
from PIL import Image

# obsws-python 1.7.0 (for obs-websocket 5.x)
from obsws_python import obsws, requests

# -------------------------
# Config
# -------------------------

DEFAULT_FPS = 10                  # OCR/redaction updates per second
MAX_BOXES = 40                    # Max black rectangles per frame
MIN_WORD_CONF = 60                # Tesseract confidence threshold (0..100)
LINE_PAD_PX = 4                   # Extra padding around detected text (scene pixels, scaled later)
MERGE_IOU = 0.25                  # Merge overlapping boxes if IoU above this
SCREENSHOT_WIDTH = 1280           # OCR at this width for speed; height auto per aspect
OCR_LANG = "eng"                  # Tesseract language

# Patterns: curated and fast. Extend as needed.
PATTERNS = [
    # Private key blocks
    r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----",
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
    # AWS secret keys (heuristic: 40 chars base64ish)
    r"\b[0-9A-Za-z/+]{40}\b",
    # Generic high-entropy tokens
    r"\b[A-Za-z0-9_\-]{22,}\b",
    # Emails
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    # Phone numbers (basic, international)
    r"\b(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}\b",
    # .env style
    r"\b[A-Z0-9_]{2,32}\s*=\s*[^ \t\r\n#]{1,}\b",
    # Billing hints: names and address fragments (coarse)
    r"\b(?:Address|Billing|Cardholder|Name|Street|St\.|Ave|Avenue|Road|Rd\.|Postcode|ZIP|City|State|Country)\b",
    # Street address structures
    r"(?i)\b\d{1,6}\s+(?:[A-Za-z0-9]+\.?\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Circle|Cir|Way|Trail|Trl|Parkway|Pkwy|Place|Pl|Terrace|Ter)\b",
    # City, state, ZIP formats
    r"(?i)\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
    # Apartment/unit formats
    r"(?i)\b(?:apt|apartment|suite|ste|unit|floor|fl)\s*[#\-\s]?\s*\w{1,6}\b",
    # Credit card numbers (grouped 4s or flexible digits)
    r"(?i)\b(?:\d{4}[- ]?){3}\d{4}\b",
    r"(?i)\b(?:\d{4}[- ]?){2}\d{5}\b",
    r"(?i)\b(?:\d[ -]?){13,19}\d\b",
    # Card expiry formats
    r"(?i)\b(0[1-9]|1[0-2])[\/\-](?:\d{2}|\d{4})\b",
    # CVV/CVC numbers when labeled
    r"(?i)\b(?:cvv|cvc|cid)[^\d]{0,3}\d{3,4}\b",
    # Uppercase config/secret style
    r"\b[A-Z][A-Z0-9_]{1,49}\s*=\s*[^\s]{1,}\b",
    r"\b[A-Z][A-Z0-9_]{1,49}\s*:\s*[^\s]{1,}\b",
]

PATTERNS_COMPILED = [re.compile(p) for p in PATTERNS]

BILLING_KEYWORDS = {
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
BILLING_NAME_PATTERN = re.compile(r"\b(?:[A-Z][a-z]+|[A-Z]{2,}) (?:[A-Z][a-z]+|[A-Z]{2,})(?: (?:[A-Z][a-z]+|[A-Z]{2,}))?\b")
BILLING_SINGLE_NAME_PATTERN = re.compile(r"^(?:[A-Z][a-z]+|[A-Z]{2,})(?:[-'][A-Z][a-z]+|[A-Z]{2,})?$")
CARD_LAST4_HINTS = {"ending", "last", "digits", "final", "ending in", "last 4", "last four"}
CARD_CONTEXT_HINTS = {"card", "credit", "debit", "account", "acct", "payment", "billing", "checkout"}
NAME_FIELD_KEYWORDS = {
    "first name",
    "last name",
    "cardholder name",
    "card holder",
    "card holder name",
    "name on card",
    "full name",
}
NAME_EXCLUDE_TOKENS = {"cvv", "cvc", "cid"}
AUTOFILL_KEYWORDS = {
    "autofill",
    "auto-fill",
    "auto fill",
    "suggested",
    "suggestion",
    "suggestions",
    "use suggested",
    "use suggestion",
    "saved info",
    "saved information",
    "saved address",
    "saved addresses",
    "saved payment",
    "fill form",
    "form fill",
}
GENERIC_SAFE_WORDS = {
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


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float


def iou(a: Box, b: Box) -> float:
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = a.w * a.h
    area_b = b.w * b.h
    return inter / (area_a + area_b - inter + 1e-6)


def merge_boxes(boxes: List[Box], iou_thresh: float) -> List[Box]:
    if not boxes:
        return []
    boxes_sorted = sorted(boxes, key=lambda b: b.w * b.h, reverse=True)
    kept: List[Box] = []
    for b in boxes_sorted:
        merged = False
        for i, k in enumerate(kept):
            if iou(b, k) >= iou_thresh:
                x1 = min(b.x, k.x)
                y1 = min(b.y, k.y)
                x2 = max(b.x + b.w, k.x + k.w)
                y2 = max(b.y + b.h, k.y + k.h)
                kept[i] = Box(x1, y1, x2 - x1, y2 - y1)
                merged = True
                break
        if not merged:
            kept.append(b)
    return kept


def decode_obs_image(image_data_b64: str) -> Image.Image:
    raw = base64.b64decode(image_data_b64)
    return Image.open(BytesIO(raw)).convert("RGB")


def pil_to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def ocr_boxes(img_bgr: np.ndarray) -> Tuple[Dict[str, Any], np.ndarray]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # Light denoise improves Tesseract stability on UI text
    gray = cv2.bilateralFilter(gray, 5, 50, 50)
    # OCR with word-level boxes
    data = pytesseract.image_to_data(
        gray,
        lang=OCR_LANG,
        output_type=pytesseract.Output.DICT,
        config="--oem 1 --psm 6",
    )
    return data, gray


def find_secret_boxes(ocr: Dict[str, Any], scale_x: float, scale_y: float) -> List[Box]:
    n = len(ocr["text"])
    boxes: List[Box] = []
    tokens_lower = [(ocr["text"][i] or "").lower() for i in range(n) if ocr["text"][i]]
    joined_tokens = " ".join(tokens_lower)
    billing_context = any(keyword in joined_tokens for keyword in BILLING_KEYWORDS)
    name_field_context = any(keyword in joined_tokens for keyword in NAME_FIELD_KEYWORDS)
    autofill_context = any(keyword in joined_tokens for keyword in AUTOFILL_KEYWORDS)
    # Build per-line aggregates to cover entire sensitive lines
    by_line: Dict[Tuple[int, int, int], List[int]] = {}
    for i in range(n):
        if int(ocr["conf"][i]) < MIN_WORD_CONF:
            continue
        word = (ocr["text"][i] or "").strip()
        if not word:
            continue
        key = (
            (ocr.get("page_num", [0])[i] if isinstance(ocr.get("page_num"), list) else 0),
            ocr["block_num"][i],
            ocr["line_num"][i],
        )
        by_line.setdefault(key, []).append(i)

    def word_rect(idx: int) -> Box:
        x = int(ocr["left"][idx]) * scale_x
        y = int(ocr["top"][idx]) * scale_y
        w = int(ocr["width"][idx]) * scale_x
        h = int(ocr["height"][idx]) * scale_y
        return Box(x, y, w, h)

    # Fast pass: per-word secret match
    for i in range(n):
        if int(ocr["conf"][i]) < MIN_WORD_CONF:
            continue
        token = (ocr["text"][i] or "").strip()
        if not token:
            continue
        # skip trivial UI strings
        if len(token) <= 3:
            continue
        matched = any(p.search(token) for p in PATTERNS_COMPILED)
        if matched:
            b = word_rect(i)
            boxes.append(Box(b.x - LINE_PAD_PX, b.y - LINE_PAD_PX, b.w + 2 * LINE_PAD_PX, b.h + 2 * LINE_PAD_PX))

    # Line-level pass: join words and recheck with patterns
    for key, idxs in by_line.items():
        line_text = " ".join((ocr["text"][i] or "").strip() for i in idxs if int(ocr["conf"][i]) >= MIN_WORD_CONF)
        if not line_text:
            continue
        line_text_lower = line_text.lower()
        line_sensitive = False
        if any(keyword in line_text_lower for keyword in BILLING_KEYWORDS):
            billing_context = True
        if any(keyword in line_text_lower for keyword in NAME_FIELD_KEYWORDS):
            name_field_context = True
            line_sensitive = True
        if any(keyword in line_text_lower for keyword in AUTOFILL_KEYWORDS):
            autofill_context = True
            line_sensitive = True

        if any(p.search(line_text) for p in PATTERNS_COMPILED):
            line_sensitive = True

        if (billing_context or name_field_context or autofill_context) and BILLING_NAME_PATTERN.search(line_text):
            line_sensitive = True

        stripped_words = [w.strip(".,'\"") for w in line_text.split()]

        if not line_sensitive and name_field_context:
            if 1 <= len(stripped_words) <= 3:
                word_matches = [
                    bool(BILLING_SINGLE_NAME_PATTERN.match(w)) and w.lower() not in NAME_EXCLUDE_TOKENS
                    for w in stripped_words
                    if w
                ]
                if word_matches and all(word_matches):
                    line_sensitive = True

        if billing_context and re.search(r"\b\d{4}\b", line_text):
            if any(hint in line_text_lower for hint in CARD_CONTEXT_HINTS) or any(
                hint in line_text_lower for hint in CARD_LAST4_HINTS
            ):
                line_sensitive = True

        if not line_sensitive:
            title_candidates = [
                w
                for w in stripped_words
                if w
                and bool(BILLING_SINGLE_NAME_PATTERN.match(w))
                and w.lower() not in NAME_EXCLUDE_TOKENS
            ]
            meaningful_candidates = [
                w for w in title_candidates if w.lower() not in GENERIC_SAFE_WORDS
            ]
            if len(title_candidates) >= 2 and meaningful_candidates:
                if billing_context or name_field_context or autofill_context or len(title_candidates) >= 3:
                    line_sensitive = True

        if line_sensitive:
            xs, ys, xe, ye = [], [], [], []
            for i in idxs:
                b = word_rect(i)
                xs.append(b.x)
                ys.append(b.y)
                xe.append(b.x + b.w)
                ye.append(b.y + b.h)
            x1, y1, x2, y2 = min(xs), min(ys), max(xe), max(ye)
            boxes.append(Box(x1 - LINE_PAD_PX, y1 - LINE_PAD_PX, (x2 - x1) + 2 * LINE_PAD_PX, (y2 - y1) + 2 * LINE_PAD_PX))

    # Merge overlaps and cap
    merged = merge_boxes(boxes, MERGE_IOU)
    if len(merged) > MAX_BOXES:
        merged = sorted(merged, key=lambda b: b.w * b.h, reverse=True)[:MAX_BOXES]
    return merged


class ObsRedactor:
    def __init__(self, host: str, port: int, password: str, fps: int = DEFAULT_FPS):
        self.ws = obsws(host, port, password)
        self.fps = fps
        self.scene_name = None
        self.base_w = None
        self.base_h = None
        self.overlay_names: List[str] = []
        self.running = True

    def connect(self) -> None:
        self.ws.connect()

    def disconnect(self) -> None:
        try:
            self.ws.disconnect()
        except Exception:
            pass

    def _get_video_settings(self) -> None:
        r = self.ws.call(requests.GetVideoSettings())
        data = r.datain
        self.base_w = int(data["baseWidth"])
        self.base_h = int(data["baseHeight"])

    def _get_program_scene(self) -> None:
        r = self.ws.call(requests.GetCurrentProgramScene())
        self.scene_name = r.datain["currentProgramSceneName"]

    def _ensure_overlays(self, count: int) -> None:
        # Create color sources if needed
        while len(self.overlay_names) < count:
            name = f"REDACT_BOX_{len(self.overlay_names)}"
            settings = {"width": 10, "height": 10, "color": 0x000000, "opacity": 100}
            self.ws.call(
                requests.CreateInput(
                    sceneName=self.scene_name,
                    inputName=name,
                    inputKind="color_source_v3",
                    inputSettings=settings,
                    sceneItemEnabled=False,
                )
            )
            self.overlay_names.append(name)

    def _hide_all_overlays(self) -> None:
        for name in self.overlay_names:
            try:
                si = self.ws.call(
                    requests.GetSceneItemId(sceneName=self.scene_name, sourceName=name)
                ).datain["sceneItemId"]
                self.ws.call(
                    requests.SetSceneItemEnabled(
                        sceneName=self.scene_name,
                        sceneItemId=si,
                        sceneItemEnabled=False,
                    )
                )
            except Exception:
                continue

    def _apply_boxes(self, boxes: List[Box]) -> None:
        self._ensure_overlays(len(boxes))
        # First hide all
        self._hide_all_overlays()
        # Then position first N as needed
        for i, b in enumerate(boxes):
            name = self.overlay_names[i]
            try:
                si = self.ws.call(
                    requests.GetSceneItemId(sceneName=self.scene_name, sourceName=name)
                ).datain["sceneItemId"]

                # Resize the color source
                self.ws.call(
                    requests.SetInputSettings(
                        inputName=name,
                        inputSettings={
                            "width": max(1, int(b.w)),
                            "height": max(1, int(b.h)),
                            "color": 0x000000,
                            "opacity": 100,
                        },
                        overlay=False,
                    )
                )

                # Position it
                self.ws.call(
                    requests.SetSceneItemTransform(
                        sceneName=self.scene_name,
                        sceneItemId=si,
                        sceneItemTransform={
                            "positionX": float(b.x),
                            "positionY": float(b.y),
                            "rotation": 0.0,
                            "scaleX": 1.0,
                            "scaleY": 1.0,
                            "alignment": 5,
                        },
                    )
                )

                # Bring to top
                self.ws.call(
                    requests.SetSceneItemIndex(
                        sceneName=self.scene_name,
                        sceneItemId=si,
                        sceneItemIndex=9999,
                    )
                )

                # Show
                self.ws.call(
                    requests.SetSceneItemEnabled(
                        sceneName=self.scene_name,
                        sceneItemId=si,
                        sceneItemEnabled=True,
                    )
                )
            except Exception:
                continue

        # Hide any remaining overlays beyond len(boxes)
        for j in range(len(boxes), len(self.overlay_names)):
            try:
                si = self.ws.call(
                    requests.GetSceneItemId(sceneName=self.scene_name, sourceName=self.overlay_names[j])
                ).datain["sceneItemId"]
                self.ws.call(
                    requests.SetSceneItemEnabled(
                        sceneName=self.scene_name,
                        sceneItemId=si,
                        sceneItemEnabled=False,
                    )
                )
            except Exception:
                continue

    def _grab_frame(self) -> Tuple[np.ndarray, float, float]:
        # Get a screenshot of the current Program Scene at target width
        aspect = self.base_h / self.base_w
        target_w = SCREENSHOT_WIDTH
        target_h = int(round(target_w * aspect))

        img_b64 = self.ws.call(
            requests.GetSourceScreenshot(
                sourceName=self.scene_name,
                imageFormat="png",
                imageWidth=target_w,
                imageHeight=target_h,
                imageCompressionQuality=100,
            )
        ).datain["imageData"]

        pil = decode_obs_image(img_b64)
        bgr = pil_to_cv(pil)

        # scale factors to map OCR coords → scene coords
        scale_x = self.base_w / float(target_w)
        scale_y = self.base_h / float(target_h)
        return bgr, scale_x, scale_y

    def loop(self) -> None:
        self._get_video_settings()
        self._get_program_scene()
        self._hide_all_overlays()

        interval = 1.0 / max(1, self.fps)
        while self.running:
            t0 = time.time()
            try:
                # Program scene may change
                self._get_program_scene()
                frame_bgr, sx, sy = self._grab_frame()
                ocr, _ = ocr_boxes(frame_bgr)
                boxes = find_secret_boxes(ocr, sx, sy)
                self._apply_boxes(boxes)
            except KeyboardInterrupt:
                self.running = False
            except Exception:
                # Fail closed: hide overlays on error to avoid permanent blocks
                self._hide_all_overlays()
            dt = time.time() - t0
            sleep_time = max(0.0, interval - dt)
            time.sleep(sleep_time)

    def stop(self) -> None:
        self.running = False
        self._hide_all_overlays()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4455)
    ap.add_argument("--password", required=True)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    args = ap.parse_args()

    red = ObsRedactor(args.host, args.port, args.password, args.fps)

    def handle_sig(_sig: int, _frm: Any) -> None:
        red.stop()
        red.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    red.connect()
    try:
        red.loop()
    finally:
        red.stop()
        red.disconnect()


if __name__ == "__main__":
    main()
