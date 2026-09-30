"""Tests for the parts that have to be exactly right.

The wire format is a contract with someone else's server, so these tests build
packets byte by byte rather than round-tripping through our own writer wherever
a mistake in the writer would otherwise hide a mistake in the reader.

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import pathlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from anacrom.client import Client, Config                       # noqa: E402
from anacrom.net.login import (                                 # noqa: E402
    _decode_relay_address,
    _decode_server_list_address,
    _parse_character_list,
    _parse_server_list,
    _select_character,
    LoginError,
)
from anacrom.poi import Place, Places                           # noqa: E402
from anacrom.protocol import speech                             # noqa: E402
from anacrom.protocol._codebook import CODEBOOK, TERMINATOR     # noqa: E402
from anacrom.protocol.huffman import Decompressor               # noqa: E402
from anacrom.protocol.packets import (                          # noqa: E402
    DEFAULT_PROFILE,
    Profile,
    ProtocolError,
    Reader,
    Writer,
    frame,
    length_of,
)
from anacrom.world.handlers import dispatch                     # noqa: E402
from anacrom.world.state import World, direction_to, distance   # noqa: E402
from tools.dev_server import Session                            # noqa: E402


def compress(payload: bytes) -> bytes:
    """The server side of the code book, so we can test our decoder honestly."""
    bits = "".join(format(CODEBOOK[b][1], f"0{CODEBOOK[b][0]}b") for b in payload)
    length, value = CODEBOOK[TERMINATOR]
    bits += format(value, f"0{length}b")
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits), 8))


def variable(packet_id: int, body: bytes) -> bytes:
    return bytes([packet_id]) + struct.pack(">H", len(body) + 3) + body


class Compression(unittest.TestCase):
    def test_round_trip(self):
        payload = bytes(range(256)) * 2
        self.assertEqual(Decompressor().feed(compress(payload)), payload)

    def test_blocks_split_across_reads(self):
        blocks = [bytes([i]) * (i + 1) for i in range(1, 40)]
        stream = b"".join(compress(b) for b in blocks)
        decoder, out = Decompressor(), bytearray()
        for i in range(0, len(stream), 3):
            out += decoder.feed(stream[i:i + 3])
        self.assertEqual(bytes(out), b"".join(blocks))

    def test_padding_is_not_mistaken_for_data(self):
        # A one-byte block pads with zero bits, which are a valid code prefix.
        self.assertEqual(Decompressor().feed(compress(b"\x00")), b"\x00")

    def test_code_book_is_a_prefix_code(self):
        codes = {(length, code) for length, code in CODEBOOK}
        self.assertEqual(len(codes), len(CODEBOOK))
        for length, code in CODEBOOK:
            for shorter in range(1, length):
                self.assertNotIn((shorter, code >> (length - shorter)), codes)


class Framing(unittest.TestCase):
    def test_fixed_and_variable(self):
        stream = b"\x05\xDE\xAD\xBE\xEF" + variable(0x03, b"hi\x00")
        packets, tail = frame(stream)
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0], b"\x05\xDE\xAD\xBE\xEF")
        self.assertEqual(tail, b"")

    def test_quest_arrow_carries_a_serial_for_high_seas(self):
        # Young players get an arrow to the nearest healer when they die.
        arrow = bytes.fromhex("ba 01 0d af 0a b9 00 01 79 19")
        after = bytes.fromhex("1d 41 3f 18 e7")
        packets, rest = frame(arrow + after, Profile(high_seas=True))
        self.assertEqual(packets, [arrow, after])
        old_arrow = bytes.fromhex("ba 01 0d af 0a b9")
        packets, _ = frame(old_arrow + after, Profile(high_seas=False))
        self.assertEqual(packets, [old_arrow, after])

    def test_waypoint_removals_are_five_bytes(self):
        run = bytes.fromhex("e6 00 01 00 6c e6 00 06 6b 4a")
        packets, rest = frame(run)
        self.assertEqual(packets, [run[:5], run[5:]])

    def test_sa_health_bar_has_a_length_field(self):
        # Captured from UOAlive mid-fight: 0x16 is framed like 0x17, not fixed 5.
        bar = bytes.fromhex("16 000c 15c29288 0001 0001 00")
        sound = bytes.fromhex("54 01 048f 0000 0da1 0a9b 0007")
        packets, rest = frame(bar + sound)
        self.assertEqual(packets, [bar, sound])
        self.assertEqual(rest, b"")

    def test_partial_packet_is_held_back(self):
        packets, tail = frame(b"\x05\xDE\xAD")
        self.assertEqual(packets, [])
        self.assertEqual(tail, b"\x05\xDE\xAD")

    def test_partial_length_field_is_held_back(self):
        packets, tail = frame(b"\x03\x00")
        self.assertEqual(packets, [])
        self.assertEqual(tail, b"\x03\x00")

    def test_unknown_id_raises_instead_of_guessing(self):
        with self.assertRaises(ProtocolError):
            frame(b"\xFF\xCD\xEF")

    def test_nonsense_length_raises(self):
        with self.assertRaises(ProtocolError):
            frame(b"\x03\x00\x01")

    def test_profile_changes_container_lengths(self):
        self.assertEqual(length_of(0x24, Profile(high_seas=True)), 9)
        self.assertEqual(length_of(0x24, Profile(high_seas=False)), 7)
        self.assertEqual(length_of(0x25, Profile(container_grid_lines=False)), 20)


class ReaderWriter(unittest.TestCase):
    def test_writer_fills_in_the_length(self):
        packet = Writer(0x03).ascii_z("hello").build()
        self.assertEqual(packet[0], 0x03)
        self.assertEqual(struct.unpack(">H", packet[1:3])[0], len(packet))

    def test_fixed_writer_has_no_length_field(self):
        self.assertEqual(Writer(0x05).u32(0x12345678).build(), b"\x05\x12\x34\x56\x78")

    def test_reader_types(self):
        reader = Reader(bytes.fromhex("ff") + b"\xff\xff" + b"\x00\x41\x00\x42\x00\x00")
        self.assertEqual(reader.u8(), 255)
        self.assertEqual(reader.i16(), -1)
        self.assertEqual(reader.unicode_z(), "AB")

    def test_ascii_stops_at_the_terminator(self):
        self.assertEqual(Reader(b"abc\x00junkjunk").ascii(11), "abc")


class LoginParsing(unittest.TestCase):
    def test_relay_and_server_list_disagree_about_byte_order(self):
        # Same address word, written two different ways by the same server.
        self.assertEqual(_decode_relay_address(b"\x94\x71\xcc\x04"), "148.113.204.4")
        self.assertEqual(_decode_server_list_address(b"\x04\xcc\x71\x94"), "148.113.204.4")

    def test_server_list(self):
        body = b"\x5D" + struct.pack(">H", 1)
        body += struct.pack(">H", 0) + b"Test Shard".ljust(32, b"\x00")
        body += bytes([42, 0]) + b"\x04\xcc\x71\x94"
        servers = _parse_server_list(variable(0xA8, body))
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].name, "Test Shard")
        self.assertEqual(servers[0].percent_full, 42)
        self.assertEqual(servers[0].address, "148.113.204.4")

    def test_character_list(self):
        body = bytes([2])
        body += b"Alaric".ljust(30, b"\x00") + b"\x00" * 30
        body += b"".ljust(30, b"\x00") + b"\x00" * 30
        names = _parse_character_list(variable(0xA9, body))
        self.assertEqual(names[0], "Alaric".ljust(30, "\x00").split("\x00")[0])
        self.assertEqual(_select_character(names, None), 0)
        self.assertEqual(_select_character(names, "alaric"), 0)
        with self.assertRaises(LoginError):
            _select_character(names, "Nobody")

    def test_empty_account_is_reported_clearly(self):
        with self.assertRaises(LoginError):
            _select_character(["", ""], None)


class Handlers(unittest.TestCase):
    def setUp(self):
        self.client = Client(Config())
        self.world = self.client.world
        self.world.player.serial = 0x0000AAAA

    def feed(self, packet: bytes):
        dispatch(self.client, self.world, packet)

    def test_mobile_incoming_with_equipment(self):
        body = struct.pack(">IHHHbBHBB", 0x1234, 0x0190, 100, 200, 5, 2, 0x83EA, 0x00, 1)
        body += struct.pack(">IHB", 0x5555, 0x2048 | 0x8000, 0x05) + struct.pack(">H", 0x021)
        body += struct.pack(">I", 0)
        self.feed(variable(0x78, body))

        mob = self.world.mobiles[0x1234]
        self.assertEqual(mob.position, (100, 200, 5))
        self.assertEqual(mob.notoriety_name, "innocent")
        self.assertEqual(mob.facing, "east")
        self.assertEqual(mob.equipment["shirt"], 0x5555)
        self.assertEqual(self.world.items[0x5555].graphic, 0x2048)
        self.assertEqual(self.world.items[0x5555].hue, 0x21)

    def test_hostile_detection(self):
        body = struct.pack(">IHHHbBHBB", 0x99, 0x0009, 100, 100, 0, 0, 0, 0, 6)
        body += struct.pack(">I", 0)
        self.feed(variable(0x78, body))
        self.world.player.x, self.world.player.y = 100, 105
        self.assertEqual([m.serial for m in self.world.hostiles(10)], [0x99])

    def test_world_item_optional_fields(self):
        serial = 0x4000 | 0x80000000
        body = struct.pack(">IH", serial, 0x0EED)          # gold, with an amount
        body += struct.pack(">H", 25)
        body += struct.pack(">H", 300 | 0x8000)            # x, hue follows
        body += struct.pack(">H", 400)                     # y, no flags
        body += struct.pack(">b", -5)
        body += struct.pack(">H", 0x08A5)
        self.feed(variable(0x1A, body))

        item = self.world.items[0x4000]
        self.assertEqual((item.x, item.y, item.z), (300, 400, -5))
        self.assertEqual(item.amount, 25)
        self.assertEqual(item.hue, 0x08A5)
        self.assertTrue(item.on_ground)

    def test_status_updates_the_player(self):
        body = struct.pack(">I", 0x0000AAAA) + b"Alaric".ljust(30, b"\x00")
        body += struct.pack(">HH", 71, 100) + b"\x00" + bytes([5])
        body += b"\x00"                                    # male
        body += struct.pack(">HHH", 80, 60, 40)            # str dex int
        body += struct.pack(">HH", 50, 55)                 # stamina
        body += struct.pack(">HH", 30, 35)                 # mana
        body += struct.pack(">I", 12345)                   # gold
        body += struct.pack(">HH", 42, 210)                # armor, weight
        body += struct.pack(">H", 400) + b"\x01"           # max weight, human
        body += struct.pack(">H", 225) + b"\x02\x05"       # stat cap, followers
        self.feed(variable(0x11, body))

        player = self.world.player
        self.assertEqual(player.name, "Alaric")
        self.assertEqual((player.hits, player.hits_max), (71, 100))
        self.assertEqual(player.gold, 12345)
        self.assertEqual(player.strength, 80)
        self.assertEqual(player.weight_max, 400)
        self.assertEqual(player.race, "human")
        self.assertEqual(player.followers_max, 5)

    def test_hit_points_after_death_mean_resurrection(self):
        self.feed(b"\x2C\x00")
        self.assertTrue(self.world.player.dead)
        self.feed(b"\xA1" + struct.pack(">IHH", 0x0000AAAA, 100, 11))
        self.assertFalse(self.world.player.dead)

    def test_vitals(self):
        self.feed(b"\xA1" + struct.pack(">IHH", 0x0000AAAA, 120, 90))
        self.feed(b"\xA2" + struct.pack(">IHH", 0x0000AAAA, 50, 25))
        self.assertEqual((self.world.player.hits, self.world.player.hits_max), (90, 120))
        self.assertEqual(self.world.player.mana, 25)

    def test_container_contents_and_grid_slot(self):
        body = struct.pack(">H", 2)
        for serial, graphic, amount in ((0x11, 0x0EED, 250), (0x12, 0x1F03, 1)):
            body += struct.pack(">IHBHHHBIH", serial, graphic, 0, amount,
                                3, 4, 0, 0xBEEF, 0)
        self.feed(variable(0x3C, body))
        contents = self.world.contents_of(0xBEEF)
        self.assertEqual({i.serial for i in contents}, {0x11, 0x12})
        self.assertEqual(self.world.items[0x11].amount, 250)

    def test_properties_name_an_item(self):
        self.world.item(0x77).graphic = 0x0EED
        arguments = "250 gold coins".encode("utf-16-le")
        body = struct.pack(">HIHI", 1, 0x77, 0, 0x1234)
        body += struct.pack(">IH", 1050045, len(arguments)) + arguments
        body += struct.pack(">I", 0)
        self.feed(variable(0xD6, body))
        self.assertEqual(self.world.items[0x77].name, "250 gold coins")

    def test_unicode_speech_lands_in_the_journal(self):
        body = struct.pack(">IHBHH", 0x1234, 0x0190, 0x00, 0x03B2, 3)
        body += b"ENU\x00" + b"Shopkeeper".ljust(30, b"\x00")
        body += "Greetings, traveller.".encode("utf-16-be") + b"\x00\x00"
        self.feed(variable(0xAE, body))
        entry = self.world.journal[-1]
        self.assertEqual(entry.speaker, "Shopkeeper")
        self.assertEqual(entry.text, "Greetings, traveller.")

    def test_cliloc_keeps_the_arguments(self):
        arguments = "\ta silver ring".encode("utf-16-le")
        body = struct.pack(">IHBHHI", 0x1234, 0x0190, 0x06, 0, 3, 1042971)
        body += b"System".ljust(30, b"\x00") + arguments
        self.feed(variable(0xC1, body))
        self.assertIn("a silver ring", self.world.journal[-1].text)

    def test_target_cursor_is_flagged_for_the_agent(self):
        self.feed(b"\x6C" + b"\x00" + struct.pack(">I", 0xABCD) + b"\x01" + b"\x00" * 12)
        self.assertTrue(self.world.target.active)
        self.assertEqual(self.world.target.cursor_id, 0xABCD)

    def test_gump_buttons_are_extracted(self):
        layout = "{page 0}{button 10 20 4005 4007 1 0 7}{button 10 40 4005 4007 1 0 9}"
        body = struct.pack(">IIII", 0x1, 0x2, 0, 0)
        body += struct.pack(">H", len(layout)) + layout.encode()
        body += struct.pack(">H", 1) + struct.pack(">H", 2) + "hi".encode("utf-16-be")
        self.feed(variable(0xB0, body))
        gump = self.world.gumps[0x1]
        self.assertEqual(gump.buttons(), [7, 9])
        self.assertEqual(gump.lines, ["hi"])

    def test_batched_packets_are_unwrapped(self):
        inner = b"\xA1" + struct.pack(">IHH", 0x0000AAAA, 100, 60)
        inner += b"\x1D" + struct.pack(">I", 0x1234)
        self.world.mobile(0x1234)
        self.feed(b"\xF7" + struct.pack(">HH", 5 + len(inner), 2) + inner)
        self.assertEqual(self.world.player.hits, 60)
        self.assertNotIn(0x1234, self.world.mobiles)

    def test_skills_are_named(self):
        body = b"\x02"
        body += struct.pack(">HHHBH", 1, 655, 600, 0, 1000)     # Alchemy 65.5
        body += struct.pack(">H", 0)
        self.feed(variable(0x3A, body))
        self.assertAlmostEqual(self.world.skills["Alchemy"]["value"], 65.5)
        self.assertAlmostEqual(self.world.skills["Alchemy"]["cap"], 100.0)

    def test_buy_list_finds_its_serials_and_its_vendor(self):
        # 0x3C sends the stock in reverse, each item's x its place in the list.
        stock, vendor = 0x4000B0B0, 0x00001111
        body = struct.pack(">H", 2)
        body += struct.pack(">IHBHHHBIH", 0x4000F002, 0x0F85, 0, 20, 2, 1, 0, stock, 0)
        body += struct.pack(">IHBHHHBIH", 0x4000F001, 0x0F84, 0, 20, 1, 1, 0, stock, 0)
        self.feed(variable(0x3C, body))

        body = struct.pack(">IB", stock, 2)
        body += struct.pack(">I", 3) + bytes([8]) + b"1023972\x00"       # garlic
        body += struct.pack(">I", 4) + bytes([8]) + b"1023973\x00"       # ginseng
        self.feed(variable(0x74, body))
        self.feed(b"\x24" + struct.pack(">IH", vendor, 0x0030))

        entries = self.world.vendor_items[vendor]
        self.assertEqual([(e.serial, e.name, e.price) for e in entries],
                         [(0x4000F001, "garlic", 3), (0x4000F002, "ginseng", 4)])
        self.assertNotIn(vendor, self.world.opened_containers)

    def test_corpse_waypoint_is_kept_until_removed(self):
        # ServUO marks your corpse on the map with 0xE5 and clears it with 0xE6.
        name = "Jevensen".encode("utf-16-le") + b"\x00\x00"
        body = struct.pack(">IHHbBHHI", 0x4172690A, 3490, 2716, 7, 1, 1, 0, 1046414)
        body += name + b"\x00\x00"
        self.feed(variable(0xE5, body))
        point = self.world.waypoints[0x4172690A]
        self.assertEqual((point.x, point.y, point.map, point.name), (3490, 2716, 1, "Jevensen"))
        self.assertTrue(point.corpse)

        self.feed(b"\xE6" + struct.pack(">I", 0x4172690A))
        self.assertNotIn(0x4172690A, self.world.waypoints)

    def test_unhandled_packet_is_ignored_not_fatal(self):
        self.feed(b"\x54" + b"\x00" * 11)                       # a sound effect
        self.assertTrue(True)


class SpeechKeywords(unittest.TestCase):
    """NPCs answer keyword ids, so these are checked against bytes by hand."""

    def said(self, text: str) -> bytes:
        sent = []
        client = Client(Config())
        client.connection = type("Open", (), {"closed": False, "send": sent.append})()
        client.say(text)
        return sent[0].build()

    def test_plain_speech_is_utf16(self):
        body = bytes([0x00]) + b"\x03\xB2\x00\x03ENU\x00" + "hi".encode("utf-16-be") + b"\x00\x00"
        self.assertEqual(self.said("hi"), b"\xAD" + struct.pack(">H", len(body) + 3) + body)

    def test_one_keyword(self):
        # 0xC0 flag; count 1 and id 0x171 as twelve bits each; UTF-8 text.
        body = bytes([0xC0]) + b"\x03\xB2\x00\x03ENU\x00" + b"\x00\x11\x71" + b"Lyle buy\x00"
        self.assertEqual(self.said("Lyle buy"), b"\xAD" + struct.pack(">H", len(body) + 3) + body)

    def test_two_keywords_pad_the_last_nibble(self):
        body = bytes([0xC0]) + b"\x03\xB2\x00\x03ENU\x00" + b"\x00\x20\x3C\x17\x10"
        body += b"vendor buy\x00"
        self.assertEqual(self.said("vendor buy"), b"\xAD" + struct.pack(">H", len(body) + 3) + body)

    def test_matching(self):
        self.assertEqual(speech.keywords_in("Lyle buy"), [0x171])
        self.assertEqual(speech.keywords_in("BANK"), [0x02])
        self.assertEqual(speech.keywords_in("Lyle train Magery"), [0x6C])
        self.assertEqual(speech.keywords_in("well met"), [])

    def test_stub_shard_decodes_it_like_the_server(self):
        packet = b"\xAD\x00\x00" + bytes([0xC0]) + b"\x03\xB2\x00\x03ENU\x00"
        packet += b"\x00\x20\x3C\x17\x10" + b"vendor buy\x00"
        self.assertEqual(Session.read_speech(packet), ("vendor buy", [0x3C, 0x171]))


class Geometry(unittest.TestCase):
    def test_distance_counts_diagonals_as_one(self):
        self.assertEqual(distance((0, 0), (3, 3)), 3)
        self.assertEqual(distance((0, 0), (0, 7)), 7)

    def test_bearings(self):
        self.assertEqual(direction_to((0, 0), (0, -1)), "north")
        self.assertEqual(direction_to((0, 0), (1, 1)), "southeast")
        self.assertEqual(direction_to((5, 5), (5, 5)), "here")


class PlaceStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.places = Places(pathlib.Path(self.tmp.name) / "places.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_find_and_nearest(self):
        self.places.add(Place("Britain Bank", 1434, 1699, kind="bank"))
        self.places.add(Place("Moonglow Gate", 4467, 1283, kind="moongate"))

        self.assertEqual(len(self.places.search("bank")), 1)
        nearest = self.places.nearest((1440, 1700), kind="bank")
        self.assertEqual(nearest[0].name, "Britain Bank")
        self.assertIn("tiles", nearest[0].summary((1440, 1700)))

    def test_adding_the_same_name_replaces_it(self):
        self.places.add(Place("Camp", 10, 10))
        self.places.add(Place("Camp", 20, 20))
        self.assertEqual(len(self.places.entries), 1)
        self.assertEqual(self.places.entries[0].x, 20)

    def test_survives_a_reload(self):
        self.places.add(Place("Camp", 10, 10, notes="by the river"))
        self.assertEqual(Places(self.places.path).entries[0].notes, "by the river")


if __name__ == "__main__":
    unittest.main()
