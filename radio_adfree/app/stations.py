"""Station catalog: all Radio Bob and Rock Antenne channels (verified public streams)."""
from __future__ import annotations

import json
from pathlib import Path

_CATALOG = json.loads((Path(__file__).with_name("stations.json")).read_text())
BY_ID = {s["id"]: s for s in _CATALOG}


def all_stations() -> list[dict]:
    return _CATALOG


def get(station_id: str) -> dict:
    return BY_ID.get(station_id, BY_ID["rb-69"])


def groups() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in _CATALOG:
        out.setdefault(s["group"], []).append(s)
    return out


def find(query: str) -> list[dict]:
    q = query.lower().strip()
    return [s for s in _CATALOG if q in s["name"].lower() or q in s["id"].lower()]
