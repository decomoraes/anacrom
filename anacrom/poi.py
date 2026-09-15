"""Places worth remembering.

A shard is a big place and the client has no map, so the agent builds its own
gazetteer as it plays: stand somewhere useful, name it, and it can be found
again by name, by kind, or by "what is nearest to here".

The store is a plain JSON file so it can be edited, shared between characters,
or seeded from a file someone else made.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import CONFIG_DIR
from .world.state import direction_to, distance

STORE_PATH = CONFIG_DIR / "places.json"

KINDS = [
    "bank", "healer", "vendor", "moongate", "inn", "dungeon", "spawn",
    "resource", "guard_zone", "danger", "home", "note",
]


@dataclass
class Place:
    name: str
    x: int
    y: int
    z: int = 0
    map: int = 0
    kind: str = "note"
    notes: str = ""
    added_at: float = field(default_factory=time.time)

    def summary(self, origin: tuple[int, int] | None = None) -> str:
        where = f"({self.x}, {self.y}) map {self.map}"
        if origin is not None:
            gap = distance(origin, (self.x, self.y))
            bearing = direction_to(origin, (self.x, self.y))
            where = f"{where}  {gap} tiles {bearing}"
        notes = f"  -- {self.notes}" if self.notes else ""
        return f"{self.name} [{self.kind}] {where}{notes}"


class Places:
    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = Path(path)
        self.entries: list[Place] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.entries = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.entries = [Place(**entry) for entry in raw.get("places", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"places": [asdict(place) for place in self.entries]}
        self.path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def add(self, place: Place, replace: bool = True) -> Place:
        if replace:
            self.entries = [
                existing for existing in self.entries
                if not (existing.name.casefold() == place.name.casefold()
                        and existing.map == place.map)
            ]
        self.entries.append(place)
        self.save()
        return place

    def remove(self, name: str, map_id: int | None = None) -> int:
        before = len(self.entries)
        wanted = name.casefold()
        self.entries = [
            place for place in self.entries
            if not (place.name.casefold() == wanted
                    and (map_id is None or place.map == map_id))
        ]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

    def search(self, text: str, kind: str = "", map_id: int | None = None) -> list[Place]:
        needle = text.casefold()
        found = [
            place for place in self.entries
            if (not needle or needle in place.name.casefold()
                or needle in place.notes.casefold())
            and (not kind or place.kind == kind)
            and (map_id is None or place.map == map_id)
        ]
        return sorted(found, key=lambda p: p.name.casefold())

    def nearest(self, origin: tuple[int, int], kind: str = "",
                map_id: int = 0, limit: int = 5) -> list[Place]:
        candidates = [
            place for place in self.entries
            if (not kind or place.kind == kind) and place.map == map_id
        ]
        candidates.sort(key=lambda p: distance(origin, (p.x, p.y)))
        return candidates[:limit]
