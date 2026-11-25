#!/usr/bin/env python3
"""Measure the real output delay against a live-edge ICY capture.

Usage (from the repository root):
    PYTHONPATH=. FFMPEG=ffmpeg python3 radio_adfree/tests/measure_delay.py http://127.0.0.1:8097

Takes ~35 s. Requires numpy + the project package and ffmpeg on PATH.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from radio_adfree.app.icy import ICYStream  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8097"
LIVE = "https://streams.radiobob.de/bob-national/mp3-192/streams.radiobob.de/"
DUR = 34


def main():
    q: queue.Queue = queue.Queue(maxsize=4)
    stream = ICYStream(LIVE, q)

    def rec_live():
        stream.start()
        with open("/tmp/raf_live.mp3", "wb") as f:
            t = time.monotonic()
            while time.monotonic() - t < DUR:
                try:
                    d = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if d is None:
                    break
                f.write(d)
        stream.stop()

    def rec_out():
        with urllib.request.urlopen(BASE + "/stream.mp3", timeout=40) as r, \
                open("/tmp/raf_out.mp3", "wb") as f:
            t = time.monotonic()
            while time.monotonic() - t < DUR:
                d = r.read(8192)
                if not d:
                    break
                f.write(d)

    ths = [threading.Thread(target=rec_live), threading.Thread(target=rec_out)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    for src, dst in (("/tmp/raf_live.mp3", "/tmp/raf_live.wav"),
                     ("/tmp/raf_out.mp3", "/tmp/raf_out.wav")):
        subprocess.run([os.environ.get("FFMPEG", "ffmpeg"), "-hide_banner", "-loglevel", "error",
                        "-y", "-i", src, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
                       check=True)

    def load(fn):
        with wave.open(fn, "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768

    a, b = load("/tmp/raf_live.wav"), load("/tmp/raf_out.wav")
    a, b = a[6 * 16000:], b[6 * 16000:]

    def env(x, hop=800):
        n = len(x) // hop
        return np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-9)

    ea, eb = env(a), env(b)
    ea = (ea - ea.mean()) / (ea.std() + 1e-9)
    eb = (eb - eb.mean()) / (eb.std() + 1e-9)
    corr = np.correlate(eb, ea, mode="full")
    lags = np.arange(-len(ea) + 1, len(eb))
    mask = (lags >= 0) & (lags <= 400)
    best = lags[mask][np.argmax(corr[mask])]
    print(f"best lag: {best * 0.05:.2f} s (correlation {corr[mask].max() / len(ea):.3f})")


if __name__ == "__main__":
    main()
