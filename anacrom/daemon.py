"""A long-lived process that holds the connection, and a socket to drive it.

The game loop owns the world: commands arrive on a queue and run between pumps,
one at a time, so nothing ever touches world state concurrently.  One exception
is deliberate -- ``stop`` and ``ping`` are answered on the listener thread the
moment they arrive, so a runaway walk or script can be cut short while it is
still running.
"""
from __future__ import annotations

import io
import json
import os
import queue
import socket
import sys
import threading
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path

from .client import Client, Config, NotConnected
from .net.login import LoginError
from .world.state import LAYERS, distance

DEFAULT_SOCKET = os.environ.get(
    "ANACROM_SOCKET", str(Path.home() / ".anacrom" / "anacrom.sock")
)

COMMANDS: dict[str, callable] = {}
OUT_OF_BAND = {"stop", "ping", "shutdown"}


def command(name: str):
    def register(func):
        COMMANDS[name] = func
        return func
    return register


class Interrupted(Exception):
    """Raised inside a long action when the player asked us to stop."""


class Daemon:
    def __init__(self, config: Config, socket_path: str = DEFAULT_SOCKET) -> None:
        self.config = config
        self.socket_path = socket_path
        self.client = Client(config)
        self.requests: queue.Queue = queue.Queue()
        self.running = True
        self.interrupted = False
        self.script_thread: threading.Thread | None = None
        self.started_at = time.time()
        self._listener: socket.socket | None = None

    # -- interrupt plumbing ------------------------------------------------

    def check_interrupt(self) -> None:
        if self.interrupted:
            raise Interrupted("stopped by request")

    def _guarded_step(self, result: dict) -> bool:
        return not self.interrupted

    # -- socket ------------------------------------------------------------

    def serve(self) -> None:
        path = Path(self.socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()

        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(path))
        self._listener.listen(16)
        self._listener.settimeout(0.5)

        threading.Thread(target=self._accept_loop, daemon=True).start()
        self._game_loop()

    def _accept_loop(self) -> None:
        while self.running:
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(600)
            buffer = b""
            try:
                while b"\n" not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        return
                    buffer += chunk
            except (socket.timeout, OSError):
                return

            try:
                request = json.loads(buffer.split(b"\n", 1)[0])
            except json.JSONDecodeError as exc:
                self._reply(conn, {"ok": False, "error": f"bad request: {exc}"})
                return

            name = request.get("command", "")
            if name in OUT_OF_BAND:
                self._reply(conn, self._run_out_of_band(name, request.get("args", {})))
                return

            done = threading.Event()
            box: dict = {}
            self.requests.put((name, request.get("args", {}), box, done))
            if not done.wait(timeout=request.get("timeout", 300)):
                self._reply(conn, {"ok": False, "error": "command timed out in the queue"})
                return
            self._reply(conn, box.get("response", {"ok": False, "error": "no response"}))

    @staticmethod
    def _reply(conn: socket.socket, payload: dict) -> None:
        try:
            conn.sendall(json.dumps(payload).encode() + b"\n")
        except OSError:
            pass

    def _run_out_of_band(self, name: str, args: dict) -> dict:
        if name == "ping":
            return {"ok": True, "uptime": round(time.time() - self.started_at, 1),
                    "connected": self.client.connected}
        if name == "stop":
            self.interrupted = True
            return {"ok": True, "message": "interrupt raised; the current action will stop"}
        if name == "shutdown":
            self.interrupted = True
            self.running = False
            return {"ok": True, "message": "shutting down"}
        return {"ok": False, "error": f"unknown command {name}"}

    # -- the loop ----------------------------------------------------------

    def _game_loop(self) -> None:
        while self.running:
            try:
                if self.client.connected:
                    self.client.pump(0.02)
                else:
                    time.sleep(0.02)
            except Exception as exc:                     # a dropped shard, usually
                self.client.world.warn(f"connection error: {exc}")
                # The world is rebuilt on the next login, so keep the reason where it lasts.
                print(f"{time.strftime('%H:%M:%S')} connection error: {exc}", file=sys.stderr, flush=True)
                if self.client.connection is not None:
                    self.client.connection.close()
                self.client.connection = None

            try:
                name, args, box, done = self.requests.get_nowait()
            except queue.Empty:
                continue

            self.interrupted = False
            try:
                handler = COMMANDS.get(name)
                if handler is None:
                    box["response"] = {"ok": False, "error": f"unknown command {name!r}"}
                else:
                    result = handler(self, **args)
                    box["response"] = {"ok": True, "result": result}
            except Interrupted as exc:
                box["response"] = {"ok": False, "error": str(exc), "interrupted": True}
            except (NotConnected, LoginError) as exc:
                box["response"] = {"ok": False, "error": str(exc)}
            except TypeError as exc:
                box["response"] = {"ok": False, "error": f"bad arguments: {exc}"}
            except Exception as exc:
                box["response"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=6),
                }
            finally:
                self.interrupted = False
                done.set()

        if self._listener is not None:
            self._listener.close()
        Path(self.socket_path).unlink(missing_ok=True)
        self.client.disconnect()


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _require_connection(daemon: Daemon) -> Client:
    if not daemon.client.connected:
        raise NotConnected("not logged in -- run: uo login")
    return daemon.client


@command("login")
def cmd_login(daemon: Daemon, account: str = "", password: str = "",
              character: str | int | None = None, host: str = "", port: int = 0) -> dict:
    if daemon.client.connected:
        return {"already_connected": True, "player": daemon.client.world.player.name}

    config = daemon.config
    if account:
        config.account = account
    if password:
        config.password = password
    if character is not None:
        config.character = character
    if host:
        config.host = host
    if port:
        config.port = port

    daemon.client = Client(config)
    result = daemon.client.connect()
    return {
        "character": result.character_name,
        "characters": result.characters,
        "shard": result.servers[0].name if result.servers else "",
        "position": list(result.position),
        "serial": result.player_serial,
    }


@command("logout")
def cmd_logout(daemon: Daemon) -> dict:
    daemon.client.disconnect()
    return {"disconnected": True}


@command("status")
def cmd_status(daemon: Daemon, radius: int = 18) -> dict:
    client = _require_connection(daemon)
    client.pump(0.1)
    return client.snapshot(radius)


@command("look")
def cmd_look(daemon: Daemon, radius: int = 18, serial: int | None = None) -> dict:
    client = _require_connection(daemon)
    if serial is not None:
        client.look(serial)
        client.request_properties(serial)
        client.pump(0.8)
        entity = client.world.entity(serial)
        if entity is None:
            return {"serial": serial, "known": False}
        payload = {
            "serial": serial,
            "name": entity.name,
            "position": list(entity.position),
            "distance": client.world.distance_to(serial),
        }
        properties = getattr(entity, "properties", None)
        if properties:
            payload["properties"] = properties
        return payload

    snapshot = client.snapshot(radius)
    return {
        "position": snapshot["player"]["position"],
        "mobiles": snapshot["mobiles"],
        "ground_items": snapshot["ground_items"],
    }


@command("journal")
def cmd_journal(daemon: Daemon, count: int = 20, contains: str = "") -> dict:
    client = daemon.client
    entries = list(client.world.journal)
    if contains:
        needle = contains.casefold()
        entries = [e for e in entries if needle in e.text.casefold()]
    return {"entries": [e.format() for e in entries[-count:]]}


@command("say")
def cmd_say(daemon: Daemon, text: str, kind: str = "say") -> dict:
    client = _require_connection(daemon)
    {"say": client.say, "emote": client.emote,
     "whisper": client.whisper, "yell": client.yell}[kind](text)
    client.pump(0.4)
    return {"said": text, "kind": kind}


@command("step")
def cmd_step(daemon: Daemon, direction: str, run: bool | None = None) -> dict:
    client = _require_connection(daemon)
    daemon.check_interrupt()
    return client.step(direction, run=run)


@command("walk")
def cmd_walk(daemon: Daemon, x: int, y: int, run: bool | None = None,
             stop_within: int = 0, max_steps: int = 200) -> dict:
    client = _require_connection(daemon)
    result = client.walk_to(x, y, run=run, stop_within=stop_within,
                            max_steps=max_steps, on_step=daemon._guarded_step)
    daemon.check_interrupt()
    return result




@command("goto")
def cmd_goto(daemon: Daemon, serial: int, stop_within: int = 1,
             run: bool | None = None) -> dict:
    client = _require_connection(daemon)
    entity = client.world.entity(serial)
    if entity is None:
        return {"arrived": False, "stopped": f"0x{serial:08X} is not in view"}
    result = client.walk_to(entity.x, entity.y, run=run, stop_within=stop_within,
                            on_step=daemon._guarded_step)
    daemon.check_interrupt()
    return result




@command("attack")
def cmd_attack(daemon: Daemon, serial: int) -> dict:
    client = _require_connection(daemon)
    client.attack(serial)
    client.pump(0.5)
    target = client.world.mobiles.get(serial)
    return {
        "attacking": serial,
        "name": target.name if target else "",
        "health_percent": target.health_percent if target else 0,
    }


@command("warmode")
def cmd_warmode(daemon: Daemon, enabled: bool = True) -> dict:
    client = _require_connection(daemon)
    client.set_war_mode(enabled)
    client.pump(0.3)
    return {"war_mode": client.world.player.war_mode}


@command("use")
def cmd_use(daemon: Daemon, serial: int) -> dict:
    client = _require_connection(daemon)
    client.use(serial)
    client.pump(0.6)
    return {"used": serial, "target_request": client.world.target.active}


@command("contents")
def cmd_contents(daemon: Daemon, serial: int, refresh: bool = True) -> dict:
    client = _require_connection(daemon)
    items = client.open_container(serial) if refresh else client.world.contents_of(serial)
    for item in items:
        client.request_properties(item.serial)
    client.pump(0.8)
    items = client.world.contents_of(serial)
    return {
        "container": serial,
        "items": [
            {"serial": i.serial, "name": i.name or f"item ({i.graphic:#06x})",
             "amount": i.amount, "graphic": i.graphic, "layer": i.layer_name}
            for i in items
        ],
    }


@command("backpack")
def cmd_backpack(daemon: Daemon) -> dict:
    client = _require_connection(daemon)
    backpack = client.world.player.backpack
    if not backpack:
        return {"error": "backpack not known yet; try: uo status"}
    return cmd_contents(daemon, backpack)


@command("grab")
def cmd_grab(daemon: Daemon, serial: int, amount: int = 0, container: int = 0) -> dict:
    client = _require_connection(daemon)
    destination = container or client.world.player.backpack
    if not destination:
        return {"error": "no backpack known to put it in"}
    item = client.world.items.get(serial)
    client.move_item(serial, destination, amount)
    return {
        "moved": serial,
        "name": item.name if item else "",
        "into": destination,
    }


@command("drop")
def cmd_drop(daemon: Daemon, serial: int, container: int = 0,
             x: int = 0xFFFF, y: int = 0xFFFF, z: int = 0) -> dict:
    client = _require_connection(daemon)
    client.drop(serial, container, x, y, z)
    client.pump(0.5)
    return {"dropped": serial, "into": container}


@command("equip")
def cmd_equip(daemon: Daemon, serial: int, layer: str | int = "right_hand") -> dict:
    client = _require_connection(daemon)
    if isinstance(layer, str):
        wanted = [k for k, v in LAYERS.items() if v == layer]
        if not wanted:
            return {"error": f"unknown layer {layer!r}; try one of {sorted(set(LAYERS.values()))}"}
        layer = wanted[0]
    client.equip(serial, layer)
    client.pump(0.6)
    return {"equipped": serial, "layer": LAYERS.get(layer, layer)}


@command("target")
def cmd_target(daemon: Daemon, serial: int | None = None, x: int | None = None,
               y: int | None = None, z: int = 0, cancel: bool = False,
               at_self: bool = False, wait: float = 3.0) -> dict:
    client = _require_connection(daemon)
    if not client.world.target.active:
        client.wait_for_target(wait)
    if cancel:
        return {"cancelled": client.cancel_target()}
    if at_self:
        return {"targeted": "self", "sent": client.target_self()}
    if serial is not None:
        return {"targeted": serial, "sent": client.target(serial)}
    if x is not None and y is not None:
        return {"targeted": [x, y, z], "sent": client.target_ground(x, y, z)}
    return {"error": "give a serial, or x and y, or cancel"}


@command("skill")
def cmd_skill(daemon: Daemon, name: str) -> dict:
    client = _require_connection(daemon)
    client.use_skill(name)
    client.pump(0.8)
    return {"used_skill": name, "target_request": client.world.target.active}


@command("skills")
def cmd_skills(daemon: Daemon, trained_only: bool = True) -> dict:
    client = _require_connection(daemon)
    if not client.world.skills:
        client.request_skills()
        client.wait_for(lambda: bool(client.world.skills), 3.0)
    skills = client.world.skills
    if trained_only:
        skills = {k: v for k, v in skills.items() if v["value"] > 0}
    return {"skills": dict(sorted(skills.items(), key=lambda kv: -kv[1]["value"]))}


@command("cast")
def cmd_cast(daemon: Daemon, spell: int, target: int | None = None) -> dict:
    client = _require_connection(daemon)
    delivered = client.cast(spell, target)
    client.pump(0.5)
    return {"spell": spell, "targeted": delivered if target is not None else None}


@command("gump")
def cmd_gump(daemon: Daemon, serial: int | None = None, button: int | None = None,
             entries: dict | None = None) -> dict:
    client = _require_connection(daemon)
    if serial is None:
        return {
            "gumps": [
                {"serial": g.serial, "gump_id": g.gump_id,
                 "buttons": g.buttons(), "lines": g.lines}
                for g in client.world.gumps.values()
            ]
        }
    client.gump_reply(serial, button or 0,
                      entries={int(k): v for k, v in (entries or {}).items()})
    client.pump(0.8)
    return {"replied": serial, "button": button or 0}


@command("vendor")
def cmd_vendor(daemon: Daemon, serial: int) -> dict:
    client = _require_connection(daemon)
    return {
        "vendor": serial,
        "items": [
            {"serial": v.serial, "name": v.name, "price": v.price, "amount": v.amount}
            for v in client.world.vendor_items.get(serial, [])
        ],
    }


@command("buy")
def cmd_buy(daemon: Daemon, vendor: int, purchases: list) -> dict:
    client = _require_connection(daemon)
    client.buy(vendor, [(int(s), int(a)) for s, a in purchases])
    client.pump(0.8)
    return {"bought": purchases, "gold": client.world.player.gold}


@command("sell")
def cmd_sell(daemon: Daemon, vendor: int, sales: list) -> dict:
    client = _require_connection(daemon)
    client.sell(vendor, [(int(s), int(a)) for s, a in sales])
    client.pump(0.8)
    return {"sold": sales, "gold": client.world.player.gold}


@command("wait")
def cmd_wait(daemon: Daemon, seconds: float = 1.0, journal: str = "") -> dict:
    client = daemon.client
    deadline = time.time() + seconds
    needle = journal.casefold()
    seen = len(client.world.journal)
    while time.time() < deadline:
        daemon.check_interrupt()
        client.pump(0.05)
        if needle:
            for entry in list(client.world.journal)[seen:]:
                if needle in entry.text.casefold():
                    return {"matched": entry.format(), "waited": round(
                        seconds - (deadline - time.time()), 2)}
            seen = len(client.world.journal)
    return {"matched": None, "waited": seconds}


@command("raw")
def cmd_raw(daemon: Daemon, hex_data: str) -> dict:
    """Escape hatch: send bytes we have not wrapped in a command yet."""
    client = _require_connection(daemon)
    payload = bytes.fromhex(hex_data.replace(" ", ""))
    client.send(payload)
    client.pump(0.4)
    return {"sent": payload.hex(" ")}


@command("script")
def cmd_script(daemon: Daemon, path: str = "", code: str = "",
               args: list | None = None) -> dict:
    """Run Python in the game loop, with the live client in scope.

    This is the escape hatch that matters: deciding one swing at a time over a
    command line is too slow for combat, so loops belong here, next to the
    connection, where they can be interrupted.
    """
    client = _require_connection(daemon)
    source = code
    origin = "<inline>"
    if path:
        source = Path(path).read_text(encoding="utf-8")
        origin = path

    namespace = {
        "client": client,
        "world": client.world,
        "player": client.world.player,
        "daemon": daemon,
        "args": args or [],
        "check_interrupt": daemon.check_interrupt,
        "distance": distance,
        "__name__": "__anacrom_script__",
    }

    captured = io.StringIO()
    started = time.time()
    try:
        with redirect_stdout(captured):
            exec(compile(source, origin, "exec"), namespace)   # noqa: S102
    except Interrupted:
        return {"script": origin, "interrupted": True,
                "output": captured.getvalue(), "seconds": round(time.time() - started, 2)}
    return {
        "script": origin,
        "output": captured.getvalue(),
        "returned": namespace.get("result"),
        "seconds": round(time.time() - started, 2),
    }


def main(argv: list[str] | None = None) -> int:
    from .config import load_config

    argv = sys.argv[1:] if argv is None else argv
    socket_path = argv[0] if argv else DEFAULT_SOCKET
    config = load_config()
    Daemon(config, socket_path).serve()
    return 0



# --------------------------------------------------------------------------
# places
# --------------------------------------------------------------------------

@command("place_here")
def cmd_place_here(daemon: Daemon, name: str, kind: str = "note", notes: str = "") -> dict:
    from .poi import Place, Places

    client = _require_connection(daemon)
    player = client.world.player
    place = Places().add(
        Place(name=name, x=player.x, y=player.y, z=player.z,
              map=player.map, kind=kind, notes=notes)
    )
    return {"recorded": place.summary()}


@command("place_add")
def cmd_place_add(daemon: Daemon, name: str, x: int, y: int, z: int = 0,
                  map: int = 0, kind: str = "note", notes: str = "") -> dict:
    from .poi import Place, Places

    place = Places().add(Place(name=name, x=x, y=y, z=z, map=map, kind=kind, notes=notes))
    return {"recorded": place.summary()}


@command("place_list")
def cmd_place_list(daemon: Daemon, kind: str = "") -> dict:
    from .poi import Places

    places = Places().search("", kind)
    origin = None
    if daemon.client.connected:
        origin = (daemon.client.world.player.x, daemon.client.world.player.y)
    return {"places": [p.summary(origin) for p in places]}


@command("place_near")
def cmd_place_near(daemon: Daemon, kind: str = "", limit: int = 5) -> dict:
    from .poi import Places

    client = _require_connection(daemon)
    player = client.world.player
    origin = (player.x, player.y)
    found = Places().nearest(origin, kind, player.map, limit)
    return {"places": [p.summary(origin) for p in found]}


@command("place_find")
def cmd_place_find(daemon: Daemon, text: str, kind: str = "") -> dict:
    from .poi import Places

    origin = None
    if daemon.client.connected:
        origin = (daemon.client.world.player.x, daemon.client.world.player.y)
    return {"places": [p.summary(origin) for p in Places().search(text, kind)]}


@command("place_remove")
def cmd_place_remove(daemon: Daemon, name: str) -> dict:
    from .poi import Places

    return {"removed": Places().remove(name)}


@command("place_go")
def cmd_place_go(daemon: Daemon, name: str, stop_within: int = 1) -> dict:
    from .poi import Places

    client = _require_connection(daemon)
    matches = Places().search(name)
    if not matches:
        return {"error": f"no place matching {name!r}"}
    place = matches[0]
    if place.map != client.world.player.map:
        return {"error": f"{place.name} is on map {place.map}, we are on "
                         f"map {client.world.player.map}"}
    result = client.walk_to(place.x, place.y, stop_within=stop_within,
                            on_step=daemon._guarded_step)
    daemon.check_interrupt()
    result["place"] = place.summary()
    return result


# --------------------------------------------------------------------------
# jev
# --------------------------------------------------------------------------

@command("jev")
def cmd_jev(daemon: Daemon, seconds: float = 120.0, ticks: int = 0, radius: int = 12,
            flee_percent: int = 30, dry_run: bool = False) -> dict:
    """Hand the controls to Jev until time runs out, we are stopped, or spoken to."""
    from .autopilot import Autopilot, Policy
    from .config import CONFIG_DIR, load_jev_settings
    from .jev import Jev

    client = _require_connection(daemon)
    key, model = load_jev_settings()
    pilot = Autopilot(
        client, Jev(key, model=model),
        Policy(radius=radius, flee_percent=flee_percent),
        should_stop=lambda: daemon.interrupted,
        dry_run=dry_run,
        log_path=CONFIG_DIR / "jev.jsonl",
        creatures_path=CONFIG_DIR / "creatures.json",
    )
    started = time.time()
    summary = pilot.run(seconds=seconds, max_ticks=ticks)
    # Kept for `uo stats`: what a run earned, which a terminal scrollback forgets.
    if not dry_run and summary["ticks"]:
        from .stats import record_run
        record_run(CONFIG_DIR / "runs.jsonl", summary, started, client.world.player.name)
    return summary


@command("train")
def cmd_train(daemon: Daemon, seconds: float = 600.0, use_jev: bool = True,
              until: str = "") -> dict:
    """Train the free skills, with Jev judging whether the spot is safe."""
    from .config import load_jev_settings
    from .jev import Jev
    from .trainer import Trainer

    client = _require_connection(daemon)
    key, model = load_jev_settings()
    jev = Jev(key, model=model) if use_jev and key else None
    return Trainer(client, jev, should_stop=lambda: daemon.interrupted).run(
        seconds, until=until)


@command("play")
def cmd_play(daemon: Daemon, minutes: float = 60.0, hunt: bool = False,
             max_deaths: int = 2) -> dict:
    """Let Jev run the character: train, shop, rest and, if told, hunt."""
    from .config import CONFIG_DIR, load_jev_settings
    from .jev import Jev
    from .planner import Limits, Player

    client = _require_connection(daemon)
    key, model = load_jev_settings()
    player = Player(client, Jev(key, model=model), hunt_allowed=hunt,
                    limits=Limits(seconds=minutes * 60, max_deaths=max_deaths),
                    should_stop=lambda: daemon.interrupted)
    started = time.time()
    summary = player.run()
    from .stats import record_run
    record_run(CONFIG_DIR / "plays.jsonl", {k: v for k, v in summary.items() if k != "output"},
               started, client.world.player.name)
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
