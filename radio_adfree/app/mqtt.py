"""MQTT discovery and state publishing (Home Assistant native entities, no login data)."""
from __future__ import annotations

import json
import logging
import threading

import paho.mqtt.client as mqtt

from . import ha, stations

log = logging.getLogger("mqtt")

PREFIX = "radio_adfree"
DISCOVERY = "homeassistant"


class MQTTClient:
    def __init__(self, controller, version: str):
        self.controller = controller
        self.version = version
        self.client: mqtt.Client | None = None
        self.connected = False
        self._started = False
        self._state = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._started or not self.controller.cfg.mqtt_enabled:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        cfg = self.controller.cfg
        host, port, user, pw = cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_password
        svc = ha.mqtt_service()
        if svc:
            host = svc.get("host", host)
            port = int(svc.get("port", port))
            user = svc.get("username", user)
            pw = svc.get("password", pw)
        if not host:
            log.warning("no MQTT service available; MQTT disabled")
            return
        cid = f"radio_adfree_{self.version}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, clean_session=True)
        if user:
            client.username_pw_set(user, pw)
        client.will_set(f"{PREFIX}/availability", "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self.client = client
        while True:
            try:
                client.connect(host, port, keepalive=45)
                client.loop_forever(retry_first_connection=True)
            except Exception as exc:
                log.warning("MQTT connection failed: %s", exc)
                threading.Event().wait(10)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.connected = False
        log.warning("MQTT disconnected (%s)", rc)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            log.warning("MQTT connect refused: %s", rc)
            return
        self.connected = True
        log.info("MQTT connected")
        self._publish_discovery()
        client.subscribe(f"{PREFIX}/cmd/#", qos=0)
        client.publish(f"{PREFIX}/availability", "online", retain=True)
        self.publish_state(self._state)

    def _device(self) -> dict:
        return {
            "identifiers": ["radio_adfree"],
            "name": "Radio AdFree",
            "manufacturer": "OpenHands",
            "model": "AdFree Radio Proxy",
            "sw_version": self.version,
        }

    def _pub(self, topic: str, payload, retain: bool = True) -> None:
        if self.client and self.connected:
            if not isinstance(payload, str):
                payload = json.dumps(payload, ensure_ascii=False)
            self.client.publish(topic, payload, retain=retain)

    def _sensor(self, key: str, name: str, unit: str = "", icon: str = "mdi:radio",
                device_class: str = "", state_class: str = "") -> None:
        cfg = {
            "name": name, "unique_id": f"radio_adfree_{key}",
            "state_topic": f"{PREFIX}/state/{key}",
            "availability_topic": f"{PREFIX}/availability",
            "icon": icon, "device": self._device(),
        }
        if unit:
            cfg["unit_of_measurement"] = unit
        if device_class:
            cfg["device_class"] = device_class
        if state_class:
            cfg["state_class"] = state_class
        self._pub(f"{DISCOVERY}/sensor/radio_adfree/{key}/config", cfg)

    def _publish_discovery(self) -> None:
        self._sensor("radio_title", "Radio Titel", icon="mdi:music-note")
        self._sensor("radio_artist", "Radio Interpret", icon="mdi:account-music")
        self._sensor("radio_class", "Radio Segment", icon="mdi:information-outline")
        self._sensor("filler", "Ersatzsong", icon="mdi:music-box-multiple")
        self._sensor("mode", "Status", icon="mdi:radio-tower")
        self._sensor("ad_remaining", "Werbung verbleibend", "s", "mdi:timer-outline")
        self._sensor("ads_today", "Werbung ersetzt heute", "", "mdi:counter", state_class="total_increasing")
        self._sensor("learned_duration", "Gelernte Werbedauer", "s", "mdi:brain")
        self._sensor("next_ad", "Nächster Werbeblock", "", "mdi:clock-outline")
        self._sensor("delay", "Verzögerung", "s", "mdi:timer-sand")
        self._sensor("buffer", "Puffer", "s", "mdi:database")
        self._sensor("stream_url", "Stream URL", "", "mdi:link-variant")
        self._sensor("stream_path", "Stream Pfad (Ingress)", "", "mdi:link-variant")
        self._sensor("station", "Sender", "", "mdi:radio")
        self._sensor("last_ad", "Letzte Werbung", "s", "mdi:history")

        self._pub(f"{DISCOVERY}/binary_sensor/radio_adfree/ad_break/config", {
            "name": "Werbung aktiv", "unique_id": "radio_adfree_ad_break",
            "state_topic": f"{PREFIX}/state/ad_break",
            "payload_on": "ON", "payload_off": "OFF",
            "availability_topic": f"{PREFIX}/availability",
            "icon": "mdi:advertisements", "device": self._device(),
        })

        names = [f"{s['group']} · {s['short']}" for s in stations.all_stations()]
        self._pub(f"{DISCOVERY}/select/radio_adfree/station/config", {
            "name": "Sender", "unique_id": "radio_adfree_station_select",
            "command_topic": f"{PREFIX}/cmd/station",
            "state_topic": f"{PREFIX}/state/station_name",
            "options": names,
            "availability_topic": f"{PREFIX}/availability",
            "icon": "mdi:radio", "device": self._device(),
        })
        self._pub(f"{DISCOVERY}/switch/radio_adfree/enabled/config", {
            "name": "Werbung ersetzen", "unique_id": "radio_adfree_enabled",
            "command_topic": f"{PREFIX}/cmd/enabled",
            "state_topic": f"{PREFIX}/state/enabled",
            "payload_on": "ON", "payload_off": "OFF",
            "availability_topic": f"{PREFIX}/availability",
            "icon": "mdi:advertisements-off", "device": self._device(),
        })
        self._pub(f"{DISCOVERY}/number/radio_adfree/delay/config", {
            "name": "Verzögerung", "unique_id": "radio_adfree_delay_set",
            "command_topic": f"{PREFIX}/cmd/delay",
            "state_topic": f"{PREFIX}/state/delay",
            "min": 5, "max": 10, "step": 0.5, "unit_of_measurement": "s",
            "availability_topic": f"{PREFIX}/availability",
            "icon": "mdi:timer-sand", "device": self._device(),
        })
        for key, name, icon in [
            ("was_ad", "War Werbung", "mdi:check-circle-outline"),
            ("was_no_ad", "War keine Werbung", "mdi:close-circle-outline"),
            ("test_ad", "Test-Werbung", "mdi:test-tube"),
            ("reconnect", "Sender neu verbinden", "mdi:refresh"),
        ]:
            self._pub(f"{DISCOVERY}/button/radio_adfree/{key}/config", {
                "name": name, "unique_id": f"radio_adfree_btn_{key}",
                "command_topic": f"{PREFIX}/cmd/button_{key}",
                "payload_press": "PRESS",
                "availability_topic": f"{PREFIX}/availability",
                "icon": icon, "device": self._device(),
            })

        self._pub(f"{DISCOVERY}/media_player/radio_adfree/player/config", {
            "name": "Radio AdFree", "unique_id": "radio_adfree_player",
            "state_topic": f"{PREFIX}/state/player",
            "command_topic": f"{PREFIX}/cmd/player",
            "power_command_topic": f"{PREFIX}/cmd/player_power",
            "volume_command_topic": f"{PREFIX}/cmd/volume",
            "volume_state_topic": f"{PREFIX}/state/volume",
            "volume_scale": "0.0-1.0",
            "media_title_topic": f"{PREFIX}/state/radio_title",
            "media_artist_topic": f"{PREFIX}/state/radio_artist",
            "source_state_topic": f"{PREFIX}/state/station_name",
            "source_command_topic": f"{PREFIX}/cmd/station",
            "source_list": names,
            "availability_topic": f"{PREFIX}/availability",
            "device_class": "speaker",
            "icon": "mdi:radio", "device": self._device(),
        })

    def publish_state(self, status: dict) -> None:
        if not self.connected:
            self._state = status
            return
        self._state = status
        try:
            radio = status.get("radio", {})
            ad = status.get("ad", {})
            learn = status.get("learn", {})
            today = status.get("today", {})
            engine = status.get("engine", {})
            st = status.get("station_info", {})
            urls = status.get("urls", {})
            enabled = "ON" if status.get("enabled") else "OFF"
            values = {
                "radio_title": radio.get("title", ""),
                "radio_artist": radio.get("artist", ""),
                "radio_class": radio.get("class", ""),
                "filler": status.get("filler_text", ""),
                "mode": status.get("mode", ""),
                "ad_remaining": round(float(ad.get("remaining", 0)), 1),
                "ads_today": int(today.get("ads", 0)),
                "learned_duration": round(float(learn.get("predicted_duration", 0)), 1),
                "next_ad": learn.get("window") or "",
                "delay": round(float(status.get("delay", {}).get("target", 0)), 1),
                "buffer": round(float(engine.get("buffer_seconds", 0)), 1),
                "stream_url": urls.get("direct", ""),
                "stream_path": urls.get("stream", ""),
                "station": st.get("name", status.get("station", "")),
                "station_name": st.get("label", ""),
                "last_ad": round(float(learn.get("last_duration", 0)), 1),
                "enabled": enabled,
                "ad_break": "ON" if ad.get("active") else "OFF",
                "player": "playing" if status.get("enabled") else "off",
                "volume": round(float(status.get("volume", 0.8)), 2),
            }
            for k, v in values.items():
                self._pub(f"{PREFIX}/state/{k}", v)
        except Exception:
            log.exception("state publish failed")

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", "replace")
            key = msg.topic.rsplit("/", 1)[-1]
            log.info("MQTT command %s = %s", key, payload)
            self.controller.handle_mqtt_command(key, payload)
        except Exception:
            log.exception("MQTT command failed")
