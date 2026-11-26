"""Persistent learning: ad durations, ad-block start windows, filler history, user feedback."""
from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("learn")

TZ = ZoneInfo("Europe/Berlin")


def _circ_dist(a: int, b: int) -> int:
    d = abs(a - b) % 60
    return min(d, 60 - d)


class Learner:
    def __init__(self, path: Path, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ad_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station TEXT NOT NULL,
                start_wall REAL NOT NULL,
                end_wall REAL NOT NULL,
                duration REAL NOT NULL,
                source TEXT,
                confidence REAL,
                minute INTEGER,
                hour INTEGER,
                weekday INTEGER,
                confirmed INTEGER,
                created REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ad_station ON ad_events(station, start_wall);
            CREATE TABLE IF NOT EXISTS filler_plays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station TEXT,
                track_id TEXT,
                title TEXT,
                started REAL,
                played_s REAL,
                completed INTEGER,
                created REAL
            );
            CREATE INDEX IF NOT EXISTS idx_filler_track ON filler_plays(track_id, started);
            CREATE TABLE IF NOT EXISTS track_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                station TEXT, artist TEXT, title TEXT,
                start_wall REAL, bpm REAL, energy REAL
            );
            """)
            self.db.commit()

    # ---------- ads ----------
    def record_ad(self, station: str, start_wall: float, end_wall: float,
                  source: str, confidence: float, confirmed: int | None = None) -> int:
        dt = datetime.datetime.fromtimestamp(start_wall, TZ)
        dur = max(1.0, end_wall - start_wall)
        if source not in ("test", "manual"):
            lo, hi = (40.0, 200.0) if station.startswith("rb-") else (8.0, 120.0)
            if not (lo <= dur <= hi):
                log.warning("ignoring implausible %s ad duration %.1fs (source=%s)",
                            station, dur, source)
                return 0
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO ad_events(station,start_wall,end_wall,duration,source,confidence,"
                "minute,hour,weekday,confirmed,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (station, start_wall, end_wall, dur, source, confidence,
                 dt.minute, dt.hour, dt.weekday(), confirmed, time.time()))
            self.db.commit()
            return int(cur.lastrowid)

    def last_ad(self) -> dict | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM ad_events WHERE (confirmed IS NULL OR confirmed=1) "
                "ORDER BY start_wall DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def feedback(self, is_ad: bool) -> dict | None:
        row = self.last_ad()
        if not row:
            return None
        with self.lock:
            self.db.execute("UPDATE ad_events SET confirmed=? WHERE id=?",
                            (1 if is_ad else 0, row["id"]))
            self.db.commit()
        if not is_ad and self.cfg.learn_enabled:
            log.info("user rejected ad event %s; it will not influence the schedule", row["id"])
        return {**row, "confirmed": 1 if is_ad else 0}

    def predict_duration(self, station: str, now_wall: float | None = None) -> float:
        now_wall = now_wall or time.time()
        with self.lock:
            rows = self.db.execute(
                "SELECT duration, start_wall FROM ad_events WHERE station=? AND "
                "(confirmed IS NULL OR confirmed=1) AND duration BETWEEN 40 AND 200 "
                "ORDER BY start_wall DESC LIMIT 30",
                (station,)).fetchall()
        if not rows:
            return 150.0 if station.startswith("rb-") else 120.0
        now_dt = datetime.datetime.fromtimestamp(now_wall, TZ)
        vals = []
        for r in rows:
            d = datetime.datetime.fromtimestamp(r["start_wall"], TZ)
            # same hour-of-day and weekday are stronger signals
            w = 1.0
            if d.hour == now_dt.hour:
                w += 0.5
            if d.weekday() == now_dt.weekday():
                w += 0.3
            vals.append((r["duration"], w))
        vals.sort()
        total = sum(w for _, w in vals)
        acc = 0.0
        for v, w in vals:
            acc += w
            if acc > total / 2:
                return float(v)
        return float(vals[-1][0])

    def ad_start_minutes(self, station: str, days: int = 60) -> dict[int, int]:
        since = time.time() - days * 86400
        with self.lock:
            rows = self.db.execute(
                "SELECT minute, COUNT(*) n FROM ad_events WHERE station=? AND start_wall>? "
                "AND (confirmed IS NULL OR confirmed=1) GROUP BY minute ORDER BY n DESC",
                (station, since)).fetchall()
        return {int(r["minute"]): int(r["n"]) for r in rows}

    def ad_start_seconds(self, station: str, days: int = 30) -> int:
        """Median second-of-minute of recent plausible blocks (e.g. 26 for :28:26)."""
        since = time.time() - days * 86400
        lo, hi = (40.0, 200.0) if station.startswith("rb-") else (8.0, 120.0)
        with self.lock:
            rows = self.db.execute(
                "SELECT start_wall FROM ad_events WHERE station=? AND start_wall>? "
                "AND (confirmed IS NULL OR confirmed=1) AND duration BETWEEN ? AND ? "
                "ORDER BY start_wall DESC LIMIT 30",
                (station, since, lo, hi)).fetchall()
        if not rows:
            return 0
        secs = sorted(datetime.datetime.fromtimestamp(r["start_wall"], TZ).second for r in rows)
        return int(secs[len(secs) // 2])

    def is_ad_window(self, station: str, dt: datetime.datetime, tolerance: int = 6) -> tuple[bool, str]:
        learned = self.ad_start_minutes(station)
        if sum(learned.values()) >= 4:
            minutes = []
            for m, n in learned.items():
                minutes.extend([m] * min(n, 5))
            label = "gelernt"
        else:
            minutes = list(self.cfg.rb_schedule_prior)
            label = "Erfahrungswert"
        hit = any(_circ_dist(dt.minute, m) <= tolerance for m in minutes)
        return hit, label

    def next_ad_window(self, station: str) -> str | None:
        learned = self.ad_start_minutes(station)
        minutes = sorted(learned) if sum(learned.values()) >= 4 else list(self.cfg.rb_schedule_prior)
        if not minutes:
            return None
        now = datetime.datetime.now(TZ)
        for delta in range(1, 180):
            t = (now + datetime.timedelta(minutes=delta)).replace(second=0, microsecond=0)
            if any(t.minute == m for m in minutes):
                return t.strftime("%H:%M")
        return None

    def stats(self, station: str) -> dict:
        since = time.time() - 30 * 86400
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) n, AVG(duration) avg, MIN(duration) mn, MAX(duration) mx "
                "FROM ad_events WHERE station=? AND start_wall>? AND (confirmed IS NULL OR confirmed=1)",
                (station, since)).fetchone()
            recent = self.db.execute(
                "SELECT duration FROM ad_events WHERE station=? AND start_wall>? "
                "AND (confirmed IS NULL OR confirmed=1) ORDER BY start_wall DESC LIMIT 20",
                (station, since)).fetchall()
        return {
            "count": int(row["n"] or 0),
            "avg_duration": round(float(row["avg"] or 0), 1),
            "min_duration": round(float(row["mn"] or 0), 1),
            "max_duration": round(float(row["mx"] or 0), 1),
            "predicted_duration": round(self.predict_duration(station), 1),
            "recent": [round(float(r["duration"]), 1) for r in recent],
            "window": self.next_ad_window(station),
            "window_source": "gelernt" if sum(self.ad_start_minutes(station).values()) >= 4 else "Erfahrungswert",
        }

    # ---------- filler ----------
    def record_filler_play(self, station: str, track_id: str, title: str,
                           started: float, played_s: float, completed: bool) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO filler_plays(station,track_id,title,started,played_s,completed,created) "
                "VALUES(?,?,?,?,?,?,?)",
                (station, track_id, title, started, played_s, 1 if completed else 0, time.time()))
            self.db.commit()

    def recent_filler_plays(self, minutes: int = 90) -> dict[str, int]:
        since = time.time() - minutes * 60
        with self.lock:
            rows = self.db.execute(
                "SELECT track_id, COUNT(*) n FROM filler_plays WHERE created>? GROUP BY track_id",
                (since,)).fetchall()
        return {r["track_id"]: int(r["n"]) for r in rows}

    def filler_score(self, track_id: str) -> float:
        with self.lock:
            row = self.db.execute(
                "SELECT SUM(played_s) s, SUM(completed) c, COUNT(*) n FROM filler_plays WHERE track_id=?",
                (track_id,)).fetchone()
        if not row or not row["n"]:
            return 0.0
        return float(row["c"] or 0) / max(1, row["n"])

    # ---------- tracks ----------
    def record_track(self, station: str, artist: str, title: str,
                     start_wall: float, bpm: float = 0, energy: float = 0) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO track_history(station,artist,title,start_wall,bpm,energy) "
                "VALUES(?,?,?,?,?,?)",
                (station, artist, title, start_wall, bpm, energy))
            self.db.commit()
