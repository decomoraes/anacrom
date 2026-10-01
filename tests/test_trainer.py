"""Free-skill training: which skill, when to leave, and Jev's say on staying.

    python3 -m unittest tests.test_trainer -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.client import Client, Config                 # noqa: E402
from anacrom.jev import Response                          # noqa: E402
from anacrom.trainer import Trainer                       # noqa: E402

ME = 0x1000


class OpenConnection:
    closed = False

    def poll(self):
        return []

    def send(self, packet):
        pass


class Canned:
    def __init__(self, choice, confidence=0.9):
        self.answer = {"type": "choice", "choice": choice, "confidence": confidence,
                       "probabilities": {choice: confidence}}
        self.asked = []

    def ask(self, state, questions):
        self.asked.append(state)
        return Response("jev-test", {"action": self.answer}, {}, 0.01)


def world(skills=None, people=()):
    client = Client(Config())
    client.connection = OpenConnection()
    w = client.world
    w.player.serial, w.player.x, w.player.y, w.player.backpack = ME, 100, 100, 0x4000
    w.player.hits = w.player.hits_max = 100
    # Skills a test does not mention are capped, so only the ones it names compete.
    levels = {n: 100.0 for n in ("Evaluate Int", "Anatomy", "Detect Hidden", "Item ID",
                                 "Spirit Speak", "Hiding", "Meditation")}
    levels.update(skills or {})
    w.skills = {n: {"value": v, "cap": 100.0} for n, v in levels.items()}
    for n, (notoriety, x) in enumerate(people):
        m = w.mobile(0x2000 + n)
        m.name, m.notoriety, m.x, m.y = f"person {n}", notoriety, x, 100
    return client


class Training(unittest.TestCase):
    def test_lowest_free_skill_first(self):
        client = world({"Evaluate Int": 67.0, "Anatomy": 3.0, "Detect Hidden": 20.0},
                       people=[(1, 103)])
        self.assertEqual(Trainer(client, None).usable(), ["Anatomy", "Detect Hidden", "Evaluate Int"])

    def test_skills_that_need_people_wait_for_people(self):
        client = world({"Evaluate Int": 10.0, "Anatomy": 10.0, "Detect Hidden": 50.0})
        self.assertEqual(Trainer(client, None).usable(), ["Detect Hidden"])

    def test_a_capped_skill_is_left_alone(self):
        client = world({"Evaluate Int": 100.0, "Anatomy": 40.0}, people=[(1, 103)])
        self.assertNotIn("Evaluate Int", Trainer(client, None).usable())

    def test_a_murderer_near_means_leaving_without_asking(self):
        client = world({"Anatomy": 5.0}, people=[(1, 103), (6, 106)])
        client.walk_to = lambda *a, **k: {"arrived": True}
        jev = Canned("stay")
        result = Trainer(client, jev).run(30)
        self.assertEqual(result["stopped"], "a murderer came near")
        self.assertEqual(jev.asked, [])

    def test_an_unsure_stop_is_only_a_stay(self):
        client = world({"Anatomy": 5.0}, people=[(1, 103)])
        self.assertEqual(Trainer(client, Canned("stop", 0.41)).ask_jev(), "stay")
        self.assertEqual(Trainer(client, Canned("stop", 0.85)).ask_jev(), "stop")

    def test_jev_can_call_a_stop(self):
        client = world({"Anatomy": 5.0}, people=[(1, 103)])
        trainer = Trainer(client, Canned("stop"))
        client.use_skill = lambda name: None
        client.wait_for_target = lambda timeout=0: False
        client.pump = lambda duration=0: 0
        import anacrom.trainer as module
        module.CHECK_EVERY = 0.0
        try:
            result = trainer.run(5)
        finally:
            module.CHECK_EVERY = 30.0
        self.assertEqual(result["stopped"], "Jev said stop")
        self.assertEqual(result["jev_calls"], 1)

    def test_it_stops_when_the_goal_skill_is_reached(self):
        client = world({"Evaluate Int": 100.0, "Anatomy": 5.0}, people=[(1, 103)])
        result = Trainer(client, None).run(30, until="Evaluate Int")
        self.assertEqual(result["stopped"], "goals reached: Evaluate Int 100.0")

    def test_goals_come_before_the_lowest_skill(self):
        client = world({"Evaluate Int": 70.0, "Anatomy": 2.0}, people=[(1, 103)])
        trainer = Trainer(client, None)
        self.assertEqual(trainer.pick([]), "Anatomy")
        self.assertEqual(trainer.pick(["Evaluate Int"]), "Evaluate Int")

    def test_a_goal_that_cannot_be_used_yet_lets_others_run(self):
        client = world({"Evaluate Int": 70.0, "Detect Hidden": 5.0})
        self.assertEqual(Trainer(client, None).pick(["Evaluate Int"]), "Detect Hidden")

    def test_meditation_goal_burns_mana_with_spirit_speak_first(self):
        client = world({"Meditation": 94.0, "Spirit Speak": 5.0})
        client.world.player.mana, client.world.player.mana_max = 80, 100
        used = []
        trainer = Trainer(client, None)
        trainer.use = used.append
        self.assertEqual(trainer.pick(["Meditation"]), "Meditation")
        trainer.meditate()
        self.assertEqual(used, ["Spirit Speak"])

    def test_spirit_speak_is_not_used_from_zero(self):
        client = world({"Spirit Speak": 0.0})
        client.world.player.mana, client.world.player.mana_max = 90, 100
        self.assertNotIn("Spirit Speak", Trainer(client, None).usable())

    def test_a_skill_at_99_9_is_still_trained(self):
        client = world({"Evaluate Int": 99.5}, people=[(1, 103)])
        self.assertIn("Evaluate Int", Trainer(client, None).usable())

    def test_spirit_speak_needs_the_mana(self):
        client = world({"Spirit Speak": 5.0})
        client.world.player.mana, client.world.player.mana_max = 5, 100
        self.assertNotIn("Spirit Speak", Trainer(client, None).usable())

    def test_what_jev_is_told_has_kinds_not_names_or_serials(self):
        client = world({"Anatomy": 5.0}, people=[(1, 103), (4, 105)])
        state = Trainer(client, None).situation()
        self.assertEqual([p["kind"] for p in state["people_near"]], ["innocent", "criminal"])
        self.assertNotIn("person", str(state))


if __name__ == "__main__":
    unittest.main()
