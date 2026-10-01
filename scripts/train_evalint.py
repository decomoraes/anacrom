"""Train Evaluate Int on whoever is standing nearby.

Run it with:  uo script scripts/train_evalint.py [minutes]

Aims the skill at the nearest mobile in range and keeps at it, dropping any
target the server will not teach from.  Shop keepers and town criers answer
with a stock line and no skill check (Skills/EvalInt.cs); anything out of sight
or reach is no use either.  The skill's own delay is one second.

Don't meditate while this runs: using a skill breaks the trance.
"""
import time

RANGE = 8
USELESS = {
    "500907": "town crier",
    "500909": "shop keeper",
    "500910": "ourselves",
    "500237": "out of sight",
    "500446": "too far away",
    "500447": "not accessible",
}

minutes = float(args[0]) if args else 5.0


def skill():
    return world.skills.get("Evaluate Int", {}).get("value", 0.0)


before = skill()
skipped = {}
uses = 0
result = "time limit"
deadline = time.time() + minutes * 60

def red_name_near():
    return next((m for m in world.nearby_mobiles(14) if m.hostile and m.notoriety == 6), None)


def leave(mob):
    """Murderers are not worth the skill points: walk off and stop."""
    print(f"{mob.name or 'a murderer'} came within {world.distance_to(mob.serial)} tiles -- leaving")
    dx = (player.x > mob.x) - (player.x < mob.x) or 1
    dy = (player.y > mob.y) - (player.y < mob.y)
    client.walk_to(player.x + dx * 20, player.y + dy * 20, max_steps=30,
                   on_step=lambda _: not daemon.interrupted)


while time.time() < deadline:
    check_interrupt()
    danger = red_name_near()
    if danger is not None:
        leave(danger)
        result = "a murderer came near"
        break
    candidates = [m for m in world.nearby_mobiles(RANGE) if m.serial not in skipped]
    if not candidates:
        result = "nobody in range worth evaluating"
        break
    target = candidates[0]

    mark = time.time()
    client.use_skill("Evaluate Int")
    if not client.wait_for_target(2.0):
        client.pump(1.0)                         # still on the skill timer
        continue
    client.target(target.serial)
    client.pump(1.2)
    uses += 1

    said = " ".join(e.text for e in list(world.journal) if e.at > mark)
    for code, why in USELESS.items():
        if code in said:
            skipped[target.serial] = f"{target.name or 'unnamed'} ({why})"
            break

print(f"{uses} evaluations, stopped: {result}")
print(f"  Evaluate Int {before:5.1f} -> {skill():5.1f}")
for reason in skipped.values():
    print(f"  skipped {reason}")
