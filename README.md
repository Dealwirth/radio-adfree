# Radio AdFree — Home Assistant Add-on

Werbefreies **RADIO BOB!** und **ROCK ANTENNE** für Home Assistant — mit
gelerntem Werbe-Erkenner, legalen CC-BY-Instrumental-Fillern, echtem
Equal-Power-Crossfade und einer nativen HA-Oberfläche.

> **Real getestet am 25.11.2026:** echter RB-Werbeblock 20:28 (105 s) im
> Add-on vollständig ersetzt, Moderation davor live, danach Jimi Hendrix;
> Delay 5,95 s; 9/9 Unit-Tests; Dashboard + Lovelace-Karte in HA angelegt.
> Zusätzlich: Rock Antenne `COMMERCIAL`, RB `:28`/`:58`-Raster inkl.
> Nachrichten-Split und simulierte Blöcke.

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
- **Verzögerung:** Ziel 5,5 s; gemessen **7,95 s** gegenüber dem echten Radio
  (Kreuzkorrelation, 50-ms-Raster; innerhalb der geforderten 5–10 s).
  Nachrichten kommen dadurch relativ pünktlich.
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
| `port` | `18097` | Interner HTTP-Port (Ingress; fest auf 18097 abgestimmt) |

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

Das Add-on liefert die native Lovelace-Karte selbst unter `/card.js` aus; in
dieser Installation sind Dashboard **Radio AdFree** (`/radio-adfree`) und
Ressource automatisch angelegt:

- Ressource: `/api/hassio_ingress/<INGRESS>/card.js`, Typ `module`
- Dashboard: `radio-adfree` → View `radio` → Karte `custom:radio-adfree-card`

**Manuell (andere HA-Instanz):**

1. `dashboard/radio-adfree-card.js` nach `/config/www/` kopieren.
2. Einstellungen → Dashboards → Ressourcen → URL `/local/radio-adfree-card.js`,
   Typ **JavaScript-Modul**.
3. Karte `type: custom:radio-adfree-card` hinzufügen.

Zusätzlich: Player-Seite `…/player` (Sender, Quellen, Lautstärke, Demo, Live)
und Beispiel-Dashboard `dashboard/radio-adfree-dashboard.yaml`.

Die Karte nutzt die MQTT-Entitäten. In dieser HA-Instanz heißen sie deutsch;
die Karte löst beide Varianten auf:

```
sensor.radio_adfree_radio_titel / _interpret / _segment
sensor.radio_adfree_status / _ersatzsong / _werbung_verbleibend
sensor.radio_adfree_werbung_ersetzt_heute / _gelernte_werbedauer
sensor.radio_adfree_nachster_werbeblock / _puffer / _verzogerung
sensor.radio_adfree_stream_url / _stream_pfad_ingress
binary_sensor.radio_adfree_werbung_aktiv
select.radio_adfree_sender
switch.radio_adfree_werbung_ersetzen
number.radio_adfree_verzogerung
button.radio_adfree_test_werbung / _war_werbung / _war_keine_werbung / _sender_neu_verbinden
```

> **Hinweis `media_player`:** Diese HA-Instanz legt die MQTT-`media_player`-
> Discovery nicht an (Sensoren/Schalter schon). Die Karte spielt daher den
> Stream direkt im Browser; für Echo/Music Assistant/DLNA dient
> `sensor.radio_adfree_stream_url` (oder die Option `target_player`).

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

### Real: 25.11.2026, RB-Werbeblock 20:28

| Prüfung | Lokale App (Testlib) | HA-Add-on 1.1.3 |
|---|---|---|
| Erkennung | `rb:gelernt`, 125 s (Median) | `rb:Erfahrungswert`, 150 s |
| Echte Blocklänge | **105 s** | **105 s** (am echten nächsten Song geschlossen) |
| Filler | „Neo Western“, 84 BPM | „Motivator“, 126 BPM (Radio 117 BPM) |
| Moderation davor | 5,9 s live | 4,3 s live |
| Rückkehr | 20:29:52 Programm, Jimi Hendrix | 20:29:52 Programm, Jimi Hendrix |
| Lernen | count 5, last 105 s | count 1, last 105 s |
| Delay | 5,5/5,99 s | 5,5/5,95 s |

Audio-Mitschnitt (310 s): Filler von Sekunde 66–171 (105 s), RMS −16…−32 dB,
Crossfade an beiden Kanten, keine NaN/Clipping; ZCR Filler 0,036 vs. Radio
0,148 (Testlib-Filler ruhiger; das Add-on wählt aus 23 Tracks „Motivator“
passend zu 117 BPM / 0,63 Energie).

### Weitere Tests

| Test | Ergebnis |
|---|---|
| Senderkatalog | 89 Streams, live erreichbar |
| Rock-Antenne-API | `COMMERCIAL`/`Promotion`/`Cart`/`Live` beobachtet |
| RB-Werbeblock 19:58 | nach App-Neustart nur letzte Sekunde ersetzt (bekannt) |
| RB-Werbeblock 20:28 | **vollständig ersetzt**, siehe oben |
| RB-Werbeblock 20:58 + Nachrichten 21:00 | siehe Monitor3 (läuft) |
| Unit-Tests | **9/9 grün** |
| Delay | **5,95 s** (Add-on), Ziel 5,5 s |
| Lautheit | Filler-Gain automatisch ~0,65, RMS ~−20 dB |

Die Live-Delay-Messung erfolgt per Kreuzkorrelation der RMS-Hüllkurven von
Live-Stream und Ausgabe (50-ms-Raster); `tests/measure_delay.py`.

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
| `GET /card.js` | Lovelace-Karte (vom Add-on ausgeliefert) |

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
- **Blocklängen variieren** (real 105 s, gelernt 125 s, Erstschätzung 150 s).
  Der Detektor schließt am echten nächsten Song und lernt die Dauer; die
  Erstschätzung ist nur der Startwert.
- **`media_player.radio_adfree`:** Diese HA-Instanz legt die MQTT-
  `media_player`-Discovery nicht an (Sensoren/Schalter schon). Die Karte
  spielt den Stream im Browser; für Echo/Music Assistant/DLNA dient
  `sensor.radio_adfree_stream_url` (oder `target_player`).
- **Filler-Bibliothek:** 23 CC-BY-Tracks; Auswahl BPM-/Energie-basiert. Mehr
  Tracks über `filler_max_tracks` erhöhen die Trefferquote.
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

---

## Changelog

- **1.1.3** — Karte wird vom Add-on unter `/card.js` ausgeliefert; Karte löst
  deutsche/englische Entity-IDs auf; Stationsfarbe/Logo; 20:28-Realtest.
- **1.1.2** — `media_player`-Discovery bereinigt, `CancelledError` beim Stop,
  Karten-Fixes.
- **1.1.1** — MQTT über HA-WebSocket (keine Broker-Zugangsdaten),
  44,1 kHz/Decoder-Loop-Fix.
- **1.1.0** — Port 18097, WS-MQTT, RB-Detektor-Fixes (geplante Blöcke, Gap,
  4-min-Lookahead).
- **1.0.0** — erstes Add-on.
