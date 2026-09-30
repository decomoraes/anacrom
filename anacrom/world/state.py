"""Everything the client knows about the world right now.

The agent never sees packets; it sees this.  So the model leans towards what a
player would ask -- "what is near me", "what is in that bag", "what did the last
few lines of chat say" -- rather than mirroring the wire format.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

# Notoriety, which is what the coloured name over a mobile means.
NOTORIETY = {
    1: "innocent",
    2: "friend",
    3: "animal",
    4: "criminal",
    5: "enemy",
    6: "murderer",
    7: "invulnerable",
}

# Paperdoll layers, for reading what someone is wearing.
LAYERS = {
    0x01: "right_hand", 0x02: "left_hand", 0x03: "shoes", 0x04: "pants",
    0x05: "shirt", 0x06: "helm", 0x07: "gloves", 0x08: "ring",
    0x09: "talisman", 0x0A: "neck", 0x0B: "hair", 0x0C: "waist",
    0x0D: "torso", 0x0E: "bracelet", 0x0F: "face", 0x10: "beard",
    0x11: "tunic", 0x12: "earrings", 0x13: "arms", 0x14: "cloak",
    0x15: "backpack", 0x16: "robe", 0x17: "eggs", 0x18: "legs",
    0x19: "mount", 0x1A: "shop_buy_restock", 0x1B: "shop_buy",
    0x1C: "shop_sell", 0x1D: "bank",
}

DIRECTIONS = ["north", "northeast", "east", "southeast",
              "south", "southwest", "west", "northwest"]

DIRECTION_DELTAS = {
    "north": (0, -1), "northeast": (1, -1), "east": (1, 0), "southeast": (1, 1),
    "south": (0, 1), "southwest": (-1, 1), "west": (-1, 0), "northwest": (-1, -1),
}

SKILL_NAMES = [
    "Alchemy", "Anatomy", "Animal Lore", "Item ID", "Arms Lore", "Parrying",
    "Begging", "Blacksmithy", "Bowcraft", "Peacemaking", "Camping", "Carpentry",
    "Cartography", "Cooking", "Detect Hidden", "Discordance", "Evaluate Int",
    "Healing", "Fishing", "Forensic Eval", "Herding", "Hiding", "Provocation",
    "Inscription", "Lockpicking", "Magery", "Magic Resist", "Tactics",
    "Snooping", "Musicianship", "Poisoning", "Archery", "Spirit Speak",
    "Stealing", "Tailoring", "Taming", "Taste ID", "Tinkering", "Tracking",
    "Veterinary", "Swords", "Macing", "Fencing", "Wrestling", "Lumberjacking",
    "Mining", "Meditation", "Stealth", "Remove Trap", "Necromancy",
    "Focus", "Chivalry", "Bushido", "Ninjitsu", "Spellweaving", "Mysticism",
    "Imbuing", "Throwing",
]


def distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    """UO distance: diagonals cost the same as straight steps."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def direction_to(origin: tuple[int, int], target: tuple[int, int]) -> str:
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    if dx == 0 and dy == 0:
        return "here"
    angle = math.atan2(dy, dx)
    # atan2 gives east=0 growing clockwise in screen coordinates.
    index = int(round(angle / (math.pi / 4))) % 8
    return ["east", "southeast", "south", "southwest",
            "west", "northwest", "north", "northeast"][index]


@dataclass
class Entity:
    serial: int
    graphic: int = 0
    hue: int = 0
    x: int = 0
    y: int = 0
    z: int = 0
    name: str = ""
    updated: float = field(default_factory=time.time)

    @property
    def position(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)

    def touch(self) -> None:
        self.updated = time.time()


@dataclass
class Mobile(Entity):
    direction: int = 0
    notoriety: int = 0
    flags: int = 0
    hits: int = 0
    hits_max: int = 0
    equipment: dict[str, int] = field(default_factory=dict)
    is_player: bool = False

    @property
    def notoriety_name(self) -> str:
        return NOTORIETY.get(self.notoriety, "unknown")

    @property
    def facing(self) -> str:
        return DIRECTIONS[(self.direction & 0x07)]

    @property
    def hostile(self) -> bool:
        return self.notoriety in (5, 6)

    @property
    def dead(self) -> bool:
        return bool(self.flags & 0x00) and self.hits == 0

    @property
    def health_percent(self) -> int:
        if not self.hits_max:
            return 0
        return round(self.hits * 100 / self.hits_max)

    @property
    def warmode(self) -> bool:
        return bool(self.flags & 0x40)

    @property
    def hidden(self) -> bool:
        return bool(self.flags & 0x80)

    @property
    def poisoned(self) -> bool:
        return bool(self.flags & 0x04)


@dataclass
class Item(Entity):
    amount: int = 1
    container: int = 0      # serial of the container holding it, 0 if on the ground
    layer: int = 0
    flags: int = 0
    properties: list[str] = field(default_factory=list)

    @property
    def on_ground(self) -> bool:
        return self.container == 0

    @property
    def layer_name(self) -> str:
        return LAYERS.get(self.layer, "")


@dataclass
class JournalEntry:
    text: str
    speaker: str = ""
    serial: int = 0
    kind: str = "regular"
    hue: int = 0
    at: float = field(default_factory=time.time)

    def format(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.at))
        who = f"{self.speaker}: " if self.speaker else ""
        return f"[{stamp}] {who}{self.text}"


@dataclass
class Player:
    serial: int = 0
    name: str = ""
    x: int = 0
    y: int = 0
    z: int = 0
    direction: int = 0
    body: int = 0
    map: int = 0

    hits: int = 0
    hits_max: int = 0
    mana: int = 0
    mana_max: int = 0
    stamina: int = 0
    stamina_max: int = 0

    strength: int = 0
    dexterity: int = 0
    intelligence: int = 0
    gold: int = 0
    weight: int = 0
    weight_max: int = 0
    armor: int = 0
    luck: int = 0
    followers: int = 0
    followers_max: int = 0
    gender: str = ""
    race: str = ""

    war_mode: bool = False
    dead: bool = False
    backpack: int = 0
    last_target: int = 0

    @property
    def position(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)

    @property
    def facing(self) -> str:
        return DIRECTIONS[self.direction & 0x07]


@dataclass
class TargetRequest:
    """The server is waiting for us to point at something."""
    active: bool = False
    cursor_id: int = 0
    allow_ground: bool = False
    cursor_type: int = 0
    requested_at: float = 0.0


@dataclass
class Gump:
    serial: int
    gump_id: int
    x: int = 0
    y: int = 0
    layout: str = ""
    lines: list[str] = field(default_factory=list)

    def buttons(self) -> list[int]:
        ids = []
        for token in self.layout.split("{"):
            parts = token.strip("} ").split()
            if parts and parts[0].lower() == "button" and len(parts) >= 8:
                try:
                    ids.append(int(parts[7]))
                except ValueError:
                    pass
        return sorted(set(ids))


@dataclass
class Waypoint:
    """A marker the server puts on the client's map -- our own corpse, above all."""
    serial: int
    x: int
    y: int
    z: int
    map: int
    kind: int
    name: str
    corpse: bool = False


@dataclass
class VendorItem:
    serial: int
    graphic: int
    amount: int
    price: int
    name: str


class World:
    """Mutable snapshot of the shard as this client understands it."""

    def __init__(self, journal_size: int = 500) -> None:
        self.player = Player()
        self.mobiles: dict[int, Mobile] = {}
        self.items: dict[int, Item] = {}
        self.journal: deque[JournalEntry] = deque(maxlen=journal_size)
        self.skills: dict[str, dict[str, float]] = {}
        self.target = TargetRequest()
        self.gumps: dict[int, Gump] = {}
        self.vendor_items: dict[int, list[VendorItem]] = {}
        self.opened_containers: list[int] = []
        self.light_level = 0
        self.season = 0
        self.logged_in_at = time.time()
        self.login_complete = False
        self.last_packet_at = time.time()
        self.warnings: deque[str] = deque(maxlen=50)
        self.waypoints: dict[int, Waypoint] = {}

    # -- lookup ------------------------------------------------------------

    def entity(self, serial: int) -> Entity | None:
        return self.mobiles.get(serial) or self.items.get(serial)

    def mobile(self, serial: int) -> Mobile:
        mob = self.mobiles.get(serial)
        if mob is None:
            mob = Mobile(serial=serial)
            self.mobiles[serial] = mob
        return mob

    def item(self, serial: int) -> Item:
        it = self.items.get(serial)
        if it is None:
            it = Item(serial=serial)
            self.items[serial] = it
        return it

    def forget(self, serial: int) -> None:
        self.mobiles.pop(serial, None)
        self.items.pop(serial, None)

    def contents_of(self, container: int) -> list[Item]:
        return [i for i in self.items.values() if i.container == container]

    def distance_to(self, serial: int) -> int | None:
        entity = self.entity(serial)
        if entity is None:
            return None
        if isinstance(entity, Item) and entity.container:
            holder = self.entity(entity.container)
            if holder is None:
                return None
            entity = holder
        return distance((self.player.x, self.player.y), (entity.x, entity.y))

    # -- views the agent actually asks for ---------------------------------

    def nearby_mobiles(self, radius: int = 18) -> list[Mobile]:
        here = (self.player.x, self.player.y)
        found = [
            m for m in self.mobiles.values()
            if m.serial != self.player.serial and distance(here, (m.x, m.y)) <= radius
        ]
        found.sort(key=lambda m: distance(here, (m.x, m.y)))
        return found

    def ground_items(self, radius: int = 12) -> list[Item]:
        here = (self.player.x, self.player.y)
        found = [
            i for i in self.items.values()
            if i.on_ground and distance(here, (i.x, i.y)) <= radius
        ]
        found.sort(key=lambda i: distance(here, (i.x, i.y)))
        return found

    def hostiles(self, radius: int = 18) -> list[Mobile]:
        return [m for m in self.nearby_mobiles(radius) if m.hostile]

    def add_journal(self, entry: JournalEntry) -> None:
        self.journal.append(entry)

    def warn(self, message: str) -> None:
        self.warnings.append(f"{time.strftime('%H:%M:%S')} {message}")
