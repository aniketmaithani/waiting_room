"""HMAC token signer."""

from __future__ import annotations

import time

import fakeredis
import pytest
import redis

from waiting_room.core.exceptions import (
    BackendUnavailableError,
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenFingerprintMismatchError,
)
from waiting_room.core.tokens import HMACTokenSigner


@pytest.fixture
def signer(redis_client: redis.Redis) -> HMACTokenSigner:
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
    assert len(ticket.token) <= 1024
    assert all(c.isalnum() or c in "-_." for c in ticket.token)


def test_purpose_is_enforced(signer: HMACTokenSigner) -> None:
    ticket = signer.issue(
        session_id="s1", room="r", fingerprint="fp", ttl_seconds=60, purpose="pass"
    )
    assert signer.verify(ticket.token, fingerprint="fp", purpose="pass").session_id == "s1"
    with pytest.raises(InvalidTokenError, match="purpose"):
        signer.verify(ticket.token, fingerprint="fp")


@pytest.mark.parametrize("token", ["", "no-dot", "!!!.???", "a.b.c", "x" * 5000])
def test_garbage_tokens_are_invalid(signer: HMACTokenSigner, token: str) -> None:
    with pytest.raises(InvalidTokenError):
        signer.verify(token, fingerprint="fp")


def test_mark_used_wraps_backend_errors() -> None:

    class _Down(fakeredis.FakeRedis):
        def set(self, *args: object, **kwargs: object) -> bool:
            raise redis.ConnectionError("down")

    signer = HMACTokenSigner("x" * 64, redis_client=_Down())
    ticket = signer.issue(session_id="s1", room="r", fingerprint="fp", ttl_seconds=60)
    with pytest.raises(BackendUnavailableError):
        signer.mark_used(ticket.token)


def test_long_room_names_fit() -> None:
    signer = HMACTokenSigner("x" * 64)
    ticket = signer.issue(session_id="a" * 32, room="r" * 128, fingerprint="fp", ttl_seconds=60)
    assert signer.verify(ticket.token, fingerprint="fp").room == "r" * 128
