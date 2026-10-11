import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from redaction import Redactor


def ocr(lines, confidence=10):
    data = {key: [] for key in ("text", "conf", "page_num", "block_num", "par_num",
                               "line_num", "left", "top", "width", "height")}
    for i, text in enumerate(lines):
        values = (text, confidence, 1, 1, 1, i + 1, 30, 30 + i * 40, 250, 20)
        for key, value in zip(data, values):
            data[key].append(value)
    return data


class RedactionTests(unittest.TestCase):
    def run_frame(self, lines, **options):
        image = Image.new("RGB", (800, max(300, len(lines) * 40 + 60)), "white")
        redactor = Redactor(**options)
        with patch.object(redactor, "_ocr", return_value=ocr(lines)):
            safe, counts = redactor.redact(image)
        return image, safe, counts

    def test_selective_sensitive_categories(self):
        lines = ["Alice Martin", "ALICE MARTIN", "42 rue du Test", "8000 Zurich", "Musterstrasse 12",
                 "alice@example.invalid", "+41 79 123 45 67", "4111 1111 1111 1111",
                 "3782 822463 10005", "**** 1234", "Visa ending in 9876",
                 "09/2028", "CVV 123", "/work/Família-Privée/docs"]
        _, safe, counts = self.run_frame(lines, mode="selective", terms=["familia-privee"])
        self.assertEqual(counts["masked_lines"], len(lines))
        for i in range(len(lines)):
            self.assertEqual(safe.getpixel((40, 35 + i * 40)), (0, 0, 0))

    def test_protective_low_confidence_without_mask_cap(self):
        lines = [f"ordinary text {i}" for i in range(61)]
        _, safe, counts = self.run_frame(lines)
        self.assertEqual(counts["masked_lines"], 61)
        self.assertEqual(safe.getpixel((40, 35 + 60 * 40)), (0, 0, 0))

    def test_input_stays_intact_and_safe_text_remains_selective(self):
        image, safe, counts = self.run_frame(["ordinary text", "Alice Martin"], mode="selective")
        self.assertEqual(image.getpixel((40, 75)), (255, 255, 255))
        self.assertEqual(safe.getpixel((40, 75)), (0, 0, 0))
        self.assertEqual(safe.getpixel((40, 35)), (255, 255, 255))
        self.assertEqual(counts["masked_lines"], 1)

    def test_multiline_unicode_term(self):
        _, safe, counts = self.run_frame(["private CAFÉ", "família project"], mode="selective",
                                         terms=["cafe familia"])
        self.assertEqual(counts["masked_lines"], 2)
        self.assertEqual(safe.getpixel((40, 35)), (0, 0, 0))
        self.assertEqual(safe.getpixel((40, 75)), (0, 0, 0))

    def test_manual_region_even_when_no_text(self):
        image, safe, counts = self.run_frame([], regions=[{"x": .5, "y": 0, "w": .5, "h": 1}])
        self.assertEqual(safe.getpixel((600, 100)), (0, 0, 0))
        self.assertEqual(image.getpixel((600, 100)), (255, 255, 255))
        self.assertEqual(counts["manual_regions"], 1)

    def test_invalid_configuration(self):
        for region in [(-.1, 0, .2, .2), (0, 0, 0, 1), (.9, 0, .2, 1),
                       (0, 0, float("nan"), 1), (0, 0, True, 1), (0, 0, 2, 1), None]:
            with self.subTest(region=region), self.assertRaises(ValueError):
                Redactor(regions=[region])
        with self.assertRaises(ValueError):
            Redactor(mode="unsafe")

    def test_failure_has_no_raw_fallback_or_sensitive_error(self):
        redactor = Redactor()
        with patch.object(redactor, "_ocr", side_effect=RuntimeError("PRIVATE DATA")):
            with self.assertRaisesRegex(RuntimeError, "^Local redaction failed$"):
                redactor.redact(Image.new("RGB", (100, 100)))

    def test_tesseract_timeout_bound_and_safe_exception(self):
        redactor = Redactor()
        with patch("redaction.subprocess.run", side_effect=RuntimeError("PRIVATE DATA")) as call:
            with self.assertRaisesRegex(RuntimeError, "^Local text recognition failed$"):
                redactor._ocr(Image.new("RGB", (100, 100)))
            self.assertEqual(call.call_args.kwargs["timeout"], 8)
            self.assertEqual(call.call_args.args[0],
                             ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "11", "tsv"])
            self.assertTrue(call.call_args.kwargs["input"].startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(call.call_args.kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(call.call_args.kwargs["stdout"], subprocess.PIPE)
            self.assertTrue(call.call_args.kwargs["check"])
            self.assertNotIn("shell", call.call_args.kwargs)

    def test_stdin_tsv_parser_and_malformed_rejection(self):
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        valid = header + "5\t1\t1\t1\t1\t1\t30\t30\t100\t20\t9.5\tSynthetic\n"
        redactor = Redactor()
        frame = Image.new("RGB", (200, 200))
        with patch("redaction.subprocess.run", return_value=SimpleNamespace(stdout=valid.encode())):
            parsed = redactor._ocr(frame)
            self.assertEqual(parsed["conf"], [9.5])
            self.assertEqual(parsed["left"], [30])
            self.assertEqual(parsed["text"], ["Synthetic"])
        for malformed in ["wrong\theader", header + "5\t1\n", valid.replace("9.5", "nan"),
                          valid.rstrip("\n") + "\textra\n"]:
            with patch("redaction.subprocess.run", return_value=SimpleNamespace(stdout=malformed.encode())):
                with self.assertRaisesRegex(RuntimeError, "^Local text recognition failed$"):
                    redactor._ocr(frame)


if __name__ == "__main__":
    unittest.main()
