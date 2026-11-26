#!/usr/bin/env python3
"""Generate the vision fixture: a short number drawn large, black on white.

Usage: generate.py            (rewrites token.png and SHA256SUMS beside this file)

The image is drawn here from a 5x7 block font, so it contains no third-party media.
Standard library only. The launcher never imports this file: checks/vision.py reads
the committed PNG's bytes. The compressed bytes can differ between zlib builds, so
the tests compare the committed file to SHA256SUMS and its PIXELS to raster().
"""
from __future__ import annotations

import hashlib
import os
import struct
import zlib

TOKEN = "7319"                      # checks/vision.py VISION_TOKEN must say the same
WIDTH, HEIGHT = 448, 224            # multiples of the 14, 16 and 28 px patches encoders use
CELL = 16                           # pixels per font cell: a digit is 80 x 112
NAME = "token.png"

_FONT = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
}


def raster(token=TOKEN):
    """The image as HEIGHT rows of WIDTH luminance values (0 black, 255 white)."""
    cols = len(token) * 5 + len(token) - 1          # one empty column between digits
    left, top = (WIDTH - cols * CELL) // 2, (HEIGHT - 7 * CELL) // 2
    if left < 0:
        raise ValueError(f"{token!r} does not fit in {WIDTH} px")
    rows = [bytearray(b"\xff" * WIDTH) for _ in range(HEIGHT)]
    for i, ch in enumerate(token):
        for gy, line in enumerate(_FONT[ch]):
            for gx, bit in enumerate(line):
                if bit == "1":
                    x = left + (i * 6 + gx) * CELL
                    for y in range(top + gy * CELL, top + (gy + 1) * CELL):
                        rows[y][x:x + CELL] = b"\x00" * CELL
    return [bytes(r) for r in rows]


def png(rows):
    """An 8-bit RGB PNG of luminance rows."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(v for v in row for _ in range(3)) for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    data = png(raster())
    with open(os.path.join(here, NAME), "wb") as f:
        f.write(data)
    with open(os.path.join(here, "SHA256SUMS"), "w") as f:
        f.write(f"{hashlib.sha256(data).hexdigest()}  {NAME}\n")
    print(f"{NAME}: {len(data)} bytes")


if __name__ == "__main__":
    main()
