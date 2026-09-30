---
name: playing-uo
description: Drive the Ultima Online client from the terminal with the `uo` command - logging in, reading the world, moving, talking, handling target cursors and gumps, and deciding when to write a script instead of typing commands. Use whenever playing, exploring, or automating on the shard.
---

# Playing Ultima Online

`uo --help` lists every command and `uo <command> --help` its arguments; that is
the source of truth, so read it rather than guessing. What follows is what the
help text cannot tell you.

## Start of every session

```
uo start          # background client process; survives between your commands
uo login          # enters the world with the configured account
uo status         # position, vitals, who and what is nearby
```

If `uo status` says "not logged in", the process is up but the connection is
not: run `uo login` again. Serials print as `0x0040A1B2` and every command that
takes one accepts that form.

## The loop

Work one `uo status` at a time. Each status gives the serials near you, and
every other command is aimed at one of those serials. Re-run `uo status` after
anything that changes the world, because entities move and serials go stale.

`uo journal` is the other half of perception: quest text, NPC replies, damage
messages and system errors all land there and nowhere else. When a command
seems to do nothing, read the journal before concluding it failed.

## Movement

`uo walk X Y` heads for a coordinate, stepping around what it bumps into. There
is no map data behind it, so it can get stuck against a long wall: when it
reports `stopped: blocked`, walk to an intermediate point that clears the
obstacle and continue from there.

`uo goto 0x...` walks to something in view. `uo place go <name>` walks to
somewhere you recorded earlier.

Record places as you find them - this is the only map you get:

```
uo place here "Britain bank" --kind bank
uo place near --kind bank
```

## Target cursors

Many actions (casting, most skills, some items) make the server ask you to
point at something. `uo status` shows `** the server is waiting for a target **`
while that is pending. Answer it - `uo target 0x...`, `uo target self`,
`uo target X Y`, or `uo target cancel` - because an unanswered cursor blocks the
next action.

## Gumps

Dialogs arrive as gumps. `uo gump` lists the open ones with their button
numbers and text; `uo gump 0x... <button>` presses one. Button 0 closes.

## When to write a script instead

A command per swing is too slow for combat, and too slow for anything that
needs to react within a second. Write the loop as Python and run it inside the
client:

```
uo script scripts/combat.py 12 35 3
```

Scripts get `client`, `world`, `player`, `args` and `check_interrupt` in scope.
Call `check_interrupt()` inside any loop so `uo stop` can cut it short. Read
`scripts/combat.py` for the shape; copy and edit it rather than starting blank.

`uo stop` interrupts the running action immediately - it is answered out of
band, so it works even while a walk or script is mid-flight.

## Handing the reflexes to Jev

`uo jev --seconds 300` lets TypeSafe's Jev decide each second, choosing
between fight, flee, heal, loot and wait, while you stay the one who plans. It
needs `TYPESAFE_API_KEY`. Try `uo jev --dry-run --ticks 5` first on an
unfamiliar area.

Read how the run ended before doing anything else:

- `stopped: someone is talking to us` - the lines follow, prefixed `>`. Jev
  cannot write a reply, so you answer with `uo say`, then restart it if you
  want.
- `stopped: we died` - see the uo-recovery skill.
- `stopped: Jev failed: ...` - a 401 means the key is wrong. Anything else,
  play by hand for a while.

It hunts red and orange names, grey monsters Jev has judged it can take, and
anything that attacks first. People are never targets, and creatures judged too
strong (or that have beaten it) are kept away from, so walking it through town
is safe. It roams within about 30 tiles of where it started; aim it with
`uo walk` or `uo place go`, then hand over.
