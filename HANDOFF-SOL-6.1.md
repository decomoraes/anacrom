# Handoff to SOL 6.1: Jevensen on UOAlive

Date: 2026-10-01. Branch `feat/terminal-uo-client`. Read `CLAUDE.md` and `HANDOFF.md` first; this file is the update since then.

## Rules
- One driver at a time. The user's ClassicUO and the headless client share the account. The user logs in to hunt; the client must be shut down while they do (`bin/uo logout; bin/uo shutdown`).
- Hunting for loot only while the user is at the keyboard. AFK skill training is allowed on UOAlive.
- Python 3.10, stdlib only. Tests: `python3 -m unittest discover -s tests` (138 pass).
- Commands queue behind a running script: one command at a time inside the client. Long scripts run with `run_in_background`; the CLI times out, the script keeps going. `uo stop` interrupts.

## Where things stand
Jevensen (serial 0x15C29288) is **logged out**, last standing at the New Haven Mine entrance, (3510, 2777, 25), map 1.

| | |
|---|---|
| Skills | Evaluate Int 100, Meditation 100, Magery 48.6, Wrestling 38 |
| Stats | Str 46, Dex 66, Int 113, hp 108, armor 0 |
| Pack gold | 100 |
| Bank (Britain) | 2,061 gold (check with `bank` then `balance`) |
| Reserved for user | 100 black pearl, in a bag in the pack (never use for training) |
| Also carried | garlic, ginseng, mandrake, silk, ash, nightshade, blood moss, a few magic items |

## What works
- **Mine:** the entrance is a teleporter. From (3510, 2777) step west, west, northwest, northwest, north, west and you land at (5912, 355). Exit: stand on the "Back to New Haven Bank" gate (about 2 tiles away) and double-click it. Earth elementals there pay 175-225 gold: 76-93 hp, hit for 17-32, slow movers. Four Fireballs (black pearl) kill one. The user hunts by hand with an F1 Fireball macro; paragon elementals are too tough for now.
- **Selling:** vendors pay into the bank, not the pack. `sell` works since the 0x9F fix. Shopkeeper sweep script: `scratchpad/sellall.py` (walks Britain and offers the pack to every vendor; run it in the background).
- **Britain moongate:** (1336, 1997). Stand on it, then reply to the menu. Gump ids change between server updates (seen 1246969774 and 24915327), so match on the title "Public Moongate". Switch 200 = New Haven, 201 = Britain. New Haven arrival is on the bank roof, (3483, 2569, z 45-48); `walk` then reaches the ground.
- **Banking:** `say bank`, find the box (layer 0x1D, graphic 0x0E7C), `move_item(gold_serial, box_serial, amount)`. Gold shown by `balance`, not by the box contents.
- **ClassicUO settings:** files are under `D:\UO\UOAlive_Package\Part 1 Launcher\ClassicUO\Data\Profiles\energyzer123\UOAlive\Jevensen\` (`/mnt/d/...`). Edit only with the client closed. Corpse auto-open and grid loot are already enabled; backups are `*.bak-claude`.

## Open work
1. **Magery 48.6 to 100.** Bless (3rd circle) no longer teaches anything: 40 casts gave 0 gain. Use **Greater Heal**, spell id 29, 11 mana, garlic + ginseng + mandrake + spider's silk. It is in the spellbook and now defined as `GREATER_HEAL` in `anacrom/routines.py`; pass it to `routines.train_magery(client, seconds, spell=GREATER_HEAL)`. Earlier measurement: about 160 gold per skill point on Bless; expect more per point now.
2. **Reagent supply limit.** Fleta (Britain mage shop, serial 0x00065C93, (1409, 1819)) sells garlic and mandrake 40 at a time, and they refill slowly. Pearls, moss, ash, nightshade, ginseng and silk are plentiful. Find a spell that uses the plentiful ones, or a second reagent vendor.
3. **Tooling gaps:**
   - Update any gate script to match the moongate by title instead of a fixed gump id.
   - `scripts/hunt_mage.py` has kiting and gem looting added; the elemental hunt via script never produced a report because the CLI timed out. Test it from a background job.
   - Hunts and training outside `uo jev` do not write run records, so the stats page (https://claude.ai/artifact/MpLHF6xLz1JFRXp9vrWSSw, version 4) undercounts income.
   - `uo play` has never run live.
4. **Deferred idea:** a local proxy so the user and the client share one connection (watch-only first, then injected actions, then shared movement).

## Traps
- Grey names are monsters. Judge by name.
- Do not step on gates by accident. Routes avoid portals, so stepping onto one is a manual `step`.
- The shard went down for a while in this session. If the client disconnects with "Connection reset", test `login.uoalive.com:2593` before assuming the user logged in.
- Death is cheap but not free: the first death menu button resurrects at Britain's summoner (1418, 1715) with items. Open the menu with `[ghostprompt`, which toggles, so check `uo gump` first.
