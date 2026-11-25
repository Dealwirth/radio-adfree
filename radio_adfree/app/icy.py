"""ICY/Icecast HTTP stream reader with StreamTitle metadata and automatic reconnect."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
import urllib.request

log = logging.getLogger("icy")

_META_RE = re.compile(rb"StreamTitle='(.*?)';", re.S)


class ICYStream:
    def __init__(self, url: str, audio_queue: "queue.Queue[bytes | None]", on_meta=None,
                 on_status=None, user_agent: str = "RadioAdFree/1.0 (HA add-on)"):
        self.url = url
        self.audio_queue = audio_queue
        self.on_meta = on_meta
        self.on_status = on_status
        self.user_agent = user_agent
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resp = None
        self.stats = {
            "connected": False, "icy_name": "", "bitrate": "", "metaint": 0,
            "bytes": 0, "reconnects": 0, "last_audio": 0.0, "last_meta": 0.0,
            "title": "", "error": "",
        }

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="icy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._resp:
                self._resp.close()
        except Exception:
            pass
        try:
            self.audio_queue.put_nowait(None)
        except Exception:
            pass

    def _status(self) -> None:
        if self.on_status:
            try:
                self.on_status(dict(self.stats))
            except Exception:
                log.exception("status callback failed")

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._connect_and_read()
                backoff = 1.0
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.stats["connected"] = False
                self.stats["error"] = f"{type(exc).__name__}: {exc}"
                self.stats["reconnects"] += 1
                self._status()
                log.warning("stream error: %s (reconnect in %.1fs)", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 1.7, 15.0)

    def _connect_and_read(self) -> None:
        req = urllib.request.Request(self.url, headers={
            "Icy-MetaData": "1",
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Connection": "keep-alive",
        })
        resp = urllib.request.urlopen(req, timeout=20)
        self._resp = resp
        headers = {k.lower(): v for k, v in resp.headers.items()}
        metaint = int(headers.get("icy-metaint", "0") or 0)
        self.stats.update({
            "connected": True, "icy_name": headers.get("icy-name", ""),
            "bitrate": headers.get("icy-br", ""), "metaint": metaint, "error": "",
        })
        self._status()
        log.info("connected: %s (icy-name=%s, metaint=%s)", resp.geturl(),
                 headers.get("icy-name", ""), metaint)
        last_title = None
        while not self._stop.is_set():
            if metaint:
                audio = self._read_exact(resp, metaint)
                if not audio:
                    raise IOError("stream ended")
                self._push(audio)
                ln = self._read_exact(resp, 1)
                if not ln:
                    raise IOError("stream ended")
                mlen = ln[0] * 16
                meta = self._read_exact(resp, mlen) if mlen else b""
                if meta:
                    m = _META_RE.search(meta)
                    if m:
                        title = m.group(1).decode("utf-8", "replace").strip()
                        if title and title != last_title:
                            last_title = title
                            self.stats["title"] = title
                            self.stats["last_meta"] = time.monotonic()
                            if self.on_meta:
                                try:
                                    self.on_meta(title, time.monotonic())
                                except Exception:
                                    log.exception("metadata callback failed")
            else:
                chunk = resp.read(4096)
                if not chunk:
                    raise IOError("stream ended")
                self._push(chunk)

    def _push(self, data: bytes) -> None:
        self.stats["bytes"] += len(data)
        self.stats["last_audio"] = time.monotonic()
        while not self._stop.is_set():
            try:
                self.audio_queue.put(data, timeout=0.5)
                return
            except queue.Full:
                # The decoder is behind; drop the oldest audio instead of stalling metadata.
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    pass

    @staticmethod
    def _read_exact(resp, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = resp.read(n - len(buf))
            if not chunk:
                return b""
            buf += chunk
        return buf
