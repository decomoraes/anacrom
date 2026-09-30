"""An autopilot that asks Jev what to do, a second at a time.

Run it with:  uo jev [--seconds 120] [--flee 30] [--dry-run]

The split follows what Jev is for.  It makes the judgement calls: which of the
creatures around us are monsters worth hunting and which would kill us, and
then, a second at a time, whether to fight, back off, run, heal, loot, rest or
stand still -- from a short picture of the moment written in words.  Code
keeps everything that is arithmetic or bookkeeping: distances and health
become named buckets before Jev sees them, only the actions possible right now
are offered, reagents and ranges are counted here, and the one rule that is
never up for discussion (run when nearly dead) is applied without asking.

Creatures are judged once per name and remembered.  On shards like UOAlive a
skeleton and a goat wear the same grey name, so whether something is prey is
exactly the judgement Jev is for; what we do not trust it to be sure of, we
leave alone.

Every answer comes with a confidence.  Below the bar for an action we do the
cautious thing instead.  When someone speaks to us we stop and hand control
back, because Jev chooses between options and cannot write a reply.

Rule 5 still holds: the request runs on a worker thread, but all it carries is
a JSON state built on the game loop, which keeps pumping while it waits.
"""
from __future__ import annotations

import json
import random
import re
import statistics
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .client import Client, NotConnected
from .jev import Jev, JevError, Response, choice, noul, score
from .world.state import Item, distance

CORPSE_GRAPHIC = 0x2006
GOLD_GRAPHIC = 0x0EED
BANDAGE_GRAPHIC = 0x0E21
BAGS = {0x0E75, 0x0E76, 0x0E79, 0x09B0}          # backpack, bag, pouch, belt pouch
BLACK_PEARL, SULFUROUS_ASH = 0x0F7A, 0x0F8C
GARLIC, GINSENG, SPIDERS_SILK = 0x0F84, 0x0F85, 0x0F8D

# Spells by cast-macro id, with their mana and the reagents they burn.
FIREBALL = (18, 9, (BLACK_PEARL,))
MAGIC_ARROW = (5, 4, (SULFUROUS_ASH,))
HEAL = (4, 4, (GARLIC, GINSENG, SPIDERS_SILK))
CAST_RANGE = 8
# A target we cannot hurt -- behind a wall, out of sight -- will hold a spell
# caster forever.  No progress in this long, and it is dropped for a while.
NO_PROGRESS = 25.0
GIVE_UP_FOR = 120.0
CANNOT_SEE = 500237                                 # "Target can not be seen."

# What the shard says when a bandage is over, one way or another.  Applying a
# second one before then cancels the first (BandageContext.BeginHeal), so the
# heal option stays off the table until one of these arrives.
BANDAGE_DONE = {500962, 500963, 500968, 500969, 1005000, 1010058, 1010060, 1010395}
BANDAGE_TIMEOUT = 15.0

SPOKEN_KINDS = {"regular", "emote", "whisper", "yell", "guild", "alliance"}
CLILOC = re.compile(r"cliloc (\d+)")

PRICE_PER_TOKEN = 0.042 / 1_000_000          # jev-1.13 input; output is free

# How sure Jev must be before we do each thing.  Starting a fight is the costly
# mistake; running, backing off or healing when it was not needed costs little.
MIN_CONFIDENCE = {"fight": 0.6, "back_off": 0.3, "flee": 0.3, "heal": 0.4,
                  "loot": 0.4, "rest": 0.3, "roam": 0.3, "wait": 0.0}
# Keeping at a fight we are already in costs far less than starting one.
CARRY_ON = 0.35

ACTIONS = {
    "fight": {
        "do": "Attack a creature in `targets`, or keep attacking our target.",
        # Jev reads literally: "a target is near" kept it waiting on far ones.
        "when": "We are hunting: attack any creature in `targets` while we are healthy, "
                "even a far one.",
    },
    "back_off": {
        "do": "Walk away from the creature rated too strong for us, before it reaches us.",
        "when": "A creature rated too strong for us is nearby, close or adjacent. "
                "This comes before fighting anything else.",
    },
    "flee": {
        "do": "Run away from everything that is attacking us.",
        "when": "We are badly hurt, outnumbered, or our health is dropping fast.",
    },
    "heal": {
        "do": "Heal ourselves.",
        "when": "We are wounded or poisoned and not about to die this second.",
    },
    "loot": {
        "do": "Walk to the corpse or gold in `loot_nearby` and take the valuables.",
        "when": "Nothing in `creatures` is attacking us, and no target is close.",
    },
    "rest": {
        "do": "Sit still and meditate to get our mana back.",
        "when": "Our mana is low or empty and nothing is attacking us.",
    },
    "roam": {
        "do": "Walk on around the hunting ground to find something to hunt.",
        "when": "`targets` is empty, we are healthy with mana to spare, and nothing "
                "too strong for us is near.",
    },
    "wait": {
        "do": "Stand still and do nothing this second.",
        "when": "`targets` is empty and there is nothing to heal, loot, rest or roam for.",
    },
}

DANGER_LEVELS = [
    "safe: nothing near can hurt us",
    "some danger: a hostile is close but we are healthy",
    "serious danger: we are hurt and under attack",
    "deadly: we will die soon unless we leave",
]
SERIOUS_DANGER = 2.0

# How a creature is judged, once per name.  The threat levels are words Jev
# rates against; the thresholds that turn them into "hunt" or "avoid" are ours.
THREAT_LEVELS = ["harmless", "an easy kill", "a fair fight", "too strong for us"]
PREY_BAR = 0.6            # sure enough it is a monster that attacks travellers
# Score levels are weak in numerical calibration (TypeSafe's own warning), and
# a spectre came back 2.53 once and 2.1 the next time -- then killed us.  So the
# bar sits low, and experience overrides the rating (see blame()).
TOO_STRONG = 2.0
AVOID_RANGE = 10
US = "a novice mage: about 100 health, weak spells, no armour"


# --------------------------------------------------------------------------
# numbers into words -- Jev reads buckets far better than arithmetic
# --------------------------------------------------------------------------

def percent(current: int, maximum: int) -> int | None:
    if not maximum:
        return None
    return max(0, min(100, round(current * 100 / maximum)))


def health_bucket(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 90:
        return "unhurt"
    if value >= 60:
        return "lightly wounded"
    if value >= 35:
        return "badly wounded"
    return "near death"


def mana_bucket(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 80:
        return "full"
    if value >= 40:
        return "half"
    return "low" if value >= 15 else "empty"


def distance_bucket(tiles: int) -> str:
    if tiles <= 1:
        return "adjacent"
    if tiles <= 4:
        return "close"
    if tiles <= 8:
        return "nearby"
    return "far"


def trend_bucket(change: int) -> str:
    if change <= -15:
        return "dropping fast"
    if change < -3:
        return "dropping"
    if change > 3:
        return "rising"
    return "steady"


def load_bucket(weight: int, maximum: int) -> str:
    if not maximum:
        return "unknown"
    if weight >= maximum:
        return "overloaded"
    return "heavy" if weight * 100 >= maximum * 85 else "light"


def rating_word(threat: float) -> str:
    return THREAT_LEVELS[max(0, min(3, round(threat)))]


# --------------------------------------------------------------------------
# the pieces of one tick
# --------------------------------------------------------------------------

@dataclass
class Policy:
    radius: int = 14
    flee_percent: int = 30          # below this with a threat near, run without asking
    tick: float = 1.0               # seconds per decision, at least
    max_creatures: int = 8          # more is noise: Jev loses accuracy on a bloated state
    speech_threshold: float = 0.7
    loot_threshold: float = 0.5
    roam: int = 30                  # how far from where we started to go looking
    min_confidence: dict = field(default_factory=lambda: dict(MIN_CONFIDENCE))


@dataclass
class Judgement:
    """What Jev made of a creature's name, asked once and remembered."""
    prey: float                     # probability it is a monster that attacks travellers
    threat: float                   # 0 harmless .. 3 too strong for us
    learned: bool = False           # set by experience: it beat us, whatever Jev thought

    @property
    def hunt(self) -> bool:
        return self.prey >= PREY_BAR and self.threat < TOO_STRONG

    @property
    def avoid(self) -> bool:
        return self.prey >= PREY_BAR and self.threat >= TOO_STRONG


@dataclass
class Memory:
    """What the autopilot carries from one tick to the next."""
    health: deque = field(default_factory=deque)            # (time, percent)
    journal_since: float = field(default_factory=time.time)
    speech: deque = field(default_factory=lambda: deque(maxlen=6))
    target: int = 0
    target_last_at: tuple[int, int] | None = None
    attacked_at: float = 0.0
    kill_sites: deque = field(default_factory=lambda: deque(maxlen=10))
    done_with: set[int] = field(default_factory=set)
    loot_tries: dict[int, int] = field(default_factory=dict)
    heal_ready_at: float = 0.0
    judged: dict[str, Judgement] = field(default_factory=dict)
    home: tuple[int, int] | None = None            # the middle of our hunting ground
    heading: tuple[int, int] | None = None         # where roaming is taking us
    target_name: str = ""                           # for counting what we kill
    engaged_at: float = 0.0                         # when we started on our target
    engaged_health: int | None = None               # its health then
    unseen: int = 0                                 # "target can not be seen" in a row
    given_up: dict[int, float] = field(default_factory=dict)    # serial -> until when


@dataclass
class Moment:
    state: dict                      # what Jev sees
    options: dict                    # action -> what it means; only what is possible
    targets: dict[str, int]          # "m1" -> serial, for the creatures we may attack
    labels: dict[str, str]           # "m1" -> "a skeleton, 4 tiles, unhurt, a fair fight"
    health: int | None
    heard: bool                      # someone spoke since the last tick
    unjudged: list[str] = field(default_factory=list)
    avoid: list[int] = field(default_factory=list)          # serials too strong for us
    attackers: list[int] = field(default_factory=list)
    corpse: Item | None = None
    gold: Item | None = None
    bandage: Item | None = None


@dataclass
class Decision:
    action: str
    reason: str = ""
    source: str = "jev"              # jev, rule, fallback or idle
    target: int = 0
    confidence: float | None = None


class Autopilot:
    def __init__(self, client: Client, jev: Jev, policy: Policy | None = None,
                 should_stop: Callable[[], bool] = lambda: False,
                 dry_run: bool = False, log_path: Path | None = None,
                 creatures_path: Path | None = None) -> None:
        self.client = client
        self.world = client.world
        self.jev = jev
        self.policy = policy or Policy()
        self.should_stop = should_stop
        self.dry_run = dry_run
        self.log_path = log_path
        self.memory = Memory()
        self.creatures_path = creatures_path
        self._load_judgements()

        self.lines: list[str] = []
        self.actions: Counter = Counter()
        self.calls = 0
        self.recorded = 0
        self.tokens = 0
        self.latencies: list[float] = []
        self.model = ""
        self.gold_at_start = client.world.player.gold
        self.kills = 0
        self.kills_by_name: Counter = Counter()
        self.items_taken = 0
        self._started = time.monotonic()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev")

    # -- what we know about creatures ------------------------------------

    def _load_judgements(self) -> None:
        if self.creatures_path is None or not self.creatures_path.exists():
            return
        try:
            saved = json.loads(self.creatures_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for name, fields in saved.items():
            self.memory.judged[name] = Judgement(**fields)

    def _save_judgements(self) -> None:
        if self.creatures_path is None:
            return
        try:
            self.creatures_path.parent.mkdir(parents=True, exist_ok=True)
            self.creatures_path.write_text(json.dumps(
                {name: asdict(j) for name, j in sorted(self.memory.judged.items())},
                indent=1) + "\n", encoding="utf-8")
        except OSError:
            pass

    def blame(self, why: str) -> None:
        """Whatever was hitting us when it went this badly is too strong for us."""
        for serial in self.client.attackers(within=8):
            mob = self.world.mobiles.get(serial)
            if mob is None or not mob.name:
                continue
            old = self.memory.judged.get(mob.name)
            if old is not None and old.learned:
                continue
            self.memory.judged[mob.name] = Judgement(
                prey=max(old.prey if old else 0.0, PREY_BAR), threat=3.0, learned=True)
            self.say(f"         {mob.name} is too strong for us: {why}")
        self._save_judgements()

    # -- what we carry -----------------------------------------------------

    def carried(self, graphic: int) -> int:
        """Counted through bags, the way the server looks for reagents."""
        total, todo = 0, [self.world.player.backpack]
        while todo:
            for item in self.world.contents_of(todo.pop()):
                if item.graphic == graphic:
                    total += item.amount
                elif item.graphic in BAGS:
                    todo.append(item.serial)
        return total

    def can_cast(self, spell: tuple) -> bool:
        _, mana, reagents = spell
        return self.world.player.mana >= mana and all(self.carried(r) for r in reagents)

    def attack_spell(self) -> tuple | None:
        return next((s for s in (FIREBALL, MAGIC_ARROW) if self.can_cast(s)), None)

    # -- perceive ----------------------------------------------------------

    def perceive(self) -> Moment:
        world, memory, policy = self.world, self.memory, self.policy
        player = world.player
        here = (player.x, player.y)
        now = time.time()

        health = percent(player.hits, player.hits_max)
        mana = percent(player.mana, player.mana_max)
        if health is not None:
            memory.health.append((now, health))
        while memory.health and now - memory.health[0][0] > 6:
            memory.health.popleft()
        change = memory.health[-1][1] - memory.health[0][1] if memory.health else 0

        heard = self._read_journal(now)
        self._follow_target()

        attackers = self.client.attackers()
        creatures, targets, labels = [], {}, {}
        unjudged, avoid, attacking_us = [], [], []
        threat_near = False
        for mob in world.nearby_mobiles(policy.radius)[:policy.max_creatures]:
            tag = f"m{len(creatures) + 1}"
            gap = distance(here, (mob.x, mob.y))
            mob_health = percent(mob.hits, mob.hits_max)
            name = mob.name or "an unnamed creature"
            judged = memory.judged.get(name)
            entry = {
                "id": tag,
                "name": name,
                "distance": distance_bucket(gap),
                "health": health_bucket(mob_health),
            }
            attacking = mob.serial in attackers
            if attacking:
                entry["attacking_us"] = True
                attacking_us.append(mob.serial)
            if mob.serial == memory.target and player.war_mode:
                entry["our_target"] = True

            if mob.notoriety in (1, 2, 7):              # blue, green, yellow: people
                entry["rating"] = "not a monster"
            elif judged is None and mob.name and mob.notoriety == 3:
                entry["rating"] = "not judged yet"
                if name not in unjudged:
                    unjudged.append(name)
            elif judged is not None:
                entry["rating"] = rating_word(judged.threat) if judged.prey >= PREY_BAR \
                    else "not a monster"
            creatures.append(entry)

            alive = mob_health != 0 and memory.given_up.get(mob.serial, 0) < now
            too_strong = judged is not None and judged.avoid
            if too_strong and alive and gap <= AVOID_RANGE:
                avoid.append(mob.serial)
            # Fair game: red and orange names, grey ones judged to be monsters
            # we can take, and anything that swung first.  Never people.
            fair = mob.hostile or (judged is not None and judged.hunt) or attacking
            if fair and alive and mob.notoriety not in (1, 2, 7):
                targets[tag] = mob.serial
                labels[tag] = ", ".join(
                    [name, entry["distance"], entry["health"], entry.get("rating", "")]
                    + (["attacking us"] if attacking else [])).rstrip(", ")
                threat_near = threat_near or gap <= 10

        ground = world.ground_items(8)
        corpse = next((i for i in ground if i.graphic == CORPSE_GRAPHIC
                       and i.serial not in memory.done_with
                       and any(distance((i.x, i.y), site) <= 3 for site in memory.kill_sites)),
                      None)
        gold = next((i for i in ground if i.graphic == GOLD_GRAPHIC
                     and i.serial not in memory.done_with), None)
        bandage = next((i for i in world.contents_of(player.backpack)
                        if i.graphic == BANDAGE_GRAPHIC), None) if player.backpack else None

        me_mobile = world.mobiles.get(player.serial)
        poisoned = bool(me_mobile and me_mobile.poisoned)
        fighting = world.mobiles.get(memory.target) if player.war_mode else None
        spell = self.attack_spell()

        me = {
            "name": player.name,
            "health": health_bucket(health),
            "health_trend": trend_bucket(change),
            "mana": mana_bucket(mana),
            "fighting": (fighting.name or "an unnamed creature") if fighting else "nobody",
            "attack": "spells" if spell else "fists",
            "load": load_bucket(player.weight, player.weight_max),
        }
        if poisoned:
            me["poisoned"] = True
        state: dict = {"me": me, "creatures": creatures}
        if targets:
            state["targets"] = list(labels.values())

        can_heal_spell = self.can_cast(HEAL)
        hurt = (health is not None and health < 100) or poisoned
        options = {}
        if targets:
            options["fight"] = ACTIONS["fight"]
        if [s for s in avoid if s not in attacking_us]:
            options["back_off"] = ACTIONS["back_off"]
        if threat_near or attacking_us or avoid:
            options["flee"] = ACTIONS["flee"]
        if hurt and (can_heal_spell or (bandage is not None and now >= memory.heal_ready_at)):
            options["heal"] = ACTIONS["heal"]
        if corpse is not None or gold is not None:
            options["loot"] = ACTIONS["loot"]
            state["loot_nearby"] = {
                "corpse": distance_bucket(distance(here, (corpse.x, corpse.y)))
                if corpse else "none",
                "gold": distance_bucket(distance(here, (gold.x, gold.y))) if gold else "none",
            }
        if mana is not None and mana < 60 and not attacking_us:
            options["rest"] = ACTIONS["rest"]
        if (not targets and not attacking_us and not avoid
                and (health is None or health >= 70) and (mana is None or mana >= 50)):
            options["roam"] = ACTIONS["roam"]
        options["wait"] = ACTIONS["wait"]
        if memory.speech:
            state["recent_speech"] = list(memory.speech)

        return Moment(state=state, options=options, targets=targets, labels=labels,
                      health=health, heard=heard, unjudged=unjudged, avoid=avoid,
                      attackers=attacking_us, corpse=corpse, gold=gold, bandage=bandage)

    def _read_journal(self, now: float) -> bool:
        memory, me = self.memory, self.world.player.serial
        heard = False
        for entry in self.world.journal:
            if entry.at <= memory.journal_since:
                continue
            found = CLILOC.search(entry.text)
            if found and int(found.group(1)) in BANDAGE_DONE:
                memory.heal_ready_at = 0.0
            if found and int(found.group(1)) == CANNOT_SEE:
                memory.unseen += 1
            if (entry.kind in SPOKEN_KINDS and entry.speaker and not found
                    and entry.serial not in (0, 0xFFFFFFFF, me)):
                memory.speech.append(f"{entry.speaker}: {entry.text}")
                heard = True
        memory.journal_since = now
        return heard

    def _follow_target(self) -> None:
        """Note where our target was last seen, so its corpse is ours to loot."""
        memory = self.memory
        if not memory.target:
            return
        mob = self.world.mobiles.get(memory.target)
        if mob is not None and percent(mob.hits, mob.hits_max) != 0:
            memory.target_last_at = (mob.x, mob.y)
            return
        if memory.target_last_at is not None:
            memory.kill_sites.append(memory.target_last_at)
            self.kills += 1
            self.kills_by_name[memory.target_name or "something"] += 1
        memory.target, memory.target_last_at, memory.target_name = 0, None, ""

    # -- decide ------------------------------------------------------------

    def rule(self, moment: Moment) -> Decision | None:
        """What we do without asking."""
        floor = self.policy.flee_percent
        if moment.health is not None and moment.health < floor and "flee" in moment.options:
            self.blame(f"it put us under {floor}% health")
            return Decision("flee", f"health {moment.health}% is under {floor}%", source="rule")
        return None

    def questions(self, moment: Moment) -> dict:
        questions = {}
        if len(moment.options) > 1:
            questions["action"] = choice(
                "We are a character in Ultima Online, out hunting monsters for gold. "
                "What should we do in the next second?",
                moment.options)
        if "fight" in moment.options and len(moment.targets) > 1:
            questions["target"] = choice(
                "If we fight, which of `targets` should we attack?", moment.labels)
        if "flee" in moment.options:
            questions["danger"] = score("How much danger are we in right now?", DANGER_LEVELS)
        if moment.heard:
            questions["addressed"] = noul(
                "Is someone in `recent_speech` talking directly to us and expecting an answer?",
                true="They greet us, ask us something, or use our name.",
                false="Talk between other people, calling out to nobody in particular, "
                      "or game messages.")
        if moment.unjudged:
            moment.state["unjudged"] = moment.unjudged
            moment.state["us"] = US
            for n in range(len(moment.unjudged)):
                questions[f"prey_{n}"] = noul(
                    f"Is `unjudged[{n}]` a monster that attacks travellers?",
                    true="Undead, demons, elementals, monstrous beasts and hostile "
                         "creatures of the wild.",
                    false="Livestock, deer, pets, harmless small animals, or people.")
                questions[f"threat_{n}"] = score(
                    f"How dangerous is `unjudged[{n}]` to `us` in a fight?", THREAT_LEVELS)
        return questions

    def learn(self, moment: Moment, answers: dict) -> None:
        """Keep Jev's verdict on each new name, so it is asked once."""
        for n, name in enumerate(moment.unjudged):
            prey = answers.get(f"prey_{n}")
            threat = answers.get(f"threat_{n}")
            if prey and threat:
                self.memory.judged[name] = Judgement(prey["noul"], threat["score"])
                verdict = ("hunt" if self.memory.judged[name].hunt else
                           "avoid" if self.memory.judged[name].avoid else "ignore")
                self.say(f"         judged {name}: {verdict} "
                         f"(prey {prey['noul']:.2f}, threat {threat['score']:.1f})")
        if moment.unjudged:
            self._save_judgements()

    def decide(self, moment: Moment, answers: dict) -> Decision:
        self.learn(moment, answers)
        addressed = answers.get("addressed")
        if addressed and addressed["noul"] >= self.policy.speech_threshold:
            return Decision("hand_back", f"someone is talking to us ({addressed['noul']:.2f})")

        picked = answers.get("action")
        if picked is None:
            return Decision("wait", "nothing else is possible", source="idle")

        action, confidence = picked["choice"], picked["confidence"]
        bar = self.policy.min_confidence.get(action, 0.5)
        if action == "fight" and self.memory.target in moment.targets.values():
            bar = min(bar, CARRY_ON)
        if action not in moment.options:
            return Decision("wait", f"{action} is not possible now", source="fallback")
        if confidence < bar:
            danger = answers.get("danger")
            if "flee" in moment.options and danger and danger["score"] >= SERIOUS_DANGER:
                return Decision("flee", f"unsure ({action} {confidence:.2f}) and in danger "
                                        f"({danger['score']:.1f})", source="fallback")
            if "back_off" in moment.options:
                return Decision("back_off", f"unsure ({action} {confidence:.2f}) with "
                                            f"something too strong near", source="fallback")
            return Decision("wait", f"unsure ({action} {confidence:.2f} < {bar:.2f})",
                            source="fallback", confidence=confidence)

        if action == "fight":
            serial, why = self._pick_target(moment, answers.get("target"))
            return Decision("fight", why, target=serial, confidence=confidence)
        return Decision(action, confidence=confidence)

    def _pick_target(self, moment: Moment, answer: dict | None) -> tuple[int, str]:
        tags = list(moment.targets)                  # nearest first
        if len(tags) == 1:
            return moment.targets[tags[0]], ""
        if answer and answer["choice"] in moment.targets and answer["confidence"] >= 0.3:
            return moment.targets[answer["choice"]], f"target confidence {answer['confidence']:.2f}"
        if self.memory.target in moment.targets.values():
            return self.memory.target, "unsure of target, staying on ours"
        return moment.targets[tags[0]], "unsure of target, taking the nearest"

    # -- act ---------------------------------------------------------------

    def act(self, decision: Decision, moment: Moment) -> None:
        handler = getattr(self, f"_do_{decision.action}", None)
        if handler is not None:
            handler(decision, moment)

    def _keep_going(self, _step: dict) -> bool:
        return not self.should_stop()

    def _away_from(self, mobiles: list, tiles: int, max_steps: int) -> None:
        """Walk straight away from the middle of some mobiles."""
        client, player = self.client, self.world.player
        if player.war_mode:
            client.set_war_mode(False)
        if not mobiles:
            return
        cx = sum(m.x for m in mobiles) / len(mobiles)
        cy = sum(m.y for m in mobiles) / len(mobiles)
        dx = (player.x > cx) - (player.x < cx)
        dy = (player.y > cy) - (player.y < cy)
        if dx == dy == 0:
            dx = 1
        client.walk_to(player.x + dx * tiles, player.y + dy * tiles, max_steps=max_steps,
                       on_step=self._keep_going)

    def _do_fight(self, decision: Decision, moment: Moment) -> None:
        client, memory, player = self.client, self.memory, self.world.player
        mob = self.world.mobiles.get(decision.target)
        if mob is None:
            return
        now = time.time()
        mob_health = percent(mob.hits, mob.hits_max)
        if decision.target != memory.target:
            memory.target, memory.target_last_at = decision.target, (mob.x, mob.y)
            memory.target_name = mob.name or ""
            memory.engaged_at, memory.engaged_health, memory.unseen = now, mob_health, 0
        elif self._no_progress(mob_health, now, moment):
            memory.given_up[decision.target] = now + GIVE_UP_FOR
            self.say(f"         giving up on {mob.name or 'it'} for now: "
                     f"{'out of sight' if memory.unseen >= 2 else 'no progress'}")
            memory.target, memory.target_last_at, memory.unseen = 0, None, 0
            return
        gap = distance((player.x, player.y), (mob.x, mob.y))

        spell = self.attack_spell()
        if spell is not None:
            if gap > CAST_RANGE:
                client.walk_to(mob.x, mob.y, stop_within=CAST_RANGE - 2, max_steps=4,
                               on_step=self._keep_going)
                return
            client.cast(spell[0], target=decision.target)
            client.pump(0.6)
            return

        # Nothing left to cast: close in and use our fists.
        if not player.war_mode:
            client.set_war_mode(True)
        # Re-issue now and then: the server stops swinging if it lost the target.
        if now - memory.attacked_at > 4 or client.world.player.last_target != decision.target:
            client.attack(decision.target)
            memory.attacked_at = now
        if gap > 1:
            client.walk_to(mob.x, mob.y, stop_within=1, max_steps=4, on_step=self._keep_going)

    def _no_progress(self, mob_health: int | None, now: float, moment: Moment) -> bool:
        """Out of sight twice running, or unhurt by us for a good while."""
        memory = self.memory
        if memory.unseen >= 2:
            return True
        if memory.target in moment.attackers:
            return False                             # it is fighting us; keep at it
        if now - memory.engaged_at < NO_PROGRESS:
            return False
        if mob_health is not None and memory.engaged_health is not None \
                and mob_health < memory.engaged_health:
            memory.engaged_at, memory.engaged_health = now, mob_health     # it is working
            return False
        return True

    def _do_back_off(self, decision: Decision, moment: Moment) -> None:
        strong = [m for m in map(self.world.mobiles.get, moment.avoid) if m]
        self._away_from(strong, tiles=16, max_steps=10)

    def _do_flee(self, decision: Decision, moment: Moment) -> None:
        self.memory.target, self.memory.target_last_at = 0, None
        serials = set(moment.targets.values()) | set(moment.attackers) | set(moment.avoid)
        self._away_from([m for m in map(self.world.mobiles.get, serials) if m],
                        tiles=12, max_steps=6)

    def _do_heal(self, decision: Decision, moment: Moment) -> None:
        client = self.client
        if self.can_cast(HEAL):
            client.cast(HEAL[0], target=self.world.player.serial)
            client.pump(0.6)
            return
        client.use(moment.bandage.serial)
        if client.wait_for_target(2.0):
            client.target_self()
            self.memory.heal_ready_at = time.time() + BANDAGE_TIMEOUT

    def _do_rest(self, decision: Decision, moment: Moment) -> None:
        self.client.use_skill("Meditation")

    def _do_roam(self, decision: Decision, moment: Moment) -> None:
        """A few steps towards a point on the hunting ground, a new one on arrival."""
        memory, player = self.memory, self.world.player
        here = (player.x, player.y)
        home = memory.home or here
        if memory.heading is None or distance(here, memory.heading) <= 2:
            reach = self.policy.roam
            memory.heading = (home[0] + random.randint(-reach, reach),
                              home[1] + random.randint(-reach, reach))
        walked = self.client.walk_to(*memory.heading, stop_within=2, max_steps=6,
                                     on_step=self._keep_going)
        if walked.get("stopped") in ("no route", "blocked"):
            memory.heading = None                    # somewhere we cannot go; pick again

    def _do_loot(self, decision: Decision, moment: Moment) -> None:
        client, player = self.client, self.world.player
        thing = moment.corpse or moment.gold
        memory = self.memory
        # A corpse behind a wall takes more than a few steps to reach, so it
        # gets three goes, not one; only then is it written off.
        memory.loot_tries[thing.serial] = memory.loot_tries.get(thing.serial, 0) + 1
        if memory.loot_tries[thing.serial] >= 3:
            memory.done_with.add(thing.serial)
        walked = client.walk_to(thing.x, thing.y, stop_within=1, max_steps=30,
                                on_step=self._keep_going)
        if not walked["arrived"]:
            self.say(f"         could not reach {thing.name or 'it'} ({walked.get('stopped')})")
            return
        memory.done_with.add(thing.serial)
        if not player.backpack:
            return
        if thing is moment.corpse:
            self._loot_corpse(thing.serial)
        else:
            client.move_item(thing.serial, player.backpack)
            self.say(f"         picked up {thing.name or 'gold'}")

    def _loot_corpse(self, serial: int) -> None:
        client, player = self.client, self.world.player
        items = client.open_container(serial)
        for item in items:
            client.request_properties(item.serial)
        client.pump(0.6)
        items = self.world.contents_of(serial)[:40]

        # Gold is always worth it; for the rest, one yes/no per item, all in
        # one request, and the counting stays here.
        take = [i for i in items if i.graphic == GOLD_GRAPHIC]
        others = [i for i in items if i.graphic != GOLD_GRAPHIC]
        if others:
            response = self._ask(
                {"items": [i.name or "an unidentified item" for i in others],
                 "our_load": load_bucket(player.weight, player.weight_max)},
                {f"item_{n}": noul(
                    f"Is `items[{n}]` worth carrying away to use or sell?",
                    true="Gear, weapons, armour, reagents, gems, ammunition, "
                         "or anything a merchant would buy.",
                    false="Bones, body parts, empty containers or worthless scraps.")
                 for n in range(len(others))})
            if response is not None:
                take += [i for n, i in enumerate(others)
                         if response.answers.get(f"item_{n}", {}).get("noul", 0)
                         >= self.policy.loot_threshold]
        for item in take:
            if self.should_stop():
                return
            client.move_item(item.serial, player.backpack)
            self.items_taken += 1
        names = ", ".join(i.name or "gold" for i in take) or "nothing"
        self.say(f"         looted {len(take)} of {len(items)}: {names}")

    # -- talking to Jev ----------------------------------------------------

    def _ask(self, state, questions: dict) -> Response | None:
        """One request, with the game loop pumping until it comes back."""
        future = self._executor.submit(self.jev.ask, state, questions)
        while not future.done():
            if self.should_stop():
                return None
            self.client.pump(0.02)
        response = future.result()
        self.calls += 1
        self.tokens += response.input_tokens
        self.latencies.append(response.seconds)
        self.model = response.model
        return response

    # -- the loop ----------------------------------------------------------

    def run(self, seconds: float = 120.0, max_ticks: int = 0) -> dict:
        client, player = self.client, self.world.player
        if player.backpack:
            # So we know about bandages and reagents, bags included.
            pack = self.world.contents_of(player.backpack) or client.open_container(
                player.backpack)
            for item in pack:
                if item.graphic in BAGS and not self.world.contents_of(item.serial):
                    client.open_container(item.serial)

        self.memory.home = (player.x, player.y)
        self._started = time.monotonic()
        stopped, ticks, failures = "time limit", 0, 0
        previous: Decision | None = None
        speech: list[str] = []
        try:
            while True:
                if self.should_stop():
                    stopped = "interrupted"
                    break
                if not client.connected:
                    stopped = "disconnected"
                    break
                if player.dead or (player.hits_max and player.hits == 0):
                    stopped = "we died"
                    self.blame("it killed us")
                    break
                if time.monotonic() - self._started >= seconds:
                    break
                if max_ticks and ticks >= max_ticks:
                    stopped = "tick limit"
                    break
                ticks += 1
                tick_started = time.monotonic()

                moment = self.perceive()
                response = None
                decision = self.rule(moment)
                if decision is None:
                    questions = self.questions(moment)
                    if not questions:
                        decision = Decision("wait", "nothing to decide", source="idle")
                    else:
                        try:
                            response = self._ask(moment.state, questions)
                        except JevError as exc:
                            failures += 1
                            if exc.status in (401, 403, 404, 422) or failures >= 3:
                                stopped = f"Jev failed: {exc}"
                                break
                            decision = Decision("wait", f"Jev failed: {exc}",
                                                source="fallback")
                        else:
                            if response is None:
                                stopped = "interrupted"
                                break
                            failures = 0
                            decision = self.decide(moment, response.answers)

                self.actions[decision.action] += 1
                self._record(moment, response, decision)
                self._narrate(decision, previous)
                previous = decision

                if decision.action == "hand_back":
                    stopped = "someone is talking to us"
                    speech = list(self.memory.speech)
                    break
                if not self.dry_run:
                    try:
                        self.act(decision, moment)
                    except JevError as exc:          # the loot question
                        self.say(f"         Jev failed while looting: {exc}")

                spare = self.policy.tick - (time.monotonic() - tick_started)
                if spare > 0:
                    client.pump(spare)
        except NotConnected:
            stopped = "disconnected"
        finally:
            self._executor.shutdown(wait=False)

        summary = {
            "stopped": stopped,
            "seconds": round(time.monotonic() - self._started, 1),
            "ticks": ticks,
            "actions": dict(self.actions),
            "jev_calls": self.calls,
            "model": self.model,
            "median_latency_ms": round(statistics.median(self.latencies) * 1000)
            if self.latencies else None,
            "input_tokens": self.tokens,
            "cost_usd": round(self.tokens * PRICE_PER_TOKEN, 6),
            "health": [player.hits, player.hits_max],
            "gold": [self.gold_at_start, player.gold],
            "kills": self.kills,
            "kills_by_name": dict(self.kills_by_name),
            "items_taken": self.items_taken,
            "position": [player.x, player.y],
            "map": player.map,
            "dry_run": self.dry_run,
            "output": "\n".join(self.lines),
        }
        if speech:
            summary["speech"] = speech
        if self.recorded:
            summary["log"] = str(self.log_path)
        return summary

    # -- telling people what happened -------------------------------------

    def say(self, line: str) -> None:
        self.lines.append(line)

    def _narrate(self, decision: Decision, previous: Decision | None) -> None:
        """One line per change of mind, not one per tick."""
        if previous is not None and (previous.action, previous.target, previous.source) == (
                decision.action, decision.target, decision.source):
            return
        parts = []
        if decision.action == "fight":
            mob = self.world.mobiles.get(decision.target)
            name = (mob.name if mob else "") or "an unnamed creature"
            parts.append(f"{name} (0x{decision.target:08X})")
        if decision.reason:
            parts.append(decision.reason)
        if decision.confidence is not None and decision.source == "jev":
            parts.append(f"confidence {decision.confidence:.2f}")
        if decision.source != "jev":
            parts.append(f"[{decision.source}]")
        elapsed = time.monotonic() - self._started
        self.say(f"{elapsed:6.1f}s  {decision.action:<9} {' - '.join(parts)}".rstrip())

    def _record(self, moment: Moment, response: Response | None, decision: Decision) -> None:
        """Every judged tick as a JSON line, for tuning the confidence bars later."""
        if self.log_path is None or decision.source == "idle":
            return
        row = {"at": round(time.time(), 3), "state": moment.state,
               "options": list(moment.options), "decision": asdict(decision),
               "dry_run": self.dry_run}
        if response is not None:
            row.update(model=response.model, answers=response.answers,
                       seconds=response.seconds, input_tokens=response.input_tokens)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as log:
                log.write(json.dumps(row) + "\n")
            self.recorded += 1
        except OSError:
            pass
