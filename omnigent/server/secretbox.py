"""Symmetric encryption for secrets stored at rest.

A thin wrapper over :class:`cryptography.fernet.Fernet` used to encrypt
per-user credentials (currently GitHub user access / refresh tokens)
before they land in the database. The plaintext only exists in the
server process; the column holds the Fernet ciphertext.

The Fernet key is *derived* from an operator-supplied secret via
SHA-256 so the operator can pass any high-entropy string (a hex secret,
the app client secret, …) rather than a base64 32-byte Fernet key. Two
processes that share the same input secret derive the same key and can
decrypt each other's rows, which is what a multi-replica deployment
needs.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet, InvalidToken


@runtime_checkable
class SecretCipher(Protocol):
    """Port for encrypting integration secrets at rest.

    The credential store depends on this, not on a concrete backend, so a
    deployment can swap the default local-Fernet :class:`SecretBox` for a
    KMS/Secrets-Manager/Databricks/Vault adapter without a schema change. See
    ``designs/CREDENTIAL_STORE.md``.
    """

    def encrypt(self, plaintext: str) -> str:
        """Return the ciphertext for *plaintext*."""
        ...

    def decrypt(self, ciphertext: str) -> str | None:
        """Return the plaintext, or ``None`` when the ciphertext is unusable."""
        ...


def build_secret_cipher(secret: str | bytes) -> SecretCipher:
    """Construct the configured secret cipher.

    Single seam where a deployment selects a secret backend. Returns the local
    Fernet :class:`SecretBox` today; KMS/Databricks/Vault adapters slot in here
    when a deployment needs them (see ``designs/CREDENTIAL_STORE.md``).
    """
    return SecretBox(secret)


class SecretBox:
    """Encrypt/decrypt short secrets with a key derived from *secret*.

    :param secret: Operator-supplied key material of arbitrary length.
        Its SHA-256 digest is base64url-encoded into a 32-byte Fernet
        key, so the caller need not supply a Fernet-formatted key.
    """

    def __init__(self, secret: str | bytes) -> None:
        material = secret.encode("utf-8") if isinstance(secret, str) else secret
        key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Return the URL-safe base64 ciphertext for *plaintext*."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str | None:
        """Return the plaintext for *ciphertext*, or ``None`` if invalid.

        Returns ``None`` (rather than raising) when the ciphertext was
        written under a different key or is corrupt, so a rotated
        encryption secret degrades to "connection needs reconnecting"
        instead of a 500 at launch time.
        """
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return None
