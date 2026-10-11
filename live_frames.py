"""Continuous local capture; only certified pixels reach the publication slot."""
import hashlib
import json
import math
import logging
import struct
import subprocess
import threading
import time
from collections import OrderedDict
from io import BytesIO

from PIL import Image, ImageDraw


def rectangles(values, size, allow_id=False):
    if not isinstance(values, list) or len(values) > 4096:
        raise ValueError("Invalid capture geometry")
    result = []
    for value in values:
        keys = {"x", "y", "w", "h"}
        if not isinstance(value, dict) or set(value) not in (keys, keys | {"id"} if allow_id else keys):
            raise ValueError("Invalid capture geometry")
        if any(isinstance(value[k], bool) or not isinstance(value[k], (int, float))
               or not math.isfinite(value[k]) for k in keys):
            raise ValueError("Invalid capture geometry")
        x, y, w, h = (value[k] for k in ("x", "y", "w", "h"))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > size[0] or y+h > size[1]:
            raise ValueError("Capture geometry outside selected display")
        if "id" in value and (type(value["id"]) is not int or value["id"] < 0):
            raise ValueError("Invalid capture identity")
        result.append(dict(value))
    return result


class PixelGate:
    """Changing pixels are opaque until a matching local scan certifies them.

    The cache contains only redacted crops, keyed by pixel content, the entire
    owning window context and privacy epoch. OCR contextual rules are scoped to
    that same window. Old boxes never authorize new pixels at a new location.
    Scope windows stay private through moves to other displays.
    """
    def __init__(self, size, redactor, tile=128):
        self.size, self.redactor, self.tile = size, redactor, tile
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.private_ids = set()
        self.counts = {}
        self.boxes = [(x, y, min(x+tile, size[0]), min(y+tile, size[1]))
                      for y in range(0, size[1], tile) for x in range(0, size[0], tile)]

    def normalized(self, image, windows, masks=()):
        with self.lock:
            hard = [w for w in windows if w.get("id") in self.private_ids] + list(masks)
        if not hard:
            return image
        result = image.copy()
        draw = ImageDraw.Draw(result)
        for w in hard:
            draw.rectangle((math.floor(w["x"]), math.floor(w["y"]),
                            math.ceil(w["x"]+w["w"])-1, math.ceil(w["y"]+w["h"])-1), fill="black")
        return result

    def keys(self, image, windows, epoch):
        result = []
        contexts = [(w, hashlib.blake2b(image.crop((w["x"], w["y"], w["x"]+w["w"], w["y"]+w["h"])).tobytes(),
                                       digest_size=20).hexdigest()) for w in windows]
        background = hashlib.blake2b(image.tobytes(), digest_size=20).hexdigest()
        for x1, y1, x2, y2 in self.boxes:
            identities = [(w.get("id"), w["x"], w["y"], w["w"], w["h"], fingerprint)
                          for w, fingerprint in contexts if w["x"] < x2 and w["x"]+w["w"] > x1
                          and w["y"] < y2 and w["y"]+w["h"] > y1]
            # Any background portion shares the frame-wide background OCR group.
            covered = any(w["x"] <= x1 and w["y"] <= y1 and w["x"]+w["w"] >= x2
                          and w["y"]+w["h"] >= y2 for w in windows)
            digest = hashlib.blake2b(image.crop((x1,y1,x2,y2)).tobytes(), digest_size=20)
            digest.update(json.dumps([epoch, identities, None if covered else background], separators=(",", ":")).encode())
            result.append(digest.digest())
        return result

    def certify(self, image, windows, epoch):
        image = self.normalized(image, windows)
        keys = self.keys(image, windows, epoch)
        started = time.monotonic()
        processed, counts = self.redactor.redact(image, windows=windows)
        if processed.size != self.size:
            raise ValueError("Processed frame size changed")
        with self.lock:
            self.private_ids.update(getattr(self.redactor, "last_private_window_ids", ()))
            for box, key in zip(self.boxes, keys):
                self.cache[(box, key)] = processed.crop(box)
                self.cache.move_to_end((box, key))
            while len(self.cache) > len(self.boxes)*3:
                self.cache.popitem(last=False)
            self.counts = {**counts, "ocr_ms": round((time.monotonic()-started)*1000)}

    def render(self, image, windows, epoch, masks=()):
        if image.size != self.size:
            raise ValueError("Captured frame size changed")
        image = self.normalized(image, windows, masks)
        keys = self.keys(image, windows, epoch)
        safe = Image.new("RGB", self.size, "black")
        pending = 0
        with self.lock:
            for box, key in zip(self.boxes, keys):
                crop = self.cache.get((box, key))
                if crop is None:
                    pending += 1
                else:
                    safe.paste(crop, box)
            counts = dict(self.counts)
            hard = [w for w in windows if w.get("id") in self.private_ids] + list(masks)
        draw = ImageDraw.Draw(safe)
        for w in hard:
            draw.rectangle((math.floor(w["x"]), math.floor(w["y"]),
                            math.ceil(w["x"]+w["w"])-1, math.ceil(w["y"]+w["h"])-1), fill="black")
        return safe, {**counts, "pending_tiles": pending, "total_tiles": len(self.boxes),
                      "tracked_private_windows": len(hard)}


def read_exact(pipe, length):
    result = bytearray()
    while len(result) < length:
        chunk = pipe.read(length-len(result))
        if not chunk:
            raise EOFError("Local capture ended")
        result.extend(chunk)
    return bytes(result)


def read_packet(pipe, size):
    length = struct.unpack(">I", read_exact(pipe, 4))[0]
    if not 2 <= length <= 1_000_000:
        raise ValueError("Invalid capture metadata length")
    metadata = json.loads(read_exact(pipe, length))
    length = struct.unpack(">I", read_exact(pipe, 4))[0]
    if not 8 <= length <= min(128_000_000, size[0]*size[1]*8):
        raise ValueError("Invalid capture image length")
    payload = read_exact(pipe, length)
    if not isinstance(metadata, dict) or metadata.get("width") != size[0] or metadata.get("height") != size[1]:
        raise ValueError("Capture display differs")
    captured = metadata.get("capture_monotonic")
    if (type(captured) not in (int, float) or not math.isfinite(captured)
            or captured > time.monotonic()+0.1 or captured < 0
            or (metadata.get("fail_closed") is not True and time.monotonic()-captured > 3)):
        raise ValueError("Capture is not fresh")
    metadata["windows"] = rectangles(metadata.get("windows", []), size, allow_id=True)
    metadata["masks"] = rectangles(metadata.get("masks", []), size)
    if type(metadata.get("scene_epoch", 0)) is not int or type(metadata.get("fail_closed")) is not bool:
        raise ValueError("Invalid capture state")
    if metadata.get("pixel_format", "png") == "rgbx":
        if len(payload) != size[0]*size[1]*4:
            raise ValueError("Capture image differs")
        image = Image.frombytes("RGB", size, payload, "raw", "RGBX")
    elif metadata.get("pixel_format", "png") == "png":
        with Image.open(BytesIO(payload)) as source:
            if source.size != size or source.format != "PNG":
                raise ValueError("Capture image differs")
            image = source.convert("RGB")
    else:
        raise ValueError("Capture image differs")
    return image, metadata


def run_live(frames, redactor, display, stop, fps, root, policy_path, pause_path):
    size = (display["width"], display["height"])
    command = [str(root/".build/native-stream"),
               *(str(display[k]) for k in ("left", "top", "width", "height")),
               str(policy_path), str(int(fps)), "--raw"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    gate = PixelGate(size, redactor)
    condition = threading.Condition()
    latest, failure = [None], [False]

    def reader():
        try:
            while not stop.is_set():
                packet = read_packet(process.stdout, size)
                with condition:
                    latest[0] = packet
                    condition.notify_all()
        except Exception as error:
            known = {"Capture is not fresh": "stale-native-frame",
                     "Capture display differs": "display-mismatch",
                     "Capture geometry outside selected display": "geometry-outside-display",
                     "Invalid capture geometry": "invalid-geometry",
                     "Invalid capture state": "invalid-state",
                     "Local capture ended": "native-worker-ended"}
            logging.warning("Local capture refused (%s)", known.get(str(error), type(error).__name__))
            with condition:
                failure[0] = True
                latest[0] = None
                condition.notify_all()
            frames.pause("capture-failed")

    def scanner():
        previous = None
        while not stop.is_set():
            with condition:
                condition.wait(timeout=0.1)
                packet = latest[0]
            if packet is None or packet[1]["fail_closed"] or pause_path.exists():
                continue
            image, meta = packet
            image = gate.normalized(image, meta["windows"], meta["masks"])
            digest = hashlib.blake2b(image.tobytes(), digest_size=20).digest()
            identity = (digest, meta.get("scene_epoch", 0), json.dumps(meta["windows"], sort_keys=True))
            if identity == previous:
                continue
            try:
                gate.certify(image, meta["windows"], meta.get("scene_epoch", 0))
                previous = identity
            except Exception:
                frames.pause("processing-failed")
                stop.wait(0.5)

    reading = threading.Thread(target=reader, daemon=True)
    scanning = threading.Thread(target=scanner, daemon=True)
    reading.start()
    scanning.start()
    try:
        while not stop.is_set():
            started = time.monotonic()
            with condition:
                packet, failed = latest[0], failure[0]
            if failed:
                raise RuntimeError("Local capture ended")
            if packet is None or pause_path.exists():
                frames.pause()
            else:
                image, meta = packet
                if meta["fail_closed"] or started-meta["capture_monotonic"] > 1:
                    frames.pause("capture-unavailable")
                else:
                    safe, counts = gate.render(image, meta["windows"], meta.get("scene_epoch", 0), meta["masks"])
                    counts.update(capture_ms=round((started-meta["capture_monotonic"])*1000),
                                  processing_ms=round((time.monotonic()-started)*1000),
                                  capture_sequence=meta.get("sequence", 0))
                    if not stop.is_set() and not pause_path.exists():
                        frames.publish(safe, meta["capture_monotonic"], counts)
            stop.wait(max(0, 1/fps-(time.monotonic()-started)))
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stdout.close()
        frames.pause("stopped")
