"""A player: Jev picks what to do next, code keeps it alive and within limits.

Run it with:  uo play [--minutes 60] [--hunt]

Three layers, each doing only what suits it:

  Rules (code)    stay alive, reconnect, resurrect, never exceed the limits,
                  never hunt unless told a person is watching.
  Planner (Jev)   from a short word-picture of where things stand, pick the
                  next task out of the ones that are possible right now.
  Tasks (code)    the routines and autopilots already built: free training,
                  Magery training, shopping, hunting.

Hunting is off unless ``--hunt`` is given.  UOAlive counts killing for loot
while away as resource gathering; training skills is allowed unattended.  The
flag is the person saying they are at the keyboard, and it is not remembered
between runs.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from . import routines
from .autopilot import Autopilot, Policy
from .client import Client, NotConnected
from .jev import Jev, JevError, choice
from .trainer import FREE_SKILLS, Trainer

GOALS = {"Evaluate Int": 100.0, "Meditation": 100.0, "Magery": 100.0}
MIN_CONFIDENCE = 0.35
REAGENT_WANTS = {"mandrake root": 40, "garlic": 40}
BLESS_PRICE = 6                         # gold per cast, two reagents at about 3


@dataclass
class Limits:
    seconds: float = 3600.0
    max_deaths: int = 2
    keep_gold: int = 30                 # never spend below this
    task_seconds: float = 300.0         # one task at a time, this long at most


@dataclass
class Memory:
    deaths: int = 0
    reconnects: int = 0
    history: list = field(default_factory=list)       # (time, task, outcome)
    last_hunt_gold: int | None = None
    hunts_paid_nothing: int = 0


TASKS = {
    "train_free": {"do": "Train Evaluate Int, Anatomy and other free skills on people nearby.",
                   "when": "A free skill is below 100, we are healthy, and it costs nothing."},
    "train_magery": {"do": "Cast Bless on ourselves and meditate to raise Magery and Meditation.",
                     "when": "We carry the reagents for it and have the mana."},
    "restock": {"do": "Walk to the mage shop and buy reagents for Magery training.",
                "when": "We have gold to spare but few or no reagents."},
    "hunt": {"do": "Hunt monsters for gold, with Jev fighting, healing, looting and backing off.",
             "when": "We are healthy with mana, a person is watching, and we need gold."},
    "rest": {"do": "Stand still and let health and mana come back.",
             "when": "We are hurt or low on mana and nothing is attacking us."},
    "stop": {"do": "Stop playing and hand control back.",
             "when": "Every goal is met, or nothing useful is possible."},
}


class Player:
    def __init__(self, client: Client, jev: Jev, hunt_allowed: bool = False,
                 limits: Limits | None = None,
                 should_stop: Callable[[], bool] = lambda: False) -> None:
        self.client, self.world, self.jev = client, client.world, jev
        self.hunt_allowed = hunt_allowed
        self.limits = limits or Limits()
        self.should_stop = should_stop
        self.memory = Memory()
        self.lines: list[str] = []
        self.tasks: Counter = Counter()
        self.calls = 0
        self._started = time.monotonic()

    # -- what we know ------------------------------------------------------

    def skill(self, name: str) -> float:
        return self.world.skills.get(name, {}).get("value", 0.0)

    def reagents_for_bless(self) -> int:
        return min(routines.carried(self.client, routines.REAGENTS[n])
                   for n in routines.BLESS[2])

    def possible(self) -> dict:
        player, limits = self.world.player, self.limits
        hp = player.hits * 100 // player.hits_max if player.hits_max else 100
        mana = player.mana * 100 // player.mana_max if player.mana_max else 100
        options = {}
        if any(self.skill(n) < 99.5 for n in FREE_SKILLS):
            options["train_free"] = TASKS["train_free"]
        if self.reagents_for_bless() >= 5 and (self.skill("Magery") < 99.5
                                               or self.skill("Meditation") < 99.5):
            options["train_magery"] = TASKS["train_magery"]
        spare = player.gold - limits.keep_gold
        if spare >= 30 and self.reagents_for_bless() < 10:
            options["restock"] = TASKS["restock"]
        if self.hunt_allowed and hp >= 70 and mana >= 50:
            options["hunt"] = TASKS["hunt"]
        if hp < 80 or mana < 40:
            options["rest"] = TASKS["rest"]
        options["stop"] = TASKS["stop"]
        return options

    def picture(self) -> dict:
        player = self.world.player
        hp = player.hits * 100 // player.hits_max if player.hits_max else 100
        mana = player.mana * 100 // player.mana_max if player.mana_max else 100
        recent = [f"{task}: {outcome}" for _, task, outcome in self.memory.history[-4:]]
        return {
            "skills": {n: ("capped" if self.skill(n) >= 99.5 else
                           "close to 100" if self.skill(n) >= 90 else "mid" if self.skill(n) >= 50
                           else "low") for n in GOALS},
            "gold": "none" if player.gold < 30 else "little" if player.gold < 300 else "some",
            "health": "hurt" if hp < 70 else "fine",
            "mana": "low" if mana < 40 else "fine",
            "reagents": "none" if self.reagents_for_bless() < 5 else "enough to train",
            "someone_watching": self.hunt_allowed,
            "deaths_so_far": self.memory.deaths,
            "hunts_that_earned_nothing_in_a_row": self.memory.hunts_paid_nothing,
            "recent_tasks": recent,
        }

    # -- Jev's call ----------------------------------------------------------

    def choose(self, options: dict) -> tuple[str, float]:
        if len(options) == 1:
            return next(iter(options)), 1.0
        response = self.jev.ask(self.picture(), {"next": choice(
            "We are a player in Ultima Online working towards raising skills to 100. "
            "What is the best thing to do next?", options)})
        self.calls += 1
        answer = response.answers["next"]
        return answer["choice"], answer["confidence"]

    # -- the loop ----------------------------------------------------------

    def say(self, line: str) -> None:
        self.lines.append(f"{time.monotonic() - self._started:6.0f}s  {line}")

    def keep_alive(self) -> str | None:
        """Rules before any planning.  A reason to stop, or None."""
        memory, limits = self.memory, self.limits
        if not self.client.connected:
            self.say("disconnected: reconnecting")
            if not routines.reconnect(self.client):
                return "could not reconnect"
            memory.reconnects += 1
            routines.open_pack(self.client)
        if self.world.player.dead:
            memory.deaths += 1
            self.say(f"died (death {memory.deaths}): resurrecting")
            if memory.deaths > limits.max_deaths:
                return f"died {memory.deaths} times"
            if not routines.resurrect(self.client)["alive"]:
                return "could not resurrect"
        return None

    def do(self, task: str) -> str:
        client, limits, stop = self.client, self.limits, self.should_stop
        seconds = min(limits.task_seconds, limits.seconds - (time.monotonic() - self._started))
        if task == "train_free":
            wanted = next((n for n in GOALS if n in FREE_SKILLS and self.skill(n) < 99.5), "")
            result = Trainer(client, self.jev, should_stop=stop).run(seconds, until=wanted)
            gains = ", ".join(f"{n} {a}->{b}" for n, (a, b) in result["gains"].items())
            return f"{result['stopped']}; {gains or 'no gains'}"
        if task == "train_magery":
            result = routines.train_magery(client, seconds, should_stop=stop)
            gains = ", ".join(f"{n} {a}->{b}" for n, (a, b) in result["gains"].items())
            return f"{result['stopped']}; {result['casts']} casts; {gains}"
        if task == "restock":
            spare = self.world.player.gold - limits.keep_gold
            wants = {n: min(c, spare // (2 * 3)) for n, c in REAGENT_WANTS.items()}
            result = routines.buy_reagents(client, wants, spare, should_stop=stop)
            return result.get("note") or f"bought {result['bought']}, gold {result['gold']}"
        if task == "hunt":
            gold = self.world.player.gold
            pilot = Autopilot(client, self.jev, Policy(radius=18, flee_percent=40),
                              should_stop=stop)
            summary = pilot.run(seconds=seconds)
            earned = self.world.player.gold - gold
            self.memory.hunts_paid_nothing = 0 if earned > 0 else self.memory.hunts_paid_nothing + 1
            return f"{summary['stopped']}; gold {earned:+d}; {summary['kills']} kills"
        if task == "rest":
            client.use_skill("Meditation")
            client.pump(min(60.0, seconds))
            return "rested"
        return "nothing"

    def run(self) -> dict:
        limits, memory = self.limits, self.memory
        stopped = "time limit"
        try:
            while time.monotonic() - self._started < limits.seconds:
                if self.should_stop():
                    stopped = "interrupted"
                    break
                reason = self.keep_alive()
                if reason:
                    stopped = reason
                    break
                options = self.possible()
                try:
                    task, confidence = self.choose(options)
                except JevError as exc:
                    self.say(f"Jev failed ({exc}): resting instead")
                    task, confidence = "rest", 1.0 if "rest" in options else 0.0
                    if task not in options:
                        stopped = f"Jev failed: {exc}"
                        break
                if confidence < MIN_CONFIDENCE and task != "stop":
                    # Unsure: take the cheapest and safest thing on offer.
                    task = "rest" if "rest" in options else "train_free" if "train_free" in options else "stop"
                    self.say(f"unsure ({confidence:.2f}); falling back to {task}")
                if task == "stop":
                    stopped = "Jev chose to stop" if len(options) > 1 else "nothing useful left"
                    break
                self.say(f"{task} (confidence {confidence:.2f})")
                outcome = self.do(task)
                self.tasks[task] += 1
                memory.history.append((time.time(), task, outcome))
                self.say(f"  -> {outcome}")
        except NotConnected:
            stopped = "disconnected"
        player = self.world.player
        return {
            "stopped": stopped,
            "seconds": round(time.monotonic() - self._started, 1),
            "tasks": dict(self.tasks), "deaths": memory.deaths, "reconnects": memory.reconnects,
            "jev_calls": self.calls, "gold": player.gold,
            "skills": {n: round(self.skill(n), 1) for n in GOALS},
            "output": "\n".join(self.lines),
        }
