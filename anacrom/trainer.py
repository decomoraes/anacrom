"""Free skill training, with Jev deciding whether the place is safe.

Run it with:  uo train [--seconds 600]

Some skills cost nothing to raise: Evaluate Int and Anatomy on whoever is
standing nearby, Detect Hidden on the air, Item ID on something in the pack.
Which one to use is arithmetic -- the lowest -- so code does it.  What is not
arithmetic is whether to stay: whether the people around look like trouble,
whether the spot has gone quiet.  Every half minute Jev is asked that, from a
short description of who is near, and answers with stay, move or stop.

A red name inside 14 tiles is never put to Jev: we leave at once.  Training
skills gains no items and no gold, which is what the shard allows unattended;
killing for loot is not, and this never does it.
"""
from __future__ import annotations

import random
import time
from collections import Counter
from typing import Callable

from .client import Client, NotConnected
from .jev import Jev, JevError, choice
from .world.state import distance

# skill -> what it must be pointed at
FREE_SKILLS = {
    "Evaluate Int": "person",
    "Anatomy": "person",
    "Detect Hidden": "ground",
    "Item ID": "pack item",
    "Spirit Speak": "self",             # 10 mana, no reagents: burns mana for Meditation
    "Hiding": "self",
}
SPIRIT_SPEAK_MANA = 10
SKILL_CAP = 100.0
STOP_CONFIDENCE = 0.7
CHECK_EVERY = 30.0
DANGER_RANGE = 14
USELESS = {"500907", "500909", "500910", "500237", "500446", "500447"}     # see train_evalint

ACTIONS = {
    "stay": {"do": "Keep training here.",
             "when": "The people near look harmless, or there are none worth worrying about."},
    "move": {"do": "Walk to another spot nearby and train there.",
             "when": "Few people are near to train on, or someone near looks like trouble but "
                     "is not yet dangerous."},
    "stop": {"do": "Stop training and stand down.",
             "when": "Someone near looks set on attacking us, or we have been refused many times."},
}
KINDS = {1: "innocent", 2: "friend", 3: "grey", 4: "criminal", 5: "enemy", 6: "murderer", 7: "invulnerable"}


def bucket(tiles: int) -> str:
    return "close" if tiles <= 4 else "nearby" if tiles <= 9 else "far"


class Trainer:
    def __init__(self, client: Client, jev: Jev | None,
                 should_stop: Callable[[], bool] = lambda: False) -> None:
        self.client, self.world, self.jev = client, client.world, jev
        self.should_stop = should_stop
        self.uses: Counter = Counter()
        self.skipped: dict[int, str] = {}
        self.home: tuple[int, int] | None = None
        self.calls = 0
        self.lines: list[str] = []

    # -- what to train ---------------------------------------------------

    def value(self, name: str) -> float:
        return self.world.skills.get(name, {}).get("value", 0.0)

    def people(self, radius: int = 10) -> list:
        return [m for m in self.world.nearby_mobiles(radius) if m.serial not in self.skipped]

    def usable(self) -> list[str]:
        """Free skills we can point at something right now, lowest first."""
        player = self.world.player
        options = []
        for name, needs in FREE_SKILLS.items():
            if self.value(name) >= SKILL_CAP - 0.05:
                continue
            if needs == "person" and not self.people(8):
                continue
            if name == "Spirit Speak" and (player.mana < SPIRIT_SPEAK_MANA
                                           or self.value(name) < 1.0):
                continue                         # from zero it can never succeed, so spends nothing
            if needs == "pack item" and not self._pack_item():
                continue
            options.append(name)
        return sorted(options, key=self.value)

    def _pack_item(self):
        pack = self.world.contents_of(self.world.player.backpack)
        return next((i for i in pack if i.graphic not in (0x0E76, 0x0E75)), None)

    # -- safety ----------------------------------------------------------

    def red_name(self):
        return next((m for m in self.world.nearby_mobiles(DANGER_RANGE)
                     if m.notoriety == 6 or (m.hostile and m.notoriety == 5)), None)

    def situation(self) -> dict:
        crowd = [{"kind": KINDS.get(m.notoriety, "unknown"), "distance": bucket(distance(
            (self.world.player.x, self.world.player.y), (m.x, m.y)))}
            for m in self.world.nearby_mobiles(14)[:10]]
        player = self.world.player
        return {"us": {"health": "hurt" if player.hits_max and player.hits * 100 < player.hits_max * 70 else "fine"},
                "people_near": crowd, "refused_so_far": len(self.skipped)}

    def ask_jev(self) -> str:
        if self.jev is None:
            return "stay"
        try:
            response = self.jev.ask(self.situation(), {"action": choice(
                "We are training skills in a town, hoping not to be attacked. What should we do?",
                ACTIONS)})
        except JevError as exc:
            self.say(f"Jev failed ({exc}); staying")
            return "stay"
        self.calls += 1
        answer = response.answers["action"]
        verdict, confidence = answer["choice"], answer["confidence"]
        # Stopping ends the whole session, so it needs far more certainty than
        # moving does; an unsure "stop" is read as "stay".
        needed = STOP_CONFIDENCE if verdict == "stop" else 0.3
        if confidence < needed:
            if verdict != "stay":
                self.say(f"Jev says {verdict} but is unsure ({confidence:.2f}); staying")
            return "stay"
        self.say(f"Jev says {verdict} (confidence {confidence:.2f}) "
                 f"with {len(self.world.nearby_mobiles(14))} people near")
        return verdict

    # -- acting ----------------------------------------------------------

    def say(self, line: str) -> None:
        self.lines.append(f"{time.strftime('%H:%M:%S')} {line}")

    def use(self, name: str) -> None:
        client, player = self.client, self.world.player
        needs = FREE_SKILLS[name]
        mark = time.time()
        client.use_skill(name)
        if needs != "self" and not client.wait_for_target(2.0):
            client.pump(1.0)
            return
        if needs == "self":
            client.pump(1.3)                     # no target to answer
            self.uses[name] += 1
            return
        if needs == "person":
            target = self.people(8)[0]
            client.target(target.serial)
        elif needs == "pack item":
            client.target(self._pack_item().serial)
        else:
            client.target_ground(player.x, player.y, player.z)
        client.pump(1.3)
        self.uses[name] += 1
        said = " ".join(e.text for e in list(self.world.journal) if e.at > mark)
        if needs == "person" and any(code in said for code in USELESS):
            self.skipped[target.serial] = target.name or "?"

    def relocate(self) -> None:
        player = self.world.player
        home = self.home or (player.x, player.y)
        spot = (home[0] + random.randint(-12, 12), home[1] + random.randint(-12, 12))
        self.client.walk_to(*spot, stop_within=2, max_steps=60,
                            on_step=lambda _: not self.should_stop())

    def leave(self, mob) -> None:
        player = self.world.player
        self.say(f"{mob.name or 'a murderer'} within {self.world.distance_to(mob.serial)} tiles: leaving")
        dx = (player.x > mob.x) - (player.x < mob.x) or 1
        dy = (player.y > mob.y) - (player.y < mob.y)
        self.client.walk_to(player.x + dx * 25, player.y + dy * 25, max_steps=40,
                            on_step=lambda _: not self.should_stop())

    # -- the loop --------------------------------------------------------

    def pick(self, goals: list[str]) -> str | None:
        """The goals first, in order; whatever else is usable only when they are not."""
        options = self.usable()
        for goal in goals:
            if self.value(goal) >= SKILL_CAP - 0.05:
                continue
            if goal == "Meditation":
                return "Meditation"
            if goal in options:
                return goal
        return options[0] if options else None

    def meditate(self) -> None:
        """Mana up needs Spirit Speak to spend it, then Meditation to bring it back."""
        player, client = self.world.player, self.client
        if (self.value("Spirit Speak") >= 1.0 and player.mana >= player.mana_max * 0.3
                and player.mana >= SPIRIT_SPEAK_MANA):
            self.use("Spirit Speak")
            return
        client.use_skill("Meditation")
        self.uses["Meditation"] += 1
        deadline = time.time() + 45
        while (player.mana < player.mana_max * 0.95 and time.time() < deadline
               and not self.should_stop() and self.red_name() is None):
            client.pump(1.0)

    def run(self, seconds: float = 600.0, until: str = "", goal: float = SKILL_CAP) -> dict:
        player = self.world.player
        self.home = (player.x, player.y)
        self.home_map = player.map
        goals = [g.strip() for g in until.split(",") if g.strip()]
        before = {name: self.value(name) for name in [*FREE_SKILLS, "Meditation"]}
        deadline = time.time() + seconds
        next_check = time.time() + CHECK_EVERY
        stopped = "time limit"
        try:
            while time.time() < deadline:
                if self.should_stop():
                    stopped = "interrupted"
                    break
                if not self.client.connected:
                    stopped = "disconnected"
                    break
                if player.dead:
                    stopped = "we died"
                    break
                if goals and all(self.value(g) >= goal - 0.05 for g in goals):
                    stopped = "goals reached: " + ", ".join(f"{g} {self.value(g):.1f}" for g in goals)
                    break
                if (player.map, ) != (self.home_map, ):
                    stopped = "moved to another map"      # a portal we did not mean to take
                    break
                danger = self.red_name()
                if danger is not None:
                    self.leave(danger)
                    stopped = "a murderer came near"
                    break
                if time.time() >= next_check:
                    next_check = time.time() + CHECK_EVERY
                    verdict = self.ask_jev()
                    if verdict == "stop":
                        stopped = "Jev said stop"
                        break
                    if verdict == "move":
                        self.relocate()
                        continue
                chosen = self.pick(goals)
                if chosen is None:
                    self.relocate()                  # nobody to train on here
                    continue
                if chosen == "Meditation":
                    self.meditate()
                else:
                    self.use(chosen)
        except NotConnected:
            stopped = "disconnected"

        gains = {n: (round(before[n], 1), round(self.value(n), 1)) for n in before
                 if self.value(n) != before[n] or self.uses[n]}
        return {"stopped": stopped, "seconds": round(seconds - max(0, deadline - time.time()), 1),
                "uses": dict(self.uses), "gains": gains, "jev_calls": self.calls,
                "output": "\n".join(self.lines)}
