"""End to end against the stub shard: login, walk, talk, look, loot.

This is the test that would have caught every bug the unit tests could not --
the ones that only appear once real packets are flowing in both directions.

    python3 -m unittest tests.test_integration -v
"""
from __future__ import annotations

import pathlib
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.client import Client, Config                 # noqa: E402
from tools.dev_server import (                            # noqa: E402
    BACKPACK_SERIAL,
    GOLD_SERIAL,
    GUARD_SERIAL,
    START,
    serve,
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        ready = threading.Event()
        threading.Thread(
            target=serve, args=(cls.port, True, ready), daemon=True
        ).start()
        if not ready.wait(5):
            raise RuntimeError("the stub shard did not start")

    def setUp(self):
        self.client = Client(Config(
            host="127.0.0.1", port=self.port,
            account="tester", password="secret",
        ))
        self.result = self.client.connect()
        self.addCleanup(self.client.disconnect)
        self.client.pump(0.5)

    def test_login_reaches_the_world(self):
        self.assertEqual(self.result.character_name, "Alaric")
        self.assertEqual(self.result.characters[0], "Alaric")
        self.assertEqual(self.client.world.player.position, START)
        self.assertTrue(self.client.world.login_complete)

    def test_status_arrives_decompressed(self):
        player = self.client.world.player
        self.assertEqual(player.name, "Alaric")
        self.assertEqual((player.hits, player.hits_max), (71, 100))
        self.assertEqual(player.gold, 1250)
        self.assertEqual(player.strength, 80)
        self.assertEqual(player.backpack, BACKPACK_SERIAL)

    def test_we_can_see_what_is_around_us(self):
        self.client.pump(0.6)
        snapshot = self.client.snapshot()
        serials = {m["serial"] for m in snapshot["mobiles"]}
        self.assertIn(GUARD_SERIAL, serials)

        guard = next(m for m in snapshot["mobiles"] if m["serial"] == GUARD_SERIAL)
        self.assertEqual(guard["name"], "a town guard")
        self.assertEqual(guard["distance"], 3)
        self.assertFalse(guard["hostile"])

        items = {i["serial"]: i for i in snapshot["ground_items"]}
        self.assertIn(GOLD_SERIAL, items)
        self.assertEqual(items[GOLD_SERIAL]["name"], "120 gold coins")
        self.assertEqual(items[GOLD_SERIAL]["amount"], 120)

    def test_stepping_moves_us(self):
        start = self.client.world.player.position
        first = self.client.step("south")
        self.assertEqual(first["outcome"], "turned")     # we were facing north
        second = self.client.step("south")
        self.assertEqual(second["outcome"], "moved")
        self.assertEqual(self.client.world.player.y, start[1] + 1)

    def test_walking_routes_around_a_wall(self):
        # The stub refuses one tile three north of the start.
        target = (START[0], START[1] - 6)
        outcome = self.client.walk_to(*target, max_steps=40)
        self.assertTrue(outcome["arrived"], outcome)
        self.assertEqual(
            (self.client.world.player.x, self.client.world.player.y), target
        )

    def test_speech_round_trips_through_the_journal(self):
        self.client.say("well met")
        self.assertTrue(
            self.client.wait_for(
                lambda: any("well met" in e.text for e in self.client.world.journal),
                3.0,
            )
        )
        entry = self.client.world.journal[-1]
        self.assertEqual(entry.speaker, "A Town Guard")

    def test_opening_a_container_lists_it(self):
        items = self.client.open_container(BACKPACK_SERIAL)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].amount, 3)

    def test_snapshot_is_json_serialisable(self):
        import json
        json.loads(json.dumps(self.client.snapshot()))


if __name__ == "__main__":
    unittest.main()
