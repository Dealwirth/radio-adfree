import datetime
import unittest
from unittest.mock import patch

import numpy as np

from radio_adfree.app.audio import AdSchedule
from radio_adfree.app.detect import RADetector, RBDetector
from radio_adfree.app.learn import TZ


class FakeClock:
    def __init__(self):
        self.wall = datetime.datetime(2026, 11, 25, 19, 0, 0, tzinfo=TZ).timestamp()
        self.mono = 1000.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds

    def at(self, hh, mm, ss=0):
        dt = datetime.datetime(2026, 11, 25, hh, mm, ss, tzinfo=TZ)
        self.wall = dt.timestamp()
        self.mono = 1000.0 + (dt.timestamp() - self.wall)


class FakeLearner:
    def __init__(self):
        self.records = []
        self.tracks = []

    def is_ad_window(self, station, dt, tolerance=6):
        return True, "Test"

    def ad_start_minutes(self, station):
        return {28: 10, 58: 10}

    def ad_start_seconds(self, station):
        return 0

    def predict_duration(self, station, now=None):
        return 150.0

    def record_ad(self, station, start, end, source, confidence):
        self.records.append((start, end, source, confidence))
        return len(self.records)

    def record_track(self, station, artist, title, start_wall, bpm=0, energy=0):
        self.tracks.append((artist, title))


def hist(entries):
    out = []
    for at, dur, artist, title in entries:
        out.append({
            "airtime": at.isoformat(),
            "duration": str(dur),
            "song": {"entry": [{"title": title, "artist": {"entry": [{"name": artist}]}}]},
        })
    return {"result": {"entry": out}}


class RBDetectorTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.clock.at(19, 27, 40)
        self.learner = FakeLearner()
        self.schedule = AdSchedule()
        self.det = RBDetector(type("C", (), {
            "delay": 6.5, "rb_long_gap": 90.0, "rb_min_gap": 20.0, "rb_aggressive": False,
            "keep_news": True, "rb_poll": 3.0,
        })(), self.schedule, self.learner)
        self.det.set_station({"id": "rb-69", "rb_station_id": "69", "group": "Radio Bob"})
        self.det._icy_title = "BANNERS - Someone To You"
        self.banners = datetime.datetime(2026, 11, 25, 19, 23, 59, tzinfo=TZ)
        self.don = datetime.datetime(2026, 11, 25, 19, 28, 0, tzinfo=TZ)
        self.bon = datetime.datetime(2026, 11, 25, 19, 29, 11, tzinfo=TZ)

    def poll(self, entries):
        with patch("radio_adfree.app.detect.time.time", self.clock.time), \
             patch("radio_adfree.app.detect.time.monotonic", self.clock.monotonic), \
             patch("radio_adfree.app.detect._get_json", return_value=hist(entries)):
            self.det.poll_once("69")

    def test_pre_ad_moderation_is_kept_and_scheduled_block_starts_at_28(self):
        self.poll([(self.banners, 215, "BANNERS", "Someone To You"),
                   (self.don, 277, "Don Henley", "The Boys of Summer")])
        self.assertIsNotNone(self.det.gap)
        self.assertTrue(self.det.gap["candidate"])
        self.assertEqual(datetime.datetime.fromtimestamp(
            self.det.gap["scheduled_start_wall"], TZ).strftime("%H:%M"), "19:28")
        # no replacement yet: the moderation before :28 stays live
        self.assertEqual(self.schedule.active(), [])

    def test_preempted_api_song_does_not_end_the_block(self):
        entries = [(self.banners, 215, "BANNERS", "Someone To You"),
                   (self.don, 277, "Don Henley", "The Boys of Summer")]
        self.clock.advance(25)  # 19:28:05
        self.poll(entries)
        self.assertIsNotNone(self.det.gap)
        self.assertEqual(len(self.schedule.active()), 1)
        self.assertIsNone(self.schedule.active()[0]["end"])
        self.assertEqual(self.learner.records, [])

        self.clock.advance(45)  # 19:28:50
        self.poll(entries)
        self.assertIsNotNone(self.det.gap)
        self.assertIsNone(self.schedule.active()[0]["end"])
        self.assertEqual(self.learner.records, [])

    def test_real_next_song_ends_the_block(self):
        entries = [(self.banners, 215, "BANNERS", "Someone To You"),
                   (self.don, 277, "Don Henley", "The Boys of Summer")]
        self.clock.advance(25)
        self.poll(entries)
        self.clock.advance(66)  # 19:29:11
        entries.append((self.bon, 240, "Bon Jovi", "Have a Nice Day"))
        self.poll(entries)
        self.assertIsNone(self.det.gap)
        self.assertEqual(self.det.current_song["title"], "Have a Nice Day")
        self.assertEqual(len(self.learner.records), 1)
        start, end, source, _ = self.learner.records[0]
        self.assertEqual(datetime.datetime.fromtimestamp(start, TZ).strftime("%H:%M"), "19:28")
        self.assertEqual(datetime.datetime.fromtimestamp(end, TZ).strftime("%H:%M:%S"), "19:29:11")


    def test_long_pre_ad_moderation_is_kept_until_28(self):
        # 18:24:31 -> 18:29:38 is a 5 min gap: moderation + ads. Only the
        # block from 18:28:00 to the next song may be replaced.
        prev = datetime.datetime(2026, 11, 25, 18, 19, 24, tzinfo=TZ)  # +307s -> 18:24:31
        self.clock.at(18, 24, 35)
        self.det._icy_title = "Def Leppard - Armageddon It"
        self.poll([(prev, 307, "Def Leppard", "Armageddon It")])
        self.assertIsNotNone(self.det.gap)
        self.assertTrue(self.det.gap["candidate"])
        self.assertEqual(datetime.datetime.fromtimestamp(
            self.det.gap["scheduled_start_wall"], TZ).strftime("%H:%M"), "18:28")
        # the 3.5 min moderation before the block is live
        self.assertEqual(self.schedule.active(), [])
        self.clock.advance(200)  # 18:27:55
        self.poll([(prev, 307, "Def Leppard", "Armageddon It")])
        self.assertEqual(self.schedule.active(), [])
        self.clock.advance(15)  # 18:28:10
        self.poll([(prev, 307, "Def Leppard", "Armageddon It")])
        self.assertEqual(len(self.schedule.active()), 1)
        nxt = datetime.datetime(2026, 11, 25, 18, 29, 38, tzinfo=TZ)
        self.clock.advance(92)  # 18:28:10 -> 18:29:42
        self.poll([(prev, 307, "Def Leppard", "Armageddon It"),
                   (nxt, 276, "Disturbed", "Down With the Sickness")])
        self.assertIsNone(self.det.gap)
        start, end, _, _ = self.learner.records[0]
        self.assertEqual(datetime.datetime.fromtimestamp(start, TZ).strftime("%H:%M"), "18:28")
        self.assertEqual(datetime.datetime.fromtimestamp(end, TZ).strftime("%H:%M:%S"), "18:29:38")


    def test_pre_ad_song_45s_before_58_does_not_close_block(self):
        prev = datetime.datetime(2026, 11, 25, 19, 52, 0, tzinfo=TZ)  # +287 -> 19:56:47
        self.clock.at(19, 56, 50)
        self.det._icy_title = "Daughtry - No Surprise"
        self.poll([(prev, 287, "Daughtry", "No Surprise")])
        self.assertIsNotNone(self.det.gap)
        self.assertEqual(datetime.datetime.fromtimestamp(
            self.det.gap["scheduled_start_wall"], TZ).strftime("%H:%M"), "19:58")
        # API lists a song 43 s into the pre-ad moderation; ICY still Daughtry.
        pre = datetime.datetime(2026, 11, 25, 19, 57, 30, tzinfo=TZ)
        self.clock.advance(42)  # 19:57:32
        self.poll([(prev, 287, "Daughtry", "No Surprise"), (pre, 240, "Kiss", "Heaven's On Fire")])
        self.assertIsNotNone(self.det.gap)
        self.assertEqual(self.schedule.active(), [])
        # 19:58:05: the learned block starts.
        self.clock.advance(33)
        self.poll([(prev, 287, "Daughtry", "No Surprise"), (pre, 240, "Kiss", "Heaven's On Fire")])
        self.assertEqual(len(self.schedule.active()), 1)
        # 19:59:58: ICY confirms the real next song -> close and record.
        real = datetime.datetime(2026, 11, 25, 19, 59, 58, tzinfo=TZ)
        self.clock.advance(113)
        self.det._icy_title = "Kiss - Heaven's On Fire"
        self.poll([(prev, 287, "Daughtry", "No Surprise"),
                   (pre, 240, "Kiss", "Heaven's On Fire"),
                   (real, 240, "Kiss", "Heaven's On Fire")])
        self.assertIsNone(self.det.gap)
        start, end, _, _ = self.learner.records[0]
        self.assertEqual(datetime.datetime.fromtimestamp(start, TZ).strftime("%H:%M"), "19:58")
        self.assertEqual(datetime.datetime.fromtimestamp(end, TZ).strftime("%H:%M:%S"), "19:59:58")


    def test_short_song_transition_five_min_before_block_closes(self):
        prev = datetime.datetime(2026, 11, 25, 19, 48, 59, tzinfo=TZ)  # +225 -> 19:52:44
        self.clock.at(19, 52, 46)
        self.det._icy_title = "RADIO BOB - Livestream National"
        self.poll([(prev, 225, "Daughtry", "No Surprise")])
        self.assertIsNotNone(self.det.gap)
        nxt = datetime.datetime(2026, 11, 25, 19, 52, 45, tzinfo=TZ)
        self.clock.advance(2)
        self.poll([(prev, 225, "Daughtry", "No Surprise"),
                   (nxt, 240, "Kiss", "Heaven's On Fire")])
        self.assertIsNone(self.det.gap)
        self.assertEqual(self.schedule.active(), [])

    def test_news_hour_is_kept_live(self):
        prev = datetime.datetime(2026, 11, 25, 19, 53, 14, tzinfo=TZ)  # ends 19:57:30
        self.clock.at(19, 57, 35)
        self.det._icy_title = "Poco - Call It Love"
        self.poll([(prev, 256, "Poco", "Call It Love")])
        self.clock.advance(30)  # 19:58:05
        self.poll([(prev, 256, "Poco", "Call It Love")])
        self.assertEqual(len(self.schedule.active()), 1)
        self.clock.advance(120)  # 20:00:05
        self.poll([(prev, 256, "Poco", "Call It Love")])
        iv = self.schedule.active()[0]
        self.assertIsNotNone(iv["end"])
        self.assertEqual(datetime.datetime.fromtimestamp(
            self.clock.wall - (self.clock.monotonic() - iv["end"]), TZ).strftime("%H:%M"), "20:00")
        # news is live; gap stays open until the real next song
        self.assertIsNotNone(self.det.gap)
        nxt = datetime.datetime(2026, 11, 25, 20, 1, 40, tzinfo=TZ)
        self.clock.advance(100)  # 20:00:05 -> 20:01:45
        self.poll([(prev, 256, "Poco", "Call It Love"), (nxt, 200, "Bon Jovi", "Have a Nice Day")])
        self.assertIsNone(self.det.gap)
        start, end, _, _ = self.learner.records[0]
        self.assertEqual(datetime.datetime.fromtimestamp(start, TZ).strftime("%H:%M"), "19:58")
        self.assertEqual(datetime.datetime.fromtimestamp(end, TZ).strftime("%H:%M"), "20:00")


class RADetectorTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.clock.at(10, 0, 0)
        self.learner = FakeLearner()
        self.schedule = AdSchedule()
        self.det = RADetector(type("C", (), {
            "delay": 6.5, "ra_poll": 0.5,
            "ra_ad_start_classes": ["COMMERCIAL", "Promotion"],
            "ra_ad_keep_classes": ["COMMERCIAL", "Promotion", "Cart", "Live"],
            "ra_program_classes": ["Music", "News", "Weather", "Traffic", "Moderation"],
        })(), self.schedule, self.learner)
        self.det.set_station({"id": "ra-rockantenne", "ra_mountpoint": "rockantenne",
                              "group": "Rock Antenne"})

    def payload(self, cls, at, artist="", title=""):
        return {"data": {"class": cls, "artist": artist, "title": title,
                         "starttime": at.isoformat()}}

    def poll(self, data):
        with patch("radio_adfree.app.detect.time.time", self.clock.time), \
             patch("radio_adfree.app.detect.time.monotonic", self.clock.monotonic), \
             patch("radio_adfree.app.detect._get_json", return_value=data):
            self.det.poll_once("rockantenne")

    def test_commercial_block_with_station_ids(self):
        t0 = datetime.datetime(2026, 11, 25, 10, 0, 0, tzinfo=TZ)
        self.poll(self.payload("Music", t0, "Ozzy", "Perry Mason"))
        self.clock.advance(60)
        self.poll(self.payload("COMMERCIAL", t0 + datetime.timedelta(seconds=60), "", "PAYPAL"))
        self.assertTrue(self.det.active)
        self.clock.advance(30)
        self.poll(self.payload("Live", t0 + datetime.timedelta(seconds=90), "", "ROCK ANTENNE"))
        self.assertTrue(self.det.active)
        self.clock.advance(5)
        self.poll(self.payload("COMMERCIAL", t0 + datetime.timedelta(seconds=95), "", "CCON"))
        self.assertTrue(self.det.active)
        self.clock.advance(60)
        self.poll(self.payload("Music", t0 + datetime.timedelta(seconds=155), "Bon Jovi", "Runaway"))
        self.assertFalse(self.det.active)
        self.assertEqual(len(self.learner.records), 1)
        start, end, source, _ = self.learner.records[0]
        self.assertEqual(round(end - start), 95)


class CrossfadeTest(unittest.TestCase):
    def test_equal_power_and_continuity(self):
        s = AdSchedule()
        s.add(100.0, 110.0, "test")
        t = np.arange(97.0, 113.0, 0.001)
        live, fill = s.gains(t, 2.5)
        self.assertTrue(np.allclose(live**2 + fill**2, 1.0, atol=1e-5))
        self.assertAlmostEqual(float(live[t < 98.7].min()), 1.0, places=3)
        self.assertAlmostEqual(float(fill[(t > 101.3) & (t < 108.7)].min()), 1.0, places=3)
        self.assertAlmostEqual(float(live[t > 111.3].min()), 1.0, places=3)
        # no discontinuity at the boundaries
        self.assertLess(float(np.max(np.abs(np.diff(live)))), 0.01)
        self.assertLess(float(np.max(np.abs(np.diff(fill)))), 0.01)


if __name__ == "__main__":
    unittest.main()


class NewsHourCloseTest(unittest.TestCase):
    def test_news_hour_stops_filler_before_late_next_song(self):
        clock = FakeClock()
        clock.at(20, 58, 23)
        learner = FakeLearner()
        schedule = AdSchedule()
        det = RBDetector(type("C", (), {
            "delay": 6.5, "rb_long_gap": 90.0, "rb_min_gap": 20.0,
            "rb_aggressive": False, "keep_news": True, "rb_poll": 3.0,
        })(), schedule, learner)
        det.set_station({"id": "rb-69", "rb_station_id": "69", "group": "Radio Bob"})
        start_wall, start_mono = clock.wall, clock.mono
        iv = schedule.set_open(start_mono, "rb:Test", 0.82)
        det.gap = {"candidate": True, "source": "rb:Test", "confidence": 0.82,
                   "start_wall": start_wall, "start_mono": start_mono,
                   "ad_start_wall": start_wall, "ad_start_mono": start_mono,
                   "scheduled_start_wall": start_wall, "interval": iv,
                   "news_done": False}
        hour = datetime.datetime(2026, 11, 25, 21, 0, 0, tzinfo=TZ).timestamp()
        self.assertTrue(det._maybe_news_close(hour + 7))
        self.assertTrue(det.gap["news_done"])
        self.assertIsNotNone(iv["end"])
        self.assertEqual(det.state["mode"], "Nachrichten")
        # idempotent: a second tick does not move the end
        self.assertFalse(det._maybe_news_close(hour + 20))
        self.assertIsNotNone(iv["end"])
