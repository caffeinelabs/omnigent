"""Transparent client-side compression for opaque text columns.

A handful of columns hold machine-generated JSON or free text that is never
queried in SQL or read by hand — per-conversation ``session_state`` /
``session_usage``, native ``terminal_launch_args``, comment bodies/anchors, and
agent descriptions. Compressing them on the client gives a uniform on-disk size
across every backend: MySQL's InnoDB does not compress ``TEXT``/``BLOB`` by
default and SQLite never does, so relying on per-backend storage compression
would leave those two uncompressed while PostgreSQL (TOAST) compresses.

Stored layout (bytes), chosen so post-migration and legacy rows coexist without
a backfill:

* **New values are framed:** a leading NUL sentinel (``0x00``) followed by a
  one-byte codec id and the payload. Valid text in these columns can never
  start with NUL — PostgreSQL forbids NUL in ``text`` outright, and the JSON
  they hold always leads with ``{``/``[``/``"`` — so the sentinel is an
  unambiguous "this row is framed" marker.
* **Legacy values are unframed UTF-8 text** (written while the column was
  ``TEXT``). They are detected by the absent sentinel — or, under SQLite's
  dynamic typing, by arriving as ``str`` — and returned unchanged. Each such
  row re-frames itself the next time it is written.
* **Frames in a PostgreSQL column that is still ``TEXT``** (the binary-type
  migration never ran there) arrive as bytea hex text, ``\\x0000…``: PostgreSQL
  accepts the bound ``bytea`` through its bytea→text assignment cast. They are
  recognised by that prefix and unwrapped, so such a column reads back
  correctly without a backfill.
"""

from __future__ import annotations

import re

import zstandard
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

# Leading byte marking a framed (post-migration) value. Legacy text never
# begins with NUL, so its presence unambiguously distinguishes the two formats.
_SENTINEL = 0x00
# Codec ids, stored as the byte after the sentinel.
_CODEC_RAW = 0x00  # payload stored uncompressed (below the size threshold)
_CODEC_ZSTD = 0x01  # payload compressed with zstd

# Below this many UTF-8 bytes, zstd's frame overhead outweighs the gain, so the
# payload is framed but left uncompressed.
_MIN_COMPRESS_BYTES = 64
# Write-once / read-rarely columns, so favour ratio over speed. The payloads are
# small enough that the window size a high level implies never fills.
_LEVEL = 19
# A frame rendered as PostgreSQL bytea hex text: ``\x`` + sentinel + a known
# codec + hex payload. Plaintext would have to be a literal ``\x0000``/``\x0001``
# followed only by hex digits, and must still decode as a frame.
_PG_HEX_FRAME = re.compile(r"\\x00(?:00|01)(?:[0-9a-fA-F]{2})*")


def encode(text: str | None) -> bytes | None:
    """Frame *text* for storage.

    :param text: The plaintext to store, or ``None``.
    :returns: ``sentinel + codec + payload`` bytes, or ``None`` when *text* is
        ``None``.
    """
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) < _MIN_COMPRESS_BYTES:
        return bytes((_SENTINEL, _CODEC_RAW)) + raw
    packed = zstandard.ZstdCompressor(level=_LEVEL).compress(raw)
    return bytes((_SENTINEL, _CODEC_ZSTD)) + packed


def decode(value: bytes | str | memoryview | None) -> str | None:
    """Inverse of :func:`encode`; also passes through legacy unframed text.

    :param value: The stored column value: framed bytes, legacy UTF-8 bytes,
        a ``str`` from a text-typed column (legacy plaintext, or a frame as
        PostgreSQL bytea hex text), a ``memoryview`` (some drivers), or ``None``.
    :returns: The decoded plaintext, or ``None`` when *value* is ``None``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return _decode_text(value)
    if isinstance(value, memoryview):
        value = value.tobytes()
    if not value or value[0] != _SENTINEL:
        # Empty, or legacy UTF-8 text (no sentinel — cannot start with NUL).
        return value.decode("utf-8")
    return _decode_frame(value)


def _decode_frame(value: bytes) -> str:
    """Decode ``sentinel + codec + payload`` bytes."""
    codec, payload = value[1], value[2:]
    if codec == _CODEC_ZSTD:
        return zstandard.ZstdDecompressor().decompress(payload).decode("utf-8")
    return payload.decode("utf-8")


def _decode_text(value: str) -> str:
    """Decode a value the driver returned as ``str`` (the column is text-typed).

    Usually legacy plaintext — under SQLite's dynamic typing a value written
    before the column became a BLOB comes back as ``str`` — returned unchanged.
    A frame that PostgreSQL stored in a ``TEXT`` column comes back as bytea hex
    text (``\\x0000…``) and is unwrapped.

    :param value: The stored text.
    :returns: The decoded plaintext.
    """
    if not _PG_HEX_FRAME.fullmatch(value):
        return value
    try:
        return _decode_frame(bytes.fromhex(value[2:]))
    except (zstandard.ZstdError, UnicodeDecodeError):
        return value  # Not a frame after all: plaintext that only looked like one.


class CompressedText(TypeDecorator[str]):
    """A ``str`` column stored as a zstd-compressed ``BLOB`` / ``BYTEA``.

    Transparent at the ORM boundary: callers read and write ``str`` exactly as
    they would with :class:`~sqlalchemy.Text`, and compression happens on the
    way in and out. Legacy rows written when the column was ``TEXT`` decode
    unchanged and re-frame on their next write, so no backfill is required.

    Use only for columns that are never filtered, ordered, or pattern-matched
    in SQL — the stored bytes are opaque to the database.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> bytes | None:
        """Compress on the way into the database."""
        del dialect
        return encode(value)

    def process_result_value(
        self, value: bytes | str | memoryview | None, dialect: object
    ) -> str | None:
        """Decompress on the way out of the database."""
        del dialect
        return decode(value)
