"""
(sample) Posters and recordings for the seeded demos. Not a test module.

A poster is an app frame drawn without fonts (a header, a sidebar, cards and
a chat column in the demo's accent), written as a PNG with the standard
library. A recording is the same frame over *seconds*, with one more chat
bubble each second, encoded as WebM by ``ffmpeg`` when it is on the PATH
(``None`` otherwise: the demo then has a poster and no recording).
"""

from __future__ import annotations

import functools
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Final

WIDTH: Final = 640
HEIGHT: Final = 360

Rgb = tuple[int, int, int]

_PAGE: Final[Rgb] = (244, 246, 249)
_WHITE: Final[Rgb] = (255, 255, 255)
_LINE: Final[Rgb] = (227, 230, 235)
_SIDE: Final[Rgb] = (236, 239, 243)
_MUTED: Final[Rgb] = (207, 213, 222)
_INK: Final[Rgb] = (32, 36, 43)


def _hex(color: str) -> Rgb:
    value = color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


class _Canvas:
    def __init__(self, fill: Rgb) -> None:
        self.rows = [bytearray(bytes(fill) * WIDTH) for _ in range(HEIGHT)]

    def rect(self, x: int, y: int, w: int, h: int, color: Rgb) -> None:
        x0, x1 = max(0, x), min(WIDTH, x + w)
        y0, y1 = max(0, y), min(HEIGHT, y + h)
        if x0 >= x1:
            return
        run = bytes(color) * (x1 - x0)
        for row in self.rows[y0:y1]:
            row[x0 * 3 : x1 * 3] = run

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(row) for row in self.rows)

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b"")
        )


def frame(accent: str, variant: int = 0, bubbles: int = 3) -> bytes:
    """
    One app frame as a PNG.

    :param accent: The demo's colour, e.g. ``"#16a34a"``.
    :param variant: Varies the card widths so demos do not all look alike.
    :param bubbles: Chat bubbles shown (a recording adds one a second).
    """
    tint = _hex(accent)
    c = _Canvas(_PAGE)
    c.rect(0, 0, WIDTH, 44, _WHITE)
    c.rect(0, 44, WIDTH, 2, _LINE)
    c.rect(16, 14, 16, 16, tint)
    c.rect(44, 18, 88, 10, _MUTED)
    c.rect(WIDTH - 104, 12, 84, 20, tint)
    c.rect(0, 46, 116, HEIGHT - 46, _SIDE)
    for i in range(6):
        c.rect(16, 68 + i * 28, 80 - (i * 13 + variant * 9) % 28, 10, _MUTED)
    c.rect(144, 64, 190 + variant * 16, 18, _INK)
    for i in range(3):
        y = 104 + i * 76
        w = 256 - ((i + variant) % 3) * 44
        c.rect(144, y, 304, 60, _LINE)
        c.rect(146, y + 2, 300, 56, _WHITE)
        c.rect(160, y + 16, w - 80, 10, (58, 64, 74))
        c.rect(160, y + 36, w, 8, _MUTED)
    c.rect(468, 60, 156, 284, _LINE)
    c.rect(470, 62, 152, 280, _WHITE)
    for i in range(min(bubbles, 6)):
        mine = i % 2 == 1
        c.rect(500 if mine else 484, 80 + i * 40, 108, 28, tint if mine else _SIDE)
    c.rect(484, 304, 124, 24, _PAGE)
    return c.png()


@functools.cache
def recording(accent: str, seconds: int, work: Path, variant: int = 0) -> bytes | None:
    """
    *seconds* of the app frame, one more chat bubble each second, as WebM.

    :param work: Where the frames and the encoding are written.
    :returns: The video, or ``None`` when ``ffmpeg`` is not installed, fails or
        takes over two minutes.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    where = work / f"{accent.lstrip('#')}-{seconds}-{variant}"
    where.mkdir(parents=True, exist_ok=True)
    for n in range(seconds):
        (where / f"f{n:03d}.png").write_bytes(frame(accent, variant, bubbles=1 + n % 6))
    out = where / "demo.webm"
    argv = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "1",
        "-i",
        str(where / "f%03d.png"),
        "-c:v",
        "libvpx",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-b:v",
        "300k",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "5",
        str(out),
    ]
    try:
        done = subprocess.run(argv, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.read_bytes() if done.returncode == 0 and out.exists() else None


__all__ = ["HEIGHT", "WIDTH", "frame", "recording"]
