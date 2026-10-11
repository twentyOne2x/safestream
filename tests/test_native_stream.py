import json
import os
import select
import struct
import subprocess
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(os.environ.get("SAFESTREAM_NATIVE_HELPER", ROOT / ".build/native-stream"))


def frame(data):
    size = struct.unpack(">I", data[:4])[0]
    metadata = json.loads(data[4:4 + size])
    offset = 4 + size
    png_size = struct.unpack(">I", data[offset:offset + 4])[0]
    assert len(data) == offset + 4 + png_size
    payload = data[offset + 4:]
    if metadata["pixel_format"] == "rgbx":
        assert len(payload) == metadata["width"] * metadata["height"] * 4
        return metadata, Image.frombytes("RGB", (metadata["width"], metadata["height"]), payload, "raw", "RGBX")
    return metadata, Image.open(BytesIO(payload)).convert("RGB")


@pytest.mark.parametrize("argument", ["--self-test", "--self-test-raw"])
def test_synthetic_frame_masks_before_output_and_uses_top_left_coordinates(argument):
    result = subprocess.run([str(HELPER), argument], capture_output=True,
                            check=True, timeout=5)
    assert result.stderr == b""
    metadata, image = frame(result.stdout)
    assert image.size == (128, 96)
    assert metadata["masks"] == [{"x": 7, "y": 11, "w": 31, "h": 23}]
    assert metadata["private_window_count"] == 1
    assert metadata["capture_monotonic"] <= metadata["emitted_monotonic"]
    assert metadata["scene_epoch"] == 0
    assert not metadata["fail_closed"]
    assert image.getpixel((8, 12)) == (14, 17, 20)
    assert image.getpixel((37, 33)) == (14, 17, 20)
    assert image.getpixel((8, 60)) == (255, 255, 255)
    assert image.getpixel((38, 34)) == (255, 255, 255)
    assert all(not isinstance(value, str) for key, value in metadata.items() if key != "pixel_format")
    assert set(metadata) == {"capture_monotonic", "capture_started_monotonic", "emitted_monotonic", "sequence",
        "width", "height", "masks", "windows", "pixel_format", "excluded_window_ids", "screenshot_heartbeat", "capture_error_code", "private_window_count", "fail_closed",
        "transition_guard", "metadata_age_ms", "scene_epoch", "idle_replay", "snapshots_ms", "encode_ms"}


def test_invalid_arguments_and_policy_fail_generically(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"private_apps": [], "private_titles": []}')
    broken = tmp_path / "broken.json"
    broken.write_text("PRIVATE SENTINEL")
    for args in [[], ["0", "0", "0", "100", str(policy)],
                 ["sentinel", "0", "100", "100", str(policy)],
                 ["0", "0", "32769", "100", str(policy)],
                 ["0", "0", "100", "100", str(broken)],
                 ["0", "0", "100", "100", str(policy), "31"]]:
        result = subprocess.run([str(HELPER), *args], capture_output=True, timeout=5)
        assert result.returncode != 0
        assert result.stdout == b""
        assert result.stderr == b"Local display stream failed\n"


@pytest.mark.parametrize("change", ["geometry", "identity", "missing"])
def test_idle_cached_pixels_reject_changed_or_unbound_metadata(change):
    result = subprocess.run([str(HELPER), "--self-test-idle", change],
                            capture_output=True, check=True, timeout=5)
    assert result.stderr == b""
    metadata, image = frame(result.stdout)
    assert metadata["idle_replay"]
    assert metadata["fail_closed"]
    assert metadata["capture_monotonic"] == 10  # Preserve original source age.
    assert metadata["masks"] == [{"x": 0, "y": 0, "w": 128, "h": 96}]
    assert set(image.getdata()) == {(14, 17, 20)}


def test_idle_same_subject_can_refresh_only_with_bound_compositor_idle_event():
    result = subprocess.run([str(HELPER), "--self-test-idle", "same"],
                            capture_output=True, check=True, timeout=5)
    metadata, image = frame(result.stdout)
    assert not metadata["fail_closed"]
    assert metadata["capture_monotonic"] == 20
    assert metadata["idle_replay"]
    assert image.getpixel((80, 80)) == (255, 255, 255)


@pytest.mark.parametrize("opacity", ["opaque", "transparent", "unexcluded"])
def test_private_geometry_clips_only_with_compositor_exclusion_receipt(opacity):
    result = subprocess.run([str(HELPER), "--self-test-occlusion", opacity],
                            capture_output=True, check=True, timeout=5)
    metadata, image = frame(result.stdout)
    assert image.getpixel((10, 10)) == (14, 17, 20)
    expected = (255, 255, 255) if opacity == "opaque" else (14, 17, 20)
    assert image.getpixel((50, 50)) == expected
    if opacity == "opaque":
        assert not any(r["x"] <= 50 < r["x"] + r["w"] and
                       r["y"] <= 50 < r["y"] + r["h"] for r in metadata["masks"])


@pytest.mark.skipif(os.environ.get("SAFESTREAM_NATIVE_LIVE_TEST") != "1",
                    reason="Explicit local synthetic-window capture check")
def test_live_synthetic_private_window_is_dark(tmp_path):
    import mss

    # Only this harmless test app is opened; no existing application is touched.
    source = tmp_path / "synthetic.swift"
    source.write_text('''import AppKit
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 320, height: 200),
                      styleMask: [.titled], backing: .buffered, defer: false)
window.title = "SAFESTREAM SYNTHETIC PRIVATE TEST"
window.level = .floating
window.backgroundColor = .white
window.orderFrontRegardless()
app.run()
''')
    executable = tmp_path / "synthetic"
    subprocess.run(["swiftc", str(source), "-o", str(executable)], check=True,
                   capture_output=True, timeout=30)
    window = subprocess.Popen([str(executable)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    capture = None
    try:
        time.sleep(0.5)
        with mss.mss() as screens:
            monitor = screens.monitors[1]
        policy = tmp_path / "policy.json"
        policy.write_text(json.dumps({"private_apps": [],
            "private_titles": ["SAFESTREAM SYNTHETIC PRIVATE TEST"]}))
        capture = subprocess.Popen([str(HELPER), str(monitor["left"]),
            str(monitor["top"]), str(monitor["width"]), str(monitor["height"]),
            str(policy), "15", "--raw"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, SAFESTREAM_NATIVE_TEST_QUIET_STREAM="1"))
        received = b""
        deadline = time.monotonic() + 25
        passed = False
        last_metadata = None
        healthy = []
        while time.monotonic() < deadline:
            if not select.select([capture.stdout], [], [], 0.2)[0]:
                continue
            received += os.read(capture.stdout.fileno(), 1024 * 1024)
            while len(received) >= 4:
                size = struct.unpack(">I", received[:4])[0]
                if len(received) < size + 8:
                    break
                png_size = struct.unpack(">I", received[size + 4:size + 8])[0]
                total = size + 8 + png_size
                if len(received) < total:
                    break
                metadata, image = frame(received[:total])
                last_metadata = metadata
                assert metadata["capture_error_code"] == 0, metadata["capture_error_code"]
                received = received[total:]
                # Primary display origin and AppKit bottom-left coordinates.
                point = (150, monitor["height"] - 150)
                if not metadata["fail_closed"]:
                    assert any(r["x"] <= point[0] < r["x"] + r["w"] and
                               r["y"] <= point[1] < r["y"] + r["h"]
                               for r in metadata["masks"])
                    assert image.getpixel(point) == (14, 17, 20)
                    assert "SAFESTREAM SYNTHETIC" not in json.dumps(metadata)
                    healthy.append(metadata)
                    assert metadata["screenshot_heartbeat"]  # Real SDK backstop, not streaming fixtures.
                    if metadata["screenshot_heartbeat"]:
                        assert not metadata["idle_replay"]
                        assert metadata["capture_started_monotonic"] <= metadata["capture_monotonic"] <= metadata["emitted_monotonic"]
                        assert metadata["emitted_monotonic"] - metadata["capture_monotonic"] <= 0.5
                    passed = len(healthy) >= 12 and (healthy[-1]["capture_monotonic"] - healthy[0]["capture_monotonic"] >= 2)
                    if passed:
                        break
            if passed:
                break
        assert passed, ("No healthy masked synthetic frame arrived", capture.poll(), len(healthy), json.dumps({k: v for k, v in (last_metadata or {}).items() if k not in ("masks", "windows")}))
    finally:
        for process in (capture, window):
            if process is not None:
                process.terminate()
                process.wait(timeout=5)
