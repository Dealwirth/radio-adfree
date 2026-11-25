"""aiohttp web server: MP3 stream, status API, control API and a small player page."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from . import stations

log = logging.getLogger("web")
HERE = Path(__file__).parent


class StreamHub:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.clients: set[asyncio.Queue] = set()
        self.bytes = 0
        self.clients_seen = 0

    def publish(self, chunk: bytes) -> None:
        self.bytes += len(chunk)
        self.loop.call_soon_threadsafe(self._publish, chunk)

    def _publish(self, chunk: bytes) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=120)
        self.clients.add(q)
        self.clients_seen += 1
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)


def _cors(resp: web.StreamResponse) -> None:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"


def create_app(controller, hub: StreamHub) -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    app["controller"] = controller
    app["hub"] = hub

    async def options(request):
        resp = web.Response(status=204)
        _cors(resp)
        return resp

    async def health(request):
        resp = web.json_response({"ok": True, "version": controller.version})
        _cors(resp)
        return resp

    async def stream(request):
        resp = web.StreamResponse(status=200, headers={
            "Content-Type": "audio/mpeg",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "icy-name": "Radio AdFree",
            "icy-genre": "Rock",
            "icy-br": str(controller.cfg.output_bitrate),
        })
        _cors(resp)
        await resp.prepare(request)
        q = await hub.subscribe()
        log.info("stream client connected (%s total)", len(hub.clients))
        try:
            while True:
                chunk = await q.get()
                await resp.write(chunk)
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:
            log.debug("stream client error: %s", exc)
        finally:
            hub.unsubscribe(q)
            log.info("stream client disconnected (%s total)", len(hub.clients))
        return resp

    async def status(request):
        resp = web.json_response(controller.status())
        _cors(resp)
        return resp

    async def station_list(request):
        resp = web.json_response({"stations": stations.all_stations(),
                                  "current": controller.cfg.station})
        _cors(resp)
        return resp

    async def set_station(request):
        data = await request.json()
        ok = controller.set_station(data.get("id", ""))
        resp = web.json_response({"ok": ok, "station": controller.cfg.station},
                                 status=200 if ok else 400)
        _cors(resp)
        return resp

    async def set_enabled(request):
        data = await request.json()
        controller.set_enabled(bool(data.get("enabled")))
        resp = web.json_response({"ok": True, "enabled": controller.cfg.enabled})
        _cors(resp)
        return resp

    async def feedback(request):
        data = await request.json()
        result = controller.feedback(bool(data.get("is_ad")))
        resp = web.json_response({"ok": True, "event": result})
        _cors(resp)
        return resp

    async def simulate_ad(request):
        data = await request.json()
        seconds = float(data.get("seconds", 30))
        controller.simulate_ad(seconds)
        resp = web.json_response({"ok": True, "seconds": seconds})
        _cors(resp)
        return resp

    async def set_target(request):
        data = await request.json()
        controller.set_target(data.get("entity_id", ""), data.get("service", "media_player"))
        resp = web.json_response({"ok": True, "target": controller.cfg.target_player})
        _cors(resp)
        return resp

    async def selftest(request):
        resp = web.json_response(controller.selftest())
        _cors(resp)
        return resp

    async def player(request):
        html = (HERE / "player.html").read_text()
        return web.Response(text=html, content_type="text/html", headers={
            "Cache-Control": "no-cache",
        })

    async def index(request):
        raise web.HTTPFound("player")

    for method, path, handler in [
        ("GET", "/", index),
        ("GET", "/player", player),
        ("GET", "/stream.mp3", stream),
        ("GET", "/status", status),
        ("GET", "/api/status", status),
        ("GET", "/api/stations", station_list),
        ("POST", "/api/station", set_station),
        ("POST", "/api/enabled", set_enabled),
        ("POST", "/api/feedback", feedback),
        ("POST", "/api/simulate_ad", simulate_ad),
        ("POST", "/api/target", set_target),
        ("GET", "/api/selftest", selftest),
        ("GET", "/health", health),
        ("OPTIONS", "/{tail:.*}", options),
    ]:
        app.router.add_route(method, path, handler)
    return app
