"""Redact, then read (Tier 0): personal and secret data never reaches a stored signal.

Every text below is sample data.
"""

from __future__ import annotations

import pytest

from omnigent.server.factory.discovery.redact import redact


@pytest.mark.parametrize(
    ("text", "kind", "placeholder"),
    [
        ("mail me at jane.doe+app@example.org please", "email", "[email]"),
        ("call +1 415 555 0134 after six", "phone", "[phone]"),
        ("my number is (020) 7946-0958", "phone", "[phone]"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789 leaked", "token", "[token]"),
        ("the key sk-live_ABCDEFGHIJKLMNOPQRSTUVWX broke", "token", "[token]"),
        ("aws AKIAABCDEFGHIJKLMNOP in the log", "token", "[token]"),
        ("Authorization: Bearer abcdefghijklmnop1234567890", "token", "[token]"),
        ("password=hunter2hunter2 in the config", "token", "[token]"),
        ("server 192.168.10.24 timed out", "ip", "[ip]"),
        ("v6 at 2001:db8:85a3::8a2e:370:7334 works", "ip", "[ip]"),
        ("open https://app.example.org/share?token=abc123def456 now", "url_secret", "[url]"),
        ("https://user:hunter2@example.org/x is the link", "url_secret", "[url]"),
        ("card 4242 4242 4242 4242 was charged", "card", "[card]"),
        ("db at postgres://admin:S3cr3t!pw@db.internal/app (sample)", "url_secret", "[url]"),
        ("cache redis://:supersecretpass@cache:6379 (sample)", "url_secret", "[url]"),
        ("token glpat-abcdefghijklmnopqrstu1 leaked (sample)", "token", "[token]"),
        ("token npm_abcdefghijklmnopqrstuvwxyz0123456789 leaked (sample)", "token", "[token]"),
        ("token hf_abcdefghijklmnopqrstuvwxyz0123456 leaked (sample)", "token", "[token]"),
        ("key rk_live_abcdefghijklmnop12 leaked (sample)", "token", "[token]"),
        ("call me on 4155550134 tomorrow (sample)", "phone", "[phone]"),
        ("my mobile: +447911123456 (sample)", "phone", "[phone]"),
    ],
)
def test_each_floor_kind_becomes_a_typed_placeholder(
    text: str, kind: str, placeholder: str
) -> None:
    out = redact(text)

    assert placeholder in out.text, out.text
    assert out.counts.get(kind, 0) >= 1, out.counts
    assert out.total == sum(out.counts.values())


@pytest.mark.parametrize(
    "text",
    [
        "Builds fail on 2026-10-30 after the 3rd try (sample)",
        "Upgraded from 1.2.3 to 1.4.0 and it broke (sample)",
        "Commit 3f9a1c2b7d8e9f00112233445566778899aabbcc fixed it (sample)",
        "It took 12:30:45 to finish (sample)",
        "Order 1234567890123 is not a card (sample)",
        "https://docs.example.org/guide?page=2 explains it (sample)",
        "Read s3://sample-bucket/reports/latest for the numbers (sample)",
        "Ticket 4155550134 was closed (sample)",
    ],
)
def test_ordinary_words_numbers_and_links_stay(text: str) -> None:
    out = redact(text)

    assert out.text == text
    assert out.counts == {}


def test_several_kinds_in_one_text_are_counted() -> None:
    out = redact(
        "Ping ana@example.org or bo@example.org from 10.0.0.7 (sample); "
        "she pasted xoxb-1234567890-abcdefghij"
    )

    assert out.counts == {"email": 2, "ip": 1, "token": 1}
    assert "example.org" not in out.text and "10.0.0.7" not in out.text
    assert "xoxb" not in out.text


def test_the_floor_applies_whatever_a_source_asks_for() -> None:
    # A source can only add kinds; the six floor kinds always run.
    out = redact("reach me at jane@example.org (sample)", kinds=())

    assert out.text == "reach me at [email] (sample)"


def test_a_plain_url_keeps_its_path_but_an_ip_in_it_goes() -> None:
    out = redact("see http://10.1.2.3/status for details (sample)")

    assert out.text == "see http://[ip]/status for details (sample)"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "open https://x.example.org/a?token=abcd1234efgh5678 now (sample)",
            "open [url] now (sample)",
        ),
        ("db postgres://admin:S3cr3t!pw@db.internal/app (sample)", "db [url] (sample)"),
        ("cache redis://:supersecretpass@cache:6379 (sample)", "cache [url] (sample)"),
    ],
)
def test_a_secret_bearing_url_goes_whole(text: str, expected: str) -> None:
    out = redact(text)

    assert out.text == expected
    assert out.counts == {"url_secret": 1}
