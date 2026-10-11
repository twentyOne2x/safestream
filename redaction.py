"""Local OCR redaction. Captured pixels and recognized text never leave this module."""

import math
import os
import json
import re
import csv
import subprocess
import unicodedata
from io import BytesIO, StringIO
from pathlib import Path

from PIL import Image, ImageDraw


def _fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
    r"(?<![\w@])@[a-z0-9_][a-z0-9_.]{0,29}\b",
    r"\b(?:https?://)?(?:www\.)?(?:instagram\.com|instagr\.am)/[a-z0-9_.]+\b",
    r"\b(?:\d[ -]?){13,19}\b",
    r"(?:\+\d[\d ()-]{6,}\d)|(?:\b\d{2,4}[ .()-]\d{2,4}[ .()-]\d{2,4}\b)",
    r"(?:[*•●xX][ -]?){2,}\d{4}\b",
    r"\b(?:ending(?: in)?|last four|last 4|se terminant|finissant|endet auf)\D{0,12}\d{4}\b",
    r"\b(?:card|carte|karte|credit|debit|visa|mastercard|amex)\b.*\b\d{4}\b",
    r"\b(?:0[1-9]|1[0-2])[/.-](?:\d{4}|\d{2})\b",
    r"\b(?:cvv|cvc|cid|security code|cryptogramme|prufnummer)\b",
    r"\b\d{1,5}\s+.*\b(?:street|avenue|boulevard|road|rue|chemin|strasse|straße|gasse|platz)\b",
    r"\b[a-z]*(?:strasse|straße|gasse|platz|weg)\s+\d{1,5}\b",
    r"\b\d{4,5}\s+[a-z][a-z-]+\b",
    r"\b(?:apt|apartment|suite|unit|appartement|wohnung)\s*[#:-]?\s*\w+",
    r"\b(?:sk_(?:live|test)_|gh[pousr]_|AKIA|AIza|xox[baprs]-)[\w-]+",
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b",
    r"\b(?:0x)?[a-f0-9]{32,128}\b",
    r"-----\s*(?:BEGIN|END)\s+(?:[A-Z ]*\s)?(?:PRIVATE|PUBLIC) KEY\s*-----",
))
# Names without a field label are supplied as private terms. Capitalization alone
# cannot distinguish a person's name from a public heading.
FIELD = re.compile(r"^\s*(?:(?:first|last|full|display|user)\s+name|name|prenom|nom|vorname|nachname|address|adresse|billing address|cardholder|titulaire|karteninhaber|cvv|cvc|cid|security code|expiry|expiration|card number|handle|instagram|challenger(?:\s+handle)?|password|passwd|passphrase|mot de passe|otp|2fa|two[- ]factor(?:\s+(?:code|secret))?|one[- ]time(?:\s+(?:code|password))?|verification code|authentication code|authenticator(?:\s+(?:code|secret))?|(?:private|public|api|secret|access|signing)[_ -]?key|(?:access|auth|bearer|refresh)[_ -]?token|seed(?:\s+phrase)?|recovery phrase|mnemonic)\s*(?:[=:：-]\s*.*)?$", re.I)
SECRET_ASSIGNMENT = re.compile(r"\b(?:password|passwd|passphrase|otp|2fa|(?:private|public|api|secret|access|signing)[_ -]?key|(?:access|auth|bearer|refresh)[_ -]?token|seed phrase|recovery phrase|mnemonic)\s*[=:：]\s*\S+", re.I)
SECRET_ENV = re.compile(r"\b[A-Z][A-Z0-9_]*(?:_KEY|_TOKEN|_PASSWORD|_PASSWD|_SECRET|_OTP)\s*[=:]\s*\S+", re.I)
BASE58_KEY = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,88}(?![A-Za-z0-9])")
PEM_BEGIN = re.compile(r"-----\s*BEGIN\s+(?:[A-Z ]*\s)?(?:PRIVATE|PUBLIC) KEY\s*-----", re.I)
PEM_END = re.compile(r"-----\s*END\s+(?:[A-Z ]*\s)?(?:PRIVATE|PUBLIC) KEY\s*-----", re.I)
SEED_FIELD = re.compile(r"^\s*(?:seed(?:\s+phrase)?|recovery phrase|mnemonic)\s*(?:[=:：-]\s*.*)?$", re.I)
WHOLE_SECRET_FIELD = re.compile(r"\b(?:password|passwd|passphrase|otp|2fa|two[- ]factor|one[- ]time|verification code|authentication code|authenticator|(?:private|public|api|secret|signing)[_ -]?key|seed|recovery phrase|mnemonic)\b", re.I)


def _nearby(label, following):
    """Only carry a label into a value in the same visual column."""
    return (0 <= following[1] - label[3] <= max(80, (label[3] - label[1]) * 4)
            and following[0] < label[2] and following[2] > label[0])


class Redactor:
    def __init__(self, mode="protective", terms=(), regions=(), backend="tesseract",
                 whole_windows_terms=("Challenger", "Instagram", "Gmail", "LastPass", "1Password", "mail.google.com")):
        if backend not in ("tesseract", "vision"):
            raise ValueError("Invalid local recognition backend")
        self.backend = backend
        if mode not in ("protective", "selective"):
            raise ValueError("Invalid redaction mode")
        if isinstance(terms, str):
            raise ValueError("Terms must be a collection")
        terms = tuple(terms)
        if any(not isinstance(term, str) for term in terms):
            raise ValueError("Invalid private term")
        if isinstance(whole_windows_terms, str):
            raise ValueError("Window terms must be a collection")
        whole_windows_terms = tuple(whole_windows_terms)
        if any(not isinstance(term, str) for term in whole_windows_terms):
            raise ValueError("Invalid private window term")
        self.whole_windows_terms = tuple(re.compile(r"(?<!\w)" + re.escape(_fold(term.strip())) + r"(?!\w)")
                                         for term in whole_windows_terms if term.strip())
        self.last_window_masks = ()
        self.last_private_window_ids = ()
        self.mode = mode
        self.terms = tuple(_fold(term.strip()) for term in terms if term.strip())
        self.regions = []
        for region in regions:
            if isinstance(region, dict):
                if set(region) != {"x", "y", "w", "h"}:
                    raise ValueError("Invalid manual region")
                region = tuple(region[key] for key in ("x", "y", "w", "h"))
            if not isinstance(region, (list, tuple)) or len(region) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                       or not math.isfinite(v) for v in region):
                raise ValueError("Invalid manual region")
            x, y, w, h = region
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
                raise ValueError("Manual region must fit inside the frame")
            self.regions.append((x, y, w, h))

    def _ocr(self, image):
        try:
            encoded = BytesIO()
            image.save(encoded, format="PNG")
            if self.backend == "vision":
                command = [str(Path(__file__).parent / ".build/native-ocr")]
                if self.mode == "protective":
                    command.append("--fast")
                result = subprocess.run(command,
                                        input=encoded.getvalue(), stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, timeout=8, check=True)
                rows = json.loads(result.stdout)
                if not isinstance(rows, list) or len(rows) > 20000:
                    raise ValueError("Invalid recognition result")
                fields = ("text", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height")
                data = {field: [] for field in fields}
                for i, row in enumerate(rows):
                    if not isinstance(row, dict) or set(row) != {"text", "x", "y", "w", "h"} or not isinstance(row["text"], str):
                        raise ValueError("Invalid recognition row")
                    x, y, w, h = (row[key] for key in ("x", "y", "w", "h"))
                    if any(isinstance(value, bool) or not isinstance(value, int) for value in (x, y, w, h)) or x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > image.width or y + h > image.height:
                        raise ValueError("Invalid recognition bounds")
                    for field, value in zip(fields, (row["text"], 1, 1, 1, i + 1, x, y, w, h)):
                        data[field].append(value)
                return data
            result = subprocess.run(
                ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"],
                input=encoded.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=True,
                env={"PATH": os.environ.get("PATH", os.defpath), "OMP_THREAD_LIMIT": "1"},
            )
            fields = ("level", "page_num", "block_num", "par_num", "line_num", "word_num",
                      "left", "top", "width", "height", "conf", "text")
            reader = csv.DictReader(StringIO(result.stdout.decode("utf-8")), delimiter="\t",
                                    quoting=csv.QUOTE_NONE)
            if tuple(reader.fieldnames or ()) != fields:
                raise ValueError("Invalid OCR header")
            data = {field: [] for field in fields}
            for row in reader:
                if set(row) != set(fields) or any(value is None for value in row.values()):
                    raise ValueError("Invalid OCR row")
                for field in fields:
                    value = row[field] if field == "text" else float(row[field]) if field == "conf" else int(row[field])
                    if field != "text" and not math.isfinite(value):
                        raise ValueError("Invalid OCR value")
                    data[field].append(value)
            return data
        except Exception:
            # Do not propagate provider error text, which can contain OCR data.
            raise RuntimeError("Local text recognition failed") from None

    def redact(self, image, windows=()):
        if not isinstance(image, Image.Image) or image.width < 1 or image.height < 1:
            raise ValueError("Invalid captured frame")
        # Native window bounds are pixels and may extend offscreen. Validate
        # before recognition; draw only their intersection with this frame.
        window_rects = []
        window_ids = []
        if not isinstance(windows, (tuple, list)):
            raise ValueError("Invalid window metadata")
        for window in windows:
            if (not isinstance(window, dict)
                    or set(window) not in ({"x", "y", "w", "h"}, {"x", "y", "w", "h", "id"})):
                raise ValueError("Invalid window metadata")
            window_id = window.get("id")
            if "id" in window and (isinstance(window_id, bool) or not isinstance(window_id, int) or window_id < 0):
                raise ValueError("Invalid window metadata")
            x, y, w, h = (window[key] for key in ("x", "y", "w", "h"))
            if (any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                    for v in (x, y, w, h)) or w <= 0 or h <= 0
                    or not math.isfinite(x + w) or not math.isfinite(y + h)):
                raise ValueError("Invalid window metadata")
            window_rects.append((x, y, x + w, y + h))
            window_ids.append(window_id)
        whole_window_regions = set()
        private_window_ids = set()
        try:
            data = self._ocr(image)
            lines = {}
            for i, raw in enumerate(data["text"]):
                word = str(raw).strip()
                if not word:
                    continue
                key = tuple(data[field][i] for field in
                            ("page_num", "block_num", "par_num", "line_num"))
                x, y, w, h = (int(data[field][i]) for field in ("left", "top", "width", "height"))
                if w <= 0 or h <= 0:
                    continue
                lines.setdefault(key, []).append((word, x, y, x + w, y + h))
            rows = []
            for words in lines.values():
                text = " ".join(word[0] for word in words)
                rect = (min(word[1] for word in words), min(word[2] for word in words),
                        max(word[3] for word in words), max(word[4] for word in words))
                rows.append((text, rect))
            rows.sort(key=lambda row: (row[1][1], row[1][0]))
            selected = set(range(len(rows))) if self.mode == "protective" else set()
            folded = [_fold(row[0]) for row in rows]
            owners, groups = [], {}
            for i, (_, rect) in enumerate(rows):
                midpoint = ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                owner = next((j + 1 for j, window in enumerate(window_rects)
                              if window[0] <= midpoint[0] < window[2]
                              and window[1] <= midpoint[1] < window[3]), 0)
                owners.append(owner)
                groups.setdefault(owner, []).append(i)
            if window_rects:
                for i, (_, rect) in enumerate(rows):
                    if not (any(term.search(folded[i]) for term in self.whole_windows_terms)
                            or FIELD.fullmatch(folded[i]) and WHOLE_SECRET_FIELD.search(folded[i])):
                        continue
                    if not owners[i]:
                        # Menu-bar scope text has no reliable content window.
                        selected.add(i)
                        continue
                    # Metadata is frontmost first; obscured background windows
                    # cannot own visible text merely because they are smaller.
                    j = owners[i] - 1
                    region = window_rects[j]
                    if window_ids[j] is not None:
                        private_window_ids.add(window_ids[j])
                    whole_window_regions.add((max(0, math.floor(region[0])), max(0, math.floor(region[1])),
                                              min(image.width, math.ceil(region[2])),
                                              min(image.height, math.ceil(region[3]))))
            if self.mode == "selective":
                positions = {i: position for indices in groups.values()
                             for position, i in enumerate(indices)}
                for i, (text, _) in enumerate(rows):
                    if (any(pattern.search(text) for pattern in PATTERNS)
                            or SECRET_ASSIGNMENT.search(text) or SECRET_ENV.search(text) or BASE58_KEY.search(text)
                            or FIELD.fullmatch(folded[i])):
                        selected.add(i)
                    # Labels and their immediately following value are often separate OCR lines.
                    following_indices = groups[owners[i]][positions[i] + 1:]
                    if FIELD.fullmatch(folded[i]) and following_indices:
                        j = following_indices[0]
                        label = rows[i][1]
                        following = rows[j][1]
                        # An inline value already belongs to the label's row.
                        if (not re.search(r"[=:：]\s*\S", text)
                                and (window_rects or _nearby(label, following))):
                            selected.add(j)
                    # PEM bodies and wrapped recovery phrases need context even
                    # when a short fragment has no standalone secret signature.
                    if PEM_BEGIN.search(text) or SEED_FIELD.fullmatch(folded[i]):
                        previous = rows[i][1]
                        for distance, j in enumerate(following_indices, 1):
                            candidate, rect = rows[j]
                            if not window_rects and not _nearby(previous, rect):
                                break
                            if PEM_BEGIN.search(text):
                                body = candidate.strip()
                                if not (PEM_END.search(candidate)
                                        or (re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", body)
                                            and (len(body) >= 16 or body.endswith("=")))):
                                    break
                            else:
                                words = re.findall(r"\b[a-z]+\b", _fold(candidate))
                                if (distance > 4 or not 3 <= len(words) <= 24
                                        or not re.fullmatch(r"[a-z]+(?:[\s,]+[a-z]+)*", candidate)
                                        or FIELD.fullmatch(_fold(candidate))):
                                    break
                            selected.add(j)
                            previous = rect
                            if PEM_END.search(candidate):
                                break
            for indices in groups.values():
                combined, spans = "", []
                for i in indices:
                    start = len(combined)
                    combined += folded[i] + " "
                    spans.append((i, start, start + len(folded[i])))
                for term in self.terms:
                    offset = 0
                    while (match := combined.find(term, offset)) >= 0:
                        end = match + len(term)
                        selected.update(i for i, start, stop in spans if start < end and stop > match)
                        offset = match + 1
        except Exception:
            raise RuntimeError("Local redaction failed") from None

        # Mutate only a detached copy; caller's private frame remains intact.
        safe = image.convert("RGB").copy()
        draw = ImageDraw.Draw(safe)
        for i in selected:
            x1, y1, x2, y2 = rows[i][1]
            pad = max(10, math.ceil((y2 - y1) * 0.45))
            draw.rectangle((max(0, x1 - pad), max(0, y1 - pad),
                            min(image.width - 1, x2 + pad), min(image.height - 1, y2 + pad)), fill="black")
        for x, y, w, h in self.regions:
            draw.rectangle((math.floor(x * image.width), math.floor(y * image.height),
                            min(image.width - 1, math.ceil((x + w) * image.width)),
                            min(image.height - 1, math.ceil((y + h) * image.height))), fill="black")
        for x1, y1, x2, y2 in whole_window_regions:
            draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill="black")
        self.last_window_masks = tuple({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}
                                       for x1, y1, x2, y2 in sorted(whole_window_regions))
        self.last_private_window_ids = tuple(sorted(private_window_ids))
        return safe, {"recognized_lines": len(rows), "masked_lines": len(selected),
                      "manual_regions": len(self.regions),
                      "whole_window_regions": len(whole_window_regions)}
