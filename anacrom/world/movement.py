"""The server's rules for one step, and routes planned by them.

A port of ModernUO's MovementImpl (Engines/Pathing/Movement.cs) for a living
player on foot, which is the judge every step we send goes before.  A step is
allowed when the tile ahead has something to stand on -- land, or a static or
item flagged surface -- no higher than a small step up from where we stand,
with sixteen units of headroom free of anything impassable.  A diagonal also
needs both of the tiles it cuts between, so corners cannot be shaved.

Players ignore other mobiles here (the server shoves instead), and closed doors
are treated as open when planning: the walker opens them on the way.

What this cannot see is anything the files do not hold: houses and other
multis, and items we have not been told about.  The walker learns those the
hard way, from the steps the server refuses.
"""
from __future__ import annotations

import heapq
from typing import Iterable

from .mapdata import BRIDGE, DOOR, IMPASSABLE, SURFACE, MapData
from .state import DIRECTION_DELTAS, DIRECTIONS

PERSON_HEIGHT = 16
STEP_HEIGHT = 2
MOVABLE = 0x20                          # item flag in 0x1A/0xF3



def _ignored_land(tile: int) -> bool:
    return tile == 2 or tile == 0x1DB or 0x1AE <= tile <= 0x1B5


class Terrain:
    def __init__(self, mapdata: MapData) -> None:
        self.map = mapdata
        self.tiles = mapdata.tiles
        self._items: dict[tuple[int, int], list[tuple[int, int, bool]]] = {}

    def see_items(self, items: Iterable) -> None:
        """Take in what lies on the ground now: (graphic, z, movable) by tile."""
        by_tile: dict[tuple[int, int], list[tuple[int, int, bool]]] = {}
        for item in items:
            if item.container == 0:
                by_tile.setdefault((item.x, item.y), []).append(
                    (item.graphic, item.z, bool(item.flags & MOVABLE)))
        self._items = by_tile

    def is_door(self, graphic: int) -> bool:
        return graphic <= self.tiles.max_item and bool(self.tiles.item(graphic)[0] & DOOR)

    def _height(self, flags: int, height: int) -> int:
        return height // 2 if flags & BRIDGE else height

    def _dynamic(self, x: int, y: int, ignore_doors: bool) -> list[tuple[int, int, int, int, bool]]:
        """(flags, height, graphic, z, movable) of the items that matter here."""
        found = []
        for graphic, z, movable in self._items.get((x, y), ()):
            if graphic > self.tiles.max_item:
                continue                                # a multi; its parts are unknown
            flags, height = self.tiles.item(graphic)
            if not flags & (IMPASSABLE | SURFACE):
                continue
            if ignore_doors and (flags & DOOR or graphic in (0x692, 0x846, 0x873)
                                 or 0x6F5 <= graphic <= 0x6F6):
                continue
            found.append((flags, height, graphic, z, movable))
        return found

    def _is_ok(self, our_z: int, our_top: int, statics, items) -> bool:
        for graphic, z in statics:
            flags, height = self.tiles.item(graphic)
            if flags & (IMPASSABLE | SURFACE):
                top = z + self._height(flags, height)
                if top > our_z and our_top > z:
                    return False
        for flags, height, _graphic, z, _movable in items:
            top = z + self._height(flags, height)
            if top > our_z and our_top > z:
                return False
        return True

    def _start_z(self, x: int, y: int, z: int, items) -> tuple[int, int]:
        land, _ = self.map.land(x, y)
        land_blocks = bool(self.tiles.land_flags[land & 0x3FFF] & IMPASSABLE)
        land_z, land_center, land_top = self.map.average_z(x, y)
        low = centre = top = 0
        is_set = False
        if not _ignored_land(land) and not land_blocks and z >= land_center:
            low, centre, top, is_set = land_z, land_center, land_top, True

        surfaces = [(*self.tiles.item(g), sz) for g, sz in self.map.statics(x, y)]
        surfaces += [(f, h, iz) for f, h, _g, iz, _m in items]
        for flags, height, sz in surfaces:
            calc_top = sz + self._height(flags, height)
            if (is_set and calc_top < centre) or z < calc_top or not flags & SURFACE:
                continue
            low, centre = sz, calc_top
            if not is_set or sz + height > top:
                top = sz + height
            is_set = True

        if not is_set:
            return z, z
        return low, max(top, z)

    def _check(self, x: int, y: int, start_top: int, start_z: int, current_z: int,
               items) -> tuple[bool, int]:
        land, _ = self.map.land(x, y)
        land_blocks = bool(self.tiles.land_flags[land & 0x3FFF] & IMPASSABLE)
        consider_land = not _ignored_land(land)
        land_z, land_center, _ = self.map.average_z(x, y)
        statics = self.map.statics(x, y)

        ok, new_z = False, 0
        step_top = start_top + STEP_HEIGHT
        check_top = start_z + PERSON_HEIGHT

        candidates = [(*self.tiles.item(g), sz) for g, sz in statics]
        candidates += [(f, h, iz) for f, h, _g, iz, movable in items if not movable]
        for flags, height, sz in candidates:
            if not flags & SURFACE or flags & IMPASSABLE:
                continue
            our_z = sz + self._height(flags, height)
            if ok:
                cmp = abs(our_z - current_z) - abs(new_z - current_z)
                if cmp > 0 or (cmp == 0 and our_z > new_z):
                    continue
            test_top = max(check_top, our_z + PERSON_HEIGHT)
            item_top = sz if flags & BRIDGE else sz + height
            if step_top < item_top:
                continue
            land_check = sz + min(height, STEP_HEIGHT)
            if consider_land and land_check < land_center and land_center > our_z \
                    and test_top > land_z:
                continue
            if self._is_ok(our_z, test_top, statics, items):
                ok, new_z = True, our_z

        if not consider_land or land_blocks or step_top < land_z:
            return ok, new_z

        test_top = max(check_top, land_center + PERSON_HEIGHT)
        should_check = True
        if ok:
            cmp = abs(land_center - current_z) - abs(new_z - current_z)
            if cmp > 0 or (cmp == 0 and land_center > new_z):
                should_check = False
        if should_check and self._is_ok(land_center, test_top, statics, items):
            ok, new_z = True, land_center
        return ok, new_z

    def step(self, x: int, y: int, z: int, direction: str,
             ignore_doors: bool = True) -> tuple[bool, int]:
        """Would the server let us take this step, and at what height would we land?"""
        index = DIRECTIONS.index(direction)
        dx, dy = DIRECTION_DELTAS[direction]
        fx, fy = x + dx, y + dy
        if not (0 <= fx < self.map.width and 0 <= fy < self.map.height):
            return False, z

        start_z, start_top = self._start_z(x, y, z, self._dynamic(x, y, ignore_doors))
        ok, new_z = self._check(fx, fy, start_top, start_z, z,
                                self._dynamic(fx, fy, ignore_doors))
        if ok and index & 1:
            for side in (DIRECTIONS[(index - 1) & 7], DIRECTIONS[(index + 1) & 7]):
                sx, sy = DIRECTION_DELTAS[side]
                side_ok, _ = self._check(x + sx, y + sy, start_top, start_z, z,
                                         self._dynamic(x + sx, y + sy, ignore_doors))
                if not side_ok:
                    return False, start_z
        return (ok, new_z) if ok else (False, start_z)

    def route(self, start: tuple[int, int, int], goal: tuple[int, int], within: int = 0,
              refused: set | None = None, limit: int = 60000) -> list[str] | None:
        """A* over (x, y, z); the directions to walk, or None if there is no way."""
        refused = refused or set()

        def distance(x: int, y: int) -> int:
            return max(abs(x - goal[0]), abs(y - goal[1]))

        frontier = [(distance(start[0], start[1]), 0, start)]
        came: dict[tuple[int, int, int], tuple | None] = {start: None}
        cost = {start: 0}
        expanded = 0
        while frontier and expanded < limit:
            _, g, here = heapq.heappop(frontier)
            if g > cost.get(here, g):
                continue
            if distance(here[0], here[1]) <= within:
                path = []
                while came[here] is not None:
                    here, direction = came[here]
                    path.append(direction)
                return path[::-1]
            expanded += 1
            for direction in DIRECTIONS:
                if (here[0], here[1], direction) in refused:
                    continue
                ok, new_z = self.step(*here, direction)
                if not ok:
                    continue
                dx, dy = DIRECTION_DELTAS[direction]
                nxt = (here[0] + dx, here[1] + dy, new_z)
                step_cost = g + (1.001 if dx and dy else 1)     # straight on ties
                if step_cost < cost.get(nxt, 1e18):
                    cost[nxt] = step_cost
                    came[nxt] = (here, direction)
                    heapq.heappush(frontier, (step_cost + distance(nxt[0], nxt[1]),
                                              step_cost, nxt))
        return None
