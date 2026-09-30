"""Jev: the HTTP client against a fake TypeSafe, and the autopilot's judgement.

The HTTP tests run a local server that checks what we send, so a wrong header
or body shape fails here rather than as a 422 from the real API.  The autopilot
tests build a world by hand and feed it canned answers: what matters is which
questions get asked, and what we do with each answer.

    python3 -m unittest tests.test_jev -v
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.autopilot import (                                 # noqa: E402
    BANDAGE_GRAPHIC,
    FIREBALL,
    GOLD_GRAPHIC,
    MAGIC_ARROW,
    Autopilot,
    Policy,
    distance_bucket,
    health_bucket,
)
from anacrom.client import Client, Config                       # noqa: E402
from anacrom.jev import Jev, JevError, Response, choice, noul, score   # noqa: E402
from anacrom.world.state import JournalEntry                    # noqa: E402

ME, MONGBAT, ORC, GUARD, PACK = 0x1000, 0x2000, 0x2001, 0x2002, 0x40001000


# --------------------------------------------------------------------------
# the HTTP client
# --------------------------------------------------------------------------

class FakeTypeSafe(BaseHTTPRequestHandler):
    """Answers /v1/systemone with whatever the test queued up."""
    replies: list = []
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "auth": self.headers["Authorization"],
                                "body": body})
        status, payload, headers = type(self).replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


ANSWER = {
    "model": "jev-1.13.0",
    "answers": {"is_urgent": {"type": "noul", "noul": 0.95}},
    "usage": {"input_tokens": 296, "output_tokens": 20},
}


class HttpClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeTypeSafe)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/v1/systemone"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeTypeSafe.replies = []
        FakeTypeSafe.seen = []

    def jev(self, **kwargs) -> Jev:
        return Jev("sk-test", url=self.url, **kwargs)

    def test_request_matches_the_documented_shape(self):
        FakeTypeSafe.replies = [(200, ANSWER, {})]
        response = self.jev().ask(
            "Help! My payouts have been failing for 3 days.",
            {"is_urgent": noul("Does this convey urgency?")},
        )
        sent = FakeTypeSafe.seen[0]
        self.assertEqual(sent["path"], "/v1/systemone")
        self.assertEqual(sent["auth"], "Bearer sk-test")
        self.assertEqual(sent["body"], {
            "state": "Help! My payouts have been failing for 3 days.",
            "model": "jev-latest",
            "questions": {"is_urgent": {"type": "noul",
                                        "instructions": "Does this convey urgency?"}},
        })
        self.assertEqual(response.model, "jev-1.13.0")
        self.assertEqual(response.answers["is_urgent"]["noul"], 0.95)
        self.assertEqual(response.input_tokens, 296)

    def test_rate_limit_is_retried(self):
        FakeTypeSafe.replies = [(429, {"error": "slow down"}, {"retry-after": "0"}),
                                (529, {"error": "overloaded"}, {"retry-after": "0"}),
                                (200, ANSWER, {})]
        response = self.jev().ask("x", {"is_urgent": noul("?")})
        self.assertEqual(len(FakeTypeSafe.seen), 3)
        self.assertEqual(response.answers["is_urgent"]["noul"], 0.95)

    def test_bad_key_is_not_retried(self):
        FakeTypeSafe.replies = [(401, {"error": "invalid key"}, {})]
        with self.assertRaises(JevError) as caught:
            self.jev().ask("x", {"q": noul("?")})
        self.assertEqual(caught.exception.status, 401)
        self.assertIn("invalid key", str(caught.exception))
        self.assertEqual(len(FakeTypeSafe.seen), 1)

    def test_retries_run_out(self):
        FakeTypeSafe.replies = [(529, {}, {"retry-after": "0"})] * 3
        with self.assertRaises(JevError) as caught:
            self.jev(retries=2).ask("x", {"q": noul("?")})
        self.assertEqual(caught.exception.status, 529)
        self.assertEqual(len(FakeTypeSafe.seen), 3)

    def test_unreachable_server_is_an_error_not_a_hang(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        jev = Jev("sk-test", url=f"http://127.0.0.1:{port}/v1/systemone",
                  timeout=1, retries=0)
        with self.assertRaises(JevError) as caught:
            jev.ask("x", {"q": noul("?")})
        self.assertEqual(caught.exception.status, 503)

    def test_no_key_says_how_to_set_one(self):
        with self.assertRaises(JevError) as caught:
            Jev("")
        self.assertIn("TYPESAFE_API_KEY", str(caught.exception))

    def test_question_shapes(self):
        self.assertEqual(noul("Urgent?", true="yes", false="no"), {
            "type": "noul", "instructions": "Urgent?",
            "criteria": {"true": "yes", "false": "no"}})
        self.assertEqual(choice("Team?", {"billing": "Payments", "sales": None}), {
            "type": "choice", "instructions": "Team?",
            "criteria": {"billing": "Payments", "sales": None}})
        self.assertEqual(score("How angry?", ["Calm", "Angry"]), {
            "type": "score", "instructions": "How angry?", "criteria": ["Calm", "Angry"]})


# --------------------------------------------------------------------------
# the autopilot
# --------------------------------------------------------------------------

class CannedJev:
    """Stands in for Jev: records every request, answers from a script."""

    def __init__(self, *answer_sets):
        self.answer_sets = list(answer_sets)
        self.requests = []

    def ask(self, state, questions):
        self.requests.append({"state": json.loads(json.dumps(state)),
                              "questions": questions})
        answers = self.answer_sets.pop(0) if self.answer_sets else {}
        return Response(model="jev-test", answers=answers,
                        usage={"input_tokens": 500}, seconds=0.01)


class OpenConnection:
    """Enough of a connection for the loop to think it is logged in."""
    closed = False

    def __init__(self):
        self.sent = []

    def poll(self):
        return []

    def send(self, packet):
        self.sent.append(packet)


def picked(option, confidence, **probabilities):
    return {"type": "choice", "choice": option, "confidence": confidence,
            "probabilities": probabilities or {option: confidence}}


def world_with_a_fight(hits=80):
    client = Client(Config())
    world = client.world
    player = world.player
    player.serial, player.name = ME, "Alaric"
    player.x, player.y = 100, 100
    player.hits, player.hits_max = hits, 100
    player.weight, player.weight_max = 100, 400
    player.backpack = PACK

    def mobile(serial, name, notoriety, x, y, hits=25, hits_max=25):
        mob = world.mobile(serial)
        mob.name, mob.notoriety, mob.x, mob.y = name, notoriety, x, y
        mob.hits, mob.hits_max = hits, hits_max
        return mob

    mobile(MONGBAT, "a mongbat", 5, 101, 100)
    mobile(ORC, "an orc", 5, 105, 100, hits=10, hits_max=40)
    mobile(GUARD, "a town guard", 1, 107, 103)

    # Something already known in the pack, so the autopilot need not open it.
    dagger = world.item(0x40001001)
    dagger.graphic, dagger.container, dagger.name = 0x0F52, PACK, "a dagger"
    return client


class Perception(unittest.TestCase):
    def test_numbers_become_words(self):
        self.assertEqual(health_bucket(None), "unknown")
        self.assertEqual(health_bucket(100), "unhurt")
        self.assertEqual(health_bucket(30), "near death")
        self.assertEqual(distance_bucket(1), "adjacent")
        self.assertEqual(distance_bucket(3), "close")
        self.assertEqual(distance_bucket(20), "far")

    def test_only_fair_game_is_offered_as_a_target(self):
        moment = Autopilot(world_with_a_fight(), CannedJev()).perceive()
        names = {moment.state["creatures"][int(t[1:]) - 1]["name"] for t in moment.targets}
        self.assertEqual(names, {"a mongbat", "an orc"})
        guard = next(c for c in moment.state["creatures"] if c["name"] == "a town guard")
        self.assertEqual(guard["rating"], "not a monster")
        self.assertEqual(moment.labels["m1"], "a mongbat, adjacent, unhurt")
        self.assertEqual(moment.labels["m2"], "an orc, nearby, near death")

    def test_state_carries_words_not_serials(self):
        moment = Autopilot(world_with_a_fight(), CannedJev()).perceive()
        text = json.dumps(moment.state)
        self.assertNotIn(str(MONGBAT), text)
        self.assertNotIn("100", text)
        self.assertEqual(moment.state["me"]["health"], "lightly wounded")

    def test_only_possible_actions_are_offered(self):
        client = world_with_a_fight()
        pilot = Autopilot(client, CannedJev())
        self.assertEqual(list(pilot.perceive().options), ["fight", "flee", "wait"])

        bandages = client.world.item(0x40002000)
        bandages.graphic, bandages.container = BANDAGE_GRAPHIC, PACK
        self.assertIn("heal", pilot.perceive().options)

        client.world.player.hits = 100
        self.assertNotIn("heal", pilot.perceive().options)

    def test_nothing_to_decide_asks_nothing(self):
        # Hurt, with nothing to heal with and nothing around: no roaming either.
        client = Client(Config())
        client.world.player.serial = ME
        client.world.player.hits, client.world.player.hits_max = 50, 100
        pilot = Autopilot(client, CannedJev())
        self.assertEqual(pilot.questions(pilot.perceive()), {})

    def test_ground_gold_is_loot_but_a_strangers_corpse_is_not(self):
        client = Client(Config())
        client.world.player.serial = ME
        corpse = client.world.item(0x40003000)
        corpse.graphic, corpse.x, corpse.y = 0x2006, 1, 1
        pilot = Autopilot(client, CannedJev())
        self.assertNotIn("loot", pilot.perceive().options)

        pilot.memory.kill_sites.append((1, 2))
        self.assertIs(pilot.perceive().corpse, corpse)

        gold = client.world.item(0x40004000)
        gold.graphic, gold.x, gold.y = GOLD_GRAPHIC, 3, 0
        moment = pilot.perceive()
        self.assertEqual(moment.state["loot_nearby"], {"corpse": "adjacent", "gold": "close"})

    def test_speech_from_others_is_heard_and_system_text_is_not(self):
        client = world_with_a_fight()
        pilot = Autopilot(client, CannedJev())
        journal = client.world.journal
        journal.append(JournalEntry("hail Alaric, got any regs?", speaker="Bob", serial=0x3000))
        journal.append(JournalEntry("cliloc 500969", speaker="System", serial=0xFFFFFFFF))
        journal.append(JournalEntry("I talk to myself", speaker="Alaric", serial=ME))
        moment = pilot.perceive()
        self.assertTrue(moment.heard)
        self.assertEqual(moment.state["recent_speech"], ["Bob: hail Alaric, got any regs?"])
        self.assertIn("addressed", pilot.questions(moment))
        self.assertFalse(pilot.perceive().heard)          # each line is news once

    def test_finished_bandage_reopens_healing(self):
        client = world_with_a_fight()
        bandages = client.world.item(0x40002000)
        bandages.graphic, bandages.container = BANDAGE_GRAPHIC, PACK
        pilot = Autopilot(client, CannedJev())
        pilot.memory.heal_ready_at = time.time() + 60
        self.assertNotIn("heal", pilot.perceive().options)
        client.world.journal.append(JournalEntry("cliloc 500969", serial=0xFFFFFFFF))
        self.assertIn("heal", pilot.perceive().options)

    def test_a_vanished_target_leaves_a_kill_site(self):
        client = world_with_a_fight()
        pilot = Autopilot(client, CannedJev())
        pilot.memory.target = MONGBAT
        pilot.perceive()
        client.world.forget(MONGBAT)
        pilot.perceive()
        self.assertEqual(list(pilot.memory.kill_sites), [(101, 100)])
        self.assertEqual(pilot.memory.target, 0)


class Judgement(unittest.TestCase):
    def setUp(self):
        self.pilot = Autopilot(world_with_a_fight(), CannedJev())
        self.moment = self.pilot.perceive()

    def test_confident_answer_is_acted_on(self):
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.9),
            "target": picked("m2", 0.8),
        })
        self.assertEqual((decision.action, decision.target), ("fight", ORC))

    def test_unsure_answer_waits(self):
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.45),
            "danger": {"type": "score", "score": 0.8, "confidence": 0.7},
        })
        self.assertEqual((decision.action, decision.source), ("wait", "fallback"))

    def test_carrying_on_a_fight_needs_less_certainty(self):
        self.pilot.memory.target = MONGBAT
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.45), "target": picked("m1", 0.9)})
        self.assertEqual((decision.action, decision.target), ("fight", MONGBAT))

    def test_unsure_answer_in_danger_runs(self):
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.45),
            "danger": {"type": "score", "score": 2.4, "confidence": 0.6},
        })
        self.assertEqual((decision.action, decision.source), ("flee", "fallback"))

    def test_unsure_target_takes_the_nearest(self):
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.9),
            "target": picked("m2", 0.1),
        })
        self.assertEqual(decision.target, MONGBAT)

    def test_being_spoken_to_hands_back(self):
        decision = self.pilot.decide(self.moment, {
            "action": picked("fight", 0.9),
            "addressed": {"type": "noul", "noul": 0.93},
        })
        self.assertEqual(decision.action, "hand_back")

    def test_near_death_runs_without_asking(self):
        pilot = Autopilot(world_with_a_fight(hits=20), CannedJev())
        decision = pilot.rule(pilot.perceive())
        self.assertEqual((decision.action, decision.source), ("flee", "rule"))

    def test_questions_ask_only_what_is_open(self):
        questions = self.pilot.questions(self.moment)
        self.assertEqual(set(questions), {"action", "target", "danger"})
        self.assertEqual(set(questions["action"]["criteria"]), {"fight", "flee", "wait"})
        self.assertEqual(set(questions["target"]["criteria"]), {"m1", "m2"})


def grey_world():
    """The fight world as UOAlive paints it: monsters and livestock all grey."""
    client = world_with_a_fight()
    world = client.world
    world.mobiles[MONGBAT].name, world.mobiles[MONGBAT].notoriety = "a skeleton", 3
    world.mobiles[ORC].name, world.mobiles[ORC].notoriety = "a wraith", 3
    goat = world.mobile(0x2003)
    goat.name, goat.notoriety, goat.x, goat.y = "a goat", 3, 103, 100
    return client


def pack(client, *contents):
    """Put a bag in the backpack holding (graphic, amount) pairs."""
    world = client.world
    bag = world.item(0x40005000)
    bag.graphic, bag.container = 0x0E76, PACK
    for n, (graphic, amount) in enumerate(contents):
        item = world.item(0x40005001 + n)
        item.graphic, item.amount, item.container = graphic, amount, bag.serial


class Hunting(unittest.TestCase):
    """Grey names are judged by Jev once, then hunted, avoided or left alone."""

    def test_names_are_judged_once_then_hunted_or_avoided(self):
        pilot = Autopilot(grey_world(), CannedJev())
        moment = pilot.perceive()
        self.assertEqual(moment.targets, {})              # nothing trusted yet
        self.assertEqual(moment.unjudged, ["a skeleton", "a goat", "a wraith"])
        questions = pilot.questions(moment)
        self.assertEqual({"prey_0", "threat_0", "prey_2", "threat_2"} - set(questions), set())

        pilot.decide(moment, {
            "prey_0": {"noul": 0.94}, "threat_0": {"score": 1.7},     # skeleton
            "prey_1": {"noul": 0.07}, "threat_1": {"score": 0.6},     # goat
            "prey_2": {"noul": 0.94}, "threat_2": {"score": 2.7},     # wraith
        })
        moment = pilot.perceive()
        self.assertEqual(moment.unjudged, [])
        self.assertEqual(list(moment.targets.values()), [MONGBAT])
        self.assertEqual(moment.avoid, [ORC])
        self.assertIn("back_off", moment.options)
        self.assertNotIn("prey_0", pilot.questions(moment))

    def test_what_beats_us_is_avoided_from_then_on(self):
        import tempfile
        client = grey_world()
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "creatures.json"
            pilot = Autopilot(client, CannedJev(), creatures_path=path)
            moment = pilot.perceive()
            pilot.decide(moment, {"prey_0": {"noul": 0.9}, "threat_0": {"score": 1.2},
                                  "prey_1": {"noul": 0.1}, "threat_1": {"score": 0.5},
                                  "prey_2": {"noul": 0.9}, "threat_2": {"score": 1.5}})
            self.assertIn(ORC, pilot.perceive().targets.values())      # the "wraith"

            client._swing_log.append((time.time(), ORC, ME))
            client.world.player.hits = 20
            decision = pilot.rule(pilot.perceive())
            self.assertEqual(decision.action, "flee")

            # A fresh autopilot remembers, and never asks about the wraith again.
            fresh = Autopilot(client, CannedJev(), creatures_path=path)
            client._swing_log.clear()
            moment = fresh.perceive()
            self.assertEqual(moment.unjudged, [])
            self.assertNotIn(ORC, moment.targets.values())
            self.assertIn(ORC, moment.avoid)

    def test_a_target_we_cannot_hurt_is_dropped_for_a_while(self):
        from anacrom.autopilot import Decision
        client = world_with_a_fight()
        pilot = Autopilot(client, CannedJev())
        moment = pilot.perceive()
        pilot.memory.target = MONGBAT
        pilot.memory.engaged_at = time.time() - 30         # half a minute of spells
        pilot.memory.engaged_health = 100                  # and it is still unhurt
        pilot._do_fight(Decision("fight", target=MONGBAT), moment)
        self.assertNotIn(MONGBAT, pilot.perceive().targets.values())
        self.assertIn(ORC, pilot.perceive().targets.values())

    def test_a_corpse_out_of_reach_is_tried_three_times_not_once(self):
        client = Client(Config())
        client.world.player.serial = ME
        corpse = client.world.item(0x40003000)
        corpse.graphic, corpse.x, corpse.y = 0x2006, 6, 0
        pilot = Autopilot(client, CannedJev())
        pilot.memory.kill_sites.append((6, 0))
        client.walk_to = lambda *a, **k: {"arrived": False, "stopped": "step limit"}
        from anacrom.autopilot import Decision
        for attempt in (1, 2, 3):
            moment = pilot.perceive()
            self.assertIs(moment.corpse, corpse, f"attempt {attempt}")
            pilot._do_loot(Decision("loot"), moment)
        self.assertIsNone(pilot.perceive().corpse)

    def test_something_that_hits_us_is_fair_game_whatever_its_rating(self):
        client = grey_world()
        pilot = Autopilot(client, CannedJev())
        client._swing_log.append((time.time(), ORC, ME))
        self.assertIn(ORC, pilot.perceive().targets.values())

    def test_people_are_never_targets(self):
        client = world_with_a_fight()
        client._swing_log.append((time.time(), GUARD, ME))
        self.assertNotIn(GUARD, Autopilot(client, CannedJev()).perceive().targets.values())

    def test_spells_come_before_fists(self):
        client = world_with_a_fight()
        pilot = Autopilot(client, CannedJev())
        client.world.player.mana, client.world.player.mana_max = 50, 60
        self.assertIsNone(pilot.attack_spell())
        pack(client, (0x0F7A, 5), (0x0F8C, 5))          # black pearl, sulfurous ash
        self.assertEqual(pilot.attack_spell(), FIREBALL)
        client.world.player.mana = 6
        self.assertEqual(pilot.attack_spell(), MAGIC_ARROW)
        self.assertEqual(pilot.perceive().state["me"]["attack"], "spells")

    def test_heal_spell_counts_as_healing(self):
        client = world_with_a_fight()
        client.world.player.mana, client.world.player.mana_max = 50, 60
        pack(client, (0x0F84, 5), (0x0F85, 5), (0x0F8D, 5))
        self.assertIn("heal", Autopilot(client, CannedJev()).perceive().options)

    def test_roaming_is_for_when_there_is_nothing_to_hunt(self):
        client = Client(Config())
        client.world.player.serial = ME
        client.world.player.hits, client.world.player.hits_max = 100, 100
        client.world.player.mana, client.world.player.mana_max = 60, 60
        pilot = Autopilot(client, CannedJev())
        self.assertIn("roam", pilot.perceive().options)
        self.assertNotIn("roam", Autopilot(world_with_a_fight(), CannedJev()).perceive().options)

    def test_low_mana_offers_rest_unless_under_attack(self):
        client = grey_world()
        client.world.player.mana, client.world.player.mana_max = 10, 60
        pilot = Autopilot(client, CannedJev())
        self.assertIn("rest", pilot.perceive().options)
        client._swing_log.append((time.time(), MONGBAT, ME))
        self.assertNotIn("rest", pilot.perceive().options)


class Stopping(unittest.TestCase):
    def test_stop_request_ends_the_run_with_a_summary(self):
        pilot = Autopilot(world_with_a_fight(), CannedJev(), should_stop=lambda: True)
        pilot.client.connection = OpenConnection()
        summary = pilot.run(seconds=5)
        self.assertEqual(summary["stopped"], "interrupted")
        self.assertEqual(summary["ticks"], 0)

    def test_hand_back_returns_what_was_said(self):
        client = world_with_a_fight()
        client.connection = OpenConnection()
        jev = CannedJev({"action": picked("wait", 0.9),
                         "addressed": {"type": "noul", "noul": 0.95}})
        pilot = Autopilot(client, jev, Policy(tick=0.05), dry_run=True)
        client.world.journal.append(
            JournalEntry("Alaric! wait up", speaker="Bob", serial=0x3000, at=time.time() + 1))
        summary = pilot.run(seconds=5)
        self.assertEqual(summary["stopped"], "someone is talking to us")
        self.assertEqual(summary["speech"], ["Bob: Alaric! wait up"])
        self.assertEqual(summary["jev_calls"], 1)


if __name__ == "__main__":
    unittest.main()
