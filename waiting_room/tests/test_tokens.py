"""HMAC token signer."""

from __future__ import annotations

import time

import fakeredis
import pytest

from waiting_room.core.exceptions import (
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenFingerprintMismatchError,
)
from waiting_room.core.tokens import HMACTokenSigner


@pytest.fixture
def signer(redis_client: fakeredis.FakeRedis) -> HMACTokenSigner:
    return HMACTokenSigner(
        "x" * 64,
        redis_client=redis_client,
        key_prefix="wr:test:nonce",
    )


def test_issue_and_verify_round_trip(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s1", room="r", fingerprint="ip|ua", ttl_seconds=60)
    verified = signer.verify(ticket.token, fingerprint="ip|ua")
    assert verified.session_id == "s1"
    assert verified.room == "r"
    assert ticket.expires_at > ticket.issued_at


def test_signature_is_validated(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s1", room="r", fingerprint="ip|ua", ttl_seconds=60)
    payload, _ = ticket.token.split(".")
    tampered = payload + ".AAAAAA"
    with pytest.raises(InvalidTokenError):
        signer.verify(tampered, fingerprint="ip|ua")


def test_fingerprint_binding(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s1", room="r", fingerprint="ip|ua", ttl_seconds=60)
    with pytest.raises(TokenFingerprintMismatchError):
        signer.verify(ticket.token, fingerprint="other-ip|other-ua")


def test_expired_token(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s1", room="r", fingerprint="fp", ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(TokenExpiredError):
        signer.verify(ticket.token, fingerprint="fp")


def test_single_use_enforced(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s1", room="r", fingerprint="fp", ttl_seconds=60)
    assert signer.mark_used(ticket.token) is True
    with pytest.raises(TokenAlreadyUsedError):
        signer.mark_used(ticket.token)


def test_rejects_short_secret() -> None:
    with pytest.raises(ValueError, match="32"):
        HMACTokenSigner("too-short")


def test_token_url_safe_and_bounded(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(session_id="s" * 32, room="r" * 16, fingerprint="fp", ttl_seconds=60)
    assert len(ticket.token) <= 256
    assert all(c.isalnum() or c in "-_." for c in ticket.token)
