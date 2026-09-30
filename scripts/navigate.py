"""Walk somewhere, learning the walls on the way.

Run it with:  uo script scripts/navigate.py <x> <y> [within] [max_steps]

There is no map data, so this plans as if every unknown tile were open, walks
the plan, and when the server refuses a step, remembers that move as blocked
and plans again.  Each refusal adds a wall the next plan goes round, so unlike
``walk_to`` it cannot oscillate between the same two tiles forever.

Blocked moves are remembered as (tile, direction), not as blocked tiles: a
diagonal can fail on a corner while the tile itself is open, and a step can
fail on a height difference that the other side does not have.
"""
import heapq

from anacrom.world.state import DIRECTIONS, DIRECTION_DELTAS, distance

goal = (int(args[0]), int(args[1]))
within = int(args[2]) if len(args) > 2 else 0
max_steps = int(args[3]) if len(args) > 3 else 400
MARGIN = 40

# Walls stay learned for the life of the client, so a second run starts wiser.
blocked = world.__dict__.setdefault("learned_walls", set())
doors_tried = set()

# Door art: the classic ranges from tiledata (metal, wooden, gates, and so on).
DOOR_GRAPHICS = [range(0x0675, 0x06F5), range(0x0824, 0x0834), range(0x0839, 0x0849),
                 range(0x084C, 0x085C), range(0x0866, 0x0876), range(0x1FED, 0x1FFD),
                 range(0x241F, 0x2425)]


def is_door(item):
    return any(item.graphic in r for r in DOOR_GRAPHICS)


def plan(start):
    """A* over an 8-connected grid; unknown is open, learned moves are not."""
    lo_x = min(start[0], goal[0]) - MARGIN
    hi_x = max(start[0], goal[0]) + MARGIN
    lo_y = min(start[1], goal[1]) - MARGIN
    hi_y = max(start[1], goal[1]) + MARGIN
    frontier = [(distance(start, goal), 0, start)]
    came = {start: None}
    cost = {start: 0}
    while frontier:
        _, g, here = heapq.heappop(frontier)
        if distance(here, goal) <= within:
            path = []
            while came[here] is not None:
                here, direction = came[here]
                path.append(direction)
            return path[::-1]
        for direction in DIRECTIONS:
            if (here, direction) in blocked:
                continue
            dx, dy = DIRECTION_DELTAS[direction]
            nxt = (here[0] + dx, here[1] + dy)
            if not (lo_x <= nxt[0] <= hi_x and lo_y <= nxt[1] <= hi_y):
                continue
            step_cost = g + (1.001 if dx and dy else 1)    # prefer straight on ties
            if step_cost < cost.get(nxt, 1e9):
                cost[nxt] = step_cost
                came[nxt] = (here, direction)
                heapq.heappush(frontier, (step_cost + distance(nxt, goal), step_cost, nxt))
    return None


steps = replans = 0
result = "step limit"
while steps < max_steps:
    check_interrupt()
    here = (player.x, player.y)
    if distance(here, goal) <= within:
        result = "arrived"
        break
    route = plan(here)
    replans += 1
    if route is None:
        result = "no way through what we have learned"
        break
    for direction in route:
        check_interrupt()
        outcome = client.step(direction)
        if outcome["outcome"] == "turned":
            outcome = client.step(direction)
        steps += 1
        if outcome["outcome"] != "moved":
            # Doors close behind people and block like walls.  Double-click
            # any door beside the tile we could not reach, once, and retry.
            dx, dy = DIRECTION_DELTAS[direction]
            ahead = (here[0] + dx, here[1] + dy)
            doors = [i for i in world.ground_items(3)
                     if is_door(i) and i.serial not in doors_tried
                     and distance((i.x, i.y), ahead) <= 1]
            if doors:
                for door in doors:
                    doors_tried.add(door.serial)
                    client.use(door.serial)
                client.pump(0.5)
                outcome = client.step(direction)
                steps += 1
        if outcome["outcome"] != "moved":
            blocked.add((here, direction))
            break
        here = (player.x, player.y)
        if steps >= max_steps:
            break

print(f"{result}: {steps} steps, {replans} plans, {len(blocked)} walls known,"
      f" at {player.x},{player.y},{player.z}")
