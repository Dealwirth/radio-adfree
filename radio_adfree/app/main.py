"""Application orchestration."""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import queue
import threading
import time
import urllib.request
from pathlib import Path

import aiohttp
from aiohttp import web

from . import ha, stations
from .audio import AudioAnalyzer, AudioEngine, AdSchedule
from .config import Config
from .detect import RADetector, RBDetector
from .filler import FillerLibrary
from .icy import ICYStream
from .learn import Learner, TZ
from .mqtt import MQTTClient
from .web import StreamHub, create_app

log = logging.getLogger("app")
VERSION = "1.1.7"


class App:
    version = VERSION

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.loop: asyncio.AbstractEventLoop | None = None
        self.hub: StreamHub | None = None
        self.schedule = AdSchedule()
        self.analyzer = AudioAnalyzer()
        self.learner = Learner(cfg.db_path, cfg)
        self.filler_lib = FillerLibrary(cfg, self.learner)
        self.engine = AudioEngine(cfg, self.schedule, self.analyzer, self.filler_lib, self.learner)
        self.detector = None
        self.icy: ICYStream | None = None
        self.audio_q: queue.Queue | None = None
        self._pump_stop = threading.Event()
        self.mqtt = MQTTClient(self, VERSION)
        self._station = stations.get(cfg.station)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ingress = ""
        self._ingress_url = ""
        self._host_ip = ""
        self._last_ad_id = None
        self._ads_today = 0
        self._today = datetime.datetime.now(TZ).date()
        self._volume = float(cfg.volume or 0.8)
        self._playing = False
        self._detector_state: dict = {}
        self._icy_stats: dict = {}
        self._ad_rec_fh = None
        self._ad_rec_path = ""
        self._last_reconnects = 0
        self._detector_generation = 0

    # ---------------- lifecycle ----------------
    def start_background(self) -> None:
        self._refresh_ha()
        threading.Thread(target=self._filler_boot, daemon=True).start()
        self.engine.start()
        self._start_station()
        self.mqtt.start()
        threading.Thread(target=self._status_loop, daemon=True).start()
        threading.Thread(target=self._ha_loop, daemon=True).start()
        log.info("Radio AdFree %s started (station=%s, delay=%.1fs)", VERSION,
                 self.cfg.station, self.cfg.delay)

    def stop(self) -> None:
        self._stop.set()
        self._pump_stop.set()
        if self.icy:
            self.icy.stop()
        try:
            if self._ad_rec_fh:
                self._ad_rec_fh.close()
        except Exception:
            pass
        if self.detector:
            self.detector.stop()
        self.engine.stop()
        try:
            self.mqtt._pub("radio_adfree/availability", "offline")
            self.mqtt.stop()
        except Exception:
            pass

    def _filler_boot(self) -> None:
        try:
            self.filler_lib.ensure_ready()
        except Exception:
            log.exception("filler boot failed")

    # ---------------- station / ICY ----------------
    def _start_station(self) -> None:
        st = self._station
        if self.icy:
            self.icy.stop()
        try:
            if self._ad_rec_fh:
                self._ad_rec_fh.close()
        except Exception:
            pass
        self._pump_stop.set()
        self.schedule.clear()
        self.engine.soft_reset()
        self.engine.decoder.restart()
        self._detector_state = {}
        self._icy_stats = {}
        self._last_reconnects = 0

        # detector
        if self.detector:
            self.detector.stop()
        on_state = self._on_detector_state
        self.detector = (RADetector(self.cfg, self.schedule, self.learner, on_state)
                         if st["group"] == "Rock Antenne"
                         else RBDetector(self.cfg, self.schedule, self.learner, on_state))
        self.detector.set_station(st)
        self.detector.start()

        # audio feed
        self.audio_q = queue.Queue(maxsize=3)
        self._pump_stop.clear()
        threading.Thread(target=self._audio_pump, daemon=True).start()
        self.icy = ICYStream(st["stream"], self.audio_q, on_meta=self._on_meta,
                             on_status=self._on_icy_status)
        self.icy.start()

    def _audio_pump(self) -> None:
        """Feed MP3 bytes at the real stream bitrate.

        Icecast sends a burst of buffered audio on connect. Without pacing the
        decoder runs seconds ahead and the true output delay grows. The ICY
        reader keeps reading at live speed (for metadata); the queue is short
        and drops old audio, so we stay near the live edge.
        """
        q = self.audio_q
        next_feed = time.monotonic()
        while not self._pump_stop.is_set():
            try:
                data = q.get(timeout=0.5)
            except queue.Empty:
                next_feed = time.monotonic()
                continue
            if data is None:
                break
            try:
                bitrate = int(self._icy_stats.get("bitrate") or 0) * 1000 or 192000
            except Exception:
                bitrate = 192000
            dur = len(data) * 8.0 / max(64000, bitrate)
            now = time.monotonic()
            if next_feed < now - 0.8:
                next_feed = now
            if next_feed > now:
                time.sleep(min(next_feed - now, 0.4))
            self._record_ad_chunk(data)
            self.engine.feed_mp3(data)
            next_feed += dur

    def _record_ad_chunk(self, data: bytes) -> None:
        if not self.cfg.record_ads:
            return
        now = time.monotonic()
        iv = None
        for cand in self.schedule.active():
            if cand.get("start") and cand.get("end") and cand["start"] <= now <= cand["end"]:
                iv = cand
                break
        if iv is None:
            if self._ad_rec_fh:
                try:
                    self._ad_rec_fh.close()
                except Exception:
                    pass
                self._ad_rec_fh = None
            return
        if self._ad_rec_fh is None:
            try:
                os.makedirs(self.cfg.ads_dir, exist_ok=True)
                ts = datetime.datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
                self._ad_rec_path = os.path.join(self.cfg.ads_dir, f"ad_{ts}.mp3")
                self._ad_rec_fh = open(self._ad_rec_path, "wb")
                log.info("recording ad: %s", self._ad_rec_path)
            except Exception:
                log.exception("ad recording failed")
                self._ad_rec_fh = None
                return
        try:
            self._ad_rec_fh.write(data)
        except Exception:
            log.exception("ad recording write failed")
            self._ad_rec_fh = None

    def _on_icy_status(self, stats: dict) -> None:
        self._icy_stats = stats
        if stats.get("reconnects", 0) > self._last_reconnects:
            self._last_reconnects = stats["reconnects"]
            log.warning("ICY reconnect detected; resetting delayed buffer")
            self.engine.soft_reset()

    def _on_meta(self, title: str, mono_ts: float) -> None:
        self._icy_stats["title"] = title
        if isinstance(self.detector, RBDetector):
            self.detector.on_icy(title, mono_ts)
        elif self._station["group"] == "Rock Antenne":
            # API is authoritative for RA; ICY is only a fallback for news detection
            up = title.upper()
            if "NACHRICHTEN" in up and isinstance(self.detector, RADetector) and self.detector.active:
                self.detector._close(time.monotonic() - 0.5, "icy-news")

    def set_station(self, station_id: str) -> bool:
        st = stations.BY_ID.get(station_id)
        if not st:
            return False
        with self._lock:
            self._station = st
            self.cfg.station = station_id
            self.cfg.save()
            self._start_station()
        log.info("station changed to %s (%s)", st["name"], station_id)
        return True

    def set_enabled(self, enabled: bool) -> None:
        self.cfg.enabled = bool(enabled)
        self.cfg.save()
        if not enabled:
            self.schedule.clear()
        log.info("ad replacement %s", "enabled" if enabled else "disabled")

    def set_target(self, entity_id: str, service: str = "media_player") -> None:
        self.cfg.target_player = entity_id
        self.cfg.target_service = service
        self.cfg.save()

    # ---------------- detector / status ----------------
    def _on_detector_state(self, state: dict) -> None:
        self._detector_state = state
        ad_id = state.get("last_ad_id")
        if ad_id and ad_id != self._last_ad_id:
            self._last_ad_id = ad_id
            self._bump_today()
        self._publish_state()

    def _bump_today(self) -> None:
        today = datetime.datetime.now(TZ).date()
        if today != self._today:
            self._today = today
            self._ads_today = 0
        self._ads_today += 1

    def _refresh_ha(self) -> None:
        info = ha.addon_info()
        self._ingress = info.get("ingress_entry", "") or ""
        self._ingress_url = info.get("ingress_url", "") or ""
        self._host_ip = ha.host_ip()

    def _ha_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(300)
            try:
                self._refresh_ha()
            except Exception:
                pass

    def _status_loop(self) -> None:
        while not self._stop.is_set():
            self._publish_state()
            self._stop.wait(2.0)

    def _publish_state(self) -> None:
        try:
            st = self.status()
            if self.mqtt:
                self.mqtt.publish_state(st)
        except Exception:
            log.exception("publish state failed")

    def today_count(self) -> int:
        try:
            midnight = datetime.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            with self.learner.lock:
                row = self.learner.db.execute(
                    "SELECT COUNT(*) n FROM ad_events WHERE station=? AND start_wall>=? "
                    "AND (confirmed IS NULL OR confirmed=1)",
                    (self.cfg.station, midnight.timestamp())).fetchone()
            return int(row["n"] if row else 0)
        except Exception:
            return self._ads_today

    def _urls(self) -> dict:
        token = self.cfg.token
        direct = f"http://{self._host_ip}:{self.cfg.port}/stream.mp3?token={token}"
        if self._ingress:
            stream = self._ingress.rstrip("/") + "/stream.mp3"
            player = self._ingress.rstrip("/") + "/player"
        else:
            stream = f"/stream.mp3?token={token}"
            player = "/player"
        return {"stream": stream, "direct": direct, "player": player,
                "ingress": self._ingress, "host": self._host_ip, "port": self.cfg.port}

    def _ad_list(self) -> list[str]:
        try:
            return sorted((p.name for p in Path(self.cfg.ads_dir).glob("*.mp3")), reverse=True)[:20]
        except Exception:
            return []

    def status(self) -> dict:
        st = self._station
        det = self._detector_state or {}
        eng = self.engine.status()
        learn = {}
        try:
            learn = self.learner.stats(self.cfg.station)
            last = self.learner.last_ad()
            if last:
                learn["last_duration"] = round(float(last["duration"]), 1)
        except Exception:
            pass
        filler_track = self.engine.filler.active_track()
        filler = filler_track.as_dict() if filler_track else None
        filler_text = f"{filler['artist']} – {filler['title']}" if filler else ""
        radio = {
            "artist": det.get("radio_artist", ""),
            "title": det.get("radio_title", ""),
            "class": det.get("radio_class", ""),
            "icy": self._icy_stats.get("title", ""),
        }
        ad_active = det.get("mode") == "Werbung"
        remaining = float(det.get("ad_remaining", 0) or 0)
        duration = float(det.get("ad_duration", 0) or 0)
        out_now = time.monotonic() - self.cfg.delay
        for iv in self.schedule.active():
            if iv.get("source") == "test" and iv.get("end"):
                if iv["start"] - self.cfg.crossfade <= out_now <= iv["end"] + self.cfg.crossfade:
                    ad_active = True
                    duration = iv["end"] - iv["start"]
                    remaining = max(0.0, iv["end"] - out_now)
                    det = {**det, "ad_source": "test", "detail": "Test-Werbung"}
                    break
        if ad_active and duration <= 0:
            duration = remaining
        urls = self._urls()
        icy = dict(self.icy.stats) if self.icy else dict(self._icy_stats)
        label = f"{st['group']} · {st['short']}"
        return {
            "ok": True, "version": VERSION, "timestamp": time.time(),
            "station": self.cfg.station,
            "station_info": {**st, "label": label},
            "enabled": self.cfg.enabled,
            "volume": self._volume,
            "playing": self._playing,
            "radio": radio,
            "filler": filler,
            "filler_active": ad_active,
            "filler_text": filler_text,
            "mode": ("Nachrichten" if (self.cfg.keep_news
                     and datetime.datetime.now(TZ).minute < 3)
                     else det.get("mode", "Programm")),
            "ad": {
                "active": ad_active,
                "remaining": round(remaining, 1),
                "duration": round(duration, 1),
                "source": det.get("ad_source", ""),
                "confidence": det.get("ad_confidence", 0),
                "last_id": det.get("last_ad_id"),
                "detail": det.get("detail", ""),
            },
            "learn": learn,
            "today": {"ads": self.today_count()},
            "delay": {"target": self.cfg.delay, "actual": eng.get("delay_actual")},
            "engine": eng,
            "icy": icy,
            "urls": urls,
            "ads": self._ad_list(),
            "ad_recording": self._ad_rec_path if self._ad_rec_fh else "",
            "filler_library": {
                "count": len(self.filler_lib.tracks),
                "licenses": sorted({t.license for t in self.filler_lib.tracks}),
            },
            "capabilities": {
                "target_player": self.cfg.target_player,
                "target_service": self.cfg.target_service,
                "ingress": bool(self._ingress),
            },
        }

    # ---------------- controls ----------------
    def feedback(self, is_ad: bool):
        return self.learner.feedback(is_ad)

    def simulate_ad(self, seconds: float = 30.0) -> None:
        start = time.monotonic() - self.cfg.delay + 2.0
        end = start + max(5.0, seconds)
        self.schedule.add(start, end, "test", 1.0)
        log.info("simulated ad: %.0fs starting in ~2s (output)", seconds)

    def handle_mqtt_command(self, key: str, payload: str) -> None:
        p = payload.strip()
        if key == "station":
            st = next((s for s in stations.all_stations()
                       if f"{s['group']} · {s['short']}" == p or s["id"] == p or s["name"] == p), None)
            if st:
                self.set_station(st["id"])
        elif key == "enabled":
            self.set_enabled(p.upper() in ("ON", "1", "TRUE"))
        elif key == "button_was_ad":
            self.feedback(True)
        elif key == "button_was_no_ad":
            self.feedback(False)
        elif key == "button_test_ad":
            self.simulate_ad(30)
        elif key == "button_reconnect":
            self.set_station(self.cfg.station)
        elif key == "player":
            if p in ("play", "pause", "stop"):
                self._playing = p == "play"
                self._control_target(p)
        elif key == "player_power":
            on = p.upper() in ("ON", "1", "TRUE")
            self._playing = on
            self._control_target("play" if on else "stop")
        elif key == "volume":
            try:
                self._volume = max(0.0, min(1.0, float(p)))
                self.cfg.update(volume=self._volume)
                self._set_volume(self._volume)
            except Exception:
                pass

    def _control_target(self, action: str) -> None:
        if not self.cfg.target_player:
            return
        try:
            if action == "play":
                ha.play_media(self.cfg.target_player, self._urls()["direct"], self.cfg.target_service)
            elif action == "stop":
                ha.stop_media(self.cfg.target_player, self.cfg.target_service)
            elif action == "pause":
                ha.call_service(self.cfg.target_service, "media_pause",
                                {"entity_id": self.cfg.target_player})
        except Exception as exc:
            log.warning("target control failed: %s", exc)

    def _set_volume(self, vol: float) -> None:
        if not self.cfg.target_player:
            return
        try:
            ha.call_service("media_player", "volume_set",
                            {"entity_id": self.cfg.target_player, "volume_level": vol})
        except Exception:
            pass

    # ---------------- self test ----------------
    def selftest(self) -> dict:
        checks = {}
        urls = self._urls()
        # stream source
        try:
            req = urllib.request.Request(self._station["stream"], headers={
                "Icy-MetaData": "1", "User-Agent": "RadioAdFree-selftest/1.0"})
            with urllib.request.urlopen(req, timeout=12) as r:
                data = r.read(4096)
                checks["source_stream"] = {"ok": len(data) > 1000,
                                           "http": r.status, "bytes": len(data)}
        except Exception as exc:
            checks["source_stream"] = {"ok": False, "error": str(exc)}
        icy = self._icy_stats
        checks["icy"] = {"ok": bool(icy.get("connected")) and time.monotonic() - icy.get("last_audio", 0) < 8,
                         "connected": bool(icy.get("connected")),
                         "last_audio_s": round(time.monotonic() - icy.get("last_audio", 0), 1) if icy else None,
                         "title": icy.get("title", "")}
        eng = self.engine.status()
        checks["buffer"] = {"ok": (eng.get("buffer_seconds") or 0) > 1.0,
                            "seconds": eng.get("buffer_seconds"),
                            "delay_actual": eng.get("delay_actual"),
                            "underruns": eng.get("underruns")}
        checks["encoder"] = {"ok": eng.get("encoder_bytes", 0) > 10000,
                             "bytes": eng.get("encoder_bytes"),
                             "error": eng.get("encoder_error")}
        det = self._detector_state
        checks["detector"] = {"ok": bool(det.get("api_ok")), "detail": det.get("detail", ""),
                              "mode": det.get("mode", "")}
        checks["filler"] = {"ok": len(self.filler_lib.tracks) > 0,
                            "tracks": len(self.filler_lib.tracks)}
        checks["mqtt"] = {"ok": bool(self.mqtt and self.mqtt.connected)}
        checks["ha_api"] = {"ok": bool(self._ingress) or bool(os.environ.get("SUPERVISOR_TOKEN")),
                            "ingress": self._ingress,
                            "host": self._host_ip}
        try:
            self.learner.db.execute("SELECT 1")
            checks["database"] = {"ok": True}
        except Exception as exc:
            checks["database"] = {"ok": False, "error": str(exc)}
        checks["urls"] = urls
        return {"ok": all(c.get("ok") for c in checks.values() if isinstance(c, dict)),
                "version": VERSION, "checks": checks, "timestamp": time.time()}

    # ---------------- HTTP ----------------
    def create_app(self) -> web.Application:
        return create_app(self, self.hub)
