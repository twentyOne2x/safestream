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
    def test_numbered_recovery_grid_hides_entire_owning_window(self):
        redactor = Redactor(mode="selective")
        lines = ["Recovery phrase", "1. apple 2. cloud 3. cedar",
                 "4. delta 5. eagle 6. fern", "7. garden 8. hill 9. ivy",
                 "10. jazz 11. kite 12. lemon"]
        windows = [{"id": 42, "x": 0, "y": 0, "w": 400, "h": 300}]
        with patch.object(redactor, "_ocr", return_value=ocr(lines)):
            safe, counts = redactor.redact(Image.new("RGB", (800, 300), "white"), windows=windows)
        self.assertEqual(counts["whole_window_regions"], 1)
        self.assertEqual(redactor.last_private_window_ids, (42,))
        self.assertEqual(safe.getpixel((40, 195)), (0, 0, 0))
        self.assertEqual(safe.getpixel((700, 195)), (255, 255, 255))

    def run_frame(self, lines, **options):
        image = Image.new("RGB", (800, max(300, len(lines) * 40 + 60)), "white")
        windows = options.pop("windows", ())
        redactor = Redactor(**options)
        with patch.object(redactor, "_ocr", return_value=ocr(lines)):
            safe, counts = redactor.redact(image, windows=windows)
        return image, safe, counts

    def test_selective_sensitive_categories(self):
        lines = ["Alice Martin", "ALICE MARTIN", "42 rue du Test", "8000 Zurich", "Musterstrasse 12",
                 "alice@example.invalid", "+41 79 123 45 67", "4111 1111 1111 1111",
                 "3782 822463 10005", "**** 1234", "Visa ending in 9876",
                 "09/2028", "CVV 123", "/work/Família-Privée/docs"]
        _, safe, counts = self.run_frame(lines, mode="selective", terms=["familia-privee", "Alice Martin"])
        self.assertEqual(counts["masked_lines"], len(lines))
        for i in range(len(lines)):
            self.assertEqual(safe.getpixel((40, 35 + i * 40)), (0, 0, 0))

    def test_protective_low_confidence_without_mask_cap(self):
        lines = [f"ordinary text {i}" for i in range(61)]
        _, safe, counts = self.run_frame(lines)
        self.assertEqual(counts["masked_lines"], 61)
        self.assertEqual(safe.getpixel((40, 35 + 60 * 40)), (0, 0, 0))

    def test_input_stays_intact_and_safe_text_remains_selective(self):
        image, safe, counts = self.run_frame(["ordinary text", "Alice Martin"], mode="selective", terms=["Alice Martin"])
        self.assertEqual(image.getpixel((40, 75)), (255, 255, 255))
        self.assertEqual(safe.getpixel((40, 75)), (0, 0, 0))
        self.assertEqual(safe.getpixel((40, 35)), (255, 255, 255))
        self.assertEqual(counts["masked_lines"], 1)

    def test_public_headings_and_configuration_remain_visible(self):
        lines = ["Project Dashboard", "PUBLIC RELEASE NOTES", "Address Validation Guide",
                 "Full Name Formatting Guide", "API Key Documentation", "PORT=8080",
                 "BUILD_MODE=production", "Version 2.4.1"]
        _, safe, counts = self.run_frame(lines, mode="selective")
        self.assertEqual(counts["masked_lines"], 0)
        for i in range(len(lines)):
            self.assertEqual(safe.getpixel((40, 35 + i * 40)), (255, 255, 255))

    def test_handles_and_credential_values_at_low_confidence(self):
        lines = ["@synthetic.challenger", "instagram.com/synthetic_account",
                 "https://www.instagram.com/synthetic.handle/", "password: synthetic-value",
                 "OTP=123456", "2FA: 654321", "SERVICE_API_KEY=synthetic-key",
                 "public_key: synthetic-public-key", "private key: synthetic-private-key",
                 "a" * 64, "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijk"]
        _, _, counts = self.run_frame(lines, mode="selective")
        self.assertEqual(counts["masked_lines"], len(lines))

    def test_field_names_and_separate_partial_secrets(self):
        for label, value in [("Full name", "ALICE MARTIN"), ("Password", "short-fragment"),
                             ("2FA", "123 456"), ("API key", "short-fragment"),
                             ("CVV", "123"), ("Challenger handle", "synthetic_handle")]:
            with self.subTest(label=label):
                _, _, counts = self.run_frame([label, value], mode="selective")
                self.assertEqual(counts["masked_lines"], 2)

    def test_inline_field_does_not_mask_next_public_row(self):
        _, _, counts = self.run_frame(["Password: synthetic", "Project Dashboard"], mode="selective")
        self.assertEqual(counts["masked_lines"], 1)

    def test_multiline_pem_and_seed_context(self):
        lines = ["-----BEGIN PRIVATE KEY-----", "YWJjZA==", "ZWZnaA==",
                 "-----END PRIVATE KEY-----", "PUBLIC RELEASE NOTES",
                 "Seed phrase", "alpha beta gamma delta", "epsilon zeta eta theta",
                 "API Key Documentation"]
        _, safe, counts = self.run_frame(lines, mode="selective")
        self.assertEqual(counts["masked_lines"], 7)
        self.assertEqual(safe.getpixel((40, 35 + 4 * 40)), (255, 255, 255))
        self.assertEqual(safe.getpixel((40, 35 + 8 * 40)), (255, 255, 255))

    def test_field_does_not_reach_another_column(self):
        redactor = Redactor(mode="selective")
        data = ocr(["Password", "Project Dashboard"])
        data["left"][1] = 500
        with patch.object(redactor, "_ocr", return_value=data):
            safe, counts = redactor.redact(Image.new("RGB", (800, 300), "white"))
        self.assertEqual(counts["masked_lines"], 1)
        self.assertEqual(safe.getpixel((510, 75)), (255, 255, 255))

    def test_partial_public_pem_stops_at_public_heading(self):
        _, safe, counts = self.run_frame(["-----BEGIN PUBLIC KEY-----", "YWJjZA==",
                                         "PUBLIC RELEASE NOTES", "Project Dashboard"],
                                        mode="selective")
        self.assertEqual(counts["masked_lines"], 2)
        self.assertEqual(safe.getpixel((40, 115)), (255, 255, 255))

    def test_private_scope_masks_frontmost_window_and_surrounding_state(self):
        windows = [{"x": 20, "y": 20, "w": 300, "h": 110},
                   {"x": 0, "y": 0, "w": 800, "h": 300}]
        image, safe, counts = self.run_frame(["Challenger", "unlabeled surrounding state",
                                            "PUBLIC RELEASE NOTES", "Project Dashboard"],
                                           mode="selective", windows=windows)
        self.assertEqual(counts["whole_window_regions"], 1)
        self.assertEqual(safe.getpixel((310, 125)), (0, 0, 0))
        self.assertEqual(safe.getpixel((40, 155)), (255, 255, 255))
        self.assertEqual(safe.getpixel((500, 75)), (255, 255, 255))
        self.assertEqual(image.getpixel((310, 125)), (255, 255, 255))
        self.assertEqual(set(counts), {"recognized_lines", "masked_lines", "manual_regions", "whole_window_regions"})

    def test_private_scope_defaults_and_static_mode(self):
        window = [{"x": 0, "y": 0, "w": 800, "h": 300}]
        for term in ["Challenger", "Instagram", "Gmail", "LastPass", "1Password", "mail.google.com"]:
            with self.subTest(term=term):
                _, safe, counts = self.run_frame([term], mode="selective", windows=window)
                self.assertEqual(counts["whole_window_regions"], 1)
                self.assertEqual(safe.getpixel((700, 250)), (0, 0, 0))
        _, safe, counts = self.run_frame(["Gmail", "PUBLIC RELEASE NOTES"], mode="selective")
        self.assertEqual(counts["whole_window_regions"], 0)
        self.assertEqual(safe.getpixel((700, 250)), (255, 255, 255))

    def test_private_scope_without_containing_window_masks_only_row(self):
        _, safe, counts = self.run_frame(["Codex Challenger", "ordinary text"], mode="selective",
                                        windows=[{"x": 500, "y": 200, "w": 200, "h": 100}])
        self.assertEqual(counts["whole_window_regions"], 0)
        self.assertEqual(counts["masked_lines"], 1)
        self.assertEqual(safe.getpixel((40, 35)), (0, 0, 0))
        self.assertEqual(safe.getpixel((799, 299)), (255, 255, 255))

    def test_moved_window_and_offscreen_bounds_are_clipped(self):
        redactor = Redactor(mode="selective")
        data = ocr(["Gmail"])
        data["left"] = [400]
        data["top"] = [160]
        with patch.object(redactor, "_ocr", return_value=data):
            safe, counts = redactor.redact(Image.new("RGB", (800, 300), "white"),
                                          windows=[{"x": 380.5, "y": 150.5, "w": 600, "h": 600}])
        self.assertEqual(counts["whole_window_regions"], 1)
        self.assertEqual(safe.getpixel((799, 299)), (0, 0, 0))
        self.assertEqual(safe.getpixel((379, 250)), (255, 255, 255))
        self.assertEqual(safe.getpixel((500, 149)), (255, 255, 255))
        _, safe, _ = self.run_frame(["Gmail"], mode="selective",
                                   windows=[{"x": -100, "y": -100, "w": 500, "h": 350}])
        self.assertEqual(safe.getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(safe.getpixel((400, 250)), (255, 255, 255))

    def test_invalid_window_metadata_is_rejected_before_ocr(self):
        invalid = [None, "window", [{"x": 0, "y": 0, "w": 0, "h": 20}],
                   [{"x": True, "y": 0, "w": 20, "h": 20}],
                   [{"x": 0, "y": float("nan"), "w": 20, "h": 20}],
                   [{"x": 0, "y": 0, "w": 20, "h": float("inf")}],
                   [{"x": 0, "y": 0, "w": 20, "h": 20, "id": True}],
                   [{"x": 0, "y": 0, "w": 20, "h": 20, "id": "synthetic"}],
                   [{"x": 0, "y": 0, "w": 20, "h": 20, "title": "synthetic"}]]
        redactor = Redactor(mode="selective")
        with patch.object(redactor, "_ocr") as recognition:
            for windows in invalid:
                with self.assertRaisesRegex(ValueError, "^Invalid window metadata$"):
                    redactor.redact(Image.new("RGB", (800, 300)), windows=windows)
            recognition.assert_not_called()

    def test_public_scope_text_does_not_trigger_window_mask(self):
        _, _, counts = self.run_frame(["PUBLIC RELEASE NOTES", "Project Dashboard", "Instagrammable Guide"],
                                     mode="selective", windows=[{"x": 0, "y": 0, "w": 800, "h": 300}])
        self.assertEqual(counts["whole_window_regions"], 0)
        self.assertEqual(counts["masked_lines"], 0)

    def test_window_mask_state_has_numeric_bounds_and_ids_only(self):
        redactor = Redactor(mode="selective")
        frame = Image.new("RGB", (800, 300), "white")
        with patch.object(redactor, "_ocr", return_value=ocr(["Gmail", "LastPass"])):
            redactor.redact(frame, windows=[{"x": 20, "y": 20, "w": 300, "h": 110, "id": 42}])
        self.assertEqual(redactor.last_window_masks, ({"x": 20, "y": 20, "w": 300, "h": 110},))
        self.assertEqual(redactor.last_private_window_ids, (42,))
        with patch.object(redactor, "_ocr", return_value=ocr(["Project Dashboard"])):
            redactor.redact(frame, windows=[{"x": 20, "y": 20, "w": 300, "h": 110, "id": 42}])
        self.assertEqual(redactor.last_window_masks, ())
        self.assertEqual(redactor.last_private_window_ids, ())

    def test_window_context_does_not_spill_fields_seed_pem_or_terms(self):
        windows = [{"x": 0, "y": 0, "w": 350, "h": 300, "id": 10},
                   {"x": 400, "y": 0, "w": 350, "h": 300, "id": 20}]
        for label, value, terms in [("Password", "ordinary text", ()),
                                    ("Seed phrase", "alpha beta gamma", ()),
                                    ("-----BEGIN PRIVATE KEY-----", "YWJjZA==", ()),
                                    ("private CAFÉ", "família project", ("cafe familia",))]:
            redactor = Redactor(mode="selective", terms=terms)
            data = ocr([label, value])
            data["left"][1] = 430
            with patch.object(redactor, "_ocr", return_value=data):
                safe, counts = redactor.redact(Image.new("RGB", (800, 300), "white"), windows=windows)
            self.assertEqual(safe.getpixel((440, 75)), (255, 255, 255))
            self.assertEqual(counts["masked_lines"], 1 if not terms else 0)

    def test_distant_seed_label_and_interleaved_other_window(self):
        redactor = Redactor(mode="selective")
        data = ocr(["Seed phrase", "PUBLIC RELEASE NOTES", "alpha beta gamma delta"])
        data["left"][1] = 430
        data["top"][2] = 230
        with patch.object(redactor, "_ocr", return_value=data):
            safe, counts = redactor.redact(Image.new("RGB", (800, 400), "white"),
                                          windows=[{"x": 0, "y": 0, "w": 350, "h": 400},
                                                   {"x": 400, "y": 0, "w": 350, "h": 400}])
        self.assertEqual(counts["masked_lines"], 2)
        self.assertEqual(safe.getpixel((40, 235)), (0, 0, 0))
        self.assertEqual(safe.getpixel((440, 75)), (255, 255, 255))

    def test_frontmost_owner_wins_over_smaller_obscured_window(self):
        redactor = Redactor(mode="selective")
        with patch.object(redactor, "_ocr", return_value=ocr(["Gmail"])):
            safe, counts = redactor.redact(Image.new("RGB", (800, 300), "white"),
                                          windows=[{"x": 0, "y": 0, "w": 700, "h": 250, "id": 1},
                                                   {"x": 20, "y": 20, "w": 300, "h": 110, "id": 2}])
        self.assertEqual(counts["whole_window_regions"], 1)
        self.assertEqual(redactor.last_private_window_ids, (1,))
        self.assertEqual(safe.getpixel((650, 200)), (0, 0, 0))

    def test_multiline_unicode_term(self):
        _, safe, counts = self.run_frame(["private CAFÉ", "família project"], mode="selective",
                                         terms=["cafe familia"])
        self.assertEqual(counts["masked_lines"], 2)
        self.assertEqual(safe.getpixel((40, 35)), (0, 0, 0))
        self.assertEqual(safe.getpixel((40, 75)), (0, 0, 0))

    def test_native_parser_refuses_bad_bounds_without_raw_fallback(self):
        import json
        import subprocess
        redactor = Redactor(backend="vision")
        for row in [{"text": "private", "x": -1, "y": 0, "w": 10, "h": 10},
                    {"text": "private", "x": 0, "y": 0, "w": 101, "h": 10},
                    {"text": "private", "x": 0, "y": 0, "w": True, "h": 10}]:
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps([row]).encode())
            with patch("redaction.subprocess.run", return_value=result), self.assertRaises(RuntimeError):
                redactor.redact(Image.new("RGB", (100, 100), "white"))

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
            self.assertEqual(call.call_args.kwargs["env"]["OMP_THREAD_LIMIT"], "1")
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
