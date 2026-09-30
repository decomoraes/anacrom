#!/usr/bin/env python3
"""A stub shard, just real enough to log into.

It speaks the parts of the protocol the client depends on -- seed, account
login, relay, game login, compression, character select, and a small world that
moves when you walk -- so the whole pipeline can be exercised without an account
on a live shard.  It is a test fixture, not an emulator: no game rules live here.

    python3 tools/dev_server.py --port 2593
"""
from __future__ import annotations

import argparse
import pathlib
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.protocol._codebook import CODEBOOK, TERMINATOR      # noqa: E402
from anacrom.protocol.packets import DEFAULT_PROFILE, Writer, frame  # noqa: E402

PLAYER_SERIAL = 0x00ABCDEF
GUARD_SERIAL = 0x00111111
GOLD_SERIAL = 0x40222222
BACKPACK_SERIAL = 0x00ABCD01
START = (1434, 1699, 0)


def compress(payload: bytes) -> bytes:
    bits = "".join(format(CODEBOOK[b][1], f"0{CODEBOOK[b][0]}b") for b in payload)
    length, value = CODEBOOK[TERMINATOR]
    bits += format(value, f"0{length}b")
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


class Session:
    """One connected client, in either the login or the game role."""

    def __init__(self, conn: socket.socket, port: int, quiet: bool = False) -> None:
        self.conn = conn
        self.port = port
        self.quiet = quiet
        self.compressed = False
        self.buffer = b""
        self.x, self.y, self.z = START
        self.direction = 0
        self.hits = 71
        self.said: list[str] = []

    def log(self, message: str) -> None:
        if not self.quiet:
            print(f"[stub] {message}", flush=True)

    def send(self, packet) -> None:
        data = packet.build() if isinstance(packet, Writer) else packet
        self.conn.sendall(compress(data) if self.compressed else data)

    def run(self) -> None:
        with self.conn:
            seeded = False
            while True:
                try:
                    chunk = self.conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                self.buffer += chunk

                if not seeded:
                    if self.buffer[:1] == b"\xEF":
                        if len(self.buffer) < 21:
                            continue
                        self.buffer = self.buffer[21:]
                    else:
                        if len(self.buffer) < 4:
                            continue
                        self.buffer = self.buffer[4:]
                    seeded = True

                packets, self.buffer = frame(self.buffer, DEFAULT_PROFILE)
                for packet in packets:
                    self.handle(packet)

    # -- packet handling ---------------------------------------------------

    def handle(self, packet: bytes) -> None:
        packet_id = packet[0]
        if packet_id == 0x80:
            account = packet[1:31].split(b"\x00", 1)[0].decode("ascii", "replace")
            self.log(f"account login: {account}")
            self.send_server_list()
        elif packet_id == 0xA0:
            self.send_relay()
        elif packet_id == 0x91:
            self.compressed = True
            self.log("game login accepted; compression on")
            self.send_character_list()
        elif packet_id == 0x5D:
            self.enter_world()
        elif packet_id == 0x02:
            self.movement(packet)
        elif packet_id == 0xAD:
            text, _keywords = self.read_speech(packet)
            self.said.append(text)
            self.log(f"player says: {text}")
            self.echo(text)
        elif packet_id == 0x34:
            if packet[5] == 0x04:
                self.send_status()
        elif packet_id == 0xD6:
            for offset in range(3, len(packet), 4):
                serial = struct.unpack_from(">I", packet, offset)[0]
                self.send_properties(serial)
        elif packet_id == 0x06:
            self.send_container(struct.unpack_from(">I", packet, 1)[0])
        elif packet_id == 0x73:
            self.send(b"\x73" + packet[1:2])

    @staticmethod
    def read_speech(packet: bytes) -> tuple[str, list[int]]:
        """Unpack 0xAD the way the server does (IncomingMessagePackets)."""
        if not packet[3] & 0xC0:
            return packet[12:].decode("utf-16-be", "replace").rstrip("\x00"), []
        offset = 12
        value = int.from_bytes(packet[offset:offset + 2], "big")
        offset += 2
        count, hold = value >> 4, value & 0xF
        keywords = []
        for i in range(count):
            if i % 2 == 0:
                keywords.append((hold << 8) | packet[offset])
                offset += 1
            else:
                value = int.from_bytes(packet[offset:offset + 2], "big")
                offset += 2
                keywords.append(value >> 4)
                hold = value & 0xF
        text = packet[offset:].split(b"\x00", 1)[0].decode("utf-8", "replace")
        return text, keywords

    # -- the login half ----------------------------------------------------

    def send_server_list(self) -> None:
        body = b"\x5D" + struct.pack(">H", 1)
        body += struct.pack(">H", 0) + b"Stub Shard".ljust(32, b"\x00")
        body += bytes([0, 0]) + bytes([1, 0, 0, 127])       # 127.0.0.1, reversed
        self.send(Writer(0xA8).raw(body))

    def send_relay(self) -> None:
        self.send(
            Writer(0x8C).raw(bytes([127, 0, 0, 1])).u16(self.port).u32(0x12345678)
        )

    def send_character_list(self) -> None:
        body = bytes([1]) + b"Alaric".ljust(30, b"\x00") + b"\x00" * 30
        body += bytes([0])                                  # no starting cities
        body += struct.pack(">I", 0x1F)
        self.send(Writer(0xA9).raw(body))

    # -- the game half -----------------------------------------------------

    def enter_world(self) -> None:
        self.log("character entering the world")
        confirm = Writer(0x1B).u32(PLAYER_SERIAL).u32(0).u16(0x0190)
        confirm.u16(self.x).u16(self.y).i16(self.z).u8(self.direction)
        confirm.raw(b"\x00" * 9).u16(6144).u16(4096).raw(b"\x00" * 6)
        self.send(confirm)

        self.send_status()
        self.send_player()
        self.send_backpack()
        self.send_guard()
        self.send_gold()
        self.send(b"\x55")

    def send_player(self) -> None:
        packet = Writer(0x20).u32(PLAYER_SERIAL).u16(0x0190).u8(0).u16(0)
        packet.u8(0).u16(self.x).u16(self.y).u16(0).u8(self.direction).u8(self.z & 0xFF)
        self.send(packet)

    def send_status(self) -> None:
        body = struct.pack(">I", PLAYER_SERIAL) + b"Alaric".ljust(30, b"\x00")
        body += struct.pack(">HH", self.hits, 100) + b"\x00" + bytes([5])
        body += b"\x00" + struct.pack(">HHH", 80, 60, 40)
        body += struct.pack(">HH", 50, 55) + struct.pack(">HH", 30, 35)
        body += struct.pack(">I", 1250) + struct.pack(">HH", 42, 210)
        body += struct.pack(">H", 400) + b"\x01"
        body += struct.pack(">H", 225) + b"\x00\x05"
        self.send(Writer(0x11).raw(body))

    def send_backpack(self) -> None:
        packet = Writer(0x2E).u32(BACKPACK_SERIAL).u16(0x0E75).u8(0)
        packet.u8(0x15).u32(PLAYER_SERIAL).u16(0)
        self.send(packet)

    def send_guard(self) -> None:
        body = struct.pack(">IHHHbBHBB", GUARD_SERIAL, 0x0190,
                           self.x + 3, self.y + 1, self.z, 2, 0, 0x00, 1)
        body += struct.pack(">I", 0)
        self.send(Writer(0x78).raw(body))

    def send_gold(self) -> None:
        body = struct.pack(">IH", GOLD_SERIAL | 0x80000000, 0x0EED)
        body += struct.pack(">H", 120)
        body += struct.pack(">H", self.x + 1) + struct.pack(">H", self.y)
        body += struct.pack(">b", self.z)
        self.send(Writer(0x1A).raw(body))

    def send_properties(self, serial: int) -> None:
        names = {
            GUARD_SERIAL: "a town guard",
            GOLD_SERIAL: "120 gold coins",
            BACKPACK_SERIAL: "a backpack",
        }
        if serial not in names:
            return
        arguments = names[serial].encode("utf-16-le")
        body = struct.pack(">HIHI", 1, serial, 0, serial)
        body += struct.pack(">IH", 1050045, len(arguments)) + arguments
        body += struct.pack(">I", 0)
        self.send(Writer(0xD6).raw(body))

    def send_container(self, serial: int) -> None:
        if serial != BACKPACK_SERIAL:
            return
        self.send(Writer(0x24).u32(serial).u16(0x003C).u16(0))
        body = struct.pack(">H", 1)
        body += struct.pack(">IHBHHHBIH", 0x40333333, 0x0F0E, 0, 3, 2, 2, 0, serial, 0)
        self.send(Writer(0x3C).raw(body))

    def movement(self, packet: bytes) -> None:
        code, sequence = packet[1], packet[2]
        direction = code & 0x07
        deltas = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]

        if direction != self.direction:
            self.direction = direction                       # a turn, not a step
        else:
            dx, dy = deltas[direction]
            # One tile is a wall, so the client's sidestep logic gets exercised.
            if (self.x + dx, self.y + dy) != (START[0], START[1] - 3):
                self.x += dx
                self.y += dy
        self.send(Writer(0x22).u8(sequence).u8(0))

    def echo(self, text: str) -> None:
        body = struct.pack(">IHBHH", GUARD_SERIAL, 0x0190, 0x00, 0x03B2, 3)
        body += b"ENU\x00" + b"A Town Guard".ljust(30, b"\x00")
        body += f"You said: {text}".encode("utf-16-be") + b"\x00\x00"
        self.send(Writer(0xAE).raw(body))


def serve(port: int, quiet: bool = False, ready: threading.Event | None = None) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    if not quiet:
        print(f"[stub] listening on 127.0.0.1:{port}", flush=True)
    if ready is not None:
        ready.set()

    while True:
        conn, _ = listener.accept()
        threading.Thread(target=Session(conn, port, quiet).run, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2593)
    parser.add_argument("--quiet", action="store_true")
    ns = parser.parse_args()
    try:
        serve(ns.port, ns.quiet)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
