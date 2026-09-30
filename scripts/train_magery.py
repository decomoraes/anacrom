"""Train Magery and Meditation together.

Run it with:  uo script scripts/train_magery.py [minutes] [spell]

Casts a beneficial spell on ourselves until mana runs low, meditates it back,
and reports what moved.  The default, Strength (16), is second circle: at
Magery 30 first-circle spells are too easy to teach anything, and the starter
book (Spellbook 0x382A8C38) has no Bless.  Spell numbers are cast-macro ids,
one-based; the
server subtracts one (IncomingPlayerPackets, case 0x56).

Nothing is gathered or gained, which is the kind of unattended training
UOAlive's rules allow.
"""
import time

MANA_BY_CIRCLE = {1: 4, 2: 6, 3: 9, 4: 11, 5: 14, 6: 20, 7: 40, 8: 50}
FIZZLED = "502632"                   # The spell fizzles.
CANNOT_CAST = {"500015": "that spell is not in our spellbook",
               "502630": "out of reagents"}

minutes = float(args[0]) if args else 3.0
spell = int(args[1]) if len(args) > 1 else 16
cost = MANA_BY_CIRCLE[(spell - 1) // 8 + 1]


def skill(name):
    return world.skills.get(name, {}).get("value", 0.0)


def heard_since(mark):
    return [e.text for e in list(world.journal) if e.at > mark]


before = {name: skill(name) for name in ("Magery", "Meditation")}
casts = fizzles = trances = 0
result = "time limit"
deadline = time.time() + minutes * 60

while time.time() < deadline:
    check_interrupt()
    if player.mana >= cost:
        mark = time.time()
        client.cast(spell, target=player.serial)
        client.pump(2.0)                             # cast delay, then recovery
        casts += 1
        said = " ".join(heard_since(mark))
        fizzles += FIZZLED in said
        blocked = [why for code, why in CANNOT_CAST.items() if code in said]
        if blocked:
            result = blocked[0]
            break
        continue

    # Meditate back to full, trying again whenever the trance breaks.
    client.use_skill("Meditation")
    trances += 1
    retry_at = time.time() + 10
    while player.mana < player.mana_max and time.time() < deadline:
        check_interrupt()
        client.pump(0.5)
        if time.time() > retry_at:
            client.use_skill("Meditation")
            trances += 1
            retry_at = time.time() + 10

after = {name: skill(name) for name in before}
print(f"{casts} casts ({fizzles} fizzled), {trances} meditations, stopped: {result}")
for name in before:
    print(f"  {name:<11} {before[name]:5.1f} -> {after[name]:5.1f}")
print(f"  mana {player.mana}/{player.mana_max}")
