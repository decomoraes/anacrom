"""The ground itself, read from the client's own data files.

Three files describe what you can stand on.  ``tiledata.mul`` gives every land
and item tile its flags (impassable, surface, bridge...) and height.
``mapN`` holds the terrain: blocks of 8x8 cells, each a land tile and a height.
``staidxN``/``staticsN`` add the fixed objects on top -- walls, floors, stairs,
trees.  The server moves you by these same files (TileMatrix, TileData), so
reading them lets us plan a route the server will accept.

The map comes as ``mapNLegacyMUL.uop`` on modern clients: the old .mul cut into
chunks inside a container, each chunk named by a hash of its path.  Only the
formats this needs are read; everything is loaded lazily, block by block.
"""
from __future__ import annotations

import struct
from pathlib import Path

IMPASSABLE = 0x00000040
WET = 0x00000080
SURFACE = 0x00000200
BRIDGE = 0x00000400
DOOR = 0x20000000

# Width and height in tiles, by map index (ModernUO's MapDefinitions).
MAP_SIZES = {0: (7168, 4096), 1: (7168, 4096), 2: (2304, 1600),
             3: (2560, 2048), 4: (1448, 1448), 5: (1280, 4096)}

LAND_BLOCK = 196            # 4-byte header, then 64 cells of (u16 id, i8 z)


class TileData:
    """Flags and heights for land and item tiles (TileData.Load)."""

    def __init__(self, path: Path) -> None:
        data = Path(path).read_bytes()
        wide = len(data) >= 3188736                 # 7.0.9.0: 64-bit flags
        item_count = 0x10000 if wide else (0x8000 if len(data) >= 1644544 else 0x4000)
        flag = struct.Struct("<Q" if wide else "<I")
        offset = 0

        self.land_flags = [0] * 0x4000
        for i in range(0x4000):
            # The 64-bit layout has a quirk: its first group header comes at
            # entry 1, not entry 0.
            if (i == 1 or (i > 0 and i & 0x1F == 0)) if wide else i & 0x1F == 0:
                offset += 4
            self.land_flags[i] = flag.unpack_from(data, offset)[0]
            offset += flag.size + 2 + 20

        self.item_flags = [0] * item_count
        self.item_height = [0] * item_count
        for i in range(item_count):
            if i & 0x1F == 0:
                offset += 4
            self.item_flags[i] = flag.unpack_from(data, offset)[0]
            self.item_height[i] = data[offset + flag.size + 12]
            offset += flag.size + 13 + 20
        self.max_item = item_count - 1

    def item(self, graphic: int) -> tuple[int, int]:
        graphic &= self.max_item
        return self.item_flags[graphic], self.item_height[graphic]


def _hash_little2(text: str) -> int:
    """Bob Jenkins' lookup3 hashlittle2, as UOP names are hashed (UOPFiles)."""
    mask = 0xFFFFFFFF

    def rot(v: int, k: int) -> int:
        return ((v << k) | (v >> (32 - k))) & mask

    data = text.encode("ascii")
    length = len(data)
    a = b = c = (0xDEADBEEF + length) & mask
    k = 0
    while length > 12:
        a = (a + int.from_bytes(data[k:k + 4], "little")) & mask
        b = (b + int.from_bytes(data[k + 4:k + 8], "little")) & mask
        c = (c + int.from_bytes(data[k + 8:k + 12], "little")) & mask
        a = (a - c) & mask; a ^= rot(c, 4); c = (c + b) & mask
        b = (b - a) & mask; b ^= rot(a, 6); a = (a + c) & mask
        c = (c - b) & mask; c ^= rot(b, 8); b = (b + a) & mask
        a = (a - c) & mask; a ^= rot(c, 16); c = (c + b) & mask
        b = (b - a) & mask; b ^= rot(a, 19); a = (a + c) & mask
        c = (c - b) & mask; c ^= rot(b, 4); b = (b + a) & mask
        length -= 12
        k += 12
    if length:
        tail = data[k:] + bytes(12 - length)
        a = (a + int.from_bytes(tail[0:4], "little")) & mask
        b = (b + int.from_bytes(tail[4:8], "little")) & mask
        c = (c + int.from_bytes(tail[8:12], "little")) & mask
        c ^= b; c = (c - rot(b, 14)) & mask
        a ^= c; a = (a - rot(c, 11)) & mask
        b ^= a; b = (b - rot(a, 25)) & mask
        c ^= b; c = (c - rot(b, 16)) & mask
        a ^= c; a = (a - rot(c, 4)) & mask
        b ^= a; b = (b - rot(a, 14)) & mask
        c ^= b; c = (c - rot(b, 24)) & mask
    return (b << 32) | c


def _uop_chunks(path: Path) -> list[tuple[int, int]]:
    """(offset, size) of each data chunk, in index order."""
    root = f"build/{path.stem.lower()}"
    wanted = {_hash_little2(f"{root}/{i:08d}.dat"): i for i in range(0x14000)}
    found: dict[int, tuple[int, int]] = {}
    with open(path, "rb") as f:
        magic, _version, _signature, next_block = struct.unpack("<IiIq", f.read(20))
        if magic != 0x50594D:
            raise ValueError(f"{path} is not a UOP file")
        while next_block:
            f.seek(next_block)
            count, next_block = struct.unpack("<iq", f.read(12))
            for _ in range(count):
                (offset, header, compressed, size, name_hash, _adler,
                 _flag) = struct.unpack("<qiiiQIh", f.read(34))
                if offset and size > 0 and name_hash in wanted:
                    found[wanted[name_hash]] = (offset + header, size)
    return [found[i] for i in sorted(found)]


class MapData:
    """Land and statics for one map, read a block at a time and cached."""

    def __init__(self, folder: Path, index: int, tiles: TileData | None = None) -> None:
        folder = Path(folder)
        self.index = index
        self.width, self.height = MAP_SIZES[index]
        self.block_height = self.height >> 3
        self.tiles = tiles or TileData(folder / "tiledata.mul")

        uop = folder / f"map{index}LegacyMUL.uop"
        self._chunks = _uop_chunks(uop) if uop.exists() else None
        self._map = open(uop if uop.exists() else folder / f"map{index}.mul", "rb")
        self._index = open(folder / f"staidx{index}.mul", "rb")
        self._statics = open(folder / f"statics{index}.mul", "rb")
        self._land: dict[int, bytes] = {}
        self._static: dict[int, list[list[tuple[int, int]]]] = {}

    def _offset(self, flat: int) -> int:
        if self._chunks is None:
            return flat
        total = 0
        for start, size in self._chunks:
            if flat < total + size:
                return start + flat - total
            total += size
        raise ValueError("offset beyond the map")

    def land(self, x: int, y: int) -> tuple[int, int]:
        """(tile id, z) of the ground at a tile."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return 0, 0
        block = (x >> 3) * self.block_height + (y >> 3)
        cells = self._land.get(block)
        if cells is None:
            self._map.seek(self._offset(block * LAND_BLOCK + 4))
            cells = self._land[block] = self._map.read(192)
        tile, z = struct.unpack_from("<Hb", cells, (((y & 7) << 3) + (x & 7)) * 3)
        return tile, z

    def statics(self, x: int, y: int) -> list[tuple[int, int]]:
        """(graphic, z) of every fixed object on a tile."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return []
        block = (x >> 3) * self.block_height + (y >> 3)
        cells = self._static.get(block)
        if cells is None:
            cells = [[] for _ in range(64)]
            self._index.seek(block * 12)
            lookup, length = struct.unpack("<ii", self._index.read(8))
            if lookup >= 0 and length > 0:
                self._statics.seek(lookup)
                raw = self._statics.read(length)
                for i in range(0, len(raw) - 6, 7):
                    graphic, sx, sy, sz = struct.unpack_from("<HBBb", raw, i)
                    cells[((sy & 7) << 3) + (sx & 7)].append((graphic, sz))
            self._static[block] = cells
        return cells[((y & 7) << 3) + (x & 7)]

    def average_z(self, x: int, y: int) -> tuple[int, int, int]:
        """Lowest, centre and highest ground height (Map.GetAverageZ)."""
        z_top = self.land(x, y)[1]
        z_left = self.land(x, y + 1)[1]
        z_right = self.land(x + 1, y)[1]
        z_bottom = self.land(x + 1, y + 1)[1]
        low = min(z_top, z_left, z_right, z_bottom)
        high = max(z_top, z_left, z_right, z_bottom)
        if abs(z_top - z_bottom) > abs(z_left - z_right):
            total = z_left + z_right
        else:
            total = z_top + z_bottom
        # C#'s FloorAverage: step a negative sum down, then divide toward zero.
        avg = int((total - 1 if total < 0 else total) / 2)
        return low, avg, high
