"""The player: what it may do, what it will not, and how it recovers.

    python3 -m unittest tests.test_planner -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom import routines                              # noqa: E402
from anacrom.client import Client, Config                 # noqa: E402
from anacrom.jev import Response                          # noqa: E402
from anacrom.planner import Limits, Player                # noqa: E402

PACK = 0x4000


class Open:
    closed = False

    def poll(self):
        return []

    def send(self, packet):
        pass


class Canned:
    def __init__(self, *choices, confidence=0.9):
        self.choices, self.confidence, self.asked = list(choices), confidence, []

    def ask(self, state, questions):
        self.asked.append((state, questions))
        pick = self.choices.pop(0) if self.choices else "stop"
        return Response("jev-test", {"next": {"type": "choice", "choice": pick,
                                              "confidence": self.confidence,
                                              "probabilities": {pick: self.confidence}}}, {}, 0.01)


def client_with(skills=None, gold=500, hits=100, mana=100, reagents=True):
    client = Client(Config())
    client.connection = Open()
    w = client.world
    p = w.player
    p.serial, p.backpack, p.gold = 0x1000, PACK, gold
    p.hits = p.hits_max = hits if hits == 100 else 100
    p.hits = hits
    p.mana, p.mana_max = mana, 100
    w.skills = {n: {"value": v, "cap": 100.0} for n, v in (skills or
                {"Evaluate Int": 70.0, "Meditation": 90.0, "Magery": 45.0}).items()}
    if reagents:
        for n, (graphic, amount) in enumerate(((0x0F86, 40), (0x0F84, 40))):
            item = w.item(0x5000 + n)
            item.graphic, item.amount, item.container = graphic, amount, PACK
    return client


class Choices(unittest.TestCase):
    def test_hunting_is_never_offered_unless_someone_is_watching(self):
        client = client_with()
        self.assertNotIn("hunt", Player(client, Canned()).possible())
        self.assertIn("hunt", Player(client, Canned(), hunt_allowed=True).possible())

    def test_training_with_reagents_and_shopping_without(self):
        with_reagents = Player(client_with(), Canned()).possible()
        self.assertIn("train_magery", with_reagents)
        self.assertNotIn("restock", with_reagents)
        without = Player(client_with(reagents=False), Canned()).possible()
        self.assertIn("restock", without)
        self.assertNotIn("train_magery", without)

    def test_no_shopping_when_broke(self):
        self.assertNotIn("restock", Player(client_with(reagents=False, gold=40), Canned()).possible())

    def test_hurt_means_rest_is_offered_and_hunting_is_not(self):
        options = Player(client_with(hits=40), Canned(), hunt_allowed=True).possible()
        self.assertIn("rest", options)
        self.assertNotIn("hunt", options)

    def test_what_jev_sees_is_words_not_numbers(self):
        picture = Player(client_with(gold=500), Canned()).picture()
        self.assertEqual(picture["skills"], {"Evaluate Int": "mid", "Meditation": "close to 100",
                                             "Magery": "low"})
        self.assertEqual(picture["gold"], "some")
        self.assertFalse(picture["someone_watching"])


class Running(unittest.TestCase):
    def play(self, jev, **kw):
        player = Player(client_with(**kw.pop("world", {})), jev, **kw)
        player.do = lambda task: f"did {task}"
        return player, player.run()

    def test_it_does_what_jev_chooses_then_stops(self):
        player, summary = self.play(Canned("train_free", "train_magery", "stop"))
        self.assertEqual(summary["tasks"], {"train_free": 1, "train_magery": 1})
        self.assertEqual(summary["stopped"], "Jev chose to stop")

    def test_an_unsure_jev_gets_the_safe_option(self):
        player, summary = self.play(Canned("train_magery", "stop", confidence=0.2),
                                    world={"hits": 40})
        self.assertEqual(list(summary["tasks"]), ["rest"])

    def test_a_third_death_ends_the_run(self):
        client = client_with()
        player = Player(client, Canned(), limits=Limits(max_deaths=2))
        with mock.patch.object(routines, "resurrect", lambda c, wait=6.0: {"alive": True}):
            for expected in (None, None, "died 3 times"):
                client.world.player.dead = True
                self.assertEqual(player.keep_alive(), expected)

    def test_a_dropped_connection_is_reconnected(self):
        client = client_with()
        client.connection = None
        player = Player(client, Canned())
        with mock.patch.object(routines, "reconnect",
                               lambda c, tries=3: setattr(c, "connection", Open()) or True):
            self.assertIsNone(player.keep_alive())
        self.assertEqual(player.memory.reconnects, 1)

    def test_giving_up_on_reconnecting_stops_cleanly(self):
        client = client_with()
        client.connection = None
        with mock.patch.object(routines, "reconnect", lambda c, tries=3: False):
            self.assertEqual(Player(client, Canned()).keep_alive(), "could not reconnect")

    def test_time_limit(self):
        player, summary = self.play(Canned("train_free", "train_free"),
                                    limits=Limits(seconds=0.0))
        self.assertEqual(summary["stopped"], "time limit")


if __name__ == "__main__":
    unittest.main()
