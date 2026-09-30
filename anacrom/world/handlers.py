"""Turn incoming packets into world state.

Handlers are registered per packet id and get ``(client, world, reader)``.  The
reader is already positioned past the id (and past the length field, for
variable-length packets), so every handler starts at the first real field.

Anything not registered here is ignored on purpose -- sounds, animations,
lighting and the like do not change what the agent can decide, and ignoring a
packet is safe because framing never depends on a handler existing.
"""
from __future__ import annotations

import zlib

from ..protocol.packets import VAR, Reader, length_of
from .state import (
    LAYERS, SKILL_NAMES, Gump, Item, JournalEntry, Mobile, VendorItem, Waypoint,
)

HANDLERS: dict[int, callable] = {}

MESSAGE_KINDS = {
    0x00: "regular", 0x01: "broadcast", 0x02: "emote", 0x06: "label",
    0x07: "focus", 0x08: "whisper", 0x09: "yell", 0x0A: "spell",
    0x0D: "guild", 0x0E: "alliance", 0x0F: "command", 0x1F: "encoded",
}


def handles(*packet_ids: int):
    def register(func):
        for packet_id in packet_ids:
            HANDLERS[packet_id] = func
        return func
    return register


def dispatch(client, world, packet: bytes) -> None:
    packet_id = packet[0]
    world.last_packet_at = __import__("time").time()

    if packet_id == 0xF7:                       # a batch of packets in a trench coat
        reader = Reader(packet, 3)
        count = reader.u16()
        offset = reader.pos
        for _ in range(count):
            if offset >= len(packet):
                break
            sub_id = packet[offset]
            size = length_of(sub_id, client.profile)
            if size == VAR:
                size = int.from_bytes(packet[offset + 1:offset + 3], "big")
            dispatch(client, world, packet[offset:offset + size])
            offset += size
        return

    handler = HANDLERS.get(packet_id)
    if handler is None:
        return

    start = 3 if length_of(packet_id, client.profile) == VAR else 1
    handler(client, world, Reader(packet, start))


# --------------------------------------------------------------------------
# our own character
# --------------------------------------------------------------------------

@handles(0x20)
def _draw_player(client, world, r: Reader) -> None:
    player = world.player
    player.serial = r.u32()
    player.body = r.u16()
    r.u8()
    r.u16()                                     # hue
    player.war_mode = bool(r.u8() & 0x40)
    player.x = r.u16()
    player.y = r.u16()
    r.u16()
    player.direction = r.u8()
    player.z = r.i8()
    client.on_position_confirmed()


@handles(0x21)
def _movement_rejected(client, world, r: Reader) -> None:
    sequence = r.u8()
    world.player.x = r.u16()
    world.player.y = r.u16()
    world.player.direction = r.u8()
    world.player.z = r.i8()
    client.on_movement_rejected(sequence)


@handles(0x22)
def _movement_accepted(client, world, r: Reader) -> None:
    sequence = r.u8()
    r.u8()                                      # notoriety
    client.on_movement_accepted(sequence)


@handles(0x11)
def _status(client, world, r: Reader) -> None:
    serial = r.u32()
    name = r.ascii(30).strip()
    hits = r.u16()
    hits_max = r.u16()
    r.bool()                                    # name change allowed
    detail = r.u8()

    if serial == world.player.serial:
        player = world.player
        player.name = name or player.name
        player.hits, player.hits_max = hits, hits_max
        if detail == 0 or r.remaining < 23:
            return
        player.gender = "female" if r.bool() else "male"
        player.strength = r.u16()
        player.dexterity = r.u16()
        player.intelligence = r.u16()
        player.stamina = r.u16()
        player.stamina_max = r.u16()
        player.mana = r.u16()
        player.mana_max = r.u16()
        player.gold = r.u32()
        player.armor = r.u16()
        player.weight = r.u16()
        if detail >= 5 and r.remaining >= 3:
            player.weight_max = r.u16()
            player.race = {1: "human", 2: "elf", 3: "gargoyle"}.get(r.u8(), "")
        if detail >= 3 and r.remaining >= 4:
            r.u16()                             # stat cap
            player.followers = r.u8()
            player.followers_max = r.u8()
        if detail >= 4 and r.remaining >= 14:
            r.skip(8)                           # elemental resistances
            player.luck = r.u16()
    else:
        mob = world.mobile(serial)
        mob.name = name or mob.name
        mob.hits, mob.hits_max = hits, hits_max
        mob.touch()


@handles(0xA1, 0xA2, 0xA3)
def _vital(client, world, r: Reader) -> None:
    packet_id = r.data[0]
    serial = r.u32()
    maximum = r.u16()
    current = r.u16()

    if serial == world.player.serial:
        player = world.player
        if packet_id == 0xA1:
            player.hits, player.hits_max = current, maximum
            if current > 0:
                player.dead = False             # nothing announces a resurrection
        elif packet_id == 0xA2:
            player.mana, player.mana_max = current, maximum
        else:
            player.stamina, player.stamina_max = current, maximum
    elif packet_id == 0xA1:
        mob = world.mobile(serial)
        mob.hits, mob.hits_max = current, maximum
        mob.touch()


@handles(0x2D)
def _attributes(client, world, r: Reader) -> None:
    serial = r.u32()
    hits_max, hits = r.u16(), r.u16()
    mana_max, mana = r.u16(), r.u16()
    stam_max, stam = r.u16(), r.u16()

    if serial == world.player.serial:
        player = world.player
        player.hits, player.hits_max = hits, hits_max
        player.mana, player.mana_max = mana, mana_max
        player.stamina, player.stamina_max = stam, stam_max
    else:
        mob = world.mobile(serial)
        mob.hits, mob.hits_max = hits, hits_max
        mob.touch()


@handles(0x17)
def _healthbar(client, world, r: Reader) -> None:
    serial = r.u32()
    count = r.u16()
    mob = world.mobile(serial)
    for _ in range(count):
        if r.remaining < 3:
            break
        bar_type = r.u16()
        enabled = r.u8()
        if bar_type == 1:                       # green: poisoned
            mob.flags = (mob.flags | 0x04) if enabled else (mob.flags & ~0x04)
        elif bar_type == 2:                     # yellow: invulnerable
            pass
    mob.touch()


@handles(0x72)
def _war_mode(client, world, r: Reader) -> None:
    world.player.war_mode = r.u8() != 0


@handles(0x2C)
def _death(client, world, r: Reader) -> None:
    world.player.dead = True
    world.add_journal(JournalEntry("You are dead.", kind="system"))


@handles(0x55)
def _login_complete(client, world, r: Reader) -> None:
    world.login_complete = True
    client.on_login_complete()


@handles(0x73)
def _ping(client, world, r: Reader) -> None:
    client.on_ping(r.u8())


@handles(0x3A)
def _skills(client, world, r: Reader) -> None:
    kind = r.u8()
    has_cap = kind in (0x02, 0xDF)
    single = kind in (0xDF, 0xFF)

    while r.remaining >= 7:
        index = r.u16()
        if index == 0 and not single:
            break
        value = r.u16() / 10.0
        base = r.u16() / 10.0
        lock = r.u8()
        cap = (r.u16() / 10.0) if (has_cap and r.remaining >= 2) else 0.0

        skill_index = index - 1 if not single else index
        name = SKILL_NAMES[skill_index] if 0 <= skill_index < len(SKILL_NAMES) else f"skill_{skill_index}"
        world.skills[name] = {
            "value": value,
            "base": base,
            "cap": cap,
            "lock": ["up", "down", "locked"][lock] if lock < 3 else "up",
        }
        if single:
            break


# --------------------------------------------------------------------------
# other mobiles
# --------------------------------------------------------------------------

def _apply_mobile(world, serial, body, x, y, z, direction, hue, flags, notoriety) -> Mobile:
    mob = world.mobile(serial)
    mob.graphic = body
    mob.x, mob.y, mob.z = x, y, z
    mob.direction = direction
    mob.hue = hue
    mob.flags = flags
    mob.notoriety = notoriety
    mob.touch()

    if serial == world.player.serial:
        world.player.x, world.player.y, world.player.z = x, y, z
        world.player.direction = direction
        mob.is_player = True
    return mob


@handles(0x77)
def _mobile_moving(client, world, r: Reader) -> None:
    serial = r.u32()
    body = r.u16()
    x, y, z = r.u16(), r.u16(), r.i8()
    direction = r.u8()
    hue = r.u16()
    flags = r.u8()
    notoriety = r.u8()
    _apply_mobile(world, serial, body, x, y, z, direction, hue, flags, notoriety)


@handles(0x78)
def _mobile_incoming(client, world, r: Reader) -> None:
    serial = r.u32()
    body = r.u16()
    x, y, z = r.u16(), r.u16(), r.i8()
    direction = r.u8()
    hue = r.u16()
    flags = r.u8()
    notoriety = r.u8()
    mob = _apply_mobile(world, serial, body, x, y, z, direction, hue, flags, notoriety)

    mob.equipment.clear()
    while r.remaining >= 7:
        item_serial = r.u32()
        if item_serial == 0:
            break
        graphic = r.u16()
        layer = r.u8()
        if graphic & 0x8000:
            graphic &= 0x7FFF
            item_hue = r.u16()
        else:
            item_hue = 0
        mob.equipment[LAYERS.get(layer, f"layer_{layer}")] = item_serial

        worn = world.item(item_serial)
        worn.graphic = graphic
        worn.hue = item_hue
        worn.layer = layer
        worn.container = serial
        worn.touch()

    client.on_mobile_seen(mob)


@handles(0x1D)
def _delete_object(client, world, r: Reader) -> None:
    world.forget(r.u32())


@handles(0x2F)
def _swing(client, world, r: Reader) -> None:
    r.u8()
    attacker = r.u32()
    defender = r.u32()
    client.on_swing(attacker, defender)


@handles(0xAA)
def _attack_target(client, world, r: Reader) -> None:
    world.player.last_target = r.u32()


@handles(0x0B)
def _damage(client, world, r: Reader) -> None:
    serial = r.u32()
    amount = r.u16()
    client.on_damage(serial, amount)


# --------------------------------------------------------------------------
# items, containers, corpses
# --------------------------------------------------------------------------

@handles(0x1A)
def _world_item(client, world, r: Reader) -> None:
    serial = r.u32()
    has_amount = bool(serial & 0x80000000)
    serial &= 0x7FFFFFFF

    graphic = r.u16()
    if graphic & 0x8000:
        graphic = (graphic & 0x7FFF) + r.u8()

    amount = r.u16() if has_amount else 1

    x = r.u16()
    has_hue = bool(x & 0x8000)
    x &= 0x7FFF

    y = r.u16()
    has_flags = bool(y & 0x8000)
    has_direction = bool(y & 0x4000)
    y &= 0x3FFF

    if has_direction:
        r.u8()
    z = r.i8()
    hue = r.u16() if has_hue else 0
    flags = r.u8() if has_flags else 0

    item = world.item(serial)
    item.graphic, item.amount = graphic, amount
    item.x, item.y, item.z = x, y, z
    item.hue, item.flags = hue, flags
    item.container = 0
    item.touch()
    if not item.name:
        client.request_properties(serial)


@handles(0xF3)
def _object_info(client, world, r: Reader) -> None:
    r.u16()                                     # always 0x0001
    kind = r.u8()
    serial = r.u32()
    graphic = r.u16() + r.u8()                  # graphic + increment
    amount = r.u16()
    r.u16()                                     # amount repeated
    x, y, z = r.u16(), r.u16(), r.i8()
    direction = r.u8()
    hue = r.u16()
    flags = r.u8()

    if kind == 0x01:                            # a mobile
        _apply_mobile(world, serial, graphic, x, y, z, direction, hue, flags, 0)
        return

    item = world.item(serial)
    item.graphic = graphic
    item.amount = max(amount, 1)
    item.x, item.y, item.z = x, y, z
    item.hue, item.flags = hue, flags
    item.container = 0
    item.touch()
    if not item.name:
        client.request_properties(serial)


@handles(0x2E)
def _worn_item(client, world, r: Reader) -> None:
    serial = r.u32()
    graphic = r.u16()
    r.u8()
    layer = r.u8()
    parent = r.u32()
    hue = r.u16()

    item = world.item(serial)
    item.graphic, item.layer, item.container, item.hue = graphic, layer, parent, hue
    item.touch()

    if parent in world.mobiles:
        world.mobiles[parent].equipment[LAYERS.get(layer, f"layer_{layer}")] = serial
    if parent == world.player.serial and layer == 0x15:
        world.player.backpack = serial


def _read_container_item(client, world, r: Reader, container_override: int | None = None) -> Item:
    serial = r.u32()
    graphic = r.u16() + r.u8()
    amount = r.u16()
    x, y = r.u16(), r.u16()
    if client.profile.container_grid_lines:
        r.u8()                                  # grid slot
    container = r.u32()
    hue = r.u16()

    item = world.item(serial)
    item.graphic = graphic
    item.amount = max(amount, 1)
    item.x, item.y = x, y
    item.container = container if container_override is None else container_override
    item.hue = hue
    item.touch()
    return item


@handles(0x25)
def _container_item(client, world, r: Reader) -> None:
    _read_container_item(client, world, r)


@handles(0x3C)
def _container_contents(client, world, r: Reader) -> None:
    count = r.u16()
    for _ in range(count):
        if r.remaining < 19:
            break
        item = _read_container_item(client, world, r)
        client.request_properties(item.serial)


@handles(0x24)
def _open_container(client, world, r: Reader) -> None:
    serial = r.u32()
    r.u16()                                     # gump graphic
    if serial not in world.opened_containers:
        world.opened_containers.append(serial)
    del world.opened_containers[:-16]


@handles(0x89)
def _corpse_clothing(client, world, r: Reader) -> None:
    corpse = r.u32()
    while r.remaining >= 5:
        layer = r.u8()
        if layer == 0:
            break
        serial = r.u32()
        item = world.item(serial)
        item.layer = layer
        item.container = corpse
        item.touch()


@handles(0xD6)
def _properties(client, world, r: Reader) -> None:
    r.u16()                                     # always 0x0001
    serial = r.u32()
    r.u16()
    r.u32()                                     # list hash

    lines: list[str] = []
    while r.remaining >= 6:
        cliloc = r.u32()
        if cliloc == 0:
            break
        arg_length = r.u16()
        raw = r.raw(arg_length)
        # Cliloc arguments are little-endian UTF-16, unlike the rest of the protocol.
        text = raw.decode("utf-16-le", "replace").rstrip("\x00")
        lines.append(text if text else f"cliloc:{cliloc}")

    entity = world.entity(serial)
    if entity is None:
        return
    if lines:
        # The first property line is the object's name, possibly "12 gold coins".
        name = lines[0].replace("\t", " ").strip()
        if name and not name.startswith("cliloc:"):
            entity.name = name
    if isinstance(entity, Item):
        entity.properties = lines
    entity.touch()


# --------------------------------------------------------------------------
# speech and messages
# --------------------------------------------------------------------------

def _record_speech(client, world, serial, kind, hue, speaker, text) -> None:
    if not text.strip():
        return
    entry = JournalEntry(
        text=text.strip(),
        speaker=speaker.strip(),
        serial=serial,
        kind=MESSAGE_KINDS.get(kind, "regular"),
        hue=hue,
    )
    world.add_journal(entry)
    client.on_journal(entry)


@handles(0x1C)
def _ascii_message(client, world, r: Reader) -> None:
    serial = r.u32()
    r.u16()                                     # body
    kind = r.u8()
    hue = r.u16()
    r.u16()                                     # font
    speaker = r.ascii(30)
    text = r.raw(r.remaining).split(b"\x00", 1)[0].decode("ascii", "replace")
    _record_speech(client, world, serial, kind, hue, speaker, text)


@handles(0xAE)
def _unicode_message(client, world, r: Reader) -> None:
    serial = r.u32()
    r.u16()
    kind = r.u8()
    hue = r.u16()
    r.u16()
    r.raw(4)                                    # language
    speaker = r.ascii(30)
    text = r.raw(r.remaining).decode("utf-16-be", "replace").split("\x00", 1)[0]
    _record_speech(client, world, serial, kind, hue, speaker, text)


CORPSE_WAYPOINT = 1046414


@handles(0xE5)
def _show_waypoint(client, world, r: Reader) -> None:
    serial = r.u32()
    x, y, z, map_index = r.u16(), r.u16(), r.i8(), r.u8()
    kind = r.u16()
    r.u16()                                     # ignore the object
    label = r.u32()
    name = r.raw(r.remaining).decode("utf-16-le", "replace").split("\x00", 1)[0]
    world.waypoints[serial] = Waypoint(serial, x, y, z, map_index, kind, name,
                                       corpse=label == CORPSE_WAYPOINT)


@handles(0xE6)
def _remove_waypoint(client, world, r: Reader) -> None:
    world.waypoints.pop(r.u32(), None)


def _cliloc_text(cliloc: int, arguments: str) -> str:
    """Without the client's string table we show the id plus its arguments.

    In practice the arguments carry the part a player cares about (names,
    numbers, item descriptions), so this stays readable.
    """
    parts = [p for p in arguments.split("\t") if p]
    if parts:
        return f"{' '.join(parts)} (cliloc {cliloc})"
    return f"cliloc {cliloc}"


@handles(0xC1)
def _cliloc_message(client, world, r: Reader) -> None:
    serial = r.u32()
    r.u16()
    kind = r.u8()
    hue = r.u16()
    r.u16()
    cliloc = r.u32()
    speaker = r.ascii(30)
    arguments = r.raw(r.remaining).decode("utf-16-le", "replace").rstrip("\x00")
    _record_speech(client, world, serial, kind, hue, speaker, _cliloc_text(cliloc, arguments))


@handles(0xCC)
def _cliloc_affix(client, world, r: Reader) -> None:
    serial = r.u32()
    r.u16()
    kind = r.u8()
    hue = r.u16()
    r.u16()
    cliloc = r.u32()
    r.u8()                                      # affix flags
    speaker = r.ascii(30)
    affix = r.ascii_z()
    arguments = r.raw(r.remaining).decode("utf-16-le", "replace").rstrip("\x00")
    text = _cliloc_text(cliloc, arguments)
    _record_speech(client, world, serial, kind, hue, speaker, f"{text} {affix}".strip())


@handles(0x88)
def _paperdoll(client, world, r: Reader) -> None:
    serial = r.u32()
    text = r.ascii(60).strip()
    if serial == world.player.serial:
        world.player.name = text.split(",")[0].strip() or world.player.name
    else:
        mob = world.mobile(serial)
        mob.name = text
        mob.touch()


# --------------------------------------------------------------------------
# prompts the agent has to answer
# --------------------------------------------------------------------------

@handles(0x6C)
def _target_cursor(client, world, r: Reader) -> None:
    allow_ground = r.u8() == 1
    cursor_id = r.u32()
    cursor_type = r.u8()
    world.target.active = cursor_type != 3
    world.target.cursor_id = cursor_id
    world.target.allow_ground = allow_ground
    world.target.cursor_type = cursor_type
    world.target.requested_at = __import__("time").time()
    if world.target.active:
        client.on_target_request()


@handles(0xB0)
def _generic_gump(client, world, r: Reader) -> None:
    serial = r.u32()
    gump_id = r.u32()
    x, y = r.u32(), r.u32()
    layout = r.ascii(r.u16())
    lines = []
    if r.remaining >= 2:
        for _ in range(r.u16()):
            if r.remaining < 2:
                break
            lines.append(r.unicode(r.u16()))
    gump = Gump(serial=serial, gump_id=gump_id, x=x, y=y, layout=layout, lines=lines)
    world.gumps[serial] = gump
    client.on_gump(gump)


@handles(0xDD)
def _compressed_gump(client, world, r: Reader) -> None:
    serial = r.u32()
    gump_id = r.u32()
    x, y = r.u32(), r.u32()

    compressed_length = r.u32()
    r.u32()                                     # decompressed length
    try:
        layout = zlib.decompress(r.raw(max(compressed_length - 4, 0))).decode("ascii", "replace")
    except zlib.error:
        world.warn(f"could not decompress gump {gump_id}")
        return
    layout = layout.rstrip("\x00")

    lines: list[str] = []
    if r.remaining >= 12:
        line_count = r.u32()
        text_compressed = r.u32()
        r.u32()
        try:
            blob = zlib.decompress(r.raw(max(text_compressed - 4, 0)))
        except zlib.error:
            blob = b""
        text_reader = Reader(blob)
        for _ in range(line_count):
            if text_reader.remaining < 2:
                break
            lines.append(text_reader.unicode(text_reader.u16()))

    gump = Gump(serial=serial, gump_id=gump_id, x=x, y=y, layout=layout, lines=lines)
    world.gumps[serial] = gump
    client.on_gump(gump)


# --------------------------------------------------------------------------
# shopping
# --------------------------------------------------------------------------

@handles(0x74)
def _buy_list(client, world, r: Reader) -> None:
    vendor = r.u32()
    count = r.u8()
    entries = []
    for _ in range(count):
        if r.remaining < 5:
            break
        price = r.u32()
        name = r.ascii(r.u8())
        entries.append(VendorItem(serial=0, graphic=0, amount=0, price=price, name=name))
    world.vendor_items[vendor] = entries
    client.on_vendor_list(vendor, entries)


@handles(0x9E)
def _sell_list(client, world, r: Reader) -> None:
    vendor = r.u32()
    count = r.u16()
    entries = []
    for _ in range(count):
        if r.remaining < 14:
            break
        serial = r.u32()
        graphic = r.u16()
        r.u16()                                 # hue
        amount = r.u16()
        price = r.u16()
        name = r.ascii(r.u16())
        entries.append(VendorItem(serial, graphic, amount, price, name))
    world.vendor_items[vendor] = entries
    client.on_vendor_list(vendor, entries)


# --------------------------------------------------------------------------
# odds and ends worth keeping
# --------------------------------------------------------------------------

@handles(0x4F)
def _light_level(client, world, r: Reader) -> None:
    world.light_level = r.u8()


@handles(0xBC)
def _season(client, world, r: Reader) -> None:
    world.season = r.u8()


@handles(0x53)
def _reject(client, world, r: Reader) -> None:
    world.warn(f"server rejected the session (code 0x{r.u8():02X})")


@handles(0xBF)
def _general_info(client, world, r: Reader) -> None:
    subcommand = r.u16()
    if subcommand == 0x08:                      # map change
        world.player.map = r.u8()
    elif subcommand == 0x04:                    # close a gump we have open
        gump_id = r.u32()
        for serial, gump in list(world.gumps.items()):
            if gump.gump_id == gump_id:
                world.gumps.pop(serial, None)
    elif subcommand == 0x22:                    # damage
        serial = r.u32()
        client.on_damage(serial, r.u8())
