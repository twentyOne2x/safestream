import json
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".build/native-ocr"


def test_native_ocr_stdin_synthetic_bounds():
    image = Image.new("RGB", (1200, 600), "white")
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 32)
    draw = ImageDraw.Draw(image)
    lines = ["Alice Martin", "42 rue du Test", "4111 1111 1111 1111", "Private family project"]
    for i, line in enumerate(lines):
        draw.text((40, 40 + i * 110), line, font=font, fill="black")
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    result = subprocess.run([str(HELPER)], input=encoded.getvalue(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=8, check=True)
    assert result.stderr == b""
    rows = json.loads(result.stdout)
    assert len(rows) == len(lines)
    assert {row["text"] for row in rows} == set(lines)
    for row in rows:
        assert set(row) == {"text", "x", "y", "w", "h"}
        assert all(isinstance(row[key], int) for key in ("x", "y", "w", "h"))
        assert 0 <= row["x"] < image.width and 0 <= row["y"] < image.height
        assert 0 < row["w"] <= image.width - row["x"]
        assert 0 < row["h"] <= image.height - row["y"]
    ordered = sorted(rows, key=lambda row: row["y"])
    assert [row["text"] for row in ordered] == lines
    assert ordered[0]["y"] < 100 and ordered[-1]["y"] > 300


def test_native_ocr_invalid_input_is_generic():
    result = subprocess.run([str(HELPER)], input=b"invalid image PRIVATE SENTINEL",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8)
    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == b"Local text recognition failed\n"
