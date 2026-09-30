# anacrom

A headless Ultima Online client you play from a terminal.

It speaks the real UO protocol to a real shard -- login handshake, Huffman
compression, world state, movement, speech, containers, targeting -- and exposes
all of it as a `uo` command plus a Python scripting hook. There is no window, no
art files and no screen scraping: the world arrives as packets and leaves as
structured text, which is the form an agent can actually reason about.

```
$ uo status
Alaric  serial 0x00ABCDEF
  at (1434, 1699, 0) map 0 facing north
  hp 71/100   mana 30/35   stam 50/55
  str 80  dex 60  int 40  armor 42
  gold 1250  weight 210/400  followers 0/5
  1 mobiles nearby:
      0x00111111 a town guard                   3 tiles southeast  innocent
  1 items on the ground:
      0x40222222 120 gold coins                 1 tiles x120
```

## Quick start

```bash
bin/uo set account YOURACCOUNT
bin/uo set password YOURPASSWORD     # stored 0600 in ~/.anacrom/config.json
bin/uo start                          # background client process
bin/uo login
bin/uo status
```

Credentials can come from `ANACROM_ACCOUNT` / `ANACROM_PASSWORD` instead, which
keeps them out of the config file entirely. `bin/uo config` shows what is set
without printing the password. Put `bin/` on your `PATH` to drop the prefix.

The default shard is `login.uoalive.com:2593`; `uo set host` changes it.

## Trying it without an account

A stub shard ships with the project. It implements just enough of the protocol
to log into, walk around, talk to and loot:

```bash
python3 tools/dev_server.py --port 2593 &
bin/uo set host 127.0.0.1
bin/uo start && bin/uo login && bin/uo status
```

The integration tests drive the real client against that stub, so `python3 -m
unittest discover -s tests` exercises the whole pipeline end to end.

## How it fits together

```
 shard  ──TCP──▶  net/connection.py   framing, capture
                  protocol/huffman.py decompression
                  net/login.py        seed → account → relay → game → character
                        │
                        ▼
                  world/handlers.py   packets ──▶ world/state.py
                        │
                        ▼
                  client.py           actions, movement, waiting
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
  daemon.py  ◀── unix socket ──  cli.py  (the `uo` command)
  holds the connection                   one shot per invocation
  runs scripts in the game loop
```

The daemon owns the connection and the world; the CLI is stateless and talks to
it over a unix socket. Commands run one at a time inside the game loop, so
nothing touches world state concurrently -- except `uo stop`, which is answered
on the listener thread precisely so it can interrupt a walk or a script that is
already running.

The code book that decompresses server traffic is generated, not transcribed:
`tools/gen_huffman.py` reads it out of the ModernUO server source vendored in
`reference/` and emits `anacrom/protocol/_codebook.py`.

## Scripting

A command per swing is too slow for combat. Loops go in Python and run inside
the game loop, next to the connection:

```bash
bin/uo script scripts/combat.py 12 35 3      # radius, flee %, kill limit
bin/uo script scripts/scout.py 1500 1600 2   # walk, stopping on anything hostile
bin/uo script scripts/follow.py 0x00111111
```

Scripts get `client`, `world`, `player`, `args`, `daemon` and `check_interrupt`
in scope. Call `check_interrupt()` in any loop so `uo stop` can cut it short.

## Letting Jev play

`uo jev` hands the controls to [Jev](https://docs.typesafe.ai/), TypeSafe's
System One model: once a second it describes the moment in words and gets
back a typed decision -- fight, flee, heal, loot or wait, and which creature
to hit -- each with a calibrated confidence.

```bash
export TYPESAFE_API_KEY=...           # or: bin/uo set typesafe_api_key ...
bin/uo jev --dry-run --ticks 5        # watch what it would do
bin/uo jev --seconds 300 --flee 30    # let it play for five minutes
```

Jev makes the judgement calls and code does the rest. Health and distance
reach it as words ("badly wounded", "adjacent"), not numbers. It is only
offered the actions that are possible right now, and only red and orange names,
or whatever swung first, count as targets. Two things never reach it: dropping
under `--flee` percent with a threat near means running, and an answer below
the confidence bar for its action means waiting (or running, if Jev also rates
the danger serious). When someone speaks to the character, the autopilot stops
and prints what was said, because Jev picks between options and cannot write a
reply.

Every decision is appended to `~/.anacrom/jev.jsonl` with the state, the full
probability distributions and the confidence, which is what you tune the bars
in `anacrom/autopilot.py` against. `uo stop` ends a run early; `uo set
jev_model jev-1.13.0` pins a version once the bars are tuned.

## Places

There is no map data, so the client keeps its own gazetteer:

```bash
bin/uo place here "Britain bank" --kind bank
bin/uo place near --kind bank
bin/uo place go "Britain bank"
```

`tools/poi.py` queries the same store without a running client.

## What it does not do

- **No map or tile data.** `walk_to` heads for the target and sidesteps what it
  bumps into, which is enough in open ground and gets stuck against long walls.
  Real pathfinding needs `map*.mul` and `tiledata.mul` from a UO install.
- **No cliloc string table.** Localised server messages show their arguments and
  the numeric id rather than the English sentence. The arguments carry the part
  that matters (names, amounts), so the journal stays readable.
- **No encryption.** Like ClassicUO's default, it connects unencrypted, which is
  what free shards accept.
- **One character at a time.** One daemon holds one connection.

## Rules

Automating play is against the rules on many shards. Check the rules of the
shard you connect to before running anything unattended -- this client makes no
attempt to hide what it is.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

71 tests: compression round-trips, framing and length-table behaviour, every
packet handler against bytes built by hand, the Jev client against a fake
TypeSafe, the autopilot's judgement, and an end-to-end run against the stub
shard.
