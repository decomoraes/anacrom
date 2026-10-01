"""Things a player does between fights, as functions the planner can call.

Each takes the live client and a ``should_stop`` callable, does one job, and
returns a small dict saying what happened.  None of them decides *whether* to
run -- that is the planner's call -- and none loops forever.

Place and vendor details are UOAlive's Britain, found by hand, and live in
``BRITAIN`` so another town is a change to one table.
"""
from __future__ import annotations

import time
from typing import Callable

from .client import Client

RESURRECTION_GUMP = 3660239720
RESURRECT_AT_SUMMONER = 1                      # the first button: Britain's summoner

BRITAIN = {
    "shop": {"name": "Fleta", "serial": 0x00065C93, "x": 1409, "y": 1819},
    "quiet": (1394, 1636),                       # north of town, out of the crowds
    "crowd": (1430, 1692),                       # the bank: many people, and murderers
}

REAGENTS = {"mandrake root": 0x0F86, "garlic": 0x0F84, "ginseng": 0x0F85,
            "black pearl": 0x0F7A, "sulfurous ash": 0x0F8C, "nightshade": 0x0F88,
            "spider's silk": 0x0F8D}
BAGS = {0x0E75, 0x0E76, 0x0E79, 0x09B0}

# Magery training spells (cast-macro id, mana, reagents), cheapest useful first.
BLESS = (17, 9, ("mandrake root", "garlic"))
CURE = (11, 6, ("garlic", "ginseng"))
GREATER_HEAL = (29, 11, ("garlic", "ginseng", "mandrake root", "spider's silk"))  # 4th circle
MANA_COST = {1: 4, 2: 6, 3: 9, 4: 11, 5: 14, 6: 20, 7: 40, 8: 50}
FIZZLE = "502632"
NO_REAGENTS = "502630"


def carried(client: Client, graphic: int) -> int:
    """How many of an item we hold, counted through bags."""
    world = client.world
    total, todo = 0, [world.player.backpack]
    while todo:
        for item in world.contents_of(todo.pop()):
            if item.graphic == graphic:
                total += item.amount
            elif item.graphic in BAGS:
                todo.append(item.serial)
    return total


def open_pack(client: Client) -> None:
    pack = client.world.player.backpack
    if not pack:
        return
    for item in client.open_container(pack):
        if item.graphic in BAGS and not client.world.contents_of(item.serial):
            client.open_container(item.serial)


def resurrect(client: Client, wait: float = 6.0) -> dict:
    """Answer the death menu, so we are alive in Britain with our things."""
    world, player = client.world, client.world.player
    if not player.dead:
        return {"alive": True, "note": "was not dead"}
    deadline = time.time() + wait
    while time.time() < deadline:
        gump = next((g for g in world.gumps.values() if g.gump_id == RESURRECTION_GUMP), None)
        if gump is not None:
            client.gump_reply(gump.serial, RESURRECT_AT_SUMMONER)
            client.pump(3.0)
            break
        client.pump(0.3)
    return {"alive": not player.dead, "position": list(player.position)}


def reconnect(client: Client, tries: int = 3) -> bool:
    """Log back in after a dropped connection, with a growing pause."""
    for attempt in range(tries):
        if client.connected:
            return True
        try:
            client.connect()
            return True
        except Exception as exc:                          # LoginError, OSError, ProtocolError...
            client.world.warn(f"reconnect {attempt + 1} failed: {exc}")
            time.sleep(5 * (attempt + 1))
    return client.connected


def buy_reagents(client: Client, wants: dict[str, int], budget: int,
                 should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Walk to the mage shop and buy what we can afford of ``wants`` (name -> amount)."""
    shop = BRITAIN["shop"]
    world, player = client.world, client.world.player
    walked = client.walk_to(shop["x"], shop["y"], stop_within=2, max_steps=900,
                            on_step=lambda _: not should_stop())
    if not walked["arrived"]:
        return {"bought": {}, "note": f"could not reach the shop ({walked.get('stopped')})"}
    keeper = world.mobiles.get(shop["serial"])
    if keeper is not None:                           # vendors only answer within a tile
        client.walk_to(keeper.x, keeper.y, stop_within=1, max_steps=12)
    client.say(f"{shop['name']} buy")
    client.pump(2.0)
    stock = {e.name: e for e in world.vendor_items.get(shop["serial"], [])}
    purchases, bought, spent = [], {}, 0
    for name, amount in wants.items():
        entry = stock.get(name)
        if entry is None or not entry.serial:
            continue
        count = min(amount, entry.amount, (budget - spent) // max(1, entry.price))
        if count > 0:
            purchases.append((entry.serial, count))
            bought[name], spent = count, spent + count * entry.price
    if purchases:
        client.buy(shop["serial"], purchases)
        client.pump(1.5)
    return {"bought": bought, "spent": spent, "gold": player.gold}


def train_magery(client: Client, seconds: float, spell: tuple = BLESS,
                 should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Cast a beneficial spell on ourselves until mana runs low, meditate, repeat."""
    world, player = client.world, client.world.player
    spell_id, mana, reagents = spell
    names = {n: REAGENTS[n] for n in reagents}

    def skill(name):
        return world.skills.get(name, {}).get("value", 0.0)

    before = {n: skill(n) for n in ("Magery", "Meditation")}
    casts = fizzles = 0
    stopped = "time limit"
    deadline = time.time() + seconds
    open_pack(client)
    while time.time() < deadline:
        if should_stop():
            stopped = "interrupted"
            break
        if player.dead or not client.connected:
            stopped = "dead or disconnected"
            break
        if any(carried(client, g) == 0 for g in names.values()):
            stopped = "out of reagents"
            break
        if player.mana >= mana:
            mark = time.time()
            client.cast(spell_id, target=player.serial)
            client.pump(2.0)
            casts += 1
            fizzles += FIZZLE in " ".join(e.text for e in list(world.journal) if e.at > mark)
            continue
        client.use_skill("Meditation")
        retry = time.time() + 10
        while player.mana < player.mana_max and time.time() < deadline and not should_stop():
            client.pump(0.5)
            if time.time() > retry:
                client.use_skill("Meditation")
                retry = time.time() + 10
    return {"stopped": stopped, "casts": casts, "fizzles": fizzles,
            "gains": {n: (round(before[n], 1), round(skill(n), 1)) for n in before}}
