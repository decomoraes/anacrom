"""Packet framing: how long is each packet, and how do we read/write one.

UO has no length prefix on the wire.  A packet's length is either a constant
implied by its id, or -- for ids marked ``VAR`` -- a big-endian ushort sitting
at offset 1.  A reader that gets one length wrong desynchronises the whole
stream, so :func:`length_of` is deliberately strict: an id it does not know
raises instead of guessing, and the framer reports the offending bytes.

A few lengths depend on what the server believes the client supports, which it
infers from the version we advertise at login.  Those live in
:class:`Profile` rather than in the flat table.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

VAR = 0  # length is a ushort at offset 1


class ProtocolError(Exception):
    """The stream stopped making sense -- almost always a bad length."""


# Lengths that never change.  Client -> server entries are the ones the server
# registers a handler for (see the vendored server's IncomingPackets); server ->
# client entries are what its Outgoing*Packets writers emit.
_FIXED: dict[int, int] = {
    0x01: 5,    # disconnect notification
    0x02: 7,    # movement request
    0x03: VAR,  # ascii speech
    0x05: 5,    # attack request
    0x06: 5,    # double click / use
    0x07: 7,    # lift (pick up) request
    0x08: 15,   # drop request
    0x09: 5,    # single click / look
    0x0B: 7,    # damage dealt to a mobile
    0x11: VAR,  # status bar / paperdoll stats
    0x12: VAR,  # text command (cast, open door, ...)
    0x13: 10,   # equip request
    0x15: 9,    # follow
    0x16: VAR,  # health bar status, SA clients; ServUO sends it, ModernUO never does
    0x17: VAR,  # health bar status update
    0x1A: VAR,  # world item appeared
    0x1B: 37,   # login confirm (our character enters the world)
    0x1C: VAR,  # ascii message
    0x1D: 5,    # object disappeared
    0x20: 19,   # draw game player (us)
    0x21: 8,    # movement rejected -> resync position
    0x22: 3,    # movement accepted
    0x23: 26,   # drag animation
    0x24: 7,    # open container gump (see Profile.container_length)
    0x25: 20,   # single container item (see Profile.container_item_length)
    0x26: 5,    # kick
    0x27: 2,    # drop rejected
    0x28: 5,    # drop / clear square
    0x29: 1,    # drop ok
    0x2C: 2,    # death / resurrect menu
    0x2D: 17,   # mobile attributes
    0x2E: 15,   # worn item
    0x2F: 10,   # swing / fight occurring
    0x30: 5,    # attack ok
    0x31: 1,    # attack ended
    0x33: 2,    # pause client
    0x34: 10,   # get player status request
    0x36: VAR,  # ?
    0x38: 7,    # pathfind
    0x3A: VAR,  # skills update
    0x3B: VAR,  # buy item(s)
    0x3C: VAR,  # container contents
    0x4E: 6,    # personal light level
    0x4F: 2,    # overall light level
    0x53: 2,    # reject character logon
    0x54: 12,   # play sound effect
    0x55: 1,    # login complete
    0x56: 11,   # map packet (treasure/sos pins)
    0x5B: 4,    # current time
    0x65: 4,    # set weather
    0x66: VAR,  # book contents
    0x6C: 19,   # target cursor
    0x6D: 3,    # play midi music
    0x6E: 14,   # character animation
    0x6F: VAR,  # secure trading
    0x70: 28,   # graphical effect
    0x71: VAR,  # bulletin board
    0x72: 5,    # war mode
    0x73: 2,    # ping
    0x74: VAR,  # open buy window
    0x76: 16,   # new subserver
    0x77: 17,   # mobile moving
    0x78: VAR,  # mobile incoming
    0x7C: VAR,  # open menu gump
    0x82: 2,    # login denied
    0x85: 2,    # delete character reply
    0x86: VAR,  # resend character list
    0x88: 66,   # open paperdoll
    0x89: VAR,  # corpse clothing
    0x8B: VAR,  # open spellbook / sound
    0x8C: 11,   # relay to game server
    0x90: 19,   # map details
    0x93: 99,   # book header (old)
    0x95: 9,    # dye window
    0x97: 2,    # move player
    0x98: VAR,  # all names (3d client)
    0x99: 26,   # house revision / multi placement (see Profile)
    0x9A: VAR,  # ascii prompt
    0x9B: 258,  # help request
    0x9C: 9,    # request assistance
    0x9E: VAR,  # sell list
    0xA1: 9,    # hit points update
    0xA2: 9,    # mana update
    0xA3: 9,    # stamina update
    0xA5: VAR,  # open web browser
    0xA6: VAR,  # tip / notice window
    0xA8: VAR,  # server list
    0xA9: VAR,  # character list
    0xAA: 5,    # attack target confirmation
    0xAB: VAR,  # gump text entry dialog
    0xAE: VAR,  # unicode speech
    0xAF: 13,   # death animation
    0xB0: VAR,  # generic gump
    0xB2: VAR,  # chat message
    0xB7: VAR,  # help / tooltip text
    0xB8: VAR,  # profile
    0xB9: 5,    # enable locked client features
    0xBA: 6,    # quest arrow; see Profile, High Seas makes it 10
    0xBC: 3,    # season / cursor
    0xBF: VAR,  # general information (many subcommands)
    0xC0: 36,   # hued effect
    0xC1: VAR,  # localised message (cliloc)
    0xC2: VAR,  # unicode prompt
    0xC4: 6,    # semivisible
    0xC7: 49,   # hued particle effect
    0xC8: 2,    # update range
    0xCB: 7,    # ?
    0xCC: VAR,  # localised message with affix
    0xD6: VAR,  # object property list (tooltips)
    0xD8: VAR,  # custom house
    0xDC: 9,    # object property list hash
    0xDD: VAR,  # compressed gump
    0xDE: VAR,  # update mobile status
    0xDF: VAR,  # buff / debuff
    0xE2: 10,   # new character animation
    0xE3: VAR,  # ?
    0xE5: VAR,  # show waypoint; ServUO (Misc/Waypoints.cs), not in ModernUO
    0xE6: 5,    # remove waypoint; ServUO
    0xF0: VAR,  # krriosclient / new movement
    0xF1: 9,    # time sync
    0xF2: 25,   # new world item (SA)
    0xF3: 26,   # object info (replaces 0x1A/0x77)
    0xF5: 21,   # new map details
    0xF6: VAR,  # boat moving
    0xF7: VAR,  # packet container (batched packets)

    # Client -> server only.  Lengths come from the server's own handler table.
    0x00: 104,  # create character
    0x5D: 73,   # play character
    0x80: 62,   # account login
    0x83: 39,   # delete character
    0x91: 65,   # game server login
    0xA0: 3,    # select shard
    0xA4: 149,  # client system info
    0xA7: 4,    # request tips
    0xAC: VAR,  # text entry response
    0xAD: VAR,  # unicode speech
    0xB1: VAR,  # gump button response
    0xB3: VAR,  # chat text
    0xB5: 64,   # open chat window
    0xB6: 9,    # request tooltip
    0xBB: 9,    # account id
    0xBD: VAR,  # client version
    0xC5: 1,    # ?
    0xD0: VAR,  # configuration file
    0xD1: 2,    # logout request
    0xD4: VAR,  # new book header
    0xD7: VAR,  # encoded command (AOS actions)
    0xD9: 268,  # client hardware info
    0xE1: VAR,  # client type (2D/3D)
    0xEC: VAR,  # equip macro
    0xED: VAR,  # unequip macro
    0xEF: 21,   # login server seed
    0xF4: VAR,  # crash report
    0xF8: 106,  # create character (new)
    0xFB: 2,    # public house content
}


@dataclass(frozen=True)
class Profile:
    """Length variations the server picks based on our advertised version."""

    high_seas: bool = True
    container_grid_lines: bool = True

    @property
    def container_length(self) -> int:
        """Length of 0x24, open container."""
        return 9 if self.high_seas else 7

    @property
    def container_item_length(self) -> int:
        """Length of 0x25, a single container item."""
        return 21 if self.container_grid_lines else 20

    @property
    def multi_length(self) -> int:
        """Length of 0x99, multi placement / house revision."""
        return 30 if self.high_seas else 26

    @property
    def quest_arrow_length(self) -> int:
        """Length of 0xBA, quest arrow; High Seas adds the target's serial."""
        return 10 if self.high_seas else 6

    def length_of(self, packet_id: int) -> int:
        if packet_id == 0x24:
            return self.container_length
        if packet_id == 0x25:
            return self.container_item_length
        if packet_id == 0x99:
            return self.multi_length
        if packet_id == 0xBA:
            return self.quest_arrow_length
        try:
            return _FIXED[packet_id]
        except KeyError:
            raise ProtocolError(f"unknown packet id 0x{packet_id:02X}") from None


DEFAULT_PROFILE = Profile()


def length_of(packet_id: int, profile: Profile = DEFAULT_PROFILE) -> int:
    return profile.length_of(packet_id)


def is_known(packet_id: int) -> bool:
    return packet_id in _FIXED


def frame(buffer: bytes, profile: Profile = DEFAULT_PROFILE) -> tuple[list[bytes], bytes]:
    """Split a decompressed byte stream into whole packets.

    Returns the complete packets and whatever tail bytes belong to a packet that
    has not fully arrived yet.
    """
    packets: list[bytes] = []
    offset = 0
    total = len(buffer)

    while offset < total:
        packet_id = buffer[offset]
        size = profile.length_of(packet_id)

        if size == VAR:
            if total - offset < 3:
                break
            size = struct.unpack_from(">H", buffer, offset + 1)[0]
            if size < 3:
                raise ProtocolError(
                    f"packet 0x{packet_id:02X} declares a {size} byte length"
                )

        if total - offset < size:
            break

        packets.append(bytes(buffer[offset:offset + size]))
        offset += size

    return packets, bytes(buffer[offset:])


class Reader:
    """Big-endian cursor over one packet's bytes."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def __len__(self) -> int:
        return len(self.data)

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos

    def skip(self, count: int) -> "Reader":
        self.pos += count
        return self

    def seek(self, pos: int) -> "Reader":
        self.pos = pos
        return self

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def i8(self) -> int:
        value = struct.unpack_from(">b", self.data, self.pos)[0]
        self.pos += 1
        return value

    def bool(self) -> bool:
        return self.u8() != 0

    def u16(self) -> int:
        value = struct.unpack_from(">H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def i16(self) -> int:
        value = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self) -> int:
        value = struct.unpack_from(">I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def i32(self) -> int:
        value = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def raw(self, count: int) -> bytes:
        value = self.data[self.pos:self.pos + count]
        self.pos += count
        return bytes(value)

    def ascii(self, count: int) -> str:
        return self.raw(count).split(b"\x00", 1)[0].decode("ascii", "replace")

    def ascii_z(self) -> str:
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            end = len(self.data)
        value = self.data[self.pos:end].decode("ascii", "replace")
        self.pos = end + 1
        return value

    def unicode(self, count: int) -> str:
        """count is a character count, not a byte count."""
        raw = self.raw(count * 2)
        text = raw.decode("utf-16-be", "replace")
        return text.split("\x00", 1)[0]

    def unicode_z(self) -> str:
        chars = []
        while self.remaining >= 2:
            unit = self.raw(2)
            if unit == b"\x00\x00":
                break
            chars.append(unit)
        return b"".join(chars).decode("utf-16-be", "replace")


class Writer:
    """Builds a packet; fills in the length field for variable-length ids."""

    __slots__ = ("_buf", "_packet_id", "_variable")

    def __init__(self, packet_id: int, profile: Profile = DEFAULT_PROFILE) -> None:
        self._buf = bytearray()
        self._packet_id = packet_id
        self._variable = profile.length_of(packet_id) == VAR
        self._buf.append(packet_id)
        if self._variable:
            self._buf.extend(b"\x00\x00")  # placeholder

    def u8(self, value: int) -> "Writer":
        self._buf.append(value & 0xFF)
        return self

    def bool(self, value: bool) -> "Writer":
        return self.u8(1 if value else 0)

    def u16(self, value: int) -> "Writer":
        self._buf.extend(struct.pack(">H", value & 0xFFFF))
        return self

    def i16(self, value: int) -> "Writer":
        self._buf.extend(struct.pack(">h", value))
        return self

    def u32(self, value: int) -> "Writer":
        self._buf.extend(struct.pack(">I", value & 0xFFFFFFFF))
        return self

    def raw(self, value: bytes) -> "Writer":
        self._buf.extend(value)
        return self

    def ascii(self, value: str, size: int) -> "Writer":
        encoded = value.encode("ascii", "replace")[:size]
        self._buf.extend(encoded.ljust(size, b"\x00"))
        return self

    def ascii_z(self, value: str) -> "Writer":
        self._buf.extend(value.encode("ascii", "replace"))
        self._buf.append(0)
        return self

    def unicode_z(self, value: str) -> "Writer":
        self._buf.extend(value.encode("utf-16-be", "replace"))
        self._buf.extend(b"\x00\x00")
        return self

    def build(self) -> bytes:
        if self._variable:
            struct.pack_into(">H", self._buf, 1, len(self._buf))
        return bytes(self._buf)
