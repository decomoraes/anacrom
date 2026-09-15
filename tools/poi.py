#!/usr/bin/env python3
"""Query the place list without a running game client.

    python3 tools/poi.py list
    python3 tools/poi.py near 1430 1690 --kind bank
    python3 tools/poi.py add "Britain bank" 1434 1699 --kind bank
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.poi import KINDS, Place, Places  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="poi")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("list")

    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--z", type=int, default=0)
    p.add_argument("--map", type=int, default=0)
    p.add_argument("--kind", default="note", choices=KINDS)
    p.add_argument("--notes", default="")

    p = sub.add_parser("near")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--map", type=int, default=0)
    p.add_argument("--kind", default="")
    p.add_argument("--limit", type=int, default=5)

    p = sub.add_parser("find")
    p.add_argument("text")
    p.add_argument("--kind", default="")

    p = sub.add_parser("remove")
    p.add_argument("name")

    ns = parser.parse_args()
    places = Places()

    if ns.action == "list":
        for place in sorted(places.entries, key=lambda p: (p.map, p.kind, p.name)):
            print(place.summary())
        if not places.entries:
            print("(no places recorded yet -- use: uo place here <name> --kind bank)")
    elif ns.action == "add":
        place = places.add(Place(ns.name, ns.x, ns.y, ns.z, ns.map, ns.kind, ns.notes))
        print(f"recorded {place.summary()}")
    elif ns.action == "near":
        for place in places.nearest((ns.x, ns.y), ns.kind, ns.map, ns.limit):
            print(place.summary((ns.x, ns.y)))
    elif ns.action == "find":
        for place in places.search(ns.text, ns.kind):
            print(place.summary())
    elif ns.action == "remove":
        print(f"removed {places.remove(ns.name)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
