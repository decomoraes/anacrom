# anacrom

Headless Ultima Online client. Python 3.10, standard library only, no build step.

## Layout

| Path | What lives there |
|---|---|
| `anacrom/protocol/` | Wire format: code book, framing, length table, reader/writer |
| `anacrom/net/` | Socket, compression toggle, login handshake |
| `anacrom/world/` | World model and the packet handlers that fill it |
| `anacrom/client.py` | Actions and movement -- the layer everything else calls |
| `anacrom/daemon.py` | Long-lived process, command registry, script host |
| `anacrom/jev.py` | TypeSafe Jev HTTP client (stdlib `urllib`, no SDK) |
| `anacrom/autopilot.py` | `uo jev`: world into words, Jev's answer into one action per tick |
| `anacrom/planner.py`, `anacrom/routines.py`, `anacrom/trainer.py` | `uo play` / `uo train`: Jev picks the next task; recovery, limits and the task routines |
| `anacrom/stats.py`, `anacrom/stats_page.py` | `uo stats`: totals from `runs.jsonl`, `jev.jsonl`, `creatures.json`, and the HTML page |
| `anacrom/world/mapdata.py`, `anacrom/world/movement.py` | Client map files read, and the server's step rules ported for route planning |
| `anacrom/protocol/speech.py` | Speech keyword ids NPCs answer to (`buy`, `train`, `bank`...) |
| `anacrom/cli.py` | The `uo` command |
| `tools/dev_server.py` | Stub shard used by the integration tests |
| `reference/modern_uo/` | Vendored server source, used as the protocol oracle |

## Rules that are easy to get wrong

1. **Packet lengths are a contract.** A wrong entry in `_FIXED` desyncs the whole
   stream. Unknown ids raise rather than guess; keep it that way. Lengths that
   vary with what the server thinks the client supports belong in `Profile`,
   not in the flat table.
2. **Compression starts at game login and nowhere else.** `enable_compression()`
   is called immediately after sending 0x91, mirroring where the server turns it
   on. Move it and everything after it is garbage.
3. **The server only acknowledges movement.** It sends 0x22 with a sequence, not
   a position. The client predicts its own step and lets 0x20/0x21 correct it.
   A move request in a direction you are not facing turns you instead of moving.
4. **Cliloc arguments are little-endian UTF-16**, unlike every other string in
   the protocol, which is big-endian. Both appear in `handlers.py`.
5. **World state is single-threaded.** Commands run inside the game loop, one at
   a time. Only `stop`, `ping` and `shutdown` are answered off-loop, and they
   only set a flag.
6. **New commands go after the command registry, never after `__main__`.**
   The daemon's module body runs top to bottom and `main()` blocks in `serve()`.

## Adding a packet

1. Add its length to `_FIXED` in `anacrom/protocol/packets.py` (`VAR` for a
   length field at offset 1). Check it against `reference/modern_uo`.
2. Write the handler in `anacrom/world/handlers.py` with `@handles(0xNN)`. The
   reader starts past the id and past the length field.
3. Add a test in `tests/test_anacrom.py` that builds the packet byte by byte --
   not through our own `Writer`, which would hide a matching mistake in both.

## Tests

```bash
python3 -m unittest discover -s tests
```

The integration tests start `tools/dev_server.py` in-process. When a change
breaks framing, they fail with the raw stream in the message; read it from the
last good packet forwards.
