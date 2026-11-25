import asyncio
import logging
import os
import signal
import sys

from aiohttp import web

from .config import Config
from .main import App
from .web import StreamHub


def main() -> None:
    cfg = Config.load()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("main")
    log.info("starting Radio AdFree")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = App(cfg)
    app.loop = loop
    app.hub = StreamHub(loop)
    app.engine.encoder.add_callback(app.hub.publish)
    app.start_background()

    aio = app.create_app()

    async def runner():
        runner_app = web.AppRunner(aio, access_log=None)
        await runner_app.setup()
        site = web.TCPSite(runner_app, "0.0.0.0", cfg.port)
        await site.start()
        log.info("web server listening on 0.0.0.0:%s (ingress port)", cfg.port)
        stop = asyncio.Event()

        def _sig(*_):
            stop.set()

        for s in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(s, _sig)
            except NotImplementedError:
                pass
        await stop.wait()
        try:
            await app.mqtt.stop_async()
        except BaseException:
            pass
        await runner_app.cleanup()

    try:
        loop.run_until_complete(runner())
    finally:
        app.stop()
        loop.close()


if __name__ == "__main__":
    main()
