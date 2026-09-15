"""Fight what is nearby, then loot it.

Run it with:  uo script scripts/combat.py [radius] [flee_percent] [max_kills]

Deciding every swing over a command line is too slow, so the whole engagement
lives here: close the distance, hold the attack, watch our own health, and when
the thing dies, open the corpse and take what is worth taking.

Nothing here is clever about terrain -- if we cannot reach the target we give
up and say so, which is the right answer for an agent that can then think again.
"""
import time

CORPSE_GRAPHIC = 0x2006
GOLD_GRAPHIC = 0x0EED

WORTH_TAKING = {
    GOLD_GRAPHIC,
    0x0F3F, 0x1BFB,                      # arrows, bolts
    0x0E76, 0x0E75,                      # bags, pouches
    0x1078, 0x0F7A, 0x0F84, 0x0F85,      # reagents
    0x0F86, 0x0F88, 0x0F8C, 0x0F8D,
}

radius = int(args[0]) if len(args) > 0 else 12
flee_at = int(args[1]) if len(args) > 1 else 35
max_kills = int(args[2]) if len(args) > 2 else 3


def health_percent():
    if not player.hits_max:
        return 100
    return round(player.hits * 100 / player.hits_max)


def pick_target():
    candidates = [
        m for m in world.hostiles(radius)
        if m.hits_max == 0 or m.hits > 0
    ]
    return candidates[0] if candidates else None


def loot(corpse_serial):
    items = client.open_container(corpse_serial)
    if not items:
        print(f"  corpse 0x{corpse_serial:08X} held nothing we could see")
        return 0

    taken = 0
    for item in list(items):
        check_interrupt()
        keep = item.graphic in WORTH_TAKING or (item.properties and "gold" in (item.name or "").lower())
        if not keep:
            continue
        client.move_item(item.serial, player.backpack)
        taken += 1
        print(f"  took {item.name or hex(item.graphic)} x{item.amount}")
    if not taken:
        print("  nothing on the corpse was worth carrying")
    return taken


kills = 0
while kills < max_kills:
    check_interrupt()

    if health_percent() < flee_at:
        print(f"health at {health_percent()}% -- breaking off")
        result = "fled"
        break

    target = pick_target()
    if target is None:
        print("nothing hostile within %d tiles" % radius)
        result = f"{kills} kills, area clear"
        break

    name = target.name or f"creature {target.graphic:#06x}"
    print(f"engaging {name} (0x{target.serial:08X}) at {target.x},{target.y}")

    approach = client.walk_to(target.x, target.y, stop_within=1,
                              on_step=lambda _: not daemon.interrupted)
    if not approach["arrived"]:
        print(f"  could not reach it ({approach.get('stopped')}) -- leaving it alone")
        result = "unreachable"
        break

    client.set_war_mode(True)
    client.attack(target.serial)
    died_at = (target.x, target.y)

    engaged_at = time.time()
    while time.time() - engaged_at < 90:
        check_interrupt()
        client.pump(0.3)

        if health_percent() < flee_at:
            break

        living = world.mobiles.get(target.serial)
        if living is None:
            break
        died_at = (living.x, living.y)

        # Re-issue the attack if the server stopped swinging for us.
        if time.time() - engaged_at > 4 and int(time.time() * 2) % 8 == 0:
            client.attack(target.serial)

        gap = world.distance_to(target.serial)
        if gap is not None and gap > 1:
            client.walk_to(living.x, living.y, stop_within=1, max_steps=6,
                           on_step=lambda _: not daemon.interrupted)

    if health_percent() < flee_at:
        print(f"health at {health_percent()}% -- breaking off")
        result = "fled"
        break

    kills += 1
    print(f"  {name} is down")
    client.set_war_mode(False)
    client.pump(1.2)

    corpses = [
        i for i in world.ground_items(6)
        if i.graphic == CORPSE_GRAPHIC and distance((i.x, i.y), died_at) <= 3
    ]
    if corpses:
        client.walk_to(corpses[0].x, corpses[0].y, stop_within=1)
        loot(corpses[0].serial)
    else:
        print("  no corpse appeared nearby")
else:
    result = f"{kills} kills, limit reached"

client.set_war_mode(False)
print(f"done: hp {player.hits}/{player.hits_max}, gold {player.gold}")
