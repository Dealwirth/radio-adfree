"""Home Assistant Supervisor helpers (no user login data required)."""
from __future__ import annotations

import json
import logging
import os
import socket
import urllib.request

log = logging.getLogger("ha")

SUPERVISOR = "http://supervisor"
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")


def _req(path: str, method: str = "GET", data: dict | None = None, timeout: float = 10.0) -> dict:
    url = SUPERVISOR + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def addon_info() -> dict:
    try:
        return _req("/addons/self/info").get("data", {})
    except Exception as exc:
        log.debug("addon_info failed: %s", exc)
        return {}


def mqtt_service() -> dict | None:
    try:
        d = _req("/services/mqtt")
        if d.get("result") == "ok":
            return d.get("data")
    except Exception as exc:
        log.debug("mqtt service discovery failed: %s", exc)
    return None


def host_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def call_service(domain: str, service: str, data: dict) -> dict:
    return _req(f"/core/api/services/{domain}/{service}", "POST", data)


def play_media(entity_id: str, url: str, service: str = "media_player") -> dict:
    if service == "music_assistant":
        return call_service("music_assistant", "play_media", {
            "entity_id": entity_id, "media_id": url, "media_type": "radio",
        })
    return call_service("media_player", "play_media", {
        "entity_id": entity_id, "media_content_id": url, "media_content_type": "music",
    })


def stop_media(entity_id: str, service: str = "media_player") -> dict:
    return call_service(service, "media_stop", {"entity_id": entity_id})


def notify(title: str, message: str) -> None:
    try:
        call_service("persistent_notification", "create", {"title": title, "message": message})
    except Exception:
        log.debug("notification failed")
