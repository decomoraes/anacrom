# anacrom

A game server and a client for **agentic gaming**: an AI agent connects, is handed a character, and plays it.

> **Status: design stage.** Nothing here is implemented yet. This README is the plan — architecture, protocol shape, and milestones. Everything marked *proposed* is open to change.

## The idea

One character, one agent. The agent does not script the world or call privileged admin APIs — it plays the game the way a player does: it receives a view of the world from where its character stands, and it sends actions the character could actually perform. Everything an agent knows comes through that channel, and everything it does goes back through it.

That constraint is the whole point. It makes runs comparable, makes behaviour legible, and keeps the server authoritative over a client it does not trust.

Three consequences shape the design:

- **Perception is bounded.** The agent sees what the character can see — range-limited, occlusion-aware, no global state dump. An agent that wants to know what's over the hill has to walk there.
- **Action is bounded.** Actions are the character's verbs (move, look, take, use, attack, say) with the same cooldowns, ranges and failure modes a human would hit. The server rejects the rest.
- **Time is the server's.** The simulation runs on a fixed tick. An agent that thinks for eight seconds has simply stood still for eight seconds; the world does not wait for it.

## Architecture

```
┌──────────────┐        ┌─────────────────┐        ┌──────────────────┐
│  Agent       │◄──────►│  Agent Gateway  │◄──────►│  Game Server     │
│  (LLM loop)  │  JSON  │  session, auth, │  in-   │  authoritative   │
│              │   /ws  │  rate limit,    │  proc  │  sim, fixed tick │
│  client SDK  │        │  obs encoding   │        │  world state     │
└──────────────┘        └─────────────────┘        └──────────────────┘
                                 ▲
                                 │ read-only
                        ┌────────┴────────┐
                        │  Spectator UI   │  humans watch; they do not play
                        └─────────────────┘
```

**Game server** — authoritative simulation. Fixed-tick loop, single-threaded game logic, all state owned by the loop. Agents are clients with no special powers.

**Agent gateway** — the seam between the simulation and the agents. Owns sessions and character binding, enforces the per-agent action budget, and turns raw world state into the observation an agent actually receives (this is where perception limits are applied, not in the agent).

**Client** — the SDK an agent author writes against: connect, receive observations, submit actions, handle rejections. Deliberately thin. The interesting behaviour belongs in the agent, not the client.

**Spectator UI** — a read-only view so a human can watch what the agents are doing. Strictly an observer; it has no action path into the server.

## Protocol sketch

*Proposed:* JSON over WebSocket, one connection per agent session, versioned envelopes. Readable by a language model without a decoding step, and trivially loggable for replay.

Server → agent, once per tick the character can act on:

```jsonc
{
  "type": "observation",
  "tick": 10432,
  "character": { "id": "ch_7a1", "hp": 41, "max_hp": 60, "pos": [112, 87], "facing": "n" },
  "vision": [
    { "kind": "mobile", "id": "mo_22", "name": "a giant rat", "pos": [114, 85], "hostile": true },
    { "kind": "item",   "id": "it_91", "name": "a wooden chest", "pos": [110, 88] }
  ],
  "inventory": [ { "id": "it_04", "name": "a dagger", "equipped": true } ],
  "events": [ { "kind": "damaged", "by": "mo_22", "amount": 6 } ],
  "budget": { "actions_left": 1, "deadline_ms": 400 }
}
```

Agent → server:

```jsonc
{ "type": "action", "tick": 10432, "action": "attack", "target": "mo_22" }
```

Every action gets an explicit `result` — `ok`, or a `rejected` with a machine-readable reason (`out_of_range`, `on_cooldown`, `unknown_target`, `dead`). Agents learn the rules from rejections rather than from a rulebook, so the reasons have to be honest and specific.

## Repo layout (proposed)

```
server/       authoritative simulation and tick loop
gateway/      agent sessions, observation encoding, action validation
client/       agent-facing SDK
spectator/    read-only viewer
protocol/     shared message schemas, the source of truth for both sides
agents/       example agents (scripted baseline + LLM-driven)
reference/    vendored read-only reference material, not a dependency
```

## Roadmap

| Milestone | What "done" means |
|---|---|
| **M0 — Protocol** | Message schemas exist and are versioned; both sides generate from them. |
| **M1 — Walking skeleton** | Server ticks a tiny world. One agent connects, walks a character, and moves. |
| **M2 — Perception** | Range and occlusion are enforced server-side; an agent cannot see what the character cannot. |
| **M3 — Interaction** | Items, inventory, combat. Rejections carry real reasons. |
| **M4 — Multi-agent** | Several agents in one world at once, each on its own budget. |
| **M5 — Replay & eval** | Every session is recorded and replayable; runs can be scored and compared. |

## Reference material

`reference/modern_uo` is a vendored copy of [ModernUO](https://github.com/modernuo/ModernUO), a .NET Ultima Online server emulator, kept here to study how a mature shard handles the problems this project will hit: a single-threaded authoritative game loop, spatial queries instead of global iteration, timers, world serialization, and packet-level networking. See its `dev-docs/` — `threading-model.md`, `networking-packets.md` and `serialization.md` are the ones worth reading first.

It is **reference only**: read it, don't build on it, and don't edit it.

## Open questions

- **Stack.** ModernUO is .NET, but nothing commits this project to it. Server language and runtime are undecided.
- **Turn discipline.** Does a slow agent simply lose ticks (real-time), or does the world block for it (turn-based)? Real-time is the assumption above; the eval story may argue for a lockstep mode too.
- **Observation format.** Structured JSON, prose, or both? Prose is easier for a language model to read and much harder to diff and score.
- **Tool surface.** How many verbs before an agent stops choosing well — and is the answer to add memory rather than remove verbs?
- **Trust.** Is an agent ever allowed a capability a human player is not?
