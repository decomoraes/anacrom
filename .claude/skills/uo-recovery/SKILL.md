---
name: uo-recovery
description: Dying on the Ultima Online shard and getting back - what death looks like in the client, finding a healer or shrine to resurrect at, and recovering the corpse and its contents afterwards. Use when the character has died, is a ghost, or needs to fetch a corpse.
---

# Death and recovery

## Recognising it

`uo status` shows `[DEAD]` and the journal carries "You are dead." As a ghost
you cannot be understood by the living, and most actions are refused.

## Getting resurrected

Walk to a healer or a shrine. If you recorded one, `uo place go <name>` takes
you there; otherwise head for the nearest town and read `uo journal` on the way -
healers greet you when you come into range.

A healer offers resurrection as a gump: `uo gump` to see the buttons, then
`uo gump 0x... <button>` to accept.

Record the healer once you find it:

```
uo place here "Britain healer" --kind healer
```

## Recovering the corpse

Your corpse stays where you died with everything you were carrying, and it
decays, so go back promptly - but go back carefully. Whatever killed you is
probably still standing on it.

Approach in stages rather than walking straight in:

```
uo script scripts/scout.py <corpse_x> <corpse_y> 2 12
```

That stops and reports the moment something hostile comes into range, which is
the information you need to decide between fighting it, waiting for it to
wander off, or pulling it away and looping back.

When the corpse is clear, `uo contents 0x...` then `uo grab` each item. Take
what you were wearing first - weapons and armour before loose goods - because a
second death mid-recovery costs whatever you have picked up.
