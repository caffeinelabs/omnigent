"""Tests for the provider-agnostic IntegrationCredentialStore."""

from __future__ import annotations

from omnigent.server.credential_store import IntegrationCredentialStore
from omnigent.server.secretbox import SecretBox


def _store(db_uri: str) -> IntegrationCredentialStore:
    return IntegrationCredentialStore(db_uri, SecretBox("enc-secret"))


def test_upsert_get_roundtrip(db_uri: str) -> None:
    store = _store(db_uri)
    store.upsert(
        "alice@example.com",
        "github",
        secret={"access_token": "ghu_1", "refresh_token": "ghr_1"},
        metadata={"github_login": "alice"},
    )
    # Metadata-only view hides the secret.
    meta_only = store.get("alice@example.com", "github")
    assert meta_only is not None
    assert meta_only.secret is None
    assert meta_only.metadata["github_login"] == "alice"
    # With-secret view decrypts.
    full = store.get("alice@example.com", "github", with_secret=True)
    assert full is not None and full.secret == {"access_token": "ghu_1", "refresh_token": "ghr_1"}


def test_provider_isolation(db_uri: str) -> None:
    store = _store(db_uri)
    store.upsert("alice", "github", secret={"k": "gh"}, metadata={})
    store.upsert("alice", "datadog", secret={"k": "dd"}, metadata={})
    gh = store.get("alice", "github", with_secret=True)
    dd = store.get("alice", "datadog", with_secret=True)
    assert gh is not None and gh.secret == {"k": "gh"}
    assert dd is not None and dd.secret == {"k": "dd"}
    assert store.get("alice", "slack") is None
    assert {c.provider for c in store.list_for_user("alice")} == {"github", "datadog"}


def test_update_secret_and_delete(db_uri: str) -> None:
    store = _store(db_uri)
    store.upsert("bob", "github", secret={"access_token": "old"}, metadata={"scopes": "repo"})
    store.update_secret("bob", "github", secret={"access_token": "new"})
    conn = store.get("bob", "github", with_secret=True)
    assert conn is not None and conn.secret == {"access_token": "new"}
    assert conn.metadata["scopes"] == "repo"  # metadata preserved when not patched
    assert store.delete("bob", "github") is True
    assert store.get("bob", "github") is None


def test_wrong_key_decrypts_to_none(db_uri: str) -> None:
    _store(db_uri).upsert("carol", "github", secret={"access_token": "x"}, metadata={})
    other = IntegrationCredentialStore(db_uri, SecretBox("different-key"))
    conn = other.get("carol", "github", with_secret=True)
    assert conn is not None and conn.secret is None  # soft-fail, not a crash


def test_list_all_filters_by_provider(db_uri: str) -> None:
    store = _store(db_uri)
    store.upsert("a", "github", secret={"k": "1"}, metadata={})
    store.upsert("b", "github", secret={"k": "2"}, metadata={})
    store.upsert("a", "datadog", secret={"k": "3"}, metadata={})
    assert len(store.list_all(provider="github")) == 2
    assert len(store.list_all()) == 3
