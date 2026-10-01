"""Hunt as a mage: burn things down from range, loot the gold, rest, repeat.

Run it with:  uo script scripts/hunt_mage.py [minutes] [radius] [flee_percent] [prey,names]

Fireball (black pearl) while there are pearls and mana for it, Magic Arrow
(sulfurous ash) when there are not, and fists only if something is on top of
us with nothing left to cast.  Mana is meditated back between fights, not
during them.  Below flee_percent health we walk straight away from whatever
is hitting us and meditate once it gives up.

Only named prey, red and orange names, and whatever attacks us are hunted,
and only corpses where our own target died are opened.  Gold and reagents are
taken; everything else stays, because a starter mage cannot carry much.

This is supervised play: UOAlive counts unattended killing as AFK resource
gathering, so stay at the keyboard while it runs.
"""
import time

FIREBALL, MAGIC_ARROW, HEAL = 18, 5, 4
BLACK_PEARL, SULFUROUS_ASH = 0x0F7A, 0x0F8C
GARLIC, GINSENG, SPIDERS_SILK = 0x0F84, 0x0F85, 0x0F8D
HEAL_BELOW = 65
CORPSE, GOLD = 0x2006, 0x0EED
REAGENTS = {0x0F7A, 0x0F7B, 0x0F84, 0x0F85, 0x0F86, 0x0F88, 0x0F8C, 0x0F8D}
CAST_RANGE = 8
KITE_WITHIN = 2

minutes = float(args[0]) if args else 10.0
radius = int(args[1]) if len(args) > 1 else 14
flee_at = int(args[2]) if len(args) > 2 else 45
# Monsters are grey (attackable) on shards like UOAlive, the same colour as the
# goats, so prey is named.  Red and orange names, and anything that swings at
# us, are always fair game.
prey = [p.strip().lower() for p in (args[3] if len(args) > 3 else
        "skeleton,zombie,ghoul,mongbat,rat,headless,slime").split(",")]
# Things that fly faster than we run, or hit far harder than they look: once
# one engages, running does not save a starter mage, so leave before it does.
DANGER = ("spectre", "wraith", "shade", "lich", "bone mage", "paragon")
DANGER_RANGE = 10


def health():
    return round(player.hits * 100 / player.hits_max) if player.hits_max else 100


BAGS = {0x0E75, 0x0E76, 0x0E79, 0x09B0}          # backpack, bag, pouch, belt pouch


def open_bags():
    """Reagents usually sit in a bag inside the pack; the server looks there too."""
    for item in client.open_container(player.backpack):
        if item.graphic in BAGS:
            client.open_container(item.serial)


def carried(graphic):
    total, todo = 0, [player.backpack]
    while todo:
        for item in world.contents_of(todo.pop()):
            if item.graphic == graphic:
                total += item.amount
            elif item.graphic in BAGS:
                todo.append(item.serial)
    return total


def keep_going(_step):
    return not daemon.interrupted


def is_prey(mob):
    if mob.serial in client.attackers():
        return True
    name = (mob.name or "").lower()
    if "paragon" in name:                  # far stronger than it looks; leave it be
        return False
    return mob.hostile or (mob.notoriety == 3 and any(p in name for p in prey))


def next_target():
    alive = [m for m in world.nearby_mobiles(radius)
             if is_prey(m) and (not m.hits_max or m.hits > 0)]
    return alive[0] if alive else None


def spell_for_now():
    if player.mana >= 9 and carried(BLACK_PEARL):
        return FIREBALL
    if player.mana >= 4 and carried(SULFUROUS_ASH):
        return MAGIC_ARROW
    return None


def danger_near():
    for mob in world.nearby_mobiles(DANGER_RANGE):
        if any(word in (mob.name or "").lower() for word in DANGER):
            return mob
    return None


def retreat_from(mob):
    print(f"        {mob.name} within {world.distance_to(mob.serial)} tiles -- backing off")
    client.set_war_mode(False)
    dx = (player.x > mob.x) - (player.x < mob.x) or 1
    dy = (player.y > mob.y) - (player.y < mob.y)
    client.walk_to(player.x + dx * 16, player.y + dy * 16, max_steps=22, on_step=keep_going)


def can_heal():
    return (player.mana >= 4 and carried(GARLIC) and carried(GINSENG)
            and carried(SPIDERS_SILK))


def heal_self():
    client.cast(HEAL, target=player.serial)
    client.pump(1.4)


def meditate(until_percent=90, limit=40.0):
    deadline = time.time() + limit
    client.use_skill("Meditation")
    retry = time.time() + 10
    while player.mana * 100 < player.mana_max * until_percent and time.time() < deadline:
        check_interrupt()
        if next_target() and world.distance_to(next_target().serial) <= 4:
            return                                   # company; stop sitting still
        client.pump(0.5)
        if time.time() > retry:
            client.use_skill("Meditation")
            retry = time.time() + 10


def flee():
    threats = [m for m in world.nearby_mobiles(12) if is_prey(m)]
    if not threats:
        return
    cx = sum(m.x for m in threats) / len(threats)
    cy = sum(m.y for m in threats) / len(threats)
    dx = (player.x > cx) - (player.x < cx) or 1
    dy = (player.y > cy) - (player.y < cy)
    client.set_war_mode(False)
    client.walk_to(player.x + dx * 14, player.y + dy * 14, max_steps=18, on_step=keep_going)


def loot(near):
    corpses = [i for i in world.ground_items(8)
               if i.graphic == CORPSE and abs(i.x - near[0]) <= 3 and abs(i.y - near[1]) <= 3]
    if not corpses:
        return 0
    corpse = corpses[0]
    client.walk_to(corpse.x, corpse.y, stop_within=1, max_steps=12, on_step=keep_going)
    gold = 0
    for item in client.open_container(corpse.serial):
        check_interrupt()
        if item.graphic == GOLD or item.graphic in REAGENTS:
            client.move_item(item.serial, player.backpack)
            if item.graphic == GOLD:
                gold += item.amount
    return gold


started = time.time()
gold_before = player.gold
skills_before = {n: world.skills.get(n, {}).get("value", 0.0) for n in ("Magery", "Meditation", "Wrestling")}
kills, casts, looted = 0, 0, 0
result = "time limit"

open_bags()
print(f"black pearl {carried(BLACK_PEARL)}, sulfurous ash {carried(SULFUROUS_ASH)}, "
      f"mana {player.mana}/{player.mana_max}")
while time.time() - started < minutes * 60:
    check_interrupt()
    if player.dead:
        result = "we died"
        break

    threat = danger_near()
    if threat is not None:
        retreat_from(threat)
        continue

    target = next_target()
    if target is None:
        if health() < 90 and can_heal():
            heal_self()
        elif player.mana < player.mana_max:
            meditate(until_percent=100, limit=20)
        else:
            client.pump(1.0)
        continue

    name = target.name or "something"
    print(f"{time.time() - started:6.0f}s  engaging {name} ({world.distance_to(target.serial)} tiles)")
    last_seen = (target.x, target.y)
    engaged = time.time()
    while time.time() - engaged < 60:
        check_interrupt()
        mob = world.mobiles.get(target.serial)
        if mob is None or (mob.hits_max and mob.hits <= 0):
            break
        last_seen = (mob.x, mob.y)

        threat = danger_near()
        if threat is not None:
            retreat_from(threat)
            break

        if health() < flee_at:
            print(f"        health {health()}% -- running")
            flee()
            while health() < 90 and can_heal():
                check_interrupt()
                heal_self()
            meditate(until_percent=60, limit=30)
            break
        if health() < HEAL_BELOW and can_heal():
            heal_self()
            continue

        gap = world.distance_to(target.serial)
        spell = spell_for_now()
        if spell and gap is not None and gap <= KITE_WITHIN:
            # Too close for a caster: open the gap before the next cast.
            dx = (player.x > mob.x) - (player.x < mob.x)
            dy = (player.y > mob.y) - (player.y < mob.y)
            client.walk_to(player.x + dx * 5, player.y + dy * 5, max_steps=4, on_step=keep_going)
            continue
        if spell and gap is not None and gap <= CAST_RANGE:
            client.cast(spell, target=target.serial)
            casts += 1
            client.pump(1.6)
        elif spell:
            client.walk_to(mob.x, mob.y, stop_within=CAST_RANGE - 2, max_steps=6, on_step=keep_going)
        else:
            # Nothing left to cast: close in and use our fists.
            client.set_war_mode(True)
            client.attack(target.serial)
            if gap is not None and gap > 1:
                client.walk_to(mob.x, mob.y, stop_within=1, max_steps=6, on_step=keep_going)
            client.pump(1.0)

    if world.mobiles.get(target.serial) is None:
        kills += 1
        client.set_war_mode(False)
        client.pump(1.0)
        got = loot(last_seen)
        looted += got
        print(f"        {name} down; looted {got} gold")

print(f"{kills} kills, {casts} casts, {looted} gold looted, stopped: {result}")
print(f"  gold {gold_before} -> {player.gold}, hp {player.hits}/{player.hits_max}")
print(f"  black pearl {carried(BLACK_PEARL)}, sulfurous ash {carried(SULFUROUS_ASH)}")
for n, v in skills_before.items():
    print(f"  {n:<11} {v:5.1f} -> {world.skills.get(n, {}).get('value', 0.0):5.1f}")
