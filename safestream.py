#!/usr/bin/env python3
"""Publish only fully processed frames to a loopback OBS Browser Source."""
import argparse
import hashlib
import json
import logging
import os
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from PIL import Image, ImageDraw
from redaction import Redactor

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".codex" / "STATE.json"
PAUSE = ROOT / ".codex" / "PAUSE"
MAX_AGE = 5.0


def encode(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def slate(size, message="Privacy paused"):
    image = Image.new("RGB", size, "#101114")
    draw = ImageDraw.Draw(image)
    draw.text((size[0] // 2, size[1] // 2), message, fill="#e3e5e8", anchor="mm", font_size=28)
    return encode(image)


class SafeFrames:
    """The sole publication slot. It never accepts a captured, unprocessed image."""
    def __init__(self, size, clock=time.monotonic):
        self.lock = threading.Lock()
        self.clock = clock
        self.blank = slate(size)
        self.data = self.blank
        self.captured_at = 0.0
        self.status = "starting"
        self.sequence = 0
        self.counts = {}

    def publish(self, processed, captured_at, counts):
        # Encoding completes before replacing the one immutable publication object.
        data = encode(processed)
        with self.lock:
            self.data, self.captured_at = data, captured_at
            self.status, self.counts = "redacted", dict(counts)
            self.sequence += 1

    def pause(self, reason="paused"):
        with self.lock:
            self.data, self.status, self.counts = self.blank, reason, {}
            self.captured_at = 0.0

    def read(self):
        with self.lock:
            age = max(0.0, self.clock() - self.captured_at)
            healthy = self.status == "redacted" and age <= MAX_AGE
            return (self.data if healthy else self.blank,
                    {"state": self.status if healthy else "paused",
                     "age_seconds": round(age, 3) if healthy else None,
                     "sequence": self.sequence, **(self.counts if healthy else {})})


def viewer():
    # No controls or raw fallback. Server and browser independently expire frames.
    return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Safestream · redacted output</title><style>
html,body{{margin:0;background:#101114;color:#e3e5e8;width:100%;height:100%;overflow:hidden}}
canvas{{width:100%;height:100%;object-fit:contain;display:block}}
</style></head><body><canvas></canvas><script>
const c=document.querySelector('canvas'),ctx=c.getContext('2d');let last=0;
function blank(){{c.width=innerWidth;c.height=innerHeight;ctx.fillStyle='#101114';
ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#e3e5e8';ctx.textAlign='center';
ctx.font='28px sans-serif';ctx.fillText('Privacy paused',c.width/2,c.height/2)}}
blank();setInterval(()=>{{if(performance.now()-last>{MAX_AGE * 1000})blank()}},100);
async function tick(){{const started=performance.now();try{{
const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),1800);
let r;try{{r=await fetch(location.pathname+'/frame',{{cache:'no-store',signal:abort.signal}})}}finally{{clearTimeout(timer)}};
if(!r.ok||r.headers.get('X-Safestream-State')!=='redacted')throw Error();
const age=Number(r.headers.get('X-Safestream-Age'))*1000;
const b=await createImageBitmap(await r.blob());
if(age+performance.now()-started>{MAX_AGE * 1000}){{b.close();throw Error()}}
c.width=b.width;c.height=b.height;ctx.drawImage(b,0,0);b.close();last=started-age;
}}catch(e){{last=0;blank()}}setTimeout(tick,180)}}tick();
</script></body></html>""".encode()


def make_server(frames, port, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            # Defend loopback content against DNS rebinding and accidental exposure.
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(403)
                return
            route = urlsplit(self.path).path
            base = "/" + token
            headers = {}
            if route == base:
                data, kind = viewer(), "text/html; charset=utf-8"
            elif route == base + "/frame":
                data, status = frames.read()
                kind = "image/png"
                headers = {"X-Safestream-State": status["state"],
                           "X-Safestream-Age": str(status["age_seconds"] or 0)}
            elif route == base + "/status":
                _, status = frames.read()
                data, kind = json.dumps(status).encode(), "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            for key, value in {"Content-Type": kind, "Content-Length": str(len(data)),
                               "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                               "X-Content-Type-Options": "nosniff", **headers}.items():
                self.send_header(key, value)
            if kind.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src blob:")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def policy(path):
    data = json.loads(path.read_text()) if path.exists() else {}
    mode = data.get("mode", "protective")
    terms = data.get("terms", [])
    terms_file = ROOT / "private-terms.txt"
    if terms_file.exists():
        terms += [x.strip() for x in terms_file.read_text().splitlines()
                  if x.strip() and not x.lstrip().startswith("#")]
    return Redactor(mode=mode, terms=terms, regions=data.get("regions", [])), mode


def capture_loop(frames, redactor, monitor, stop, fps, fixture=None):
    import mss
    with mss.MSS() as capture:
        previous, processed, counts = None, None, None
        while not stop.is_set():
            started = time.monotonic()
            try:
                if PAUSE.exists():
                    frames.pause()
                    stop.wait(0.2)
                    continue
                if fixture:
                    image = Image.open(fixture).convert("RGB")
                else:
                    shot = capture.grab(capture.monitors[monitor])
                    image = Image.frombytes("RGB", shot.size, shot.rgb)
                digest = hashlib.blake2b(image.tobytes(), digest_size=16).digest()
                if digest != previous:
                    processed, counts = redactor.redact(image)
                    previous = digest
                if not stop.is_set() and not PAUSE.exists():
                    frames.publish(processed, started, counts)
            except Exception as error:
                frames.pause("processing-failed")
                # The exception text could contain OCR/provider data. Type only.
                logging.warning("Privacy paused (%s)", type(error).__name__)
                previous = None
                stop.wait(1)
            stop.wait(max(0, 1 / fps - (time.monotonic() - started)))
        frames.pause("stopped")


def save_state(data):
    STATE.parent.mkdir(exist_ok=True)
    temporary = STATE.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out, indent=2)
    temporary.replace(STATE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--policy", type=Path, default=ROOT / "local-policy.json")
    parser.add_argument("--fixture", type=Path, help="Synthetic image for local verification")
    parser.add_argument("--setup-obs", action="store_true")
    args = parser.parse_args()
    if not 0.1 <= args.fps <= 10 or not 1024 <= args.port <= 65535:
        parser.error("Use FPS 0.1..10 and a local port 1024..65535")
    import mss
    with mss.MSS() as capture:
        if not 1 <= args.monitor < len(capture.monitors):
            parser.error("Display number unavailable")
        size = (capture.monitors[args.monitor]["width"], capture.monitors[args.monitor]["height"])
    if args.fixture:
        with Image.open(args.fixture) as image:
            size = image.size
    redactor, mode = policy(args.policy)
    policy_digest = hashlib.sha256(json.dumps({"mode": mode, "terms": redactor.terms,
                                               "regions": redactor.regions, "fps": args.fps},
                                              sort_keys=True).encode()).hexdigest()
    prior = json.loads(STATE.read_text()) if STATE.exists() else {}
    token = prior.get("token") or secrets.token_urlsafe(24)
    if not isinstance(token, str) or not 30 <= len(token) <= 64 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in token):
        raise ValueError("Invalid saved local output identity")
    url = f"http://127.0.0.1:{args.port}/{token}"
    if (args.setup_obs and not args.fixture and prior.get("capture_kind") == "live"
            and prior.get("url") == url and prior.get("monitor") == args.monitor
            and prior.get("policy_digest") == policy_digest and prior.get("size") == list(size)):
        try:
            with urlopen(url + "/status", timeout=1) as response:
                current = json.loads(response.read(4096))
            if current.get("state") == "redacted" and isinstance(current.get("sequence"), int):
                print(f"Safestream is already running. Preview: {url}", flush=True)
                return
        except Exception:
            pass
    frames = SafeFrames(size)
    # Bind before spawning capture; a second owner cannot run on the same port.
    server = make_server(frames, args.port, token)
    stop = threading.Event()
    def terminate(*_):
        frames.pause("stopped")
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, terminate)
    signal.signal(signal.SIGTERM, terminate)
    worker = threading.Thread(target=capture_loop,
                              args=(frames, redactor, args.monitor, stop, args.fps, args.fixture), daemon=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        state = {"pid": os.getpid(), "token": token, "url": url, "monitor": args.monitor,
                 "mode": mode, "size": size, "started_at": time.time(),
                 "capture_kind": "fixture" if args.fixture else "live", "policy_digest": policy_digest}
        if args.setup_obs:
            from obs_setup import install_output
            state["obs"] = install_output(url, size, prior)
        save_state(state)
        print(f"Safestream: {mode} mode. Preview: {url}", flush=True)
        print("OCR may miss text. Protective mode hides recognized text; imagery needs private regions.", flush=True)
        worker.start()
        while not stop.wait(0.5):
            if not worker.is_alive():
                terminate()
    finally:
        frames.pause("stopped")
        stop.set()
        server.shutdown()
        server.server_close()
        if worker.is_alive():
            worker.join(timeout=9)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Safestream stopped safely ({type(error).__name__}).", flush=True)
        raise SystemExit(1) from None
