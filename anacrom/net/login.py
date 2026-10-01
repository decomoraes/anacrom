"""The login handshake, from account name to a character standing in the world.

Two connections are involved: the login server hands us a server list and then
relays us -- with a one-shot auth key -- to the game server we picked.  The
game server compresses everything it sends from the moment it accepts our game
login, which is why :meth:`Connection.enable_compression` is flipped there and
nowhere else.
"""
from __future__ import annotations

import ipaddress
import random
import time
from dataclasses import dataclass, field

from ..protocol.packets import DEFAULT_PROFILE, Profile, Reader, Writer
from .connection import Connection

# What we tell the server we are.  The server gates optional packet shapes on
# this, so Profile has to agree with it.
CLIENT_VERSION = (7, 0, 114, 2)
CLIENT_VERSION_STRING = ".".join(map(str, CLIENT_VERSION))

# Bit flags in the 0x5D / 0xA9 client flag field: expansions we claim to own.
CLIENT_FLAG_ALL_EXPANSIONS = 0x0000001F

LOGIN_DENIED_REASONS = {
    0x00: "no account with that name",
    0x01: "account already logged in",
    0x02: "account blocked",
    0x03: "bad password",
    0x04: "idle for too long",
    0x05: "communication problem",
    0x06: "concurrency limit reached",
    0x07: "queue limit reached",
    0x08: "login queue busy",
    0x09: "character transfer in progress",
}

CHARACTER_REJECT_REASONS = {
    0x00: "incorrect password",
    0x01: "character does not exist",
    0x02: "character already in world",
    0x03: "login could not be completed",
    0x04: "character idle",
    0x05: "could not attach to character",
    0x06: "another character is logged in",
}


class LoginError(Exception):
    pass


@dataclass
class ShardServer:
    index: int
    name: str
    percent_full: int
    timezone: int
    address: str = ""


@dataclass
class LoginResult:
    connection: Connection
    characters: list[str]
    servers: list[ShardServer]
    character_index: int
    character_name: str
    player_serial: int = 0
    body: int = 0
    position: tuple[int, int, int] = (0, 0, 0)
    direction: int = 0
    map_width: int = 0
    map_height: int = 0
    journal: list[str] = field(default_factory=list)


def _decode_relay_address(raw: bytes) -> str:
    """Read the game server address out of the relay packet.

    The server stores the address as a little-endian word and writes that word
    little-endian into 0x8C, so the four bytes land in normal a.b.c.d order --
    unlike the shard list in 0xA8, which carries the same word big-endian and
    therefore appears reversed.  Verified against a live shard whose login host
    resolves to the address this returns.
    """
    return str(ipaddress.IPv4Address(bytes(raw)))


def _decode_server_list_address(raw: bytes) -> str:
    """Same word as above, but as 0xA8 writes it: reversed."""
    return str(ipaddress.IPv4Address(bytes(reversed(raw))))


def _collect(conn: Connection, wanted: set[int], timeout: float, log=None) -> bytes:
    """Poll until one of ``wanted`` arrives; everything else is handed to ``log``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for packet in conn.poll():
            if packet[0] in wanted:
                return packet
            if log is not None:
                log(packet)
        if conn.closed:
            raise LoginError("server closed the connection")
        time.sleep(0.01)
    wanted_text = ", ".join(f"0x{p:02X}" for p in sorted(wanted))
    raise LoginError(f"timed out waiting for {wanted_text}")


def _parse_server_list(packet: bytes) -> list[ShardServer]:
    reader = Reader(packet, 3)
    reader.u8()                     # system info flag
    count = reader.u16()
    servers = []
    for _ in range(count):
        index = reader.u16()
        name = reader.ascii(32)
        percent_full = reader.u8()
        timezone = reader.i8()
        address = _decode_server_list_address(reader.raw(4))
        servers.append(ShardServer(index, name, percent_full, timezone, address))
    return servers


def _parse_character_list(packet: bytes) -> list[str]:
    reader = Reader(packet, 3)
    count = reader.u8()
    characters = []
    for _ in range(count):
        name = reader.ascii(30)
        reader.skip(30)             # password field, always blank nowadays
        characters.append(name)
    return characters


def _select_character(characters: list[str], wanted: str | int | None) -> int:
    named = [(i, n) for i, n in enumerate(characters) if n.strip()]
    if not named:
        raise LoginError("the account has no characters")

    if wanted is None:
        return named[0][0]
    if isinstance(wanted, int):
        if not 0 <= wanted < len(characters) or not characters[wanted].strip():
            raise LoginError(f"no character in slot {wanted}")
        return wanted
    for index, name in named:
        if name.strip().casefold() == wanted.casefold():
            return index
    available = ", ".join(n.strip() for _, n in named)
    raise LoginError(f"no character named {wanted!r}; account has: {available}")


def login(
    host: str,
    port: int,
    account: str,
    password: str,
    character: str | int | None = None,
    server_index: int | str | None = None,
    profile: Profile = DEFAULT_PROFILE,
    capture: str | None = None,
    timeout: float = 20.0,
    on_packet=None,
) -> LoginResult:
    """Run the whole handshake and return a connection that is in the world."""
    seed = random.getrandbits(32) or 1

    login_conn = Connection(host, port, profile, capture=capture)
    try:
        login_conn.send_seed(seed, CLIENT_VERSION)
        login_conn.send(
            Writer(0x80, profile).ascii(account, 30).ascii(password, 30).u8(0xFF)
        )

        packet = _collect(login_conn, {0xA8, 0x82, 0x53}, timeout, on_packet)
        if packet[0] == 0x82:
            reason = LOGIN_DENIED_REASONS.get(packet[1], f"code 0x{packet[1]:02X}")
            raise LoginError(f"login denied: {reason}")
        if packet[0] == 0x53:
            reason = CHARACTER_REJECT_REASONS.get(packet[1], f"code 0x{packet[1]:02X}")
            raise LoginError(f"login rejected: {reason}")

        servers = _parse_server_list(packet)
        if not servers:
            raise LoginError("the login server offered no shards")

        chosen = servers[0]
        if isinstance(server_index, int):
            match = [s for s in servers if s.index == server_index]
            if not match:
                raise LoginError(f"no shard with index {server_index}")
            chosen = match[0]
        elif isinstance(server_index, str):
            match = [s for s in servers if s.name.casefold() == server_index.casefold()]
            if not match:
                names = ", ".join(s.name for s in servers)
                raise LoginError(f"no shard named {server_index!r}; offered: {names}")
            chosen = match[0]

        login_conn.send(Writer(0xA0, profile).u16(chosen.index))
        relay = _collect(login_conn, {0x8C, 0x82}, timeout, on_packet)
        if relay[0] == 0x82:
            reason = LOGIN_DENIED_REASONS.get(relay[1], f"code 0x{relay[1]:02X}")
            raise LoginError(f"shard select denied: {reason}")

        game_host = _decode_relay_address(relay[1:5])
        reader = Reader(relay, 5)
        game_port = reader.u16()
        auth_key = reader.u32()
    finally:
        login_conn.close()

    game_conn = Connection(game_host, game_port, profile, capture=capture)
    try:
        game_conn.send_raw_seed(auth_key)
        game_conn.send(
            Writer(0x91, profile).u32(auth_key).ascii(account, 30).ascii(password, 30)
        )
        # The server compresses from the moment it processes that packet.
        game_conn.enable_compression()

        packet = _collect(game_conn, {0xA9, 0x82, 0x53}, timeout, on_packet)
        if packet[0] in (0x82, 0x53):
            table = LOGIN_DENIED_REASONS if packet[0] == 0x82 else CHARACTER_REJECT_REASONS
            reason = table.get(packet[1], f"code 0x{packet[1]:02X}")
            raise LoginError(f"game login denied: {reason}")

        characters = _parse_character_list(packet)
        index = _select_character(characters, character)
        name = characters[index].strip()

        play = Writer(0x5D, profile)
        play.u32(0xEDEDEDED)
        play.ascii(name, 30)
        play.u16(0)
        play.u32(CLIENT_FLAG_ALL_EXPANSIONS)
        play.u32(1)
        play.u32(0)
        play.raw(b"\x00" * 16)
        play.u32(index)
        play.u32(0)                 # our own address; servers ignore it
        game_conn.send(play)

        # Some shards ask for the version string, others expect it unprompted.
        game_conn.send(Writer(0xBD, profile).ascii_z(CLIENT_VERSION_STRING))

        journal: list[str] = []
        confirm = _collect(game_conn, {0x1B, 0x53, 0x82}, timeout, on_packet)
        if confirm[0] in (0x53, 0x82):
            table = CHARACTER_REJECT_REASONS if confirm[0] == 0x53 else LOGIN_DENIED_REASONS
            reason = table.get(confirm[1], f"code 0x{confirm[1]:02X}")
            raise LoginError(f"character login rejected: {reason}")

        reader = Reader(confirm, 1)
        player_serial = reader.u32()
        reader.skip(4)
        body = reader.u16()
        x = reader.u16()
        y = reader.u16()
        z = reader.i16()
        direction = reader.u8()
        reader.skip(9)
        map_width = reader.u16()
        map_height = reader.u16()

        return LoginResult(
            connection=game_conn,
            characters=[c.strip() for c in characters],
            servers=servers,
            character_index=index,
            character_name=name,
            player_serial=player_serial,
            body=body,
            position=(x, y, z),
            direction=direction,
            map_width=map_width,
            map_height=map_height,
            journal=journal,
        )
    except Exception:
        game_conn.close()
        raise
