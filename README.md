# Radio AdFree — Home Assistant Add-on

Werbefreies **RADIO BOB!** und **ROCK ANTENNE** für Home Assistant — mit
gelerntem Werbe-Erkenner, legalen CC-BY-Instrumental-Fillern, echtem
Equal-Power-Crossfade und einer nativen HA-Oberfläche.

> Getestet mit echten Werbeblöcken (Rock Antenne `COMMERCIAL`, Radio Bob
> `:28`/`:58`-Raster inkl. Nachrichten-Split), 7 Unit-Tests, simulierten
> Werbeunterbrechungen und einer Live-Delay-Messung per Kreuzkorrelation.

---

## Was es macht

- Spielt **89 Sender** von Radio Bob und Rock Antenne (Hauptstreams + Genre-
  und Regionalstreams).
- Erkennt Werbung:
  - **Rock Antenne:** offizielle Now-Playing-API mit `class`-Feld
    (`COMMERCIAL`, `Promotion`, `Cart`, `Live`, `Music`, `News`, …).
  - **Radio Bob:** vollständige Musik-History (`search.json`) + ICY-Metadaten
    als Bestätigung. Vorab von der API gelistete, aber durch Werbung
    verdrängte Songs werden **nicht** als echte Songs gewertet.
- Ersetzt Werbeblöcke durch **legal lizenzierte Instrumental-Rock-Musik**
  (Kevin MacLeod, CC BY 4.0) und blendet mit **Equal-Power-Crossfade** zurück.
- **Loudness-Matching:** Filler wird auf die Lautheit des laufenden Radios
  angeglichen (kein Lautstärkesprung).
- **Moderation, Wetter, Nachrichten und Verkehr bleiben live.** Bei Radio Bob
  wird die Moderation vor dem `:28`/`:58`-Block nicht angetastet und die
  Nachrichten zur vollen Stunde bleiben erhalten.
- **Lernend:** SQLite speichert Werbedauern, Blockstart-Minuten, Filler-
  Historie und Nutzer-Feedback. Die Startzeit wird mit jedem Block präziser.
- **Dashboard:** native Lovelace-Karte + eigenständige Player-Seite im
  Add-on-Panel. Radio-Titel und Filler-Titel werden überblendet angezeigt,
  inklusive Werbe-Countdown und Lernstatistik.
- **Verzögerung:** Ziel 5,5 s; gemessen ~8–9 s gegenüber dem echten Radio
  (innerhalb der geforderten 5–10 s). Nachrichten kommen dadurch relativ
  pünktlich.
- **Keine Login-Daten:** nutzt nur öffentliche Streams/APIs, den lokalen
  Supervisor-Token und den HA-MQTT-Broker.

---

## Installation

### 1. Add-on-Repository hinzufügen

Home Assistant → **Einstellungen → Add-ons → Add-on Store** → oben rechts
**⋮ → Repositories** → URL eintragen:

```
https://github.com/Dealwirth/radio-adfree
```

Danach **Radio AdFree** installieren und starten. Der erste Start lädt
FFmpeg-Abhängigkeiten über das Docker-Image und (optional) 24 Filler-Tracks
(~100 MB) nach `/data/filler`. Drei Tracks sind bereits im Image gebündelt,
damit es sofort funktioniert.

### 2. Add-on konfigurieren

| Option | Standard | Bedeutung |
|---|---|---|
| `station` | `rb-69` | Start-Sender (Radio Bob National) |
| `delay` | `5.5` | Delays in Sekunden (5–10). Höher = mehr Vorlauf für die Werbeerkennung |
| `crossfade` | `2.5` | Crossfade-Länge in Sekunden |
| `enabled` | `true` | Werbeersetzung an/aus |
| `replace_ads` | `true` | Werbung ersetzen |
| `keep_news` | `true` | Nachrichten zur vollen Stunde live lassen |
| `rb_aggressive` | `false` | Radio Bob: auch Off-Schedule-Lücken ab 20 s ersetzen (riskanter) |
| `rb_long_gap` | `180` | Radio Bob: Off-Schedule-Lücken erst ab dieser Länge ersetzen |
| `filler_download` | `true` | CC-BY-Filler automatisch laden |
| `filler_max_tracks` | `24` | Maximale Anzahl heruntergeladener Filler |
| `target_player` | `""` | Optional: `media_player.…` (z. B. Echo/DLNA) |
| `target_service` | `media_player` | `media_player` oder `music_assistant` |
| `output_bitrate` | `192` | MP3-Bitrate des Ausgabestreams |
| `port` | `8097` | Interner HTTP-Port (Ingress) |

Eigene Musik (MP3/FLAC/OGG/WAV) kann zusätzlich unter
`/share/radio_adfree/music/` abgelegt werden. Sie wird automatisch erkannt.

### 3. Hören

- **Add-on-Panel:** Seitenleiste → **Radio AdFree** → Player-Seite mit
  Senderauswahl, Play-Button, Titeln, Werbestatus und Statistiken.
- **Im Dashboard:** siehe unten.
- **Externe Player:** `sensor.radio_adfree_stream_url` liefert die direkte
  HTTP-URL (z. B. für DLNA, VLC, Music Assistant).

---

## Dashboard

Die native Karte liegt in `dashboard/radio-adfree-card.js`.

**Variante A — Add-on-Panel (ohne Karte):** einfach das Panel öffnen.

**Variante B — Lovelace-Karte:**

1. Datei `dashboard/radio-adfree-card.js` nach `/config/www/radio-adfree-card.js`
   kopieren (File Editor oder Samba).
2. Einstellungen → Dashboards → Ressourcen → **Ressource hinzufügen**
   - URL: `/local/radio-adfree-card.js`
   - Typ: **JavaScript-Modul**
3. Dashboard bearbeiten → Karte hinzufügen → **Manuell**:

```yaml
type: custom:radio-adfree-card
```

Alternativ liegt ein komplettes Beispiel unter
`dashboard/radio-adfree-dashboard.yaml`.

Die Karte nutzt die vom Add-on per MQTT-Discovery erzeugten Entitäten:

```
sensor.radio_adfree_radio_title
sensor.radio_adfree_radio_artist
sensor.radio_adfree_filler
sensor.radio_adfree_mode
sensor.radio_adfree_ad_remaining
sensor.radio_adfree_ads_today
sensor.radio_adfree_learned_duration
sensor.radio_adfree_next_ad
sensor.radio_ad_free_delay / buffer / stream_path / stream_url
binary_sensor.radio_adfree_ad_break
select.radio_adfree_station
switch.radio_adfree_enabled
number.radio_adfree_delay
button.radio_adfree_was_ad / was_no_ad / test_ad / reconnect
media_player.radio_adfree
```

---

## Wie die Erkennung funktioniert

### Rock Antenne

Die offizielle API
`https://www.rockantenne.de/api/metadata/now/<mountpoint>` liefert pro
Sekunde u. a.:

```json
{"class": "COMMERCIAL", "starttime": "...", "artist": "", "title": "PAYPAL"}
```

Der Erkenner startet einen Werbeblock bei `COMMERCIAL`/`Promotion`, hält ihn
über `Cart`/`Live`/`Promotion` hinweg offen und beendet ihn beim ersten
`Music`/`News`/`Weather`/`Traffic`/`Moderation`. Dadurch werden auch
mehrere Spots mit kurzen Station-IDs dazwischen als **ein** Block ersetzt.
Echte Transkripte (Whisper) bestätigten PayPal- und Messe-Spots.

### Radio Bob

`https://iris-bob.loverad.io/search.json?station=<id>&start=…&end=…` liefert
die vollständige Musik-History mit `airtime` und `duration`. Eine Lücke
zwischen zwei Songs ist ein Nicht-Musik-Segment. Die API listet aber
manchmal Songs, die durch Werbung **verdrängt** wurden. Deshalb gilt:

- Ein API-Song wird erst als echt akzeptiert, wenn die **ICY-Metadaten** ihn
  bestätigen oder er nachweislich frisch startet.
- Das Senderraster ist gelernt: Werbeblöcke starten bei **:28** und **:58**.
- Eine Lücke, die **vor** dem Block beginnt, ist Moderation → sie bleibt live.
  Erst ab dem gelernten Blockstart wird ersetzt.
- Lücken zur vollen Stunde werden **geteilt**: Werbung bis `:00` ersetzen,
  Nachrichten ab `:00` live lassen.
- Off-Schedule-Lücken werden erst ab `rb_long_gap` (Standard 180 s) ersetzt,
  damit lange Moderation nicht fälschlich verschwindet.

### Audio-Pipeline

```
ICY-Stream ──► MP3-Chunks ──► ffmpeg ──► PCM 44,1 kHz stereo
                                              │
                                              ▼
                                    Delay-Ringpuffer (5,5 s)
                                              │
     Filler (CC BY) ──► 2-Deck-Player ──► Equal-Power-Crossfade
                                              │
                                              ▼
                                    ffmpeg MP3 192 kbps ──► HTTP
```

- Der ICY-Leser läuft am Live-Rand (für Metadaten); ein kurzer Puffer
  verhindert, dass der Icecast-Burst die Verzögerung aufbläht.
- Der Crossfade ist ein echter **Equal-Power-Fade** (`cos² + sin² = 1`) mit
  2,5 s Länge, kein hartes Schneiden.
- Filler wird auf die **Live-Lautheit** normiert (RMS-Matching) und bei Bedarf
  per Limiter begrenzt.
- Der Filler wird nach **BPM/Energie** des zuletzt gehörten Radiosongs
  ausgewählt und nicht innerhalb von 90 Minuten wiederholt.
- Ein PCM-Cache (4 Tracks) sorgt dafür, dass der Filler auch bei kurzem
  Vorlauf sofort startet.

### Lernen

SQLite (`/data/radio_adfree.db`) speichert:

- Werbedauer und Startminute jedes Blocks,
- bestätigte/abgelehnte Blöcke (Feedback-Buttons),
- gespielte Filler inkl. angespielter Dauer,
- Radio-Track-Historie.

`predict_duration()` liefert eine gewichtete Median-Dauer (ähnliche Uhrzeit
und Wochentag stärker gewichtet); `ad_start_minutes()` baut das Startraster.
Mit „War keine Werbung“ wird ein Fehlalarm aus der Lernbasis entfernt.

---

## Getestete Ergebnisse

| Test | Ergebnis |
|---|---|
| Senderkatalog | 89 Streams, alle live erreichbar (HTTP 200, Audio) |
| Rock-Antenne-API | `COMMERCIAL`/`Promotion`/`Cart`/`Live` live beobachtet |
| RA-Werbetranskript | PayPal, CCON/Messe, Promo „Hello from Heaven“ (Whisper) |
| RB-Werbetranskript | Simmon Mobile, Teufel, Podcast-Promo + Nachrichten (Whisper) |
| RB-Nachrichten-Split | Werbung bis `:00`, Nachrichten live ab `:00` |
| Unit-Tests | 7/7 grün (Erkennung, Pre-Emption, News-Split, Crossfade) |
| Simulierter 20-s-Block | Filler ~−19 dB (wie Radio), kein Klick (max. Sprung 0,316 vs. 0,282 Musik) |
| Delay-Messung | 9,2 s bei Ziel 6,5 s; Ziel 5,5 s → ~8–9 s |
| Lautheit | Filler-Gain automatisch 0,54–0,61 bei RMS −14,6 dB |

Die Delay-Messung erfolgt per Kreuzkorrelation der RMS-Hüllkurven von
Live-Stream und Ausgabe (50-ms-Raster). Durchführung:
`tests/measure_delay.py` (benötigt einen laufenden Add-on-Stream).

---

## API

| Endpoint | Zweck |
|---|---|
| `GET /stream.mp3` | MP3-Ausgabestream (192 kbps) |
| `GET /status` | vollständiger JSON-Status |
| `GET /api/stations` | Senderkatalog |
| `POST /api/station` | `{"id":"rb-69"}` Sender wechseln |
| `POST /api/enabled` | `{"enabled":true}` Werbeersetzung an/aus |
| `POST /api/feedback` | `{"is_ad":true|false}` letzten Block bewerten |
| `POST /api/simulate_ad` | `{"seconds":20}` Test-Werbeblock |
| `GET /api/selftest` | Selbsttest (Stream, Puffer, Encoder, API, DB, MQTT) |
| `GET /player` | Player-Seite |

---

## Rechtliches

- Die Streams von Radio Bob und Rock Antenne sind öffentliche Webstreams.
  Dieses Projekt hostet oder verbreitet sie nicht; es schaltet für den
  **privaten Hörgenuss** zwischen Live-Stream und lokal lizenzierter Musik um.
- Werbung wird nicht „entschlüsselt“ oder ein Paywall-Zugang umgangen. Es
  werden keine Audiodaten gespeichert oder weitergegeben.
- Filler-Musik: **Kevin MacLeod (incompetech.com), CC BY 4.0** — siehe
  `ATTRIBUTION.md`. Die Namensnennung ist im Player und in der Karte sichtbar.
- Sendernamen, Logos und Marken gehören den jeweiligen Rechteinhabern.
- Nutzung auf eigenes Risiko; keine Garantie für 100 % Werbefreiheit bei
  einem Live-Stream ohne offizielle Ad-Marker.

---

## Grenzen (ehrlich)

- **Rock Antenne** liefert explizite Werbe-Klassen → sehr präzise.
- **Radio Bob** liefert keine offiziellen Ad-Marker. Die Erkennung basiert auf
  dem gelernten Senderraster, der vollständigen History und ICY-Bestätigung.
  Bei stark abweichendem Sonderprogramm (z. B. langer Moderation genau im
  Werbefenster) kann ein Block verpasst oder zu spät erkannt werden. Die
  Feedback-Buttons und `rb_long_gap`/`rb_aggressive` sind dafür da.
- Die Verzögerung ist bewusst endlich (5–10 s). Nachrichten laufen dadurch
  einige Sekunden versetzt, nicht sekundengenau.
- Ein laufender Player muss den Stream neu laden, wenn der Add-on-Neustart
  erfolgt; die Dashboard-Karte und die Player-Seite tun das automatisch.

---

## Entwicklung

```bash
cd radio_adfree
python3 -m unittest discover -s tests -v
python3 -m app            # lokal, benötigt ffmpeg + aiohttp + numpy + paho-mqtt
```

Die Tests decken ab:

- Rock-Antenne-Block mit `Cart`/`Live`/`COMMERCIAL`-Wechseln,
- Radio-Bob-Lücke mit vorab gelistetem, aber verdrängtem Song,
- lange Moderation vor dem `:28`-Block,
- Nachrichten-Split zur vollen Stunde,
- Equal-Power-Crossfade und Stetigkeit,
- Test-/Simulationspfad.

---

## Danksagung

- Kevin MacLeod / Incompetech für die CC-BY-Musik.
- Radio Bob und Rock Antenne für die öffentlichen Streams und Metadaten.
- Home Assistant, Music Assistant und der HA-MCP-Community.
