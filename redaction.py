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
    r"\b(?:\d[ -]?){13,19}\b",
    r"(?:\+\d[\d ()-]{6,}\d)|(?:\b\d{2,4}[ .()-]\d{2,4}[ .()-]\d{2,4}\b)",
    r"(?:[*•●xX][ -]?){2,}\d{4}\b",
    r"\b(?:ending(?: in)?|last four|last 4|se terminant|finissant|endet auf)\D{0,12}\d{4}\b",
    r"\b(?:card|carte|karte|credit|debit|visa|mastercard|amex)\b.*\b\d{4}\b",
    r"\b(?:0[1-9]|1[0-2])[/.-](?:\d{4}|\d{2})\b",
    r"\b(?:cvv|cvc|cid|security code|cryptogramme|prufnummer)\b",
    r"\b(?:first name|last name|full name|cardholder|name on card|prenom|nom|titulaire|vorname|nachname|karteninhaber)\b",
    r"\b(?:address|adresse|billing|facturation|street|avenue|boulevard|road|postcode|postal|zip|rue|chemin|strasse|straße|gasse|platz|hausnummer|postleitzahl|plz)\b",
    r"\b[a-z]*(?:strasse|straße|gasse|platz|weg)\s+\d{1,5}\b",
    r"\b\d{4,5}\s+[a-z][a-z-]+\b",
    r"\b(?:apt|apartment|suite|unit|appartement|wohnung)\s*[#:-]?\s*\w+",
    r"\b(?:sk_(?:live|test)_|gh[pousr]_|AKIA|AIza|xox[baprs]-)[\w-]+",
    r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b",
    r"-----BEGIN.*PRIVATE KEY-----",
    r"\b[A-Z0-9_]{2,50}\s*[=:]\s*\S+",
))
# Deliberately conservative: ordinary title text may be hidden in selective mode.
PERSON = re.compile(r"(?<!\w)(?:[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:[-'][A-ZÀ-ÖØ-Þ]?[a-zà-öø-ÿ]+)?\s+){1,3}[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?!\w)")
UPPER_PERSON = re.compile(r"\b[A-ZÀ-ÖØ-Þ]{2,}(?:\s+[A-ZÀ-ÖØ-Þ]{2,}){1,3}\b")
FIELD = re.compile(r"\b(?:name|prenom|nom|vorname|nachname|address|adresse|cardholder|titulaire|karteninhaber)\b", re.I)


class Redactor:
    def __init__(self, mode="protective", terms=(), regions=(), backend="tesseract"):
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

    def redact(self, image):
        if not isinstance(image, Image.Image) or image.width < 1 or image.height < 1:
            raise ValueError("Invalid captured frame")
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
            if self.mode == "selective":
                for i, (text, _) in enumerate(rows):
                    if (any(pattern.search(folded[i]) for pattern in PATTERNS)
                            or PERSON.search(text) or UPPER_PERSON.search(text)):
                        selected.add(i)
                    # Labels and their immediately following value are often separate OCR lines.
                    if FIELD.search(folded[i]) and i + 1 < len(rows):
                        label = rows[i][1]
                        following = rows[i + 1][1]
                        if 0 <= following[1] - label[3] <= max(80, (label[3] - label[1]) * 4):
                            selected.add(i + 1)
            combined, spans = "", []
            for text in folded:
                start = len(combined)
                combined += text + " "
                spans.append((start, start + len(text)))
            for term in self.terms:
                offset = 0
                while (match := combined.find(term, offset)) >= 0:
                    end = match + len(term)
                    selected.update(i for i, (start, stop) in enumerate(spans)
                                    if start < end and stop > match)
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
        return safe, {"recognized_lines": len(rows), "masked_lines": len(selected),
                      "manual_regions": len(self.regions)}
