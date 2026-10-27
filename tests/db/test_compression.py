"""Tests for the opaque-column compression codec (``omnigent.db.compression``).

Covers the frame format, the raw/zstd threshold, and — critically — that
values written before a column was migrated (unframed ``TEXT``) still decode
unchanged, so the ``TEXT`` → ``BLOB`` migration needs no backfill.
"""

from __future__ import annotations

import json

import pytest

from omnigent.db.compression import _MIN_COMPRESS_BYTES, decode, encode


def test_none_round_trips() -> None:
    """``None`` encodes to ``None`` and decodes back to ``None``."""
    assert encode(None) is None
    assert decode(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x",
        json.dumps(["--dangerously-skip-permissions"]),  # small -> stored raw
        json.dumps({"input_tokens": 128345, "output_tokens": 6789, "total_tokens": 135134}),
        "café — naïve — 日本語 — 🎉 " * 5,  # multibyte utf-8
        json.dumps({"history": [{"turn": i, "note": f"entry {i}"} for i in range(300)]}),
    ],
    ids=["empty", "single-char", "small-raw", "usage-json", "unicode", "large-json"],
)
def test_round_trip(value: str) -> None:
    """Every payload decodes back to exactly what was encoded."""
    assert decode(encode(value)) == value


def test_small_values_stored_raw_large_values_compressed() -> None:
    """Sub-threshold payloads use the raw codec; larger ones use zstd."""
    small = "a" * (_MIN_COMPRESS_BYTES - 1)
    large = json.dumps({f"k{i}": f"value padding {i}" for i in range(200)})
    small_blob, large_blob = encode(small), encode(large)
    assert small_blob is not None and large_blob is not None
    assert small_blob[1] == 0x00, "small payload should be stored uncompressed"
    assert large_blob[1] == 0x01, "large payload should be zstd-compressed"
    # The whole point: the compressed blob is meaningfully smaller than raw.
    assert len(large_blob) < len(large.encode("utf-8"))


def test_framed_values_start_with_nul_sentinel() -> None:
    """New values carry the NUL sentinel that distinguishes them from legacy text."""
    blob = encode(json.dumps({"a": 1}))
    assert blob is not None and blob[0] == 0x00


def test_legacy_unframed_bytes_decode_unchanged() -> None:
    """Pre-migration UTF-8 bytes (no sentinel) pass through untouched."""
    legacy = b'{"input_tokens":5,"note":"written before migration"}'
    assert decode(legacy) == legacy.decode("utf-8")


def test_legacy_str_decodes_unchanged() -> None:
    """SQLite dynamic typing hands back legacy rows as ``str``; pass them through."""
    assert decode('{"legacy":"sqlite"}') == '{"legacy":"sqlite"}'


def test_memoryview_is_accepted() -> None:
    """Some drivers return binary columns as ``memoryview``."""
    blob = encode("x" * 200)
    assert blob is not None
    assert decode(memoryview(blob)) == "x" * 200
    assert decode(memoryview(b'{"legacy":1}')) == '{"legacy":1}'


def test_empty_bytes_decode_to_empty_string() -> None:
    """A legacy empty ``TEXT`` value (empty bytes) decodes to ``''``."""
    assert decode(b"") == ""


# ── frames stored in a PostgreSQL column that is still TEXT ─────────────────
#
# PostgreSQL accepts a bound bytea into a TEXT column through its bytea→text
# assignment cast and stores the hex rendering, ``\x0000…``, which the driver
# then returns as ``str``.


def _pg_hex(blob: bytes | None) -> str:
    """What a TEXT column holds after PostgreSQL casts *blob* to text."""
    assert blob is not None
    return "\\x" + blob.hex()


@pytest.mark.parametrize(
    "value",
    [
        json.dumps({"icon": "🏭"}),  # small -> raw codec
        json.dumps({"history": [{"turn": i} for i in range(200)]}),  # zstd codec
        "",
    ],
    ids=["raw", "zstd", "empty"],
)
def test_pg_hex_text_frame_decodes(value: str) -> None:
    """A frame stored as bytea hex text decodes to the original value."""
    assert decode(_pg_hex(encode(value))) == value


def test_pg_hex_text_frame_accepts_upper_case_digits() -> None:
    """Hex digits are case-insensitive."""
    blob = encode('{"a":1}')
    assert blob is not None
    assert decode("\\x" + blob.hex().upper()) == '{"a":1}'


@pytest.mark.parametrize(
    "text",
    [
        "\\x",  # no frame bytes
        "\\x41",  # hex, but no sentinel
        "\\x0002" + "41",  # sentinel with an unknown codec
        "\\x000",  # odd number of digits
        "\\x0000zz",  # not hex
        "\\x0001deadbeef",  # zstd codec id, but not a zstd frame
        "\\x0000ff",  # raw codec id, but not UTF-8
        "C:\\x0000",  # does not start with the prefix
    ],
)
def test_text_that_only_resembles_a_pg_hex_frame_is_unchanged(text: str) -> None:
    """Plaintext is only unwrapped when it is a well-formed frame."""
    assert decode(text) == text


@pytest.mark.parametrize(
    ("text", "unwrapped"),
    [
        ("\\x0000", ""),
        ("\\x0000" + b"hi".hex(), "hi"),
    ],
)
def test_text_that_is_a_well_formed_pg_hex_frame_is_unwrapped(text: str, unwrapped: str) -> None:
    """Accepted ambiguity: plaintext that *is* a well-formed hex frame is unwrapped.

    Only a ``str`` from a text-typed column reaches this path (legacy SQLite
    rows, or a PostgreSQL column the binary-type migration skipped), and free
    text would have to be exactly ``\\x0000`` plus UTF-8 hex digits.
    """
    assert decode(text) == unwrapped
