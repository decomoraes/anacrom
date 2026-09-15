"""Walk somewhere, looking up every few steps.

Run it with:  uo script scripts/scout.py <x> <y> [stop_within] [danger_radius]

Walking blind is how a character dies.  This stops the moment something hostile
comes into range and reports what it saw, so the agent can decide whether to
fight, go round, or turn back -- the same choice a player makes.
"""
target_x = int(args[0])
target_y = int(args[1])
stop_within = int(args[2]) if len(args) > 2 else 1
danger_radius = int(args[3]) if len(args) > 3 else 10

seen = {}
spotted = []


def watch(step_result):
    check_interrupt()
    for mob in world.hostiles(danger_radius):
        if mob.serial in seen:
            continue
        seen[mob.serial] = True
        name = mob.name or f"creature {mob.graphic:#06x}"
        gap = world.distance_to(mob.serial)
        spotted.append(f"{name} (0x{mob.serial:08X}) {gap} tiles {mob.notoriety_name}")
        print(f"! {name} at {gap} tiles -- stopping")
        return False
    return True


start = (player.x, player.y)
outcome = client.walk_to(target_x, target_y, stop_within=stop_within, on_step=watch)

print(f"from {start} to ({player.x}, {player.y}) in {outcome['steps']} steps")
if spotted:
    print("spotted:")
    for line in spotted:
        print(f"  {line}")

if outcome["arrived"]:
    result = "arrived"
elif spotted:
    result = "stopped: hostile in range"
else:
    result = f"stopped: {outcome.get('stopped', 'unknown')}"
print(result)
