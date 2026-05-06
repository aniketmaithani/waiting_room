"""HMAC-SHA256 admission tokens.

Token format (URL-safe base64): ``<payload_b64>.<sig_b64>``

The payload is JSON: ``{"sid": str, "room": str, "fp": str, "iat": int,
"exp": int, "nonce": str}``. Signing covers the payload bytes; we store the
nonce in Redis once redeemed to enforce single-use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING

from waiting_room.core._types import AdmissionTicket
from waiting_room.core.exceptions import (
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenFingerprintMismatchError,
)
from waiting_room.core.interfaces import TokenSigner

if TYPE_CHECKING:
    import redis as redis_pkg


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _fingerprint_hash(fp: str) -> str:
    """Hash before signing — keeps tokens short and obscures raw IP/UA."""
    return hashlib.sha256(fp.encode("utf-8")).hexdigest()[:16]


class HMACTokenSigner(TokenSigner):
    """HMAC-SHA256 signer with Redis-backed single-use tracking."""

    _MAX_TOKEN_LEN = 256

    def __init__(
        self,
        secret_key: str,
        *,
        redis_client: redis_pkg.Redis | None = None,
        key_prefix: str = "wr:nonce",
        bind_fingerprint: bool = True,
    ) -> None:
        if len(secret_key) < 32:
            msg = "secret_key must be at least 32 chars"
            raise ValueError(msg)
        self._key = secret_key.encode("utf-8")
        self._redis = redis_client
        self._prefix = key_prefix
        self._bind_fingerprint = bind_fingerprint

    def issue(
        self,
        *,
        session_id: str,
        room: str,
        fingerprint: str,
        ttl_seconds: int,
    ) -> AdmissionTicket:
        now = int(time.time())
        payload = {
            "sid": session_id,
            "room": room,
            "fp": _fingerprint_hash(fingerprint) if self._bind_fingerprint else "",
            "iat": now,
            "exp": now + int(ttl_seconds),
            "nonce": secrets.token_urlsafe(12),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8",
        )
        sig = hmac.new(self._key, payload_bytes, hashlib.sha256).digest()
        token = f"{_b64encode(payload_bytes)}.{_b64encode(sig)}"
        if len(token) > self._MAX_TOKEN_LEN:
            msg = "token exceeded max length — shorten room/session ids"
            raise ValueError(msg)
        return AdmissionTicket(
            token=token,
            session_id=session_id,
            room=room,
            issued_at=float(now),
            expires_at=float(payload["exp"]),
        )

    def verify(self, token: str, *, fingerprint: str) -> AdmissionTicket:
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload_bytes = _b64decode(payload_b64)
            sig = _b64decode(sig_b64)
        except (ValueError, base64.binascii.Error) as exc:
            msg = "malformed token"
            raise InvalidTokenError(msg) from exc

        expected = hmac.new(self._key, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            msg = "signature mismatch"
            raise InvalidTokenError(msg)

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = "payload is not valid JSON"
            raise InvalidTokenError(msg) from exc

        now = time.time()
        if now > float(payload.get("exp", 0)):
            msg = "token expired"
            raise TokenExpiredError(msg)

        if self._bind_fingerprint:
            expected_fp = _fingerprint_hash(fingerprint)
            if not hmac.compare_digest(expected_fp, str(payload.get("fp", ""))):
                msg = "fingerprint mismatch"
                raise TokenFingerprintMismatchError(msg)

        return AdmissionTicket(
            token=token,
            session_id=str(payload["sid"]),
            room=str(payload["room"]),
            issued_at=float(payload["iat"]),
            expires_at=float(payload["exp"]),
        )

    def mark_used(self, token: str) -> bool:
        """Atomically claim the token's nonce. Idempotent for a single caller.

        Without a Redis client (in-process tests, fail-open dev mode) we cannot
        guarantee single-use semantics across processes — return True so the
        host doesn't fail closed by accident, and surface the limitation in
        docs rather than silently rejecting valid traffic.
        """
        if self._redis is None:
            return True
        nonce = self._extract_nonce(token)
        if nonce is None:
            msg = "cannot mark malformed token as used"
            raise InvalidTokenError(msg)
        key = f"{self._prefix}:{nonce}"
        # SET ... NX returns None if the key already exists.
        # TTL caps the storage cost: we only need to remember it until exp anyway.
        ttl = self._token_remaining_ttl(token)
        if ttl <= 0:
            raise TokenExpiredError
        ok = self._redis.set(key, "1", nx=True, ex=int(ttl) + 1)
        if not ok:
            raise TokenAlreadyUsedError
        return True

    @staticmethod
    def _extract_nonce(token: str) -> str | None:
        try:
            payload_b64, _ = token.split(".", 1)
            payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
            return None
        nonce = payload.get("nonce")
        return str(nonce) if nonce else None

    @staticmethod
    def _token_remaining_ttl(token: str) -> float:
        try:
            payload_b64, _ = token.split(".", 1)
            payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
            return float(payload.get("exp", 0)) - time.time()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
            return 0.0


__all__ = ["HMACTokenSigner"]
