"""Stay near a mobile until told otherwise.

Run it with:  uo script scripts/follow.py <serial> [distance] [seconds]
"""
import time

serial = int(args[0], 16) if args[0].lower().startswith("0x") else int(args[0])
keep_within = int(args[1]) if len(args) > 1 else 2
seconds = float(args[2]) if len(args) > 2 else 120.0

deadline = time.time() + seconds
lost_since = None

while time.time() < deadline:
    check_interrupt()
    client.pump(0.2)

    mob = world.mobiles.get(serial)
    if mob is None:
        lost_since = lost_since or time.time()
        if time.time() - lost_since > 10:
            print(f"lost sight of 0x{serial:08X}")
            result = "lost"
            break
        continue

    lost_since = None
    gap = world.distance_to(serial) or 0
    if gap > keep_within:
        client.walk_to(mob.x, mob.y, stop_within=keep_within, max_steps=12,
                       on_step=lambda _: not daemon.interrupted)
else:
    result = "followed to time limit"

print(f"ended at ({player.x}, {player.y})")
