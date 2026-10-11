from io import BytesIO
import json
import struct
import time

import pytest
from PIL import Image

from live_frames import PixelGate, read_packet, rectangles, visible_private_regions


class SyntheticRedactor:
    last_private_window_ids = set()
    def redact(self, image, windows=()):
        safe = image.copy()
        safe.paste("black", (0, 0, 20, 20))
        return safe, {"masked_lines": 1}


def test_new_pixels_are_opaque_until_matching_scan_and_public_pixels_reappear():
    gate = PixelGate((512, 256), SyntheticRedactor(), tile=128)
    raw = Image.new("RGB", gate.size, "white")
    windows = [{"id": 1, "x": 0, "y": 0, "w": 256, "h": 256},
               {"id": 2, "x": 256, "y": 0, "w": 256, "h": 256}]
    assert gate.render(raw, windows, 1)[0].getpixel((50, 50)) == (0, 0, 0)
    gate.certify(raw, windows, 1)
    safe, counts = gate.render(raw, windows, 1)
    assert safe.getpixel((10, 10)) == (0, 0, 0)
    assert safe.getpixel((300, 50)) == (255, 255, 255)
    assert counts["pending_tiles"] == 0
    raw.paste("red", (260, 30, 320, 60))
    safe, counts = gate.render(raw, windows, 1)
    assert safe.getpixel((280, 40)) == (0, 0, 0)
    assert safe.getpixel((10, 220)) == (255, 255, 255)
    assert counts["pending_tiles"] > 0
    gate.certify(raw, windows, 1)
    assert gate.render(raw, windows, 1)[0].getpixel((280, 40)) == (255, 0, 0)
    assert gate.render(raw, windows, 2)[1]["pending_tiles"] == len(gate.boxes)


def test_private_window_moves_without_reusing_approval_and_remains_sticky():
    redactor = SyntheticRedactor()
    gate = PixelGate((512, 256), redactor)
    raw = Image.new("RGB", gate.size, "white")
    window = {"id": 42, "x": 0, "y": 0, "w": 128, "h": 128}
    redactor.last_private_window_ids = {42}
    gate.certify(raw, [window], 1)
    assert gate.render(raw, [window], 1)[0].getpixel((100, 100)) == (0, 0, 0)
    moved = {**window, "x": 256}
    assert gate.render(raw, [moved], 1)[0].getpixel((300, 100)) == (0, 0, 0)
    gate.render(raw, [], 1)
    # Moving to another display must not erase the window's privacy classification.
    assert gate.private_ids == {42}
    assert gate.render(raw, [moved], 1)[0].getpixel((300, 100)) == (0, 0, 0)


def test_certified_public_window_moves_with_exact_owning_context():
    gate=PixelGate((512,256),SyntheticRedactor())
    window={"id":42,"x":0,"y":0,"w":128,"h":128}
    raw=Image.new("RGB",gate.size,"grey")
    raw.paste("white",(0,0,128,128))
    gate.certify(raw,[window],1)
    moved={**window,"x":256}
    current=Image.new("RGB",gate.size,"grey")
    current.paste("white",(256,0,384,128))
    safe,_=gate.render(current,[moved],1)
    assert safe.getpixel((356,100))==(255,255,255)
    assert safe.getpixel((266,10))==(0,0,0)
    current.paste("red",(280,30,300,50))
    assert gate.render(current,[moved],1)[0].getpixel((356,100))==(0,0,0)


def test_native_masks_override_already_certified_pixels():
    gate = PixelGate((256, 256), SyntheticRedactor())
    raw = Image.new("RGB", gate.size, "white")
    gate.certify(raw, [], 1)
    masked = gate.render(raw, [], 1, [{"x": 100, "y": 100, "w": 50, "h": 50}])[0]
    assert masked.getpixel((120, 120)) == (0, 0, 0)


def test_public_foreground_stays_visible_over_private_background_window():
    public={"id":2,"x":100,"y":100,"w":100,"h":100}
    private={"id":1,"x":0,"y":0,"w":300,"h":300}
    regions=visible_private_regions([public,private],{1},{1})
    image=Image.new("RGB",(300,300),"white")
    from PIL import ImageDraw
    draw=ImageDraw.Draw(image)
    for r in regions:
        draw.rectangle((r["x"],r["y"],r["x"]+r["w"]-1,r["y"]+r["h"]-1),fill="black")
    assert image.getpixel((150,150))==(255,255,255)
    assert image.getpixel((99,150))==(0,0,0)


def test_protocol_rejects_stale_wrong_geometry_and_truncated_frame():
    out = BytesIO()
    Image.new("RGB", (100, 100)).save(out, format="PNG")
    def packet(**values):
        meta = {"capture_monotonic": time.monotonic(), "width": 100, "height": 100,
                "masks": [], "windows": [], "fail_closed": False, **values}
        encoded = json.dumps(meta).encode()
        return BytesIO(struct.pack(">I", len(encoded))+encoded+struct.pack(">I", len(out.getvalue()))+out.getvalue())
    assert read_packet(packet(), (100, 100))[0].size == (100, 100)
    assert read_packet(packet(capture_monotonic=0, fail_closed=True), (100, 100))[1]["fail_closed"]
    for value in (packet(capture_monotonic=0), packet(width=99), packet(fail_closed="no"),
                  BytesIO(b"\x00\x00\x00\x80{}")):
        with pytest.raises((ValueError, EOFError)):
            read_packet(value, (100, 100))
    with pytest.raises(ValueError):
        rectangles([{"x": 0, "y": 0, "w": float("nan"), "h": 1}], (100, 100))


def test_distant_recovery_label_invalidates_cached_body_in_production_redactor():
    from unittest.mock import patch
    from redaction import Redactor
    from tests.test_redaction import ocr
    raw = Image.new("RGB", (512, 512), "white")
    redactor = Redactor(mode="selective")
    gate = PixelGate(raw.size, redactor)
    windows = [{"id": 1, "x": 0, "y": 0, "w": 512, "h": 512}]
    before = ocr(["PUBLIC RELEASE NOTES", "alpha beta gamma", "delta epsilon zeta", "eta theta iota"])
    before["top"] = [30, 110, 190, 270]
    with patch.object(redactor, "_ocr", return_value=before):
        gate.certify(raw, windows, 1)
    assert gate.render(raw, windows, 1)[0].getpixel((40, 275)) == (255, 255, 255)
    changed = raw.copy()
    changed.paste("red", (30, 30, 100, 50))
    after = {k:list(v) for k,v in before.items()}
    after["text"][0] = "Seed phrase"
    with patch.object(redactor, "_ocr", return_value=after):
        expected, _ = redactor.redact(changed, windows=windows)
        assert expected.getpixel((40, 275)) == (0, 0, 0)
        assert gate.render(changed, windows, 1)[0].getpixel((40, 275)) == (0, 0, 0)
        gate.certify(changed, windows, 1)
    assert gate.render(changed, windows, 1)[0].getpixel((40, 275)) == (0, 0, 0)


def test_fixed_manual_region_overrides_a_translated_public_window_cache():
    from unittest.mock import patch
    from redaction import Redactor
    from tests.test_redaction import ocr
    redactor = Redactor(mode="selective", regions=[{"x": .5, "y": 0, "w": .25, "h": .5}])
    gate = PixelGate((512, 256), redactor)
    window = {"id": 42, "x": 0, "y": 0, "w": 128, "h": 128}
    raw = Image.new("RGB", gate.size, "grey")
    raw.paste("white", (0, 0, 128, 128))
    with patch.object(redactor, "_ocr", return_value=ocr([])):
        gate.certify(raw, [window], 1)
        moved = {**window, "x": 256}
        current = Image.new("RGB", gate.size, "grey")
        current.paste("white", (256, 0, 384, 128))
        expected, _ = redactor.redact(current, windows=[moved])
    assert expected.getpixel((300, 50)) == (0, 0, 0)
    assert gate.render(current, [moved], 1)[0].getpixel((300, 50)) == (0, 0, 0)
    assert gate.render(raw, [window], 1)[0].getpixel((100, 100)) == (255, 255, 255)


def test_unexcluded_private_window_cannot_be_unmasked_by_transparent_foreground():
    public = {"id": 2, "x": 100, "y": 100, "w": 100, "h": 100}
    private = {"id": 1, "x": 0, "y": 0, "w": 300, "h": 300}
    assert visible_private_regions([public, private], {1}) == [
        {"x": 0, "y": 0, "w": 300, "h": 300}]


def test_stale_packet_is_refused_then_fresh_capture_resumes_on_same_worker(tmp_path):
    import threading
    from live_frames import run_live
    executable = tmp_path / '.build' / 'native-stream'
    executable.parent.mkdir()
    payload = BytesIO()
    Image.new('RGB', (128, 96), 'white').save(payload, format='PNG')
    executable.write_text('''#!/usr/bin/env python3
import json,struct,sys,time
payload=%r
for index in range(40):
 m={'width':128,'height':96,'capture_monotonic':time.monotonic()-(5 if index==0 else 0),
    'windows':[],'masks':[],'fail_closed':False,'sequence':index}
 b=json.dumps(m).encode()
 sys.stdout.buffer.write(struct.pack('>I',len(b))+b+struct.pack('>I',len(payload))+payload)
 sys.stdout.buffer.flush()
 time.sleep(.05)
''' % payload.getvalue())
    executable.chmod(0o700)
    class Frames:
        def __init__(self): self.sources=[]; self.pauses=[]; self.ready=threading.Event()
        def publish(self, image, captured, counts):
            self.sources.append(captured)
            if image.getpixel((60,60)) == (255,255,255): self.ready.set()
        def pause(self, reason='paused'): self.pauses.append(reason)
    frames=Frames(); stop=threading.Event(); errors=[]
    started=time.monotonic()
    def execute():
        try: run_live(frames,SyntheticRedactor(),{'left':0,'top':0,'width':128,'height':96},
                      stop,15,tmp_path,tmp_path/'policy.json',tmp_path/'pause')
        except Exception as error: errors.append(error)
    worker=threading.Thread(target=execute)
    worker.start()
    try:
        assert frames.ready.wait(4), 'Same capture worker did not recover'
    finally:
        stop.set(); worker.join(timeout=4)
    assert not worker.is_alive()
    assert not errors
    assert 'capture-unavailable' in frames.pauses
    assert frames.sources and all(source >= started for source in frames.sources)


def test_public_window_survives_unrelated_epoch_but_current_private_mask_wins():
    gate = PixelGate((256,256),SyntheticRedactor())
    windows=[{'id':42,'x':0,'y':0,'w':256,'h':256}]
    raw=Image.new('RGB',gate.size,'white')
    gate.certify(raw,windows,1)
    safe,counts=gate.render(raw,windows,2)
    assert safe.getpixel((100,100))==(255,255,255)
    assert counts['pending_tiles']==len(gate.boxes)  # Background/tile scope still expires.
    mask=[{'x':0,'y':0,'w':256,'h':256}]
    assert gate.render(raw,windows,2,mask)[0].getpixel((100,100))==(0,0,0)
    gate.private_ids.add(42)
    assert gate.render(raw,windows,2)[0].getpixel((100,100))==(0,0,0)


def test_context_elimination_preserves_production_ownership_and_all_hard_masks():
    from unittest.mock import patch
    from redaction import Redactor
    from geometry import owning_windows
    from tests.test_redaction import ocr
    image=Image.new('RGB',(512,512),'white')
    # Union coverage, never opacity: two first-containing foreground boxes own
    # all of window3. Window4 has an uncovered strip and must retain its context.
    windows=[{'id':1,'x':0,'y':0,'w':256,'h':256},
             {'id':2,'x':256,'y':0,'w':256,'h':256},
             {'id':3,'x':0,'y':0,'w':512,'h':256},
             {'id':4,'x':0,'y':0,'w':512,'h':512}]
    owners=owning_windows(windows)
    assert [w['id'] for w in owners]==[1,2,4]
    rows=ocr(['PUBLIC RELEASE NOTES','alice@example.invalid','Challenger private scope'])
    rows['left']=[20,280,20];rows['top']=[20,100,350]
    rows['width']=[200,200,250]
    redactor=Redactor(mode='selective')
    with patch.object(redactor,'_ocr',return_value=rows):
        expected,_=redactor.redact(image,windows=windows)
        original_ids=redactor.last_private_window_ids
        compact,_=redactor.redact(image,windows=owners)
    assert expected.tobytes()==compact.tobytes()
    assert original_ids==redactor.last_private_window_ids==(4,)
    gate=PixelGate(image.size,redactor)
    gate.private_ids.add(3)  # Even an unowned private background remains covered.
    with patch.object(redactor,'_ocr',return_value=ocr([])):
        gate.certify(image,windows,1)
    safe,_=gate.render(image,windows,2)
    assert safe.getpixel((100,100))==(0,0,0)
    assert safe.getpixel((300,100))==(0,0,0)


def test_pem_window_stays_private_after_header_scrolls_out_of_view():
    from unittest.mock import patch
    from redaction import Redactor
    from tests.test_redaction import ocr
    gate=PixelGate((512,256),Redactor(mode='selective'))
    windows=[{'id':8,'x':0,'y':0,'w':256,'h':256}]
    raw=Image.new('RGB',gate.size,'white')
    with patch.object(gate.redactor,'_ocr',return_value=ocr(['-----BEGIN PRIVATE KEY-----'])):
        gate.certify(raw,windows,1)
    assert gate.private_ids=={8}
    # An unusual body need not match a token regex after its header disappears.
    changed=raw.copy();changed.paste('red',(20,40,220,80))
    moved=[{**windows[0],'x':256}]
    assert gate.render(changed,moved,2)[0].getpixel((300,50))==(0,0,0)
