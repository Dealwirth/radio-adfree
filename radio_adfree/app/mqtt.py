"""MQTT discovery and state publishing (Home Assistant native entities, no login data)."""
from __future__ import annotations

import asyncio
import json
import logging
import os

import aiohttp

from . import stations

log = logging.getLogger("mqtt")

PREFIX = "radio_adfree"
DISCOVERY = "homeassistant"


class MQTTClient:
    """Discovery/state publisher using Home Assistant's MQTT WebSocket API.

    The MQTT integration owns the broker connection, so the add-on needs no
    broker host/user/password. Publishing uses the standard ``call_service``
    command (``mqtt.publish``); commands are received via ``mqtt/subscribe``.
    """

    def __init__(self, controller, version: str):
        self.controller = controller
        self.version = version
        self.connected = False
        self._started = False
        self._state: dict = {}
        self._ws = None
        self._session = None
        self._task = None
        self._stop = False
        self._msg_id = 0

    def start(self) -> None:
        if self._started or not self.controller.cfg.mqtt_enabled:
            return
        self._started = True
        if not os.environ.get("SUPERVISOR_TOKEN"):
            log.info("no Supervisor token (not an add-on); MQTT disabled")
            return
        loop = getattr(self.controller, "loop", None)
        if loop is None:
            log.warning("no event loop available; MQTT disabled")
            return
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    "ws://supervisor/core/websocket", heartbeat=30)
                await self._ws.receive_json()  # auth_required
                await self._ws.send_json({
                    "type": "auth",
                    "access_token": os.environ.get("SUPERVISOR_TOKEN", ""),
                })
                auth = await self._ws.receive_json()
                if auth.get("type") != "auth_ok":
                    raise RuntimeError(f"auth failed: {auth}")
                self.connected = True
                backoff = 1.0
                log.info("MQTT via HA WebSocket connected")
                self._publish_discovery()
                await self._ws_send({"type": "mqtt/subscribe",
                                     "topic": f"{PREFIX}/cmd/#", "qos": 0})
                await self._publish(f"{PREFIX}/availability", "online", True)
                if self._state:
                    await self._publish_values(self._state)
                async for msg in self._ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    if data.get("type") == "event":
                        ev = data.get("event") or {}
                        topic = ev.get("topic")
                        if topic:
                            self._on_message(topic, str(ev.get("payload", "")))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("HA WS MQTT: %s", exc)
            finally:
                self.connected = False
                try:
                    if self._ws:
                        await self._ws.close()
                except Exception:
                    pass
                try:
                    if self._session:
                        await self._session.close()
                except Exception:
                    pass
                self._ws = None
                self._session = None
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

    async def _ws_send(self, payload: dict) -> None:
        if not self._ws:
            return
        self._msg_id += 1
        payload["id"] = self._msg_id
        await self._ws.send_json(payload)

    async def _publish(self, topic: str, payload, retain: bool = True) -> None:
        if not self._ws:
            return
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        await self._ws_send({
            "type": "call_service", "domain": "mqtt", "service": "publish",
            "service_data": {"topic": topic, "payload": payload,
                             "qos": 0, "retain": retain},
        })

    async def _publish_values(self, status: dict) -> None:
        try:
            values = self._values(status)
            for k, v in values.items():
                await self._publish(f"{PREFIX}/state/{k}", v, True)
        except Exception:
            log.exception("state publish failed")

    def _pub(self, topic: str, payload, retain: bool = True) -> None:
        if not self.connected:
            return
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        try:
            asyncio.get_running_loop().create_task(self._publish(topic, payload, retain))
        except RuntimeError:
            loop = getattr(self.controller, "loop", None)
            if loop is not None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._publish(topic, payload, retain), loop)
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop = True
        loop = getattr(self.controller, "loop", None)
        if loop is not None and self._task is not None:
            try:
                loop.call_soon_threadsafe(self._task.cancel)
            except Exception:
                pass

    async def stop_async(self) -> None:
        self._stop = True
        if self.connected:
            try:
                await self._publish(f"{PREFIX}/availability", "offline", True)
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    def _device(self) -> dict:
        return {
            "identifiers": ["radio_adfree"],
            "name": "Radio AdFree",
            "manufacturer": "OpenHands",
            "model": "AdFree Radio Proxy",
            "sw_version": self.version,
        }

    def _sensor(self, key: str, name: str, unit: str = "", icon: str = "mdi:radio",
                device_class: str = "", state_class: str = "") -> None:
        cfg = {
            "name": name, "unique_id": f"radio_adfree_{key}",
            "state_topic": f"{PREFIX}/state/{key}",
            "availability_topic": f"{PREFIX}/availability",
            "expire_after": 30,
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
            "expire_after": 30,
            "icon": "mdi:advertisements", "device": self._device(),
        })

        names = [f"{s['group']} · {s['short']}" for s in stations.all_stations()]
        self._pub(f"{DISCOVERY}/select/radio_adfree/station/config", {
            "name": "Sender", "unique_id": "radio_adfree_station_select",
            "command_topic": f"{PREFIX}/cmd/station",
            "state_topic": f"{PREFIX}/state/station_name",
            "options": names,
            "availability_topic": f"{PREFIX}/availability",
            "expire_after": 30,
            "icon": "mdi:radio", "device": self._device(),
        })
        self._pub(f"{DISCOVERY}/switch/radio_adfree/enabled/config", {
            "name": "Werbung ersetzen", "unique_id": "radio_adfree_enabled",
            "command_topic": f"{PREFIX}/cmd/enabled",
            "state_topic": f"{PREFIX}/state/enabled",
            "payload_on": "ON", "payload_off": "OFF",
            "availability_topic": f"{PREFIX}/availability",
            "expire_after": 30,
            "icon": "mdi:advertisements-off", "device": self._device(),
        })
        self._pub(f"{DISCOVERY}/number/radio_adfree/delay/config", {
            "name": "Verzögerung", "unique_id": "radio_adfree_delay_set",
            "command_topic": f"{PREFIX}/cmd/delay",
            "state_topic": f"{PREFIX}/state/delay",
            "min": 5, "max": 10, "step": 0.5, "unit_of_measurement": "s",
            "availability_topic": f"{PREFIX}/availability",
            "expire_after": 30,
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
            "expire_after": 30,
            "device_class": "speaker",
            "icon": "mdi:radio", "device": self._device(),
        })

    def _values(self, status: dict) -> dict:
        radio = status.get("radio", {})
        ad = status.get("ad", {})
        learn = status.get("learn", {})
        today = status.get("today", {})
        engine = status.get("engine", {})
        st = status.get("station_info", {})
        urls = status.get("urls", {})
        enabled = "ON" if status.get("enabled") else "OFF"
        return {
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

    def publish_state(self, status: dict) -> None:
        self._state = status
        if not self.connected:
            return
        try:
            for k, v in self._values(status).items():
                self._pub(f"{PREFIX}/state/{k}", v)
        except Exception:
            log.exception("state publish failed")

    def _on_message(self, topic: str, payload: str) -> None:
        try:
            key = topic.rsplit("/", 1)[-1]
            log.info("MQTT command %s = %s", key, payload)
            self.controller.handle_mqtt_command(key, payload)
        except Exception:
            log.exception("MQTT command failed")
