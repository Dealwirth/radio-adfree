"""Ad detection.

Rock Antenne: official now-playing API exposes an explicit `class`
(COMMERCIAL / Promotion / Cart / Live / Music / News ...).

Radio Bob: official iris search.json gives the complete music history. A gap
between songs is a non-music segment. The station has a stable ad raster
(:26-:29 and :56-:59) and the top of the hour is news, so long gaps are split:
ad part before the hour is replaced, news after the hour stays live.
"""
from __future__ import annotations

import datetime
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

log = logging.getLogger("detect")
TZ = ZoneInfo("Europe/Berlin")

SKIP_TITLE = ("RADIO BOB", "ROCK ANTENNE", "Livestream", "Livestream National",
              "Werbung", "Nachrichten", "Wetter", "Verkehr", "Moderation", "Jingle")


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) RadioAdFree/1.0",
        "Accept": "application/json",
        "Referer": "https://www.rockantenne.de/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _is_music_title(title: str) -> bool:
    if not title or " - " not in title:
        return False
    up = title.upper()
    return not any(k.upper() in up for k in SKIP_TITLE)


def _parse_title(title: str) -> tuple[str, str]:
    if " - " in title:
        a, t = title.split(" - ", 1)
        return a.strip(), t.strip()
    return "", title.strip()


class BaseDetector:
    def __init__(self, cfg, schedule, learner, on_state=None, on_meta=None):
        self.cfg = cfg
        self.schedule = schedule
        self.learner = learner
        self.on_state = on_state
        self.on_meta = on_meta
        self.station = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = {
            "station": "", "mode": "Programm", "radio_artist": "", "radio_title": "",
            "radio_class": "", "icy_title": "", "ad_source": "", "ad_confidence": 0.0,
            "ad_started": 0.0, "ad_ended": 0.0, "ad_duration": 0.0,
            "ad_remaining": 0.0, "last_ad_id": None, "detail": "", "api_ok": False,
        }

    def set_station(self, station: dict) -> None:
        self.station = station
        self.state["station"] = station["id"]
        self.schedule.clear()
        self._notify()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="detector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        raise NotImplementedError

    def _notify(self) -> None:
        if self.on_state:
            try:
                self.on_state(dict(self.state))
            except Exception:
                log.exception("state callback failed")

    def _set_ad(self, active: bool, source: str = "", confidence: float = 0.0,
                start_wall: float = 0.0, end_wall: float = 0.0, start_mono: float = 0.0,
                end_mono: float = 0.0) -> None:
        self.state["mode"] = "Werbung" if active else "Programm"
        self.state["ad_source"] = source
        self.state["ad_confidence"] = confidence
        self.state["ad_started"] = start_wall
        self.state["ad_ended"] = end_wall
        self.state["ad_duration"] = max(0.0, end_wall - start_wall)
        self._notify()

    def _remaining(self, end_mono: float | None, start_mono: float, predicted: float) -> float:
        out_now = time.monotonic() - self.cfg.delay
        if end_mono:
            return max(0.0, end_mono - out_now)
        return max(0.0, predicted - max(0.0, out_now - start_mono))


class RADetector(BaseDetector):
    def __init__(self, cfg, schedule, learner, on_state=None):
        super().__init__(cfg, schedule, learner, on_state)
        self.ad_start = {c.lower() for c in cfg.ra_ad_start_classes}
        self.ad_keep = {c.lower() for c in cfg.ra_ad_keep_classes}
        self.program = {c.lower() for c in cfg.ra_program_classes}
        self.active = False
        self.block_start_wall = 0.0
        self.block_start_mono = 0.0
        self.block_source = ""
        self.last_ok = 0.0
        self.last_cls = ""

    def _run(self) -> None:
        while not self._stop.is_set():
            st = self.station
            if st and st.get("ra_mountpoint"):
                try:
                    self.poll_once(st["ra_mountpoint"])
                    self.last_ok = time.time()
                except Exception as exc:
                    log.debug("RA poll failed: %s", exc)
                    self.state["detail"] = f"API: {type(exc).__name__}"
                    if self.active and time.time() - self.last_ok > 40:
                        self._close(time.monotonic(), "timeout")
                        self.state["api_ok"] = False
                        self._notify()
            self._stop.wait(self.cfg.ra_poll)

    def poll_once(self, mountpoint: str) -> None:
        ts = int(time.time() * 1000)
        d = _get_json(f"https://www.rockantenne.de/api/metadata/now/{mountpoint}?ts={ts}").get("data", {})
        cls = (d.get("class") or "").strip()
        title = (d.get("title") or "").strip()
        artist = (d.get("artist") or "").strip()
        st_raw = d.get("starttime")
        now_wall = time.time()
        now_mono = time.monotonic()
        start_wall = now_wall
        if st_raw:
            try:
                start_wall = datetime.datetime.fromisoformat(st_raw).timestamp()
            except Exception:
                pass
        start_mono = now_mono - max(0.0, now_wall - start_wall)
        self.state["api_ok"] = True
        self.state["radio_class"] = cls
        self.state["detail"] = f"RA class={cls or '?'}"
        low = cls.lower()

        if low == "music" or (low in self.program and low not in ("news", "weather", "traffic", "moderation")):
            if cls == "Music" or low == "music":
                self.state["radio_artist"] = artist
                self.state["radio_title"] = title
                if self.learner and title:
                    try:
                        self.learner.record_track(self.station["id"], artist, title, start_wall)
                    except Exception:
                        pass
            if self.active:
                self._close(start_mono, f"ra:{low}")
            self.last_cls = cls
            self._notify()
            return

        if low in self.program and not self.active:
            self.state["radio_class"] = cls
            self.state["detail"] = f"RA class={cls}"
            self._notify()
            return

        if low in self.ad_start and not self.active:
            start = start_mono
            # Never schedule a fade entirely in the past.
            if start + self.cfg.delay < now_mono - 0.5:
                start = now_mono - self.cfg.delay + 0.5
            self.active = True
            self.block_start_wall = start_wall
            self.block_start_mono = start
            self.block_source = f"ra:{low}"
            self.schedule.set_open(start, self.block_source, 0.98)
            self.state["radio_class"] = cls
            self.state["detail"] = f"RA Werbung ({title or cls})"
            self._set_ad(True, self.block_source, 0.98, start_wall, 0.0, start, 0.0)
            log.info("RA ad start: %s (%s), start=%.1fs, lead=%.1fs",
                     title or cls, cls, start, start + self.cfg.delay - now_mono)
            return

        if self.active and low in self.ad_keep:
            self.state["detail"] = f"RA Werbung ({cls}: {title})"
            self._notify()
            return

        if self.active and low in self.program:
            self._close(start_mono, f"ra:{low}")
            self.state["radio_class"] = cls
            self._notify()
            return

        # unknown class: keep an open block, but don't start one
        if self.active:
            self.state["detail"] = f"RA {cls}: {title}"
            self._notify()

    def _close(self, end_mono: float, source: str) -> None:
        if not self.active:
            return
        out_now = time.monotonic() - self.cfg.delay
        if end_mono < out_now:
            end_mono = time.monotonic() + 0.5
        end_wall = time.time() - (time.monotonic() - end_mono)
        if end_wall <= self.block_start_wall + 1:
            end_wall = time.time()
        self.schedule.close(end_mono)
        ad_id = None
        if self.learner:
            try:
                ad_id = self.learner.record_ad(self.station["id"], self.block_start_wall,
                                               end_wall, self.block_source, 0.98)
            except Exception:
                log.exception("record_ad failed")
        self.state["last_ad_id"] = ad_id
        self.active = False
        self._set_ad(False, self.block_source, 0.98, self.block_start_wall,
                     end_wall, self.block_start_mono, end_mono)
        log.info("RA ad end: %.1fs", end_wall - self.block_start_wall)


class RBDetector(BaseDetector):
    """Radio Bob uses the complete music history and the ICY metadata as the
    confirmation source. The API sometimes lists a song that was pre-empted by
    an ad block; such a song never appears in ICY, so it must not close a gap."""

    def __init__(self, cfg, schedule, learner, on_state=None):
        super().__init__(cfg, schedule, learner, on_state)
        self.songs: dict[str, dict] = {}
        self.current_song: dict | None = None
        self.gap: dict | None = None
        self._icy_title = ""
        self._icy_seen_mono = 0.0
        self._api_ok = False
        self._started_wall = time.time()

    def _run(self) -> None:
        while not self._stop.is_set():
            st = self.station
            if st and st.get("rb_station_id"):
                try:
                    self.poll_once(st["rb_station_id"])
                    self._api_ok = True
                except Exception as exc:
                    self._api_ok = False
                    self.state["detail"] = f"API: {type(exc).__name__}"
                    log.debug("RB poll failed: %s", exc)
            self._stop.wait(self.cfg.rb_poll)

    @staticmethod
    def _norm(s: str) -> str:
        out = []
        for ch in (s or "").lower():
            out.append(ch if ch.isalnum() else " ")
        return " ".join("".join(out).split())

    def _match_icy(self, song: dict) -> bool:
        if not self._icy_title or not song:
            return False
        icy = self._norm(self._icy_title)
        a = self._norm(song.get("artist", ""))
        t = self._norm(song.get("title", ""))
        if a and len(a) > 2 and a in icy:
            return True
        if t and len(t) > 3 and t in icy:
            return True
        return False

    def _find_icy_match(self) -> dict | None:
        best = None
        for s in self.songs.values():
            if self._match_icy(s):
                if best is None or s["wall"] > best["wall"]:
                    best = s
        return best

    def on_icy(self, title: str, mono_ts: float) -> None:
        self.state["icy_title"] = title
        self._icy_title = title
        self._icy_seen_mono = mono_ts
        up = title.upper()
        if any(k.upper() in up for k in ("NACHRICHTEN", "NEWS", "WETTER", "VERKEHR")):
            if self.gap and self.gap.get("interval") is not None:
                self._close_interval(mono_ts, reason="news-icy")
                self.gap["news_done"] = True
            return
        if _is_music_title(title):
            a, t = _parse_title(title)
            self.state["radio_artist"] = a
            self.state["radio_title"] = t
            self.state["radio_class"] = "Music"
            if self.learner and a:
                try:
                    self.learner.record_track(self.station["id"], a, t, time.time())
                except Exception:
                    pass
            # If the ICY confirms an API song, it is the real song after an ad.
            match = self._find_icy_match()
            if match and (self.current_song is None or match["wall"] > self.current_song["wall"] + 1):
                if self.gap is not None:
                    self._finish_gap(match["wall"], time.monotonic())
                self.current_song = match
            self._notify()

    def _last_completed(self, now_wall: float) -> dict | None:
        done = [s for s in self.songs.values() if s["wall"] + s["duration"] <= now_wall - 1]
        return max(done, key=lambda s: s["wall"]) if done else None

    def poll_once(self, station_id: str) -> None:
        now_local = datetime.datetime.now(TZ)
        start = now_local - datetime.timedelta(minutes=50)
        end = now_local + datetime.timedelta(minutes=2)
        q = urllib.parse.urlencode({
            "station": station_id,
            "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"),
        })
        d = _get_json(f"https://iris-bob.loverad.io/search.json?{q}")
        entries = d.get("result", {}).get("entry", [])
        if not entries:
            return
        for e in entries:
            at = e.get("airtime")
            if not at:
                continue
            song = (e.get("song", {}).get("entry") or [{}])[0]
            artist = ((song.get("artist", {}).get("entry") or [{}])[0].get("name") or "").strip()
            title = (song.get("title") or "").strip()
            try:
                wall = datetime.datetime.fromisoformat(at).timestamp()
                dur = float(e.get("duration") or 0)
            except Exception:
                continue
            self.songs[at] = {"airtime": at, "wall": wall, "duration": dur,
                              "artist": artist, "title": title}
        cutoff = time.time() - 95 * 60
        self.songs = {k: v for k, v in self.songs.items() if v["wall"] >= cutoff}
        newest = max(self.songs.values(), key=lambda s: s["wall"])
        now_wall = time.time()
        now_mono = time.monotonic()
        self.state["api_ok"] = True

        if self.current_song is None:
            self.current_song = self._find_icy_match() or self._last_completed(now_wall) or newest
            cur = self.current_song
            self.state["radio_artist"] = cur["artist"]
            self.state["radio_title"] = cur["title"]
            self.state["radio_class"] = "Music"

        # A gap is active: only close it on a song that is real (ICY confirmed)
        # or that started recently and the block has run long enough.
        if self.gap is not None:
            if newest["wall"] > self.gap["start_wall"] + 1:
                accept = False
                dur = now_wall - self.gap["start_wall"]
                sched = self.gap.get("scheduled_start_wall")
                if not self.gap["candidate"]:
                    accept = True
                elif self._match_icy(newest):
                    accept = True
                elif sched:
                    # A song that starts at/after the learned block and is seen
                    # fresh (<=20 s) can be the real next song, but never the
                    # first 30 s of the block (those are pre-empted entries).
                    if (newest["wall"] >= sched and now_wall >= sched + 30.0
                            and (now_wall - newest["wall"]) <= 20):
                        accept = True
                    elif (dur < 15.0 and newest["wall"] <= now_wall + 2.0
                          and (now_wall - newest["wall"]) <= 20):
                        # Short gap (station ID between songs), next song real.
                        accept = True
                elif dur >= 45 and (now_wall - newest["wall"]) <= 20:
                    # Off-schedule late block without a learned start.
                    accept = True
                if accept:
                    self._finish_gap(newest["wall"], now_mono)
                    self.current_song = newest
            self._advance_gap(now_wall, now_mono)
            self.state["detail"] = (f"RB Lücke {self._gap_len():.0f}s" if self.gap else "RB Musik")
            self._notify()
            return

        cur = self.current_song
        cur_end = cur["wall"] + cur["duration"]

        # Current song ended. If ICY already confirms the next song, accept it;
        # otherwise a non-music block (moderation/ad/news) is in progress.
        if now_wall > cur_end + 1.0:
            if (newest["wall"] > cur["wall"] + 1 and newest["wall"] >= cur_end - 5
                    and self._match_icy(newest)):
                self.current_song = newest
                cur = newest
                cur_end = cur["wall"] + cur["duration"]
            else:
                self._start_gap(cur_end, now_mono)
                self._advance_gap(now_wall, now_mono)
                self.state["detail"] = f"RB Lücke {self._gap_len():.0f}s"
                self._notify()
                return

        # No gap: advance only on an ICY-confirmed newer song.
        if newest["wall"] > cur["wall"] + 1 and self._match_icy(newest):
            self.current_song = newest
            cur = newest
        self.state["radio_artist"] = cur["artist"]
        self.state["radio_title"] = cur["title"]
        self.state["radio_class"] = "Music"
        self.state["mode"] = "Programm"
        self.state["ad_remaining"] = 0.0
        self.state["detail"] = "RB Musik"
        self._notify()

    def _gap_len(self) -> float:
        return (time.time() - self.gap["start_wall"]) if self.gap else 0.0

    def _start_gap(self, end_wall: float, now_mono: float) -> None:
        now_wall = time.time()
        start_mono = now_mono - max(0.0, now_wall - end_wall)
        self.gap = {
            "start_wall": end_wall, "start_mono": start_mono,
            "ad_start_wall": end_wall, "ad_start_mono": start_mono,
            "scheduled_start_wall": None, "scheduled_start_mono": None,
            "candidate": False, "interval": None, "news_done": False,
            "source": "", "confidence": 0.0,
        }
        log.info("RB gap detected at %s", datetime.datetime.fromtimestamp(end_wall, TZ).strftime("%H:%M:%S"))

    def _scheduled_start(self, start_dt: datetime.datetime) -> datetime.datetime | None:
        """The learned ad raster is :28 and :58. A gap at :27:3x is the
        moderation before the block; start replacing at :28:00, not at the
        beginning of the moderation."""
        learned = self.learner.ad_start_minutes(self.station["id"]) if self.learner else {}
        minutes = sorted(learned) if sum(learned.values()) >= 4 else [28, 58]
        best = None
        for m in minutes:
            cand = start_dt.replace(minute=m % 60, second=0, microsecond=0)
            if m >= 60:
                cand += datetime.timedelta(hours=1)
            for off in (-1, 0, 1):
                c = cand + datetime.timedelta(hours=off)
                delta = (c - start_dt).total_seconds()
                if -60 <= delta <= 100:
                    if best is None or abs(delta) < abs(best[0]):
                        best = (delta, c)
        return best[1] if best else None

    def _next_scheduled_start(self, start_dt: datetime.datetime,
                              max_ahead: float = 600.0) -> datetime.datetime | None:
        """Next learned block (:28/:58) after the gap start. A long gap before
        the block is the moderation; keep it live and start at the block."""
        learned = self.learner.ad_start_minutes(self.station["id"]) if self.learner else {}
        minutes = sorted(learned) if sum(learned.values()) >= 4 else [28, 58]
        best = None
        for m in minutes:
            cand = start_dt.replace(minute=m % 60, second=0, microsecond=0)
            if m >= 60:
                cand += datetime.timedelta(hours=1)
            if cand < start_dt:
                cand += datetime.timedelta(hours=1)
            delta = (cand - start_dt).total_seconds()
            if 0 <= delta <= max_ahead:
                if best is None or delta < best[0]:
                    best = (delta, cand)
        return best[1] if best else None

    def _advance_gap(self, now_wall: float, now_mono: float) -> None:
        g = self.gap
        if g is None:
            return
        dur = now_wall - g["start_wall"]
        start_dt = datetime.datetime.fromtimestamp(g["start_wall"], TZ)
        in_window, window_src = (self.learner.is_ad_window(self.station["id"], start_dt, tolerance=2)
                                 if self.learner else (True, "Erfahrungswert"))
        if not g["candidate"]:
            sched = self._scheduled_start(start_dt)
            nxt = self._next_scheduled_start(start_dt, max_ahead=240.0)
            if in_window and dur >= 3.0 and sched is not None:
                g["scheduled_start_wall"] = sched.timestamp()
                g["scheduled_start_mono"] = g["start_mono"] + (sched.timestamp() - g["start_wall"])
                g["ad_start_wall"] = g["scheduled_start_wall"]
                g["ad_start_mono"] = g["scheduled_start_mono"]
                g["candidate"] = True
                g["source"] = f"rb:{window_src}"
                g["confidence"] = 0.82
            elif nxt is not None and dur >= 3.0:
                # Gap shortly before the next block: this is moderation; keep it live.
                g["scheduled_start_wall"] = nxt.timestamp()
                g["scheduled_start_mono"] = g["start_mono"] + (nxt.timestamp() - g["start_wall"])
                g["ad_start_wall"] = g["scheduled_start_wall"]
                g["ad_start_mono"] = g["scheduled_start_mono"]
                g["candidate"] = True
                g["source"] = f"rb:{window_src}:next"
                g["confidence"] = 0.78
            elif in_window and dur >= 45.0:
                g["candidate"] = True
                g["source"] = f"rb:{window_src}:late"
                g["confidence"] = 0.65
                g["ad_start_wall"] = g["start_wall"]
                g["ad_start_mono"] = g["start_mono"]
            elif dur >= self.cfg.rb_long_gap and g["start_wall"] > self._started_wall:
                # Do not guess on a long gap that was already running before the
                # add-on started (could be moderation). Wait for the next block.
                g["candidate"] = True
                g["source"] = "rb:gap"
                g["confidence"] = 0.55
                g["ad_start_wall"] = g["start_wall"]
                g["ad_start_mono"] = g["start_mono"]
            elif self.cfg.rb_aggressive and dur >= self.cfg.rb_min_gap:
                g["candidate"] = True
                g["source"] = "rb:aggressive"
                g["confidence"] = 0.45
                g["ad_start_wall"] = g["start_wall"]
                g["ad_start_mono"] = g["start_mono"]

        if not g["candidate"]:
            self.state["mode"] = "Programm"
            self.state["ad_remaining"] = 0.0
            return

        # A pre-ad moderation gap is kept live until the learned block starts.
        if g.get("scheduled_start_wall") and now_wall < g["scheduled_start_wall"] - 0.2:
            self.state["mode"] = "Programm"
            self.state["ad_remaining"] = 0.0
            self.state["detail"] = "Werbung ab " + datetime.datetime.fromtimestamp(
                g["scheduled_start_wall"], TZ).strftime("%H:%M")
            return

        if g["interval"] is None:
            start = g["ad_start_mono"]
            if start + self.cfg.delay < now_mono - 0.5:
                start = now_mono - self.cfg.delay + 0.5
            g["interval"] = self.schedule.set_open(start, g["source"], g["confidence"])
            g["ad_start_mono"] = start
            self._set_ad(True, g["source"], g["confidence"], g["ad_start_wall"], 0.0,
                         start, 0.0)
            log.info("RB ad replacement start: gap %.1fs, source=%s, lead=%.1fs",
                     dur, g["source"], start + self.cfg.delay - now_mono)

        if self.cfg.keep_news and not g["news_done"]:
            ad_dt = datetime.datetime.fromtimestamp(g["ad_start_wall"], TZ)
            hour = ad_dt.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
            hour_wall = hour.timestamp()
            if g["ad_start_wall"] < hour_wall <= now_wall:
                hour_mono = g["ad_start_mono"] + (hour_wall - g["ad_start_wall"])
                if hour_mono > g["ad_start_mono"] + 5:
                    self._close_interval(hour_mono, reason="news-hour")
                    g["news_done"] = True
                    self.state["detail"] = "RB Werbung bis zur vollen Stunde, dann Nachrichten live"

        self.state["ad_remaining"] = self._remaining(
            g["interval"].get("end") if g["interval"] else None,
            g["ad_start_mono"],
            self.learner.predict_duration(self.station["id"]) if self.learner else 150.0,
        )

    def _close_interval(self, end_mono: float, reason: str) -> None:
        g = self.gap
        if not g or not g.get("interval"):
            return
        out_now = time.monotonic() - self.cfg.delay
        if end_mono < out_now:
            end_mono = time.monotonic() + 0.5
        self.schedule.close(end_mono)
        end_wall = time.time() - (time.monotonic() - end_mono)
        g["interval"]["end"] = end_mono
        g["interval_end_wall"] = end_wall
        g["interval_end_reason"] = reason
        self._set_ad(True, g["source"], g["confidence"], g["ad_start_wall"], end_wall,
                     g["ad_start_mono"], end_mono)

    def _finish_gap(self, next_wall: float, now_mono: float) -> None:
        g = self.gap
        if not g:
            return
        if g.get("interval") is not None and g["interval"].get("end") is None:
            end_mono = g["ad_start_mono"] + max(0.0, next_wall - g["ad_start_wall"])
            self._close_interval(end_mono, reason="next-song")
            # Record the real next-song time, not the 1-4 s clamp used by the mixer.
            g["interval_end_wall"] = next_wall
        if g["candidate"] and self.learner:
            end_wall = g.get("interval_end_wall") or next_wall
            duration = end_wall - g["ad_start_wall"]
            if duration >= 8:
                try:
                    ad_id = self.learner.record_ad(self.station["id"], g["ad_start_wall"],
                                                   end_wall, g["source"], g["confidence"])
                    self.state["last_ad_id"] = ad_id
                except Exception:
                    log.exception("record_ad failed")
        self._set_ad(False, g["source"], g["confidence"], g["ad_start_wall"],
                     g.get("interval_end_wall") or next_wall, g["ad_start_mono"], now_mono)
        self.gap = None
        self.state["ad_remaining"] = 0.0
