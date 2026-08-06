"""Per-user integration credential entity (provider-agnostic).

Plain dataclass returned from
:class:`omnigent.server.credential_store.IntegrationCredentialStore`. The
``secret`` mapping carries the *decrypted* secret material and is only ever
populated on server-side vend paths, never serialized to a client;
``metadata`` holds non-secret provider fields. See
``designs/CREDENTIAL_STORE.md``.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class IntegrationConnection:
    """A user's connected third-party integration.

    :param user_id: The omnigent user id the connection belongs to.
    :param provider: Provider key, e.g. ``"github"``.
    :param account_id: Provider account discriminator (``""`` = single account).
    :param secret: Decrypted secret material as a mapping, or ``None`` when the
        store returned a metadata-only view (status endpoints).
    :param metadata: Non-secret provider metadata (login, ids, scopes, expiries).
    :param created_at: Unix epoch seconds the connection was first made.
    :param updated_at: Unix epoch seconds of the last refresh / reconnect.
    """

    user_id: str
    provider: str
    account_id: str
    secret: dict[str, Any] | None
    metadata: dict[str, Any]
    created_at: int
    updated_at: int
