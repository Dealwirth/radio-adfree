"""Configuration for the Radio AdFree add-on (HA options + persisted runtime settings)."""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DATA_DIR = Path(os.environ.get("RADIO_ADFREE_DATA", "/data"))
if not DATA_DIR.exists():
    DATA_DIR = Path("/workspace/radio_adfree_data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    station: str = "rb-69"
    delay: float = 5.5
    crossfade: float = 2.5
    enabled: bool = True
    replace_ads: bool = True
    keep_news: bool = True
    rb_aggressive: bool = False
    rb_min_gap: float = 20.0
    rb_long_gap: float = 180.0
    rb_schedule_prior: list[int] = field(default_factory=lambda: [28, 58])
    rb_poll: float = 3.0
    ra_poll: float = 1.0
    ra_ad_start_classes: list[str] = field(default_factory=lambda: ["COMMERCIAL", "Promotion", "Advertising", "Sponsor"])
    ra_ad_keep_classes: list[str] = field(default_factory=lambda: ["COMMERCIAL", "Promotion", "Cart", "Live", "Advertising", "Sponsor"])
    ra_program_classes: list[str] = field(default_factory=lambda: ["Music", "News", "Weather", "Traffic", "Moderation", "Show", "Sport", "Talk"])
    filler_dir: str = "/share/radio_adfree/music"
    filler_download: bool = True
    filler_max_tracks: int = 24
    filler_min_free_mb: int = 300
    target_player: str = ""
    target_service: str = "media_player"
    volume: float = 0.8
    output_bitrate: int = 192
    port: int = 18097
    token: str = ""
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    log_level: str = "INFO"
    decode_latency: float = 0.0
    buffer_max: float = 30.0
    silence_on_underrun: bool = True
    learn_enabled: bool = True
    debug_audio: bool = False

    # User-facing runtime choices persist across restarts; technical options
    # from the HA add-on configuration always win after an add-on restart.
    RUNTIME_FIELDS = {"station", "enabled", "delay", "volume", "target_player",
                      "target_service", "token"}

    @classmethod
    def load(cls) -> "Config":
        options: dict = {}
        settings: dict = {}
        for path, target in ((DATA_DIR / "options.json", options),
                             (DATA_DIR / "settings.json", settings)):
            if path.exists():
                try:
                    target.update(json.loads(path.read_text()))
                except Exception:
                    pass
        values = dict(options)
        if not options:
            values.update(settings)
        else:
            for k in cls.RUNTIME_FIELDS:
                if k in settings:
                    values[k] = settings[k]
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in values.items() if k in known})
        if not cfg.token:
            cfg.token = secrets.token_urlsafe(18)
            cfg.save()
        return cfg

    def save(self) -> None:
        tmp = DATA_DIR / "settings.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False))
        tmp.replace(DATA_DIR / "settings.json")

    def update(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        self.save()

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "radio_adfree.db"

    @property
    def filler_cache(self) -> Path:
        return DATA_DIR / "filler"
