"""Small PNG drawing helpers for the starter figure."""

from pathlib import Path
import struct
import zlib


COLORS = {
    "background": (255, 255, 255),
    "axis": (45, 45, 45),
    "grid": (220, 220, 220),
    "primary": (46, 92, 138),
}


def write_png(path, width, height, pixels):
    """Write RGB pixels to a PNG file using only the Python standard library."""
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


class Canvas:
    def __init__(self, width=900, height=520, background=COLORS["background"]):
        self.width = width
        self.height = height
        self.pixels = [[*background] * width for _ in range(height)]

    def point(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y][x * 3 : x * 3 + 3] = list(color)

    def line(self, x1, y1, x2, y2, color, width=2):
        steps = max(abs(x2 - x1), abs(y2 - y1), 1)
        for i in range(steps + 1):
            x = round(x1 + (x2 - x1) * i / steps)
            y = round(y1 + (y2 - y1) * i / steps)
            for dx in range(-(width // 2), width // 2 + 1):
                for dy in range(-(width // 2), width // 2 + 1):
                    self.point(x + dx, y + dy, color)

    def save(self, path):
        write_png(path, self.width, self.height, self.pixels)
