"""Minimal RGB PNG writer, so the demo job needs no image library."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


class Canvas:
    def __init__(self, width: int, height: int, color=(0, 0, 0)):
        self.width = width
        self.height = height
        self.pixels = bytearray(bytes(color) * (width * height))

    def fill_rect(self, x: int, y: int, w: int, h: int, color) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(y0, y1):
            start = (yy * self.width + x0) * 3
            self.pixels[start:start + len(row)] = row

    def outline(self, x: int, y: int, w: int, h: int, color, thickness: int = 1) -> None:
        t = thickness
        self.fill_rect(x, y, w, t, color)
        self.fill_rect(x, y + h - t, w, t, color)
        self.fill_rect(x, y, t, h, color)
        self.fill_rect(x + w - t, y, t, h, color)

    def save(self, path: Path) -> None:
        stride = self.width * 3
        raw = b"".join(b"\x00" + bytes(self.pixels[y * stride:(y + 1) * stride]) for y in range(self.height))

        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
        Path(path).write_bytes(data)
