---
name: uo-combat
description: Fighting on the Ultima Online shard - choosing a target, closing distance, holding an attack, breaking off before dying, and looting the corpse. Use when engaging a monster or player, or when something hostile is nearby.
---

# Combat

Run the loop as a script, not as commands: `uo script scripts/combat.py
<radius> <flee_percent> <max_kills>`. The rest of this is the judgement the
script cannot make for you.

## Before engaging

Check three numbers in `uo status`: your health, the target's `notoriety`, and
its distance.

- `innocent` names, and grey `attackable` animals, are usually safe to leave alone - killing an innocent
  makes you a criminal and the guards will act on it.
- `criminal`, `enemy` and `murderer` will fight you.
- A mobile whose name you do not recognise is worth a `uo look 0x...` first.
  Its properties tell you what it is before it tells you the hard way.

## Holding the fight

`uo attack 0x...` starts it and the server swings for you. You stay in the
fight as long as you are within range, so the failure mode is drifting out of
it, not forgetting to swing.

Watch your own health every few seconds. Decide the number you will break off
at before you start, and leave when you hit it - the corpse is recoverable,
the character's time is not.

## Breaking off

Walk away in a straight line, then keep walking. Most creatures give up after
a dozen tiles. `uo warmode off` first, so you stop retaliating.

## Looting

The corpse is a container: `uo contents 0x...` lists it, `uo grab 0x...` moves
one thing into your backpack. Gold stacks and is always worth taking; check
weight in `uo status` before hauling armour.

A corpse near where something died is usually yours, but not always - looting
another player's corpse is stealing and is treated as such.
