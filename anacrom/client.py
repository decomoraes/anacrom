"""The playable client: state in, actions out.

Everything above this layer -- the daemon, the CLI, agent scripts -- talks to
one :class:`Client`.  Actions are synchronous from the caller's point of view:
they send a packet and pump the socket until the server's answer arrives, so a
script reads like a player's intent rather than a state machine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .net.login import CLIENT_VERSION_STRING, LoginError, LoginResult, login
from .protocol import speech
from .protocol.packets import DEFAULT_PROFILE, Profile, Writer
from .world.handlers import dispatch
from .world.state import (
    DIRECTIONS,
    DIRECTION_DELTAS,
    Gump,
    JournalEntry,
    Mobile,
    SKILL_NAMES,
    World,
    direction_to,
    distance,
)

# The server drops anyone who out-walks these, so they are floors, not targets.
STEP_INTERVAL_WALK = 0.40
STEP_INTERVAL_RUN = 0.22
STEP_INTERVAL_MOUNTED_RUN = 0.13

MAX_REPLANS = 40                # refusals in one walk before we call it blocked
REFUSAL_MEMORY = 120.0          # seconds a refused step stays off the map

TEXT_COMMAND_USE_SKILL = 0x24
TEXT_COMMAND_CAST_FROM_BOOK = 0x27
TEXT_COMMAND_OPEN_SPELLBOOK = 0x43
TEXT_COMMAND_CAST_MACRO = 0x56
TEXT_COMMAND_OPEN_DOOR = 0x58


@dataclass
class Config:
    host: str = "login.uoalive.com"
    port: int = 2593
    account: str = ""
    password: str = ""
    character: str | int | None = None
    shard: str | int | None = None
    capture: str | None = None
    run_by_default: bool = True
    mounted_speed: bool = False
    uo_data: str | None = None          # folder with tiledata.mul, map*.uop, statics*.mul
    profile: Profile = field(default_factory=lambda: DEFAULT_PROFILE)


class NotConnected(Exception):
    pass


class Client:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.profile = config.profile
        self.world = World()
        self.connection = None
        self.login_result: LoginResult | None = None

        self._move_sequence = 0
        self._last_step_at = 0.0
        self._pending_step: dict | None = None
        self._last_ping_at = 0.0
        self._properties_wanted: list[int] = []
        self._properties_requested: set[int] = set()
        self._events: list[Callable] = []
        self._terrain: dict[int, object] = {}
        self._refused: dict[tuple[int, int, str], float] = {}
        self._damage_log: list[tuple[float, int, int]] = []
        self._swing_log: list[tuple[float, int, int]] = []

    # -- connection --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self.connection is not None and not self.connection.closed

    def connect(self) -> LoginResult:
        config = self.config
        if not config.account:
            raise LoginError("no account configured")

        early_map: list[int] = []

        def before_confirmation(packet: bytes) -> None:
            # 0xBF/0x08 can come ahead of 0x1B; Trammel and Felucca share
            # coordinates, so without it we would plan on the wrong map.
            if packet[0] == 0xBF and len(packet) >= 6 and packet[3:5] == b"\x00\x08":
                early_map.append(packet[5])

        result = login(
            config.host,
            config.port,
            config.account,
            config.password,
            character=config.character,
            server_index=config.shard,
            profile=self.profile,
            capture=config.capture,
            on_packet=before_confirmation,
        )
        self.connection = result.connection
        self.login_result = result

        player = self.world.player
        player.serial = result.player_serial
        player.name = result.character_name
        player.body = result.body
        player.x, player.y, player.z = result.position
        player.direction = result.direction
        if early_map:
            player.map = early_map[-1]

        # What a client does on entering the world: announce itself, ask for the
        # numbers the paperdoll shows, and set how far it wants to see.
        self.send(Writer(0xBD, self.profile).ascii_z(CLIENT_VERSION_STRING))
        self.send(Writer(0xE1, self.profile).u32(0).u8(0x00).u32(0))
        self.send(Writer(0xC8, self.profile).u8(18))
        self.request_status()
        self.request_skills()
        self.pump(1.0)
        return result

    def disconnect(self) -> None:
        if self.connection is not None:
            try:
                self.send(Writer(0xD1, self.profile).u8(0))
            except Exception:
                pass
            self.connection.close()
            self.connection = None

    def send(self, packet) -> None:
        if not self.connected:
            raise NotConnected("not connected to a game server")
        self.connection.send(packet)

    # -- the loop ----------------------------------------------------------

    def pump(self, duration: float = 0.0) -> int:
        """Read whatever has arrived. Returns how many packets were processed."""
        deadline = time.monotonic() + duration
        processed = 0
        while True:
            if not self.connected:
                break
            for packet in self.connection.poll():
                dispatch(self, self.world, packet)
                processed += 1
            self._housekeeping()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.005)
        return processed

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.pump(0.02)
        return predicate()

    def _housekeeping(self) -> None:
        now = time.time()
        if now - self._last_ping_at > 30 and self.connected:
            self._last_ping_at = now
            try:
                self.send(Writer(0x73, self.profile).u8(0))
            except Exception:
                pass
        self._flush_property_requests()

    # -- callbacks the handlers use ---------------------------------------

    def on_ping(self, sequence: int) -> None:
        pass  # the server is answering us, nothing to do

    def on_login_complete(self) -> None:
        self.request_status()
        self.request_skills()

    def on_position_confirmed(self) -> None:
        self._pending_step = None

    def on_movement_accepted(self, sequence: int) -> None:
        if self._pending_step is not None:
            self._pending_step["acked"] = True

    def on_movement_rejected(self, sequence: int) -> None:
        self._move_sequence = 0
        if self._pending_step is not None:
            self._pending_step["rejected"] = True

    def on_mobile_seen(self, mob: Mobile) -> None:
        if not mob.name:
            self.request_properties(mob.serial)

    def on_journal(self, entry: JournalEntry) -> None:
        pass

    def on_gump(self, gump: Gump) -> None:
        pass

    def on_target_request(self) -> None:
        pass

    def on_vendor_list(self, vendor: int, entries) -> None:
        pass

    def on_swing(self, attacker: int, defender: int) -> None:
        self._swing_log.append((time.time(), attacker, defender))
        del self._swing_log[:-200]

    def on_damage(self, serial: int, amount: int) -> None:
        self._damage_log.append((time.time(), serial, amount))
        del self._damage_log[:-200]

    # -- naming ------------------------------------------------------------

    def request_properties(self, serial: int) -> None:
        if serial in self._properties_requested:
            return
        self._properties_requested.add(serial)
        self._properties_wanted.append(serial)

    def _flush_property_requests(self) -> None:
        if not self._properties_wanted or not self.connected:
            return
        batch, self._properties_wanted = self._properties_wanted[:40], self._properties_wanted[40:]
        writer = Writer(0xD6, self.profile)
        for serial in batch:
            writer.u32(serial)
        try:
            self.send(writer)
        except Exception:
            pass

    def request_status(self) -> None:
        self.send(
            Writer(0x34, self.profile).u32(0xEDEDEDED).u8(0x04).u32(self.world.player.serial)
        )

    def request_skills(self) -> None:
        self.send(
            Writer(0x34, self.profile).u32(0xEDEDEDED).u8(0x05).u32(self.world.player.serial)
        )

    def resync(self) -> None:
        self.send(Writer(0x22, self.profile).u8(0).u8(0))

    # -- the ground --------------------------------------------------------

    def terrain(self):
        """The map we stand on, from the client's data files; None without them."""
        folder = self.config.uo_data
        if not folder:
            return None
        index = self.world.player.map
        terrain = self._terrain.get(index)
        if terrain is None:
            from .world.mapdata import MapData
            from .world.movement import Terrain
            shared = next(iter(self._terrain.values()), None)
            try:
                terrain = Terrain(MapData(folder, index, shared.tiles if shared else None))
            except (OSError, ValueError, KeyError) as exc:
                self.world.warn(f"map data unusable, walking without it: {exc}")
                self.config.uo_data = None
                return None
            self._terrain[index] = terrain
        return terrain

    # -- movement ----------------------------------------------------------

    @property
    def step_interval(self) -> float:
        if not self.config.run_by_default:
            return STEP_INTERVAL_WALK
        return STEP_INTERVAL_MOUNTED_RUN if self.config.mounted_speed else STEP_INTERVAL_RUN

    def step(self, direction: str, run: bool | None = None, timeout: float = 2.0) -> dict:
        """One movement request. A request in a new direction turns us first.

        Returns what actually happened: ``moved``, ``turned`` or ``blocked``.
        """
        if direction not in DIRECTION_DELTAS:
            raise ValueError(f"unknown direction {direction!r}")

        run = self.config.run_by_default if run is None else run
        wait = self.step_interval - (time.monotonic() - self._last_step_at)
        if wait > 0:
            self.pump(wait)

        player = self.world.player
        facing = player.facing

        # The server acknowledges a step without saying how high we ended up,
        # so with map data we work the height out the way it does.
        landing = None
        terrain = self.terrain()
        if terrain is not None and facing == direction:
            fits, z = terrain.step(player.x, player.y, player.z, direction)
            landing = z if fits else None

        code = DIRECTIONS.index(direction) | (0x80 if run else 0x00)
        sequence = self._move_sequence
        self._pending_step = {"acked": False, "rejected": False, "sequence": sequence}

        self.send(Writer(0x02, self.profile).u8(code).u8(sequence).u32(0))
        self._move_sequence = 1 if sequence >= 255 else sequence + 1
        self._last_step_at = time.monotonic()

        pending = self._pending_step
        self.wait_for(lambda: pending["acked"] or pending["rejected"], timeout)

        if pending["rejected"]:
            # 0x21 carried the authoritative position; the handler already applied it.
            outcome = "blocked"
        elif not pending["acked"]:
            outcome = "blocked"
        elif facing != direction:
            # A request in a direction we were not facing turns us on the spot.
            player.direction = DIRECTIONS.index(direction)
            outcome = "turned"
        else:
            # The server only acknowledges, so we keep our own position and let
            # 0x20 or 0x21 correct us when the server disagrees.
            dx, dy = DIRECTION_DELTAS[direction]
            player.x += dx
            player.y += dy
            if landing is not None:
                player.z = landing
            outcome = "moved"

        return {"outcome": outcome, "position": (player.x, player.y, player.z),
                "direction": player.facing}

    def walk_to(
        self,
        x: int,
        y: int,
        run: bool | None = None,
        max_steps: int = 200,
        stop_within: int = 0,
        on_step: Callable[[dict], bool] | None = None,
    ) -> dict:
        """Walk to a tile: by a planned route with map data, feeling the way without."""
        terrain = self.terrain()
        if terrain is None:
            return self._walk_blind(x, y, run, max_steps, stop_within, on_step)
        return self._walk_route(terrain, x, y, run, max_steps, stop_within, on_step)

    def _walk_route(self, terrain, x: int, y: int, run: bool | None, max_steps: int,
                    stop_within: int, on_step: Callable[[dict], bool] | None) -> dict:
        """Plan with the server's own movement rules, walk it, re-plan on refusal.

        The files hold the land and the fixed objects; houses and things we have
        not been shown do not appear in them, so a refused step is remembered
        for a while and the next plan goes round it.
        """
        player = self.world.player
        steps = plans = 0
        while True:
            if distance((player.x, player.y), (x, y)) <= stop_within:
                return {"arrived": True, "steps": steps, "position": player.position}
            if steps >= max_steps:
                return {"arrived": False, "steps": steps, "stopped": "step limit",
                        "position": player.position}
            if plans >= MAX_REPLANS:
                return {"arrived": False, "steps": steps, "stopped": "blocked",
                        "position": player.position}

            now = time.time()
            refused = {edge for edge, at in self._refused.items() if now - at < REFUSAL_MEMORY}
            terrain.see_items(self.world.items.values())
            route = terrain.route(player.position, (x, y), stop_within, refused)
            plans += 1
            if route is None:
                return {"arrived": False, "steps": steps, "stopped": "no route",
                        "position": player.position}

            for direction in route:
                here = (player.x, player.y)
                self._open_door_ahead(terrain, direction)
                result = self.step(direction, run=run)
                if result["outcome"] == "turned":
                    result = self.step(direction, run=run)
                steps += 1
                if on_step is not None and not on_step(result):
                    return {"arrived": False, "steps": steps, "stopped": "caller",
                            "position": player.position}
                if result["outcome"] != "moved":
                    self._refused[(here[0], here[1], direction)] = time.time()
                    break
                if steps >= max_steps or distance((player.x, player.y), (x, y)) <= stop_within:
                    break

    def _open_door_ahead(self, terrain, direction: str) -> None:
        """Open a closed door the next step would walk into."""
        player = self.world.player
        terrain.see_items(self.world.items.values())
        if terrain.step(player.x, player.y, player.z, direction, ignore_doors=False)[0]:
            return
        dx, dy = DIRECTION_DELTAS[direction]
        for item in list(self.world.items.values()):
            if (item.container == 0 and (item.x, item.y) == (player.x + dx, player.y + dy)
                    and terrain.is_door(item.graphic)):
                self.use(item.serial)
                self.pump(0.3)

    def _walk_blind(self, x: int, y: int, run: bool | None, max_steps: int,
                    stop_within: int, on_step: Callable[[dict], bool] | None) -> dict:
        """Head the right way, and when a wall stops you, try the next direction round.

        What a player does when they cannot see the whole route, and all we can
        do without the client's map files.
        """
        player = self.world.player
        steps = 0
        blocked_in_a_row = 0
        detour = 0

        while steps < max_steps:
            here = (player.x, player.y)
            if distance(here, (x, y)) <= stop_within:
                return {"arrived": True, "steps": steps, "position": player.position}

            wanted = direction_to(here, (x, y))
            if wanted == "here":
                return {"arrived": True, "steps": steps, "position": player.position}

            if detour:
                index = (DIRECTIONS.index(wanted) + detour) % 8
                wanted = DIRECTIONS[index]

            result = self.step(wanted, run=run)
            steps += 1

            if on_step is not None and not on_step(result):
                return {"arrived": False, "steps": steps, "stopped": "caller",
                        "position": player.position}

            if result["outcome"] == "moved":
                blocked_in_a_row = 0
                detour = 0
            elif result["outcome"] == "turned":
                continue
            else:
                blocked_in_a_row += 1
                # Fan out: right, left, further right, further left.
                detour = [1, -1, 2, -2, 3, -3][min(blocked_in_a_row, 6) - 1]
                if blocked_in_a_row > 6:
                    return {"arrived": False, "steps": steps, "stopped": "blocked",
                            "position": player.position}

        return {"arrived": False, "steps": steps, "stopped": "step limit",
                "position": player.position}

    def walk_to_entity(self, serial: int, stop_within: int = 1, **kwargs) -> dict:
        entity = self.world.entity(serial)
        if entity is None:
            return {"arrived": False, "stopped": f"0x{serial:08X} is not in view"}
        return self.walk_to(entity.x, entity.y, stop_within=stop_within, **kwargs)

    # -- talking -----------------------------------------------------------

    def say(self, text: str, hue: int = 0x03B2, kind: int = 0x00) -> None:
        # NPCs answer keyword ids, not words; see protocol/speech.py.
        keywords = speech.keywords_in(text)
        writer = Writer(0xAD, self.profile)
        writer.u8(kind | (speech.ENCODED if keywords else 0))
        writer.u16(hue)
        writer.u16(3)                           # font
        writer.raw(b"ENU\x00")
        if keywords:
            writer.raw(speech.pack(keywords))
            writer.raw(text.encode("utf-8") + b"\x00")
        else:
            writer.unicode_z(text)
        self.send(writer)

    def emote(self, text: str) -> None:
        self.say(text, kind=0x02)

    def whisper(self, text: str) -> None:
        self.say(text, kind=0x08)

    def yell(self, text: str) -> None:
        self.say(text, kind=0x09)

    # -- interacting -------------------------------------------------------

    def look(self, serial: int) -> None:
        self.send(Writer(0x09, self.profile).u32(serial))

    def use(self, serial: int) -> None:
        """Double click: open a container, eat food, equip a weapon, ride a horse."""
        self.send(Writer(0x06, self.profile).u32(serial))

    def attack(self, serial: int) -> None:
        self.send(Writer(0x05, self.profile).u32(serial))
        self.world.player.last_target = serial

    def set_war_mode(self, enabled: bool) -> None:
        self.send(Writer(0x72, self.profile).u8(1 if enabled else 0).raw(b"\x00\x32\x00"))
        self.world.player.war_mode = enabled

    def open_container(self, serial: int, timeout: float = 4.0) -> list:
        before = len(self.world.contents_of(serial))
        self.use(serial)
        self.wait_for(
            lambda: len(self.world.contents_of(serial)) != before
            or serial in self.world.opened_containers,
            timeout,
        )
        self.pump(0.3)
        return self.world.contents_of(serial)

    def pick_up(self, serial: int, amount: int = 0) -> None:
        item = self.world.items.get(serial)
        if amount <= 0:
            amount = item.amount if item else 1
        self.send(Writer(0x07, self.profile).u32(serial).u16(amount))

    def drop(self, serial: int, container: int = 0, x: int = 0xFFFF, y: int = 0xFFFF,
             z: int = 0) -> None:
        writer = Writer(0x08, self.profile)
        writer.u32(serial).u16(x).u16(y).u8(z & 0xFF).u8(0xFF).u32(container)
        self.send(writer)

    def move_item(self, serial: int, container: int, amount: int = 0,
                  settle: float = 0.6) -> None:
        """Lift and drop in one gesture, which is how the server expects it."""
        self.pick_up(serial, amount)
        self.pump(0.25)
        self.drop(serial, container)
        self.pump(settle)

    def equip(self, serial: int, layer: int) -> None:
        self.pick_up(serial, 1)
        self.pump(0.2)
        self.send(
            Writer(0x13, self.profile).u32(serial).u8(layer).u32(self.world.player.serial)
        )

    # -- targeting ---------------------------------------------------------

    def target(self, serial: int, graphic: int = 0) -> bool:
        request = self.world.target
        if not request.active:
            return False
        entity = self.world.entity(serial)
        x = entity.x if entity else 0xFFFF
        y = entity.y if entity else 0xFFFF
        z = entity.z if entity else 0
        writer = Writer(0x6C, self.profile)
        writer.u8(0).u32(request.cursor_id).u8(request.cursor_type)
        writer.u32(serial).u16(x).u16(y).u8(0).u8(z & 0xFF).u16(graphic)
        self.send(writer)
        request.active = False
        return True

    def target_ground(self, x: int, y: int, z: int = 0, graphic: int = 0) -> bool:
        request = self.world.target
        if not request.active:
            return False
        writer = Writer(0x6C, self.profile)
        writer.u8(1).u32(request.cursor_id).u8(request.cursor_type)
        writer.u32(0).u16(x).u16(y).u8(0).u8(z & 0xFF).u16(graphic)
        self.send(writer)
        request.active = False
        return True

    def target_self(self) -> bool:
        return self.target(self.world.player.serial)

    def cancel_target(self) -> bool:
        request = self.world.target
        if not request.active:
            return False
        writer = Writer(0x6C, self.profile)
        writer.u8(0).u32(request.cursor_id).u8(3)
        writer.u32(0).u16(0xFFFF).u16(0xFFFF).u8(0).u8(0).u16(0)
        self.send(writer)
        request.active = False
        return True

    def wait_for_target(self, timeout: float = 4.0) -> bool:
        return self.wait_for(lambda: self.world.target.active, timeout)

    # -- skills, spells, doors --------------------------------------------

    def _text_command(self, kind: int, command: str) -> None:
        self.send(Writer(0x12, self.profile).u8(kind).ascii_z(command))

    def use_skill(self, skill: str | int) -> None:
        if isinstance(skill, str):
            wanted = skill.casefold()
            matches = [i for i, n in enumerate(SKILL_NAMES) if n.casefold() == wanted]
            if not matches:
                matches = [i for i, n in enumerate(SKILL_NAMES) if wanted in n.casefold()]
            if not matches:
                raise ValueError(f"no skill matching {skill!r}")
            skill = matches[0]
        self._text_command(TEXT_COMMAND_USE_SKILL, f"{skill} 0")

    def cast(self, spell_id: int, target: int | None = None, timeout: float = 4.0) -> bool:
        self._text_command(TEXT_COMMAND_CAST_MACRO, str(spell_id))
        if target is None:
            return True
        if not self.wait_for_target(timeout):
            return False
        return self.target(target)

    def open_door(self) -> None:
        self._text_command(TEXT_COMMAND_OPEN_DOOR, "")

    # -- gumps and menus ---------------------------------------------------

    def gump_reply(self, serial: int, button: int, switches: list[int] | None = None,
                   entries: dict[int, str] | None = None) -> None:
        gump = self.world.gumps.get(serial)
        if gump is None:
            raise ValueError(f"no open gump with serial 0x{serial:08X}")
        writer = Writer(0xB1, self.profile)
        writer.u32(gump.serial).u32(gump.gump_id).u32(button)
        switches = switches or []
        writer.u32(len(switches))
        for switch in switches:
            writer.u32(switch)
        entries = entries or {}
        writer.u32(len(entries))
        for entry_id, text in entries.items():
            writer.u16(entry_id)
            encoded = text.encode("utf-16-be")
            writer.u16(len(encoded) // 2)
            writer.raw(encoded)
        self.send(writer)
        self.world.gumps.pop(serial, None)

    def close_gump(self, serial: int) -> None:
        self.gump_reply(serial, 0)

    # -- shopping ----------------------------------------------------------

    def buy(self, vendor: int, purchases: list[tuple[int, int]]) -> None:
        """purchases is a list of (item serial, amount)."""
        writer = Writer(0x3B, self.profile).u32(vendor).u8(0x02)
        for serial, amount in purchases:
            writer.u8(0x1A).u32(serial).u16(amount)
        self.send(writer)

    def sell(self, vendor: int, sales: list[tuple[int, int]]) -> None:
        writer = Writer(0x9F, self.profile).u32(vendor).u16(len(sales))
        for serial, amount in sales:
            writer.u32(serial).u16(amount)
        self.send(writer)

    # -- summary for the agent --------------------------------------------

    def snapshot(self, radius: int = 18) -> dict:
        world = self.world
        player = world.player
        return {
            "connected": self.connected,
            "player": {
                "name": player.name,
                "serial": player.serial,
                "position": list(player.position),
                "map": player.map,
                "facing": player.facing,
                "hits": [player.hits, player.hits_max],
                "mana": [player.mana, player.mana_max],
                "stamina": [player.stamina, player.stamina_max],
                "stats": {
                    "str": player.strength,
                    "dex": player.dexterity,
                    "int": player.intelligence,
                },
                "gold": player.gold,
                "weight": [player.weight, player.weight_max],
                "armor": player.armor,
                "followers": [player.followers, player.followers_max],
                "war_mode": player.war_mode,
                "dead": player.dead,
                "backpack": player.backpack,
            },
            "mobiles": [
                {
                    "serial": m.serial,
                    "name": m.name or f"unknown ({m.graphic:#06x})",
                    "position": list(m.position),
                    "distance": distance((player.x, player.y), (m.x, m.y)),
                    "direction": direction_to((player.x, player.y), (m.x, m.y)),
                    "notoriety": m.notoriety_name,
                    "hostile": m.hostile,
                    "health_percent": m.health_percent,
                    "war_mode": m.warmode,
                    "hidden": m.hidden,
                }
                for m in world.nearby_mobiles(radius)
            ],
            "ground_items": [
                {
                    "serial": i.serial,
                    "name": i.name or f"item ({i.graphic:#06x})",
                    "amount": i.amount,
                    "position": list(i.position),
                    "distance": distance((player.x, player.y), (i.x, i.y)),
                }
                for i in world.ground_items(min(radius, 12))
            ],
            "target_request": {
                "active": world.target.active,
                "allows_ground": world.target.allow_ground,
            },
            "open_gumps": [
                {"serial": g.serial, "gump_id": g.gump_id, "buttons": g.buttons(),
                 "lines": g.lines[:12]}
                for g in world.gumps.values()
            ],
            "journal_tail": [e.format() for e in list(world.journal)[-8:]],
            "warnings": list(world.warnings)[-5:],
        }
