"""The movement port against a small made-up map.

The rules come from ModernUO's MovementImpl; these pin down the parts a route
depends on -- walls, the corner rule for diagonals, stepping onto stairs,
doors -- without needing the client's data files.

    python3 -m unittest tests.test_movement -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.world.mapdata import BRIDGE, DOOR, IMPASSABLE, SURFACE, MapData  # noqa: E402
from anacrom.world.movement import Terrain                                     # noqa: E402

GRASS = 0x0003
WALL, STAIR, FLOOR, DOOR_TILE = 0x0080, 0x0751, 0x0495, 0x0675


class FakeTiles:
    max_item = 0x3FFF
    land_flags = [0] * 0x4000
    items = {
        WALL: (IMPASSABLE, 20),
        STAIR: (SURFACE | BRIDGE, 10),
        FLOOR: (SURFACE, 0),
        DOOR_TILE: (DOOR | IMPASSABLE, 20),
    }

    def item(self, graphic):
        return self.items.get(graphic, (0, 0))


class FakeMap(MapData):
    """Flat grass at z 0, with whatever statics a test puts down."""

    def __init__(self, statics=None):                       # noqa: D401 - no files
        self.width = self.height = 64
        self.tiles = FakeTiles()
        self._placed = statics or {}

    def land(self, x, y):
        return GRASS, 0

    def statics(self, x, y):
        return self._placed.get((x, y), [])


class Door:
    def __init__(self, x, y):
        self.x, self.y, self.z = x, y, 0
        self.graphic, self.container, self.flags = DOOR_TILE, 0, 0


class Movement(unittest.TestCase):
    def test_open_ground(self):
        terrain = Terrain(FakeMap())
        for direction in ("north", "northeast", "east", "southwest"):
            self.assertEqual(terrain.step(10, 10, 0, direction), (True, 0))

    def test_a_wall_blocks_and_so_does_its_corner(self):
        terrain = Terrain(FakeMap({(11, 10): [(WALL, 0)]}))
        self.assertFalse(terrain.step(10, 10, 0, "east")[0])
        # A diagonal needs both tiles it cuts between.
        self.assertFalse(terrain.step(10, 10, 0, "northeast")[0])
        self.assertTrue(terrain.step(10, 10, 0, "north")[0])

    def test_stairs_lift_us(self):
        # A bridge counts half its height to stand on and none to step over.
        terrain = Terrain(FakeMap({(11, 10): [(STAIR, 0)]}))
        self.assertEqual(terrain.step(10, 10, 0, "east"), (True, 5))

    def test_no_headroom_under_a_low_floor(self):
        terrain = Terrain(FakeMap({(11, 10): [(FLOOR, 10)]}))
        # Standing on the grass under it leaves 10 of the 16 units we need.
        self.assertFalse(terrain.step(10, 10, 0, "east")[0])

    def test_doors_are_open_for_planning_only(self):
        terrain = Terrain(FakeMap())
        terrain.see_items([Door(11, 10)])
        self.assertTrue(terrain.step(10, 10, 0, "east")[0])
        self.assertFalse(terrain.step(10, 10, 0, "east", ignore_doors=False)[0])
        self.assertTrue(terrain.is_door(DOOR_TILE))

    def test_route_goes_round_a_wall(self):
        wall = {(20, y): [(WALL, 0)] for y in range(5, 16)}
        terrain = Terrain(FakeMap(wall))
        route = terrain.route((18, 10, 0), (22, 10))
        self.assertIsNotNone(route)
        self.assertGreater(len(route), 4)

        x, y = 18, 10
        deltas = {"north": (0, -1), "northeast": (1, -1), "east": (1, 0),
                  "southeast": (1, 1), "south": (0, 1), "southwest": (-1, 1),
                  "west": (-1, 0), "northwest": (-1, -1)}
        for direction in route:
            dx, dy = deltas[direction]
            x, y = x + dx, y + dy
            self.assertNotIn((x, y), wall)
        self.assertEqual((x, y), (22, 10))

    def test_refused_steps_are_planned_round(self):
        terrain = Terrain(FakeMap())
        route = terrain.route((10, 10, 0), (12, 10), refused={(10, 10, "east")})
        self.assertNotEqual(route[0], "east")

    def test_no_way_out_is_none(self):
        box = {(x, y): [(WALL, 0)] for x in range(8, 13) for y in range(8, 13)
               if x in (8, 12) or y in (8, 12)}
        terrain = Terrain(FakeMap(box))
        self.assertIsNone(terrain.route((10, 10, 0), (30, 30), limit=5000))


class AverageHeight(unittest.TestCase):
    def test_floor_average_rounds_like_the_server(self):
        heights = {(0, 0): -3, (0, 1): 0, (1, 0): 0, (1, 1): 0}

        class Slope(FakeMap):
            def land(self, x, y):
                return GRASS, heights.get((x, y), 0)

        # |top - bottom| = 3 beats |left - right| = 0, so left and right are
        # averaged: 0.
        self.assertEqual(Slope().average_z(0, 0), (-3, 0, 0))
        # A tie averages top and bottom, with the negative-sum rule:
        # FloorAverage(-3, 0) is -2, not -1 or -1.5 rounded down.
        heights[(0, 1)] = -3
        self.assertEqual(Slope().average_z(0, 0)[1], -2)


if __name__ == "__main__":
    unittest.main()
