import json
import threading
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import patch

import pytest
from PIL import Image

from safestream import MAX_AGE, SafeFrames, make_server


def test_publication_starts_blank_and_expires_exact_processed_frame():
    clock = [10.0]
    frames = SafeFrames((300, 200), clock=lambda: clock[0])
    assert frames.read()[1]["state"] == "paused"
    processed = Image.new("RGB", (300, 200), "black")
    frames.publish(processed, 10.0, {"masked_lines": 60})
    processed.paste("red", (0, 0, 300, 200))
    data, status = frames.read()
    assert Image.open(BytesIO(data)).getpixel((10, 10)) == (0, 0, 0)
    assert status["masked_lines"] == 60
    clock[0] += MAX_AGE + 0.01
    assert frames.read()[0] == frames.blank
    assert frames.read()[1]["state"] == "paused"
    frames.pause("processing-failed")
    assert frames.read()[0] == frames.blank


def test_failed_encoding_retains_only_last_safe_frame():
    frames = SafeFrames((300, 200))
    with patch("safestream.encode", side_effect=RuntimeError("synthetic failure")):
        with pytest.raises(RuntimeError):
            frames.publish(Image.new("RGB", (300, 200), "red"), 1, {})
    assert frames.read()[0] == frames.blank


def test_http_only_exposes_safe_slot_and_numeric_status():
    frames = SafeFrames((300, 200))
    server = make_server(frames, 0, "fixture-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/fixture-token/frame") as response:
            assert response.read() == frames.blank
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Safestream-State"] == "paused"
        for path in ("/raw", "/frame", "/private-terms.txt", "/fixture-token/../private-terms.txt"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + path)
            assert error.value.code == 404
        request = urllib.request.Request(base + "/fixture-token/frame", headers={"Host": "attacker.invalid"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        assert error.value.code == 403
        with urllib.request.urlopen(base + "/fixture-token/status") as response:
            status = json.loads(response.read())
            assert set(status) == {"state", "age_seconds", "sequence"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
