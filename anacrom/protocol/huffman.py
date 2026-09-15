"""Streaming decompressor for server -> client traffic.

Everything the game server sends after game login is Huffman-coded with a fixed
code book (see ``_codebook``).  The server flushes a terminator symbol and pads
to a byte boundary at the end of every write, so the stream is a sequence of
self-terminating, byte-aligned blocks -- but a block may be split across TCP
reads, and several blocks may arrive in one read.  ``Decompressor`` therefore
keeps its position in the code tree between calls and simply drops the padding
bits whenever it meets a terminator.
"""
from __future__ import annotations

from ._codebook import CODEBOOK, TERMINATOR

_INTERNAL = -1
_NONE = -1


def _build_tree() -> tuple[list[int], list[int], list[int]]:
    left: list[int] = [_NONE]
    right: list[int] = [_NONE]
    symbol: list[int] = [_INTERNAL]

    def new_node() -> int:
        left.append(_NONE)
        right.append(_NONE)
        symbol.append(_INTERNAL)
        return len(left) - 1

    for sym, (length, code) in enumerate(CODEBOOK):
        node = 0
        for shift in range(length - 1, -1, -1):
            bit = (code >> shift) & 1
            edge = right if bit else left
            nxt = edge[node]
            if nxt == _NONE:
                nxt = new_node()
                edge[node] = nxt
            node = nxt
        symbol[node] = sym

    return left, right, symbol


_LEFT, _RIGHT, _SYMBOL = _build_tree()


class Decompressor:
    """Feed it bytes off the socket, get decompressed packet bytes back."""

    __slots__ = ("_node",)

    def __init__(self) -> None:
        self._node = 0

    def reset(self) -> None:
        self._node = 0

    def feed(self, data: bytes) -> bytes:
        node = self._node
        out = bytearray()
        left, right, symbol = _LEFT, _RIGHT, _SYMBOL

        for byte in data:
            bit_index = 7
            while bit_index >= 0:
                node = right[node] if (byte >> bit_index) & 1 else left[node]
                bit_index -= 1

                if node == _NONE:
                    raise ValueError("compressed stream desynchronised")

                sym = symbol[node]
                if sym == _INTERNAL:
                    continue

                node = 0
                if sym == TERMINATOR:
                    # End of a server write: the rest of this byte is padding.
                    break
                out.append(sym)

        self._node = node
        return bytes(out)
