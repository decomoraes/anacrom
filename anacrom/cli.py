"""``uo`` -- the command line you play from.

Output is plain text by default because that is what reads well in a terminal
and in an agent's context window; ``--json`` gives the same data verbatim for
scripts that would rather parse it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from .daemon import DEFAULT_SOCKET


class DaemonNotRunning(Exception):
    pass


def call(command: str, args: dict | None = None, timeout: float = 300.0,
         socket_path: str = DEFAULT_SOCKET) -> dict:
    if not Path(socket_path).exists():
        raise DaemonNotRunning(f"no daemon at {socket_path} -- run: uo start")

    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError):
        raise DaemonNotRunning(f"no daemon at {socket_path} -- run: uo start") from None

    with conn:
        payload = json.dumps({"command": command, "args": args or {},
                              "timeout": timeout}).encode() + b"\n"
        conn.sendall(payload)
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buffer += chunk

    if not buffer:
        raise DaemonNotRunning("daemon closed the connection without replying")
    return json.loads(buffer.split(b"\n", 1)[0])


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def _bar(current: int, maximum: int) -> str:
    if not maximum:
        return "?"
    return f"{current}/{maximum}"


def render_status(result: dict) -> str:
    player = result["player"]
    x, y, z = player["position"]
    lines = [
        f"{player['name'] or '(unnamed)'}  serial 0x{player['serial']:08X}"
        f"{'  [DEAD]' if player['dead'] else ''}"
        f"{'  [war]' if player['war_mode'] else ''}",
        f"  at ({x}, {y}, {z}) map {player['map']} facing {player['facing']}",
        f"  hp {_bar(*player['hits'])}   mana {_bar(*player['mana'])}"
        f"   stam {_bar(*player['stamina'])}",
        f"  str {player['stats']['str']}  dex {player['stats']['dex']}"
        f"  int {player['stats']['int']}  armor {player['armor']}",
        f"  gold {player['gold']}  weight {_bar(*player['weight'])}"
        f"  followers {_bar(*player['followers'])}",
    ]

    mobiles = result.get("mobiles", [])
    if mobiles:
        lines.append(f"  {len(mobiles)} mobiles nearby:")
        for mob in mobiles[:14]:
            flag = "!" if mob["hostile"] else " "
            health = f" {mob['health_percent']}%" if mob["health_percent"] else ""
            lines.append(
                f"   {flag} 0x{mob['serial']:08X} {mob['name'][:28]:<28}"
                f" {mob['distance']:>3} tiles {mob['direction']:<9} {mob['notoriety']}{health}"
            )

    items = result.get("ground_items", [])
    if items:
        lines.append(f"  {len(items)} items on the ground:")
        for item in items[:10]:
            amount = f" x{item['amount']}" if item["amount"] > 1 else ""
            lines.append(
                f"     0x{item['serial']:08X} {item['name'][:28]:<28}"
                f" {item['distance']:>3} tiles{amount}"
            )

    if result.get("target_request", {}).get("active"):
        lines.append("  ** the server is waiting for a target (uo target ...) **")

    for gump in result.get("open_gumps", []):
        lines.append(f"  gump 0x{gump['serial']:08X} id {gump['gump_id']}"
                     f" buttons {gump['buttons']}")
        for text in gump["lines"][:6]:
            lines.append(f"      | {text}")

    tail = result.get("journal_tail", [])
    if tail:
        lines.append("  recent:")
        lines.extend(f"     {line}" for line in tail)

    for warning in result.get("warnings", []):
        lines.append(f"  ! {warning}")

    return "\n".join(lines)


def render(command: str, result) -> str:
    if command in ("status",):
        return render_status(result)

    if command == "look" and "mobiles" in result:
        x, y, z = result["position"]
        lines = [f"standing at ({x}, {y}, {z})"]
        for mob in result["mobiles"]:
            flag = "!" if mob["hostile"] else " "
            health = f" {mob['health_percent']}%" if mob["health_percent"] else ""
            lines.append(
                f" {flag} 0x{mob['serial']:08X} {mob['name'][:28]:<28}"
                f" {mob['distance']:>3} tiles {mob['direction']:<9}"
                f" {mob['notoriety']}{health}"
            )
        if result["ground_items"]:
            lines.append("items on the ground:")
            for item in result["ground_items"]:
                amount = f" x{item['amount']}" if item["amount"] > 1 else ""
                lines.append(
                    f"   0x{item['serial']:08X} {item['name'][:28]:<28}"
                    f" {item['distance']:>3} tiles{amount}"
                )
        return "\n".join(lines)

    if command == "look":
        rows = [f"0x{result['serial']:08X} {result.get('name', '')}"]
        if result.get("distance") is not None:
            rows.append(f"  {result['distance']} tiles away at {result.get('position')}")
        for line in result.get("properties", []):
            rows.append(f"  | {line}")
        return "\n".join(rows)

    if command == "journal":
        return "\n".join(result["entries"]) or "(journal is empty)"

    if command in ("contents", "backpack") and "items" in result:
        if not result["items"]:
            return "(empty)"
        rows = [f"container 0x{result['container']:08X}: {len(result['items'])} items"]
        for item in result["items"]:
            amount = f" x{item['amount']}" if item["amount"] > 1 else ""
            layer = f" [{item['layer']}]" if item.get("layer") else ""
            rows.append(f"  0x{item['serial']:08X} {item['name']}{amount}{layer}")
        return "\n".join(rows)

    if command == "skills" and "skills" in result:
        rows = []
        for name, values in result["skills"].items():
            cap = f" / {values['cap']:.1f}" if values["cap"] else ""
            rows.append(f"  {name:<18} {values['value']:>6.1f}{cap}  ({values['lock']})")
        return "\n".join(rows) or "(no skills reported yet)"

    if command.startswith("place_") and "places" in result:
        return "\n".join(f"  {line}" for line in result["places"]) or "(nothing recorded)"

    if command == "script":
        output = result.get("output", "")
        tail = f"\n[{result['seconds']}s]" if "seconds" in result else ""
        if result.get("interrupted"):
            tail = f"\n[interrupted after {result['seconds']}s]"
        if result.get("returned") is not None:
            tail = f"\nresult: {result['returned']}{tail}"
        return (output.rstrip() + tail).strip() or "(no output)"

    if command == "jev" and "stopped" in result:
        rows = [result["output"].rstrip()] if result.get("output") else []
        actions = ", ".join(f"{k} {v}" for k, v in result["actions"].items()) or "none"
        rows.append(f"stopped: {result['stopped']} after {result['seconds']}s,"
                    f" {result['ticks']} ticks ({actions})")
        if result["jev_calls"]:
            rows.append(f"jev: {result['jev_calls']} calls to {result['model']},"
                        f" median {result['median_latency_ms']} ms,"
                        f" {result['input_tokens']} tokens ~ ${result['cost_usd']:.4f}")
        gold = result.get("gold")
        rows.append(f"hp {_bar(*result['health'])}"
                    + (f"   gold {gold[0]} -> {gold[1]}" if gold else ""))
        for line in result.get("speech", []):
            rows.append(f"  > {line}")
        if result.get("dry_run"):
            rows.append("(dry run: nothing was done)")
        if result.get("log"):
            rows.append(f"decisions logged to {result['log']}")
        return "\n".join(rows)

    if isinstance(result, dict):
        return "\n".join(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}"
                         for k, v in result.items())
    return str(result)


# --------------------------------------------------------------------------
# daemon lifecycle
# --------------------------------------------------------------------------

def start_daemon(socket_path: str, wait: float = 10.0) -> str:
    if Path(socket_path).exists():
        try:
            call("ping", socket_path=socket_path, timeout=3)
            return "already running"
        except Exception:
            # A socket file with nobody behind it: an old process that died, or
            # one still on its way out that will unlink this itself.
            deadline = time.time() + 3
            while Path(socket_path).exists() and time.time() < deadline:
                time.sleep(0.1)
            Path(socket_path).unlink(missing_ok=True)

    log_path = Path(socket_path).with_suffix(".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        subprocess.Popen(
            [sys.executable, "-m", "anacrom.daemon", socket_path],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )

    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            call("ping", socket_path=socket_path, timeout=2)
            return f"started (log: {log_path})"
        except Exception:
            time.sleep(0.2)
    return f"daemon did not come up in {wait}s -- see {log_path}"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uo", description="Play Ultima Online from the terminal.")
    parser.add_argument("--json", action="store_true", help="print the raw result")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=300.0, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_text, **kwargs):
        return sub.add_parser(name, help=help_text, **kwargs)

    add("start", "start the background client process")
    add("shutdown", "stop the background client process")
    add("ping", "check the client process is alive")
    add("stop", "interrupt whatever action is running right now")
    add("config", "show the shard and account that are configured")

    p = add("set", "store a config value (account, password, character, host)")
    p.add_argument("key")
    p.add_argument("value")

    p = add("login", "log in and enter the world")
    p.add_argument("--account", default="")
    p.add_argument("--password", default="")
    p.add_argument("--character", default=None)
    add("logout", "leave the world")

    p = add("status", "where we are, how we are, what is near")
    p.add_argument("--radius", type=int, default=18)

    p = add("look", "look around, or at one thing")
    p.add_argument("serial", nargs="?", default=None)
    p.add_argument("--radius", type=int, default=18)

    p = add("journal", "recent speech and system messages")
    p.add_argument("count", nargs="?", type=int, default=20)
    p.add_argument("--contains", default="")

    p = add("say", "speak out loud")
    p.add_argument("text", nargs="+")
    p.add_argument("--kind", choices=["say", "emote", "whisper", "yell"], default="say")

    p = add("step", "one step in a direction")
    p.add_argument("direction")
    p.add_argument("--walk", action="store_true", help="walk instead of run")

    p = add("walk", "walk to a coordinate")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--within", type=int, default=0)
    p.add_argument("--max-steps", type=int, default=200)
    p.add_argument("--walk", action="store_true")

    p = add("goto", "walk to something we can see")
    p.add_argument("serial")
    p.add_argument("--within", type=int, default=1)

    p = add("attack", "attack a mobile")
    p.add_argument("serial")

    p = add("warmode", "enter or leave war mode")
    p.add_argument("state", nargs="?", choices=["on", "off"], default="on")

    p = add("use", "double click something")
    p.add_argument("serial")

    p = add("contents", "open a container and list it")
    p.add_argument("serial")
    add("backpack", "list our own backpack")

    p = add("grab", "move something into our backpack")
    p.add_argument("serial")
    p.add_argument("amount", nargs="?", type=int, default=0)
    p.add_argument("--into", default="0")

    p = add("drop", "drop something on the ground or into a container")
    p.add_argument("serial")
    p.add_argument("--into", default="0")

    p = add("equip", "wear or wield something")
    p.add_argument("serial")
    p.add_argument("layer", nargs="?", default="right_hand")

    p = add("target", "answer a target cursor")
    p.add_argument("what", nargs="?", default=None,
                   help="a serial, 'self', 'cancel', or 'x y'")
    p.add_argument("y", nargs="?", default=None)

    p = add("skill", "use a skill by name")
    p.add_argument("name", nargs="+")
    p = add("skills", "list our skills")
    p.add_argument("--all", action="store_true")

    p = add("cast", "cast a spell by id, optionally at a target")
    p.add_argument("spell", type=int)
    p.add_argument("target", nargs="?", default=None)

    p = add("gump", "list open dialogs, or press a button")
    p.add_argument("serial", nargs="?", default=None)
    p.add_argument("button", nargs="?", type=int, default=None)

    p = add("vendor", "show a vendor's list")
    p.add_argument("serial")

    p = add("buy", "buy from a vendor: serial amount [serial amount ...]")
    p.add_argument("vendor")
    p.add_argument("pairs", nargs="+")

    p = add("sell", "sell to a vendor: serial amount [serial amount ...]")
    p.add_argument("vendor")
    p.add_argument("pairs", nargs="+")

    p = add("wait", "wait, optionally until something is said")
    p.add_argument("seconds", nargs="?", type=float, default=1.0)
    p.add_argument("--journal", default="")

    p = add("script", "run a python script inside the client")
    p.add_argument("path")
    p.add_argument("args", nargs="*")

    p = add("jev", "let Jev decide: fight, flee, heal and loot on its own")
    p.add_argument("--seconds", type=float, default=120.0, help="how long to play")
    p.add_argument("--ticks", type=int, default=0, help="stop after this many decisions")
    p.add_argument("--radius", type=int, default=12, help="how far to look")
    p.add_argument("--flee", type=int, default=30,
                   help="health percent to run at, without asking")
    p.add_argument("--dry-run", action="store_true", help="decide, but do nothing")

    p = add("place", "remember and revisit places")
    p.add_argument("action", choices=["here", "add", "list", "near", "find", "remove", "go"])
    p.add_argument("rest", nargs="*")
    p.add_argument("--kind", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--within", type=int, default=1)

    p = add("raw", "send raw packet bytes (hex)")
    p.add_argument("hex_data")

    return parser


def _serial(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    value = value.strip()
    return int(value, 16) if value.lower().startswith("0x") else int(value, 0)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    command = ns.command

    if command == "start":
        print(start_daemon(ns.socket))
        return 0

    if command == "shutdown":
        try:
            response = call("shutdown", socket_path=ns.socket, timeout=10)
        except DaemonNotRunning:
            print("not running")
            return 0
        deadline = time.time() + 5
        while Path(ns.socket).exists() and time.time() < deadline:
            time.sleep(0.1)
        print(response.get("message", "stopped"))
        return 0

    if command == "config":
        from .config import redacted
        for key, value in redacted().items():
            print(f"{key}: {value}")
        return 0

    if command == "set":
        from .config import save_config
        value = ns.value
        if ns.key == "port":
            value = int(value)
        path = save_config({ns.key: value})
        print(f"saved {ns.key} to {path}")
        if ns.key in ("account", "password", "character", "host", "port"):
            print("restart the client for it to take effect: uo shutdown && uo start")
        return 0

    args: dict = {}
    if command == "login":
        args = {k: v for k, v in
                {"account": ns.account, "password": ns.password,
                 "character": ns.character}.items() if v}
    elif command == "status":
        args = {"radius": ns.radius}
    elif command == "look":
        args = {"radius": ns.radius}
        if ns.serial:
            args["serial"] = _serial(ns.serial)
    elif command == "journal":
        args = {"count": ns.count, "contains": ns.contains}
    elif command == "say":
        args = {"text": " ".join(ns.text), "kind": ns.kind}
    elif command == "step":
        args = {"direction": ns.direction, "run": not ns.walk}
    elif command == "walk":
        args = {"x": ns.x, "y": ns.y, "stop_within": ns.within,
                "max_steps": ns.max_steps, "run": not ns.walk}
    elif command == "goto":
        args = {"serial": _serial(ns.serial), "stop_within": ns.within}
    elif command in ("attack", "use", "contents", "vendor"):
        args = {"serial": _serial(ns.serial)}
    elif command == "warmode":
        args = {"enabled": ns.state == "on"}
    elif command == "grab":
        args = {"serial": _serial(ns.serial), "amount": ns.amount,
                "container": _serial(ns.into)}
    elif command == "drop":
        args = {"serial": _serial(ns.serial), "container": _serial(ns.into)}
    elif command == "equip":
        args = {"serial": _serial(ns.serial), "layer": ns.layer}
    elif command == "target":
        what = (ns.what or "").lower()
        if what in ("", "cancel"):
            args = {"cancel": True}
        elif what == "self":
            args = {"at_self": True}
        elif ns.y is not None:
            args = {"x": int(ns.what), "y": int(ns.y)}
        else:
            args = {"serial": _serial(ns.what)}
    elif command == "skill":
        args = {"name": " ".join(ns.name)}
    elif command == "skills":
        args = {"trained_only": not ns.all}
    elif command == "cast":
        args = {"spell": ns.spell}
        if ns.target:
            args["target"] = _serial(ns.target)
    elif command == "gump":
        if ns.serial:
            args = {"serial": _serial(ns.serial), "button": ns.button or 0}
    elif command in ("buy", "sell"):
        pairs = [(_serial(ns.pairs[i]), int(ns.pairs[i + 1]))
                 for i in range(0, len(ns.pairs) - 1, 2)]
        args = {"vendor": _serial(ns.vendor),
                ("purchases" if command == "buy" else "sales"): pairs}
    elif command == "wait":
        args = {"seconds": ns.seconds, "journal": ns.journal}
    elif command == "script":
        args = {"path": str(Path(ns.path).resolve()), "args": ns.args}
    elif command == "raw":
        args = {"hex_data": ns.hex_data}
    elif command == "jev":
        args = {"seconds": ns.seconds, "ticks": ns.ticks, "radius": ns.radius,
                "flee_percent": ns.flee, "dry_run": ns.dry_run}
        ns.timeout = max(ns.timeout, ns.seconds + 60)
    elif command == "place":
        action, rest = ns.action, ns.rest
        command = f"place_{action}"
        if action == "here":
            args = {"name": " ".join(rest), "kind": ns.kind or "note", "notes": ns.notes}
        elif action == "add":
            args = {"name": rest[0], "x": int(rest[1]), "y": int(rest[2]),
                    "kind": ns.kind or "note", "notes": ns.notes}
        elif action in ("list",):
            args = {"kind": ns.kind}
        elif action == "near":
            args = {"kind": ns.kind}
        elif action == "find":
            args = {"text": " ".join(rest), "kind": ns.kind}
        elif action == "remove":
            args = {"name": " ".join(rest)}
        elif action == "go":
            args = {"name": " ".join(rest), "stop_within": ns.within}

    try:
        response = call(command, args, timeout=ns.timeout, socket_path=ns.socket)
    except DaemonNotRunning as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if ns.json:
        print(json.dumps(response, indent=2))
        return 0 if response.get("ok") else 1

    if not response.get("ok"):
        print(f"error: {response.get('error', 'unknown error')}", file=sys.stderr)
        if response.get("traceback") and os.environ.get("ANACROM_DEBUG"):
            print(response["traceback"], file=sys.stderr)
        return 1

    if "result" in response:
        payload = response["result"]
    else:
        # Out-of-band commands (ping, stop, shutdown) answer flat.
        payload = {k: v for k, v in response.items() if k != "ok"}
    print(render(command, payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
