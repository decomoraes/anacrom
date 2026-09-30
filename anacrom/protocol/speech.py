"""Speech keywords: the numbers NPCs actually listen for.

The real client matches every line you type against speech.mul and sends the
ids of whatever matched along with the text.  NPCs are keyed on those ids, not
on the words: a vendor sells on keyword 0x171 (*buy*), and a plain "buy" with
no keyword attached gets no answer at all (VendorAI.OnSpeech).

There is no speech.mul here, so this carries the entries NPCs act on, with the
patterns ModernUO's comments give for them.  A ``*`` at either end matches
anything on that side; without one, the line has to start or end there.

On the wire (IncomingMessagePackets.UnicodeSpeech) the message type gains
0xC0, a 12-bit count and 12-bit ids follow packed big-endian and padded to a
byte, and the text becomes null-terminated UTF-8 instead of UTF-16.
"""
from __future__ import annotations

ENCODED = 0xC0
MAX_KEYWORDS = 50                    # the server drops the line past this

KEYWORDS: dict[int, tuple[str, ...]] = {
    0x0000: ("*withdraw*",),
    0x0001: ("*balance*",),
    0x0002: ("*bank*",),
    0x0003: ("*check*",),
    0x0004: ("*join*", "*member*"),
    0x0005: ("*resign*", "*quit*"),
    0x0007: ("*guards*",),
    0x0008: ("*stable*",),
    0x0009: ("*claim*",),
    0x0030: ("*news*",),
    0x0038: ("*appraise*",),
    0x003C: ("*vendor buy*",),
    0x006C: ("*train*",),
    0x009D: ("*move*",),
    0x009E: ("*time*",),
    0x014D: ("*vendor sell*",),
    0x0171: ("*buy*",),
    0x0177: ("*sell*",),
}


def _matches(pattern: str, text: str) -> bool:
    core = pattern.strip("*")
    if not pattern.startswith("*") and not text.startswith(core):
        return False
    if not pattern.endswith("*") and not text.endswith(core):
        return False
    return core in text


def keywords_in(text: str) -> list[int]:
    lowered = text.casefold()
    return [kid for kid, patterns in KEYWORDS.items()
            if any(_matches(p, lowered) for p in patterns)][:MAX_KEYWORDS]


def pack(keywords: list[int]) -> bytes:
    """The count, then each id, twelve bits apiece; a trailing nibble is zero."""
    value, bits = 0, 0
    for number in [len(keywords), *keywords]:
        value = (value << 12) | (number & 0xFFF)
        bits += 12
    if bits % 8:
        value <<= 4
        bits += 4
    return value.to_bytes(bits // 8, "big")
