# Handoff: anacrom / Jevensen

Date: 2026-10-01. Branch `feat/terminal-uo-client` (last commit de93dca, pushed). One uncommitted change: `scripts/hunt_mage.py` gained kiting (`KITE_WITHIN`).

## Rules of the road
- **One driver at a time.** The user's ClassicUO and the headless client share the account; log one out before the other logs in.
- **Supervised only.** The user is at the keyboard. UOAlive allows AFK skill training, not AFK hunting for loot. Do not hunt unless the user says they are present.
- Python 3.10, stdlib only. Tests: `python3 -m unittest discover -s tests` (about 136 pass). Read `CLAUDE.md` first.
- Credentials and the Jev key live in `~/.anacrom/config.json` (0600). Never commit them.

## Where the character is
Jevensen (serial 0x15C29288), map 1 (Trammel), **alive, client still running and logged in**, standing at the Britain cemetery gate (1412, 1521, 13). hp ~63/107, mana full, gold 11, armor 0.
Skills: Evaluate Int 100, Meditation 94.6, Magery 43.5. Str 44, Dex 60, Int 106.

Pack: 53 black pearl, 20 ginseng, a bag with 50 sulfurous ash, 50 blood moss, 21 spider's silk. **No mandrake or garlic**, so Bless (used for training) can't be cast. Fireball needs only black pearl.

Stop cleanly: `bin/uo stop; bin/uo logout; bin/uo shutdown`. Start: `bin/uo start && bin/uo login`.

## What just happened
- New Haven Mine entrance is a **teleporter**, not a walkable cave: stepping west/northwest from (3510, 2777) lands at (5912, 355), the mine inside. A "Back to New Haven Bank" teleporter is 2 tiles from the arrival point. `walk` reports "no route" because the router avoids portals.
- Jev judged earth elementals "avoid" (threat 2.5 over the 2.0 cutoff) and just waited for 300 ticks. `scripts/hunt_mage.py` (Fireball, kiting) then got **one cast** off before an elemental killed him.
- Resurrected via the death gump (id 3660239720, button 1) at Britain's summoner at (1418, 1715): **no items lost**. If the gump is hidden, `[ghostprompt` toggles it (it toggles, so check `uo gump` first).
- Research: normal earth elementals have 76-93 hp, Greater ones 537-582 hp with 35-45% fire resist. UOAlive's seem to be the Greater kind. **Skip them** until Magery 60+ and more hp.

## Open goals, in order
1. **Gold.** Hunt easy prey with the mage script at the Britain graveyard (1376, 1484): skeletons, zombies. The Halloween invasion event announced at Britain Cemetery may have boosted spawns. Try:
   `bin/uo script scripts/hunt_mage.py 10 12 50 "skeleton,zombie,ghoul"` (minutes, radius, flee %, prey names). Rest to full hp first.
2. **Meditation 94.6 to 100** by spending mana: Fireball casts at targets, or buy garlic and mandrake from Fleta (1409, 1819) for Bless.
3. **Magery** 43.5 to 60+, then Wrestling and Strength for survivability.
4. Nice to have: `uo train` and `uo play` don't write run records yet, so the Jev Stats artifact (https://claude.ai/artifact/MpLHF6xLz1JFRXp9vrWSSw, republish `jev-stats-artifact.html`) misses them. `uo play` has never run live.

## Known traps
- Grey names are monsters on UOAlive. Judge by name, not colour.
- Do not step on moongates or teleporters by accident; routes avoid them already.
- Murderers hang around the Britain bank (1430, 1692). Train at the quiet spot (1394, 1636) instead.
- Jev "stop" needs confidence 0.7. Fight needs 0.6.

## Files worth knowing
`anacrom/autopilot.py` (Jev hunting), `anacrom/planner.py` (`uo play`), `anacrom/routines.py` (resurrect, buy, train), `scripts/hunt_mage.py`, `~/.anacrom/places.json` (gazetteer), `~/.anacrom/creatures.json` (Jev's per-name verdicts), `~/.anacrom/jev.jsonl` (decision log).
