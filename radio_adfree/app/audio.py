"""Real-time audio engine: decode live radio, delay it, mix legal filler with equal-power crossfades."""
from __future__ import annotations

import collections
import logging
import queue
import subprocess
import threading
import time

import numpy as np

from .filler import CH, SR, FillerPlayer, FillerLibrary, ffmpeg_bin

log = logging.getLogger("audio")

BLOCK = 1024                      # samples per mixer block (23.2 ms)
BLOCK_BYTES = BLOCK * CH * 2      # int16 stereo


class PCMBuffer:
    def __init__(self, max_seconds: float = 30.0):
        self._dq: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self.max_samples = int(max_seconds * SR)
        self.generation = 0
        self.dropped = 0

    def clear(self) -> None:
        import traceback
        with self._lock:
            self._dq.clear()
            self.generation += 1
            log.debug("PCMBuffer.clear -> gen %s | %s", self.generation,
                      " <- ".join(x.strip().splitlines()[-1] for x in traceback.format_stack(limit=4)[:-1]))

    def append(self, ts: float, pcm: np.ndarray) -> None:
        with self._lock:
            self._dq.append((ts, pcm))
            total = sum(len(p[1]) for p in self._dq)
            while total > self.max_samples and self._dq:
                _, old = self._dq.popleft()
                total -= len(old)
                self.dropped += 1

    def oldest_ts(self) -> float | None:
        with self._lock:
            return self._dq[0][0] if self._dq else None

    def newest_ts(self) -> float | None:
        with self._lock:
            return self._dq[-1][0] if self._dq else None

    def level(self) -> float:
        with self._lock:
            if len(self._dq) < 2:
                return 0.0
            return self._dq[-1][0] - self._dq[0][0]

    def pop_if_due(self, delay: float) -> tuple[float, np.ndarray] | None:
        now = time.monotonic()
        with self._lock:
            if not self._dq:
                return None
            if now - self._dq[0][0] < delay:
                return None
            return self._dq.popleft()

    def drop_old(self, delay: float, keep: float = 0.5) -> int:
        now = time.monotonic()
        n = 0
        with self._lock:
            while self._dq and now - self._dq[0][0] > delay + keep:
                self._dq.popleft()
                n += 1
        self.dropped += n
        return n


class AudioDecoder:
    """MP3 bytes -> PCM (44.1 kHz stereo float32), timestamped in live time."""

    def __init__(self, out_buffer: PCMBuffer, decode_latency: float = 0.0):
        self.out = out_buffer
        self.decode_latency = decode_latency
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._restart = threading.Event()
        self._in_q: queue.Queue[bytes | None] = queue.Queue(maxsize=300)
        self.samples = 0
        self.last_error = ""
        self.started_at = 0.0
        self.starts = 0

    def start(self) -> None:
        threading.Thread(target=self._supervisor, daemon=True).start()

    def feed(self, mp3: bytes) -> None:
        try:
            self._in_q.put_nowait(mp3)
        except queue.Full:
            try:
                self._in_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._in_q.put_nowait(mp3)
            except queue.Full:
                pass

    def restart(self) -> None:
        self._restart.set()
        try:
            self._in_q.put_nowait(None)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        self.restart()
        self._kill()

    def _kill(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass
        self.proc = None

    def _supervisor(self) -> None:
        while not self._stop.is_set():
            self._restart.clear()
            try:
                self._spawn()
                self._pump()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("decoder error: %s", exc)
            finally:
                self._kill()
            if self._stop.is_set():
                break
            time.sleep(0.25)
            if not self._restart.is_set():
                time.sleep(0.15)

    def _spawn(self) -> None:
        args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
                "-fflags", "+genpts+discardcorrupt", "-probesize", "32768",
                "-analyzeduration", "0", "-f", "mp3", "-i", "pipe:0",
                "-vn", "-ac", str(CH), "-ar", str(SR), "-f", "s16le",
                "-acodec", "pcm_s16le", "pipe:1"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        self.started_at = time.monotonic()
        self._anchor = None
        self._gen_samples = 0
        self.starts += 1
        threading.Thread(target=self._read_pcm, daemon=True).start()
        threading.Thread(target=self._drain_err, daemon=True).start()
        if self.starts < 6 or self.starts % 500 == 0:
            log.info("decoder started #%s", self.starts)

    def _drain_err(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        try:
            for line in proc.stderr:
                if line.strip():
                    err = line.decode("utf-8", "replace").strip()[-300:]
                    if not self.last_error:
                        log.warning("decoder stderr: %s", err)
                    self.last_error = err
        except Exception:
            pass

    def _read_pcm(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        gen = self.out.generation
        while not self._stop.is_set() and self.out.generation == gen:
            data = proc.stdout.read(BLOCK_BYTES)
            if not data:
                break
            if len(data) % (2 * CH):
                data = data[: len(data) - (len(data) % (2 * CH))]
            pcm = np.frombuffer(data, dtype=np.int16).reshape(-1, CH).astype(np.float32) / 32768.0
            if self._anchor is None:
                self._anchor = time.monotonic() - self.decode_latency
            ts = self._anchor + self._gen_samples / SR
            self._gen_samples += len(pcm)
            self.out.append(ts, pcm)
            self.samples += len(pcm)
        # EOF: signal restart unless the generation changed
        log.debug("decoder read loop exit: gen=%s/%s poll=%s err=%s", gen,
                  self.out.generation, proc.poll() if proc else None, self.last_error)
        if self.out.generation == gen and not self._stop.is_set():
            self._restart.set()

    def _pump(self) -> None:
        proc = self.proc
        if not proc or not proc.stdin:
            return
        while not self._stop.is_set() and not self._restart.is_set():
            try:
                item = self._in_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                proc.stdin.write(item)
                proc.stdin.flush()
            except Exception:
                self._restart.set()
                break


class AudioEncoder:
    """PCM -> MP3, one encoder shared by all HTTP clients."""

    def __init__(self, bitrate: int = 192):
        self.bitrate = bitrate
        self.proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.callbacks = []
        self.bytes_out = 0
        self.last_error = ""
        self.restarts = 0

    def add_callback(self, cb) -> None:
        self.callbacks.append(cb)

    def start(self) -> None:
        threading.Thread(target=self._supervisor, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except Exception:
            pass

    def feed(self, pcm: np.ndarray) -> None:
        proc = self.proc
        if not proc or not proc.stdin:
            return
        data = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        try:
            proc.stdin.write(data)
        except Exception:
            self.restarts += 1

    def _supervisor(self) -> None:
        while not self._stop.is_set():
            try:
                self._spawn()
                self._read_mp3()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("encoder error: %s", exc)
            finally:
                try:
                    if self.proc and self.proc.poll() is None:
                        self.proc.kill()
                except Exception:
                    pass
                self.proc = None
            if not self._stop.is_set():
                time.sleep(0.3)

    def _spawn(self) -> None:
        args = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(SR), "-ac", str(CH), "-i", "pipe:0",
                "-c:a", "libmp3lame", "-b:a", f"{self.bitrate}k", "-f", "mp3",
                "-flush_packets", "1", "pipe:1"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        threading.Thread(target=self._drain_err, daemon=True).start()
        log.info("encoder started (%s kbps)", self.bitrate)

    def _drain_err(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        try:
            for line in proc.stderr:
                if line.strip():
                    err = line.decode("utf-8", "replace").strip()[-300:]
                    if not self.last_error:
                        log.warning("decoder stderr: %s", err)
                    self.last_error = err
        except Exception:
            pass

    def _read_mp3(self) -> None:
        proc = self.proc
        if not proc or not proc.stdout:
            return
        while not self._stop.is_set():
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            self.bytes_out += len(chunk)
            for cb in self.callbacks:
                try:
                    cb(chunk)
                except Exception:
                    log.exception("encoder callback failed")


class AudioAnalyzer:
    """Lightweight rolling features (energy, centroid, BPM) used to match filler to the last songs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mono = collections.deque(maxlen=int(SR * 12))
        self.last = {"bpm": 0.0, "energy": 0.6, "centroid": 2000.0, "rms_db": -20.0}
        self._last_calc = 0.0

    def feed(self, pcm: np.ndarray) -> None:
        mono = pcm.mean(axis=1)
        with self.lock:
            self.mono.extend(mono.tolist())
        now = time.monotonic()
        if now - self._last_calc > 2.0:
            self._last_calc = now
            self._calc()

    def _calc(self) -> None:
        with self.lock:
            x = np.array(self.mono, dtype=np.float32)
        if len(x) < SR * 2:
            return
        # take the last 8 seconds
        x = x[-8 * SR:]
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        rms_db = 20 * np.log10(rms + 1e-9)
        spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) + 1e-9
        freqs = np.fft.rfftfreq(len(x), 1 / SR)
        centroid = float(np.sum(freqs * spec) / np.sum(spec))
        energy = float(np.clip((rms_db + 38) / 30, 0.05, 1.0))
        bpm = self._estimate_bpm(x)
        with self.lock:
            self.last = {"bpm": bpm, "energy": energy, "centroid": centroid, "rms_db": rms_db}

    @staticmethod
    def _estimate_bpm(x: np.ndarray) -> float:
        try:
            # onset envelope from spectral flux at ~86 Hz
            hop = 512
            win = 1024
            frames = (len(x) - win) // hop
            if frames < 16:
                return 0.0
            flux = np.zeros(frames)
            prev = None
            for i in range(frames):
                seg = x[i * hop:i * hop + win] * np.hanning(win)
                mag = np.abs(np.fft.rfft(seg))
                if prev is not None:
                    flux[i] = np.sum(np.maximum(mag - prev, 0))
                prev = mag
            flux = flux - flux.mean()
            ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
            fps = SR / hop
            lo = int(fps * 60 / 200)
            hi = int(fps * 60 / 60)
            if hi >= len(ac):
                return 0.0
            lag = lo + int(np.argmax(ac[lo:hi]))
            return float(60 * fps / lag) if lag else 0.0
        except Exception:
            return 0.0


class AdSchedule:
    """Ad intervals in live time. End may be open while the block is still running."""

    def __init__(self):
        self.lock = threading.Lock()
        self.intervals: list[dict] = []
        self.version = 0

    def clear(self) -> None:
        with self.lock:
            self.intervals.clear()
            self.version += 1

    def set_open(self, start: float, source: str, confidence: float = 0.7) -> dict:
        with self.lock:
            iv = {"start": start, "end": None, "source": source, "confidence": confidence}
            self.intervals.append(iv)
            self.version += 1
            return iv

    def close(self, end: float) -> None:
        with self.lock:
            for iv in self.intervals:
                if iv["end"] is None:
                    iv["end"] = max(iv["start"] + 1.0, end)
            self.intervals = [iv for iv in self.intervals if (iv["end"] or 1e18) > iv["start"]]
            self.version += 1

    def add(self, start: float, end: float, source: str, confidence: float = 0.8) -> dict:
        with self.lock:
            iv = {"start": start, "end": end, "source": source, "confidence": confidence}
            self.intervals.append(iv)
            self.intervals.sort(key=lambda x: x["start"])
            self.version += 1
            return iv

    def active(self) -> list[dict]:
        with self.lock:
            return [dict(iv) for iv in self.intervals]

    def open_interval(self) -> dict | None:
        with self.lock:
            for iv in reversed(self.intervals):
                if iv["end"] is None:
                    return dict(iv)
        return None

    def next_start(self, after: float) -> float | None:
        with self.lock:
            cand = [iv["start"] for iv in self.intervals if iv["start"] > after]
            return min(cand) if cand else None

    def prune(self, before: float) -> None:
        with self.lock:
            self.intervals = [iv for iv in self.intervals
                              if iv["end"] is None or iv["end"] > before]

    def gains(self, t: np.ndarray, xfade: float) -> tuple[np.ndarray, np.ndarray]:
        live = np.ones(len(t), dtype=np.float32)
        fill = np.zeros(len(t), dtype=np.float32)
        h = max(0.05, xfade / 2.0)
        with self.lock:
            intervals = [dict(iv) for iv in self.intervals]
        for iv in intervals:
            s = iv["start"]
            e = iv["end"]
            if e is None:
                # open-ended: fade in then keep filler
                gl = np.ones_like(t)
                gf = np.zeros_like(t)
                m = t >= s - h
                x = np.clip((t[m] - (s - h)) / (2 * h), 0, 1)
                gl[m] = np.cos(x * np.pi / 2)
                gf[m] = np.sin(x * np.pi / 2)
                live = np.minimum(live, gl)
                fill = np.maximum(fill, gf)
            else:
                if e <= s:
                    continue
                gl = np.ones_like(t)
                gf = np.zeros_like(t)
                m1 = (t >= s - h) & (t < s + h)
                x1 = np.clip((t[m1] - (s - h)) / (2 * h), 0, 1)
                gl[m1] = np.cos(x1 * np.pi / 2)
                gf[m1] = np.sin(x1 * np.pi / 2)
                m2 = (t >= s + h) & (t < e - h)
                gl[m2] = 0.0
                gf[m2] = 1.0
                m3 = (t >= e - h) & (t < e + h)
                x3 = np.clip((t[m3] - (e - h)) / (2 * h), 0, 1)
                gl[m3] = np.sin(x3 * np.pi / 2)
                gf[m3] = np.cos(x3 * np.pi / 2)
                live = np.minimum(live, gl)
                fill = np.maximum(fill, gf)
        return live, fill


class AudioEngine:
    def __init__(self, cfg, schedule: AdSchedule, analyzer: AudioAnalyzer,
                 filler_lib: FillerLibrary, learner=None):
        self.cfg = cfg
        self.schedule = schedule
        self.analyzer = analyzer
        self.filler_lib = filler_lib
        self.learner = learner
        self.buffer = PCMBuffer(max_seconds=cfg.buffer_max)
        self.decoder = AudioDecoder(self.buffer, decode_latency=cfg.decode_latency)
        self.encoder = AudioEncoder(cfg.output_bitrate)
        self.filler = FillerPlayer(filler_lib, xfade=cfg.crossfade)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.underruns = 0
        self.blocks = 0
        self.started = False
        self._last_fill_track = None
        self._fill_started = 0.0
        self._no_data_since = 0.0
        self._prepared_for: float | None = None
        self._last_preload = 0.0

    def start(self) -> None:
        self.decoder.start()
        self.encoder.start()
        threading.Thread(target=self._run, name="mixer", daemon=True).start()
        self.started = True

    def stop(self) -> None:
        self._stop.set()
        self.decoder.stop()
        self.encoder.stop()

    def feed_mp3(self, data: bytes) -> None:
        self.decoder.feed(data)

    def soft_reset(self) -> None:
        """Called after a stream reconnect: clear the delayed buffer, keep the encoder alive."""
        self.buffer.clear()
        self.filler.reset()
        self._prepared_for = None
        with self._lock:
            self.schedule.clear()

    def _prepare_filler(self, iv: dict) -> None:
        start = iv["start"] - self.cfg.crossfade / 2.0
        if self._prepared_for is not None and abs(self._prepared_for - start) < 0.2:
            return
        self._prepared_for = start
        feats = self.analyzer.last
        predicted = 150.0
        if self.learner:
            try:
                predicted = self.learner.predict_duration(self.cfg.station, time.time())
            except Exception:
                pass
        track = self.filler_lib.choose(predicted=predicted, prev_bpm=feats["bpm"],
                                       prev_energy=feats["energy"])
        if track:
            self._last_fill_track = track
            self._fill_started = start
            self.filler.prepare(track, start, predicted=predicted,
                                prev_bpm=feats["bpm"], prev_energy=feats["energy"],
                                target_db=feats.get("rms_db", -18.0))
            log.info("filler prepared: %s (%.0fs, %s bpm) for ad at %.1f",
                     track.title, track.duration, track.bpm, iv["start"])

    def _run(self) -> None:
        next_out = time.monotonic()
        block_dur = BLOCK / SR
        while not self._stop.is_set():
            delay = max(2.0, self.cfg.delay)
            now = time.monotonic()
            if now - self._last_preload > 60.0 and self.filler_lib.tracks:
                self._last_preload = now
                try:
                    feats = self.analyzer.last
                    predicted = 150.0
                    if self.learner:
                        predicted = self.learner.predict_duration(self.cfg.station, time.time())
                    t = self.filler_lib.choose(predicted=predicted, prev_bpm=feats["bpm"],
                                               prev_energy=feats["energy"])
                    if t:
                        threading.Thread(target=self.filler.preload, args=(t,), daemon=True).start()
                except Exception:
                    pass
            # The open ad interval may need a filler prepared before it starts.
            for iv in self.schedule.active():
                if iv["end"] is None or iv["end"] > now - delay - 2:
                    if now - delay >= iv["start"] - self.cfg.crossfade and \
                            (iv["end"] is None or iv["end"] > now - delay):
                        self._prepare_filler(iv)
                        break
            self.buffer.drop_old(delay, keep=0.8)
            item = self.buffer.pop_if_due(delay)
            if item is None:
                # underrun: emit silence, but only after the buffer has been empty for a moment
                level = self.buffer.level()
                if level < 0.05:
                    if self._no_data_since == 0:
                        self._no_data_since = now
                    if now - self._no_data_since > 0.35:
                        self.underruns += 1
                        self.encoder.feed(np.zeros((BLOCK, CH), dtype=np.float32))
                        next_out = now + block_dur
                        self._no_data_since = now
                else:
                    self._no_data_since = 0.0
                time.sleep(0.004)
                continue
            self._no_data_since = 0.0
            ts, live_pcm = item
            if len(live_pcm) != BLOCK:
                # keep blocks fixed-size for simple timing
                pad = np.zeros((BLOCK, CH), dtype=np.float32)
                pad[:min(BLOCK, len(live_pcm))] = live_pcm[:BLOCK]
                live_pcm = pad
            t = ts + np.arange(BLOCK, dtype=np.float64) / SR
            g_live, g_fill = self.schedule.gains(t, self.cfg.crossfade)
            out = live_pcm * g_live[:, None]
            if g_fill.max() > 0.0001:
                fill = self.filler.render(ts, BLOCK)
                out += fill * g_fill[:, None]
            self.analyzer.feed(live_pcm * g_live[:, None] if g_live.max() > 0.99 else live_pcm)
            self.encoder.feed(out.astype(np.float32))
            self.blocks += 1
            # pace to the audio clock; the buffer's delay defines the latency
            next_out += block_dur
            sleep = next_out - time.monotonic()
            if sleep > 0:
                time.sleep(min(sleep, 0.05))
            elif sleep < -0.5:
                next_out = time.monotonic()

    def status(self) -> dict:
        now = time.monotonic()
        oldest = self.buffer.oldest_ts()
        newest = self.buffer.newest_ts()
        return {
            "started": self.started,
            "buffer_seconds": round(self.buffer.level(), 3),
            "delay_target": self.cfg.delay,
            "delay_actual": round(now - oldest, 3) if oldest else None,
            "live_lag": round(now - newest, 3) if newest else None,
            "blocks": self.blocks,
            "underruns": self.underruns,
            "dropped": self.buffer.dropped,
            "encoder_bytes": self.encoder.bytes_out,
            "encoder_error": self.encoder.last_error,
            "decoder_error": self.decoder.last_error,
            "features": dict(self.analyzer.last),
            "filler": self.filler.active_track().as_dict() if self.filler.active_track() else None,
        }
