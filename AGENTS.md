# AGENTS.md — Radio AdFree (Home-Assistant-Add-on)

## Projekt
- Repo `Dealwirth/radio-adfree`; Add-on-Slug `e005aec8_radio_adfree`; Port **18097**; Version **1.1.3**.
- HA OS 2026.11.3; MQTT-Integration vorhanden. **Wichtig:** Diese HA-Instanz legt die MQTT-`media_player`-Discovery nicht an (Sensor/Schalter/Select/Number/Button/Binary schon).
- Dashboard `radio-adfree` (sections) + Karte `custom:radio-adfree-card`; Ressource `/api/hassio_ingress/<INGRESS>/card.js` (Typ `module`).

## Architektur
- `radio_adfree/app/`: `audio.py` (44,1 kHz Stereo, Delay-Ringpuffer, Equal-Power-Crossfade), `detect.py` (RA/RB-Detektor + Learner), `filler.py` (CC-BY-Library, BPM/Energy-Auswahl), `mqtt.py` (HA-WebSocket via `call_service mqtt.publish` + `mqtt/subscribe`), `web.py` (`/stream.mp3`, `/status`, `/player`, `/card.js`), `main.py`.
- `dashboard/radio-adfree-card.js` (Quelle) + `radio_adfree/app/card.js` (Deploy-Kopie, vom Add-on ausgeliefert).
- Tests: `radio_adfree/tests/` (9/9 grün).

## Befehle
```bash
cd /workspace/project && PYTHONPATH=. python3 -m unittest discover -s radio_adfree/tests -q
cd radio_adfree && python3 -m app     # lokal, FFMPEG=/workspace/tools/ffmpeg
```
- Lokal: `RADIO_ADFREE_DATA=/workspace/radio_adfree_data`, `FFMPEG=/workspace/tools/ffmpeg`, `FFPROBE=/workspace/tools/ffprobe`; App auf `127.0.0.1:8097`.
- Add-on: `ha_manage_app(action='check_updates'/'update')`; Log `ha_get_logs(source='supervisor', slug='e005aec8_radio_adfree')`; Status via Proxy `/status`.

## Reale Tests (25.11.2026)
- 20:28 RB-Block: real **105 s**; lokal `rb:gelernt` 125 s, Add-on `rb:Erfahrungswert` 105 s; Filler „Neo Western“ (84 BPM) / „Motivator“ (126 BPM); Moderation 5,9/4,3 s live; Rückkehr Jimi Hendrix; Delay 5,5/5,95 s.
- 20:58 + 21:00 Nachrichten: Monitor3, Ergebnis nachtragen (keep_news/Pünktlichkeit).

## Quirks
- MQTT ohne Broker-Credentials: HA-WebSocket `call_service`/`subscribe`; `SUPERVISOR_TOKEN`-Guard.
- Kein `paho` `self.client`; `stop_async` fängt `BaseException` (CancelledError).
- Samplerate 44,1 kHz (nicht 48), sonst negativer `live_lag`.
- Filler-Auswahl über BPM/Energy; 23 CC-BY-Tracks; `filler_max_tracks` erhöhen.
- Karte löst deutsche und englische Entity-IDs auf.
