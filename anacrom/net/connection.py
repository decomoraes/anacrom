"""The socket half of the client: seed, compression, framing, capture."""
from __future__ import annotations

import errno
import socket
import time
from collections import deque
from pathlib import Path

from ..protocol.huffman import Decompressor
from ..protocol.packets import VAR, DEFAULT_PROFILE, Profile, ProtocolError, Writer, frame


class Connection:
    """One TCP connection to a login or game server.

    Traffic we send is always plain.  Traffic we receive is plain until the
    server acknowledges game login, after which it is Huffman-coded -- callers
    flip :meth:`enable_compression` at that point, exactly where the server does.
    """

    def __init__(
        self,
        host: str,
        port: int,
        profile: Profile = DEFAULT_PROFILE,
        capture: str | Path | None = None,
        connect_timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.profile = profile
        self.connected_at = time.time()

        self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock.setblocking(False)

        self._decompressor: Decompressor | None = None
        self._pending = b""          # decompressed bytes not yet a whole packet
        self._recent: deque[bytes] = deque(maxlen=6)    # for diagnosing a desync
        self._closed = False
        self._capture = open(capture, "ab") if capture else None

    # -- lifecycle ---------------------------------------------------------

    def fileno(self) -> int:
        return self._sock.fileno()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        finally:
            if self._capture:
                self._capture.close()
                self._capture = None

    def enable_compression(self) -> None:
        self._decompressor = Decompressor()

    # -- io ----------------------------------------------------------------

    def send(self, data: bytes | Writer) -> None:
        if isinstance(data, Writer):
            data = data.build()
        if self._closed:
            raise ConnectionError("connection is closed")
        if self._capture:
            self._capture.write(b"\x01" + len(data).to_bytes(4, "big") + data)
        view = memoryview(data)
        while view:
            try:
                sent = self._sock.send(view)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            view = view[sent:]

    def send_seed(self, seed: int, version: tuple[int, int, int, int]) -> None:
        """Login-server seed: the extended form, which also states our version."""
        major, minor, revision, proto = version
        packet = (
            b"\xEF"
            + seed.to_bytes(4, "big")
            + major.to_bytes(4, "big")
            + minor.to_bytes(4, "big")
            + revision.to_bytes(4, "big")
            + proto.to_bytes(4, "big")
        )
        self.send(packet)

    def send_raw_seed(self, seed: int) -> None:
        """Game-server seed: four bare bytes, which is the auth key we were given."""
        self.send(seed.to_bytes(4, "big"))

    def poll(self) -> list[bytes]:
        """Drain the socket and return whole packets. Empty list means 'nothing yet'."""
        if self._closed:
            return []

        chunks = []
        while True:
            try:
                chunk = self._sock.recv(65536)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                self.close()
                raise ConnectionError(f"recv failed: {exc}") from exc

            if not chunk:
                self.close()
                break
            chunks.append(chunk)
            if len(chunk) < 65536:
                break

        if not chunks:
            return []

        raw = b"".join(chunks)
        if self._capture:
            self._capture.write(b"\x02" + len(raw).to_bytes(4, "big") + raw)

        if self._decompressor is not None:
            raw = self._decompressor.feed(raw)

        self._pending += raw
        try:
            packets, self._pending = frame(self._pending, self.profile)
        except ProtocolError as exc:
            self.close()
            raise ProtocolError(f"{exc}; {self._desync_report()}") from None
        self._recent.extend(packets)
        return packets

    def _desync_report(self) -> str:
        """Where framing went wrong: the bad length is in the last good packet."""
        good = list(self._recent)
        data, offset = self._pending, 0
        while offset < len(data):
            try:
                size = self.profile.length_of(data[offset])
            except ProtocolError:
                break
            if size == VAR:
                size = int.from_bytes(data[offset + 1:offset + 3], "big")
                if size < 3:
                    break
            if offset + size > len(data):
                break
            good.append(data[offset:offset + size])
            offset += size
        ids = " ".join(f"{p[0]:02X}({len(p)})" for p in good[-10:])
        last = good[-1][:48].hex(" ") if good else "(none)"
        failed = self._pending[offset:offset + 48].hex(" ")
        return f"framed before it: {ids}; last good packet: {last}; then: {failed}"
