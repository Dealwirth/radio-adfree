"""Legal filler music library and a two-deck crossfading PCM player."""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger("filler")

SR = 44100
CH = 2
FEEL_ENERGY = {
    "aggressive": 1.0, "intense": 0.95, "driving": 0.82, "epic": 0.8,
    "grooving": 0.65, "dark": 0.7, "bouncy": 0.6, "bright": 0.55,
    "uplifting": 0.55, "relaxed": 0.3, "somber": 0.35, "mysterious": 0.45,
}
AUDIO_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac"}


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/usr/bin/ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE") or shutil.which("ffprobe") or "/usr/bin/ffprobe"


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(
            [ffprobe_bin(), "-v", "error", "-show_entries",
             "format=duration:format_tags=title,artist,genre,bpm",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        d = json.loads(out.stdout or "{}").get("format", {})
        tags = {k.lower(): v for k, v in (d.get("tags") or {}).items()}
        return {"duration": float(d.get("duration") or 0), "tags": tags}
    except Exception:
        return {"duration": 0, "tags": {}}


def decode_pcm(path: Path) -> np.ndarray | None:
    """Decode an audio file to int16 stereo 44.1 kHz in memory."""
    try:
        p = subprocess.run(
            [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", str(CH),
             "-ar", str(SR), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
            capture_output=True, timeout=180,
        )
        if p.returncode != 0 or len(p.stdout) < SR * 2:
            log.warning("decode failed %s: %s", path.name, p.stderr[-300:])
            return None
        raw = p.stdout[: len(p.stdout) - (len(p.stdout) % (2 * CH))]
        return np.frombuffer(raw, dtype=np.int16).reshape(-1, CH).copy()
    except Exception as exc:
        log.warning("decode error %s: %s", path.name, exc)
        return None


class Track:
    __slots__ = ("id", "path", "title", "artist", "duration", "bpm", "feel",
                 "energy", "license", "attribution", "source", "rms_db")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


class FillerLibrary:
    def __init__(self, cfg, learner=None):
        self.cfg = cfg
        self.learner = learner
        self.tracks: list[Track] = []
        self._lock = threading.Lock()
        self.manifest = json.loads((Path(__file__).with_name("filler_manifest.json")).read_text())

    def ensure_ready(self) -> None:
        self.cfg.filler_cache.mkdir(parents=True, exist_ok=True)
        try:
            Path(self.cfg.filler_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        # Ship a few tracks so the first run works even without internet.
        bundled = Path(__file__).with_name("bundled_filler")
        if bundled.exists():
            for p in bundled.iterdir():
                if p.suffix.lower() in AUDIO_EXT:
                    dest = self.cfg.filler_cache / p.name
                    if not dest.exists() or dest.stat().st_size < 200_000:
                        try:
                            shutil.copy2(p, dest)
                        except Exception:
                            pass
        if self.cfg.filler_download:
            try:
                self._download_missing()
            except Exception:
                log.exception("filler download failed")
        self.rescan()

    def _download_missing(self) -> None:
        cache = self.cfg.filler_cache
        existing = {p.name for p in cache.iterdir() if p.suffix.lower() in AUDIO_EXT}
        free_mb = shutil.disk_usage(cache).free // (1024 * 1024)
        if free_mb < self.cfg.filler_min_free_mb:
            log.warning("not enough disk for filler downloads (%s MB)", free_mb)
            return
        have = 0
        for item in self.manifest:
            if have >= self.cfg.filler_max_tracks:
                break
            dest = cache / item["file"]
            if dest.name in existing and dest.stat().st_size > 500_000:
                have += 1
                continue
            log.info("downloading filler: %s", item["title"])
            try:
                req = urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
                    shutil.copyfileobj(r, f)
                if dest.stat().st_size < 200_000:
                    dest.unlink(missing_ok=True)
                    continue
                have += 1
            except Exception as exc:
                log.warning("download failed %s: %s", item["title"], exc)
                dest.unlink(missing_ok=True)

    def rescan(self) -> None:
        cache = self.cfg.filler_cache
        user = Path(self.cfg.filler_dir)
        files: list[tuple[Path, dict | None]] = []
        manifest_by_file = {m["file"]: m for m in self.manifest}
        for d, known in ((cache, True), (user, False)):
            if not d.exists():
                continue
            for p in sorted(d.rglob("*")):
                if p.suffix.lower() in AUDIO_EXT and p.is_file():
                    files.append((p, manifest_by_file.get(p.name) if known else None))
        tracks = []
        for p, man in files:
            info = probe(p)
            dur = info["duration"] or (man["length"] if man else 0)
            if dur < 30:
                continue
            tags = info["tags"]
            feel = (man["feel"] if man else None) or \
                [f.strip() for f in tags.get("genre", "").split(",") if f.strip()]
            bpm = int(man["bpm"]) if man and man.get("bpm") else 0
            if not bpm:
                try:
                    bpm = int(float(tags.get("bpm", 0)))
                except Exception:
                    bpm = 0
            energy = 0.6
            if feel:
                energy = float(np.mean([FEEL_ENERGY.get(f.lower(), 0.6) for f in feel]))
            title = (man["title"] if man else tags.get("title")) or p.stem
            artist = (man["artist"] if man else tags.get("artist")) or ("Kevin MacLeod" if man else "Eigene Musik")
            tracks.append(Track(
                id=p.stem, path=str(p), title=title, artist=artist, duration=dur,
                bpm=bpm, feel=feel, energy=energy,
                license=(man["license"] if man else "Eigene Datei"),
                attribution=(man["attribution"] if man else ""),
                source=("incompetech" if man else "user"), rms_db=0.0,
            ))
        with self._lock:
            self.tracks = tracks
        log.info("filler library: %d tracks", len(tracks))

    def choose(self, predicted: float = 150.0, prev_bpm: float = 0.0,
               prev_energy: float = 0.6) -> Track | None:
        with self._lock:
            tracks = list(self.tracks)
        if not tracks:
            return None
        recent = {}
        if self.learner:
            try:
                recent = self.learner.recent_filler_plays(minutes=90)
            except Exception:
                recent = {}
        best = None
        best_score = 1e9
        for t in tracks:
            dur_pen = 0.0
            if t.duration < predicted + 15:
                dur_pen = (predicted + 15 - t.duration) / 60.0
            elif t.duration > predicted + 120:
                dur_pen = (t.duration - predicted - 120) / 120.0
            bpm_pen = abs((t.bpm or prev_bpm or 120) - (prev_bpm or 120)) / 55.0 if prev_bpm else 0.0
            energy_pen = abs(t.energy - prev_energy) * 1.4
            rec_pen = 2.5 if recent.get(t.id) else 0.0
            score = dur_pen * 1.2 + bpm_pen + energy_pen + rec_pen + random.random() * 0.15
            if score < best_score:
                best, best_score = t, score
        return best

    def get(self, track_id: str) -> Track | None:
        with self._lock:
            return next((t for t in self.tracks if t.id == track_id), None)


class FillerPlayer:
    """Two-deck filler player. Sample 0 of the first track is aligned to start_ts."""

    def __init__(self, library: FillerLibrary, xfade: float = 2.0):
        self.library = library
        self.xfade = xfade
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.next: dict | None = None
        self.generation = 0
        self._cache: dict[str, tuple[np.ndarray, float]] = {}
        self._cache_order: list[str] = []
        self._cache_lock = threading.Lock()

    def _decode(self, track: Track):
        key = str(track.path)
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
        pcm = decode_pcm(Path(track.path))
        if pcm is None:
            return None, 0.0
        rms = float(np.sqrt(np.mean((pcm.astype(np.float32) / 32768.0) ** 2)) + 1e-9)
        rms_db = 20.0 * np.log10(rms)
        with self._cache_lock:
            if key not in self._cache:
                self._cache[key] = (pcm, rms_db)
                self._cache_order.append(key)
                while len(self._cache_order) > 4:
                    old = self._cache_order.pop(0)
                    self._cache.pop(old, None)
        return pcm, rms_db

    def preload(self, track: Track | None) -> None:
        if track:
            self._decode(track)

    def reset(self) -> None:
        with self.lock:
            self.current = None
            self.next = None
            self.generation += 1

    def prepare(self, track: Track, start_ts: float, predicted: float = 150.0,
                prev_bpm: float = 0.0, prev_energy: float = 0.6,
                target_db: float = -18.0) -> None:
        if track is None:
            return
        gen = self.generation

        def load():
            pcm, rms_db = self._decode(track)
            if pcm is None:
                return
            with self.lock:
                if gen != self.generation:
                    return
                gain = float(np.clip(10.0 ** ((target_db - rms_db) / 20.0), 0.05, 2.0))
                self.current = {"track": track, "pcm": pcm, "start_ts": start_ts,
                                "predicted": predicted, "prev_bpm": prev_bpm,
                                "prev_energy": prev_energy, "next_requested": False,
                                "gain": gain, "target_db": target_db, "rms_db": rms_db}
                self.next = None
                log.info("filler gain: %s rms=%.1f dB target=%.1f dB gain=%.2f",
                         track.title, rms_db, target_db, gain)

        threading.Thread(target=load, daemon=True).start()

    def _load_next(self, deck: dict) -> None:
        track = self.library.choose(
            predicted=max(60.0, deck["track"].duration * 0.7),
            prev_bpm=deck["track"].bpm or deck["prev_bpm"],
            prev_energy=deck["track"].energy,
        )
        if not track:
            return
        pcm, rms_db = self._decode(track)
        if pcm is None:
            return
        with self.lock:
            if self.current is deck and self.next is None:
                gain = float(np.clip(10.0 ** ((deck.get("target_db", -18.0) - rms_db) / 20.0), 0.05, 2.0))
                self.next = {"track": track, "pcm": pcm, "start_ts": None,
                             "next_requested": False, "gain": gain,
                             "target_db": deck.get("target_db", -18.0), "rms_db": rms_db}

    def active_track(self) -> Track | None:
        with self.lock:
            return self.current["track"] if self.current else None

    def render(self, ts: float, n: int) -> np.ndarray:
        out = np.zeros((n, CH), dtype=np.float32)
        xf = int(self.xfade * SR)
        with self.lock:
            cur = self.current
            nxt = self.next
        if cur is None:
            return out

        pos = int(round((ts - cur["start_ts"]) * SR))
        total = len(cur["pcm"])

        if nxt is not None and pos >= total:
            with self.lock:
                if self.current is cur:
                    self.current = {**nxt, "start_ts": ts - (pos - total) / SR,
                                    "predicted": cur["predicted"],
                                    "prev_bpm": cur["track"].bpm or cur["prev_bpm"],
                                    "prev_energy": cur["track"].energy,
                                    "next_requested": False,
                                    "target_db": cur.get("target_db", -18.0),
                                    "gain": nxt.get("gain", 1.0)}
                    self.next = None
            return self.render(ts, n)

        if nxt is None and total - pos < xf + 8 * SR and not cur.get("next_requested"):
            cur["next_requested"] = True
            threading.Thread(target=self._load_next, args=(cur,), daemon=True).start()
        if nxt is not None and nxt.get("start_ts") is None:
            nxt["start_ts"] = cur["start_ts"] + max(0, total - xf) / SR

        t = ts + np.arange(n, dtype=np.float64) / SR
        for deck, is_next in ((cur, False), (nxt, True)):
            if deck is None:
                continue
            s = deck["start_ts"]
            idx = np.round((t - s) * SR).astype(np.int64)
            valid = (idx >= 0) & (idx < len(deck["pcm"]))
            if not valid.any():
                continue
            if is_next:
                g = np.clip((t - s) / self.xfade, 0, 1)
                gain = np.sin(g * np.pi / 2)
            elif nxt is not None and nxt.get("start_ts") is not None:
                ns = nxt["start_ts"]
                g = np.clip((t - ns) / self.xfade, 0, 1)
                gain = np.cos(g * np.pi / 2)
            else:
                gain = np.ones(n)
            out[valid] += ((deck["pcm"][idx[valid]].astype(np.float32) / 32768.0)
                           * deck.get("gain", 1.0)) * gain[valid, None]
        return out
