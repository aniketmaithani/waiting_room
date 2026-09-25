"""HMAC-SHA256 admission tokens.

Token format (URL-safe base64): ``<payload_b64>.<sig_b64>``

The payload is JSON: ``{"sid": str, "room": str, "fp": str, "iat": int,
"exp": int, "nonce": str, "typ": str}``. Signing covers the payload bytes; we
store the nonce in Redis once redeemed to enforce single-use.

``typ`` is the token's purpose. A token minted for one purpose (e.g. the
single-use ``"admit"`` ticket) never verifies as another (e.g. the reusable
``"pass"`` an admitted user carries), so the two cannot be swapped.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING, Any

from waiting_room.core._types import AdmissionTicket
from waiting_room.core.exceptions import (
    BackendUnavailableError,
    InvalidTokenError,
    TokenAlreadyUsedError,
    TokenExpiredError,
    TokenFingerprintMismatchError,
)
from waiting_room.core.interfaces import TokenSigner

if TYPE_CHECKING:
    import redis as redis_pkg

ADMIT_PURPOSE = "admit"
PASS_PURPOSE = "pass"  # noqa: S105 - token purpose label, not a credential

_REQUIRED_CLAIMS = ("sid", "room", "iat", "exp")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _fingerprint_hash(fp: str) -> str:
    """Hash before signing — keeps tokens short and obscures raw IP/UA."""
    return hashlib.sha256(fp.encode("utf-8")).hexdigest()[:16]


def _decode_payload(token: str) -> dict[str, Any]:
    """Decode (without verifying) a token's payload. Raises ``InvalidTokenError``."""
    try:
        payload_b64, _ = token.split(".", 1)
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:  # binascii/JSON errors are ValueErrors
        msg = "malformed token"
        raise InvalidTokenError(msg) from exc
    if not isinstance(payload, dict):
        msg = "malformed token payload"
        raise InvalidTokenError(msg)
    return payload


class HMACTokenSigner(TokenSigner):
    """HMAC-SHA256 signer with Redis-backed single-use tracking."""

    _MAX_TOKEN_LEN = 1024

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
        purpose: str = ADMIT_PURPOSE,
    ) -> AdmissionTicket:
        now = int(time.time())
        expires_at = now + int(ttl_seconds)
        payload = {
            "sid": session_id,
            "room": room,
            "fp": _fingerprint_hash(fingerprint) if self._bind_fingerprint else "",
            "iat": now,
            "exp": expires_at,
            "nonce": secrets.token_urlsafe(12),
            "typ": purpose,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
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
            expires_at=float(expires_at),
        )

    def verify(
        self,
        token: str,
        *,
        fingerprint: str,
        purpose: str = ADMIT_PURPOSE,
    ) -> AdmissionTicket:
        if len(token) > self._MAX_TOKEN_LEN:
            msg = "token too long"
            raise InvalidTokenError(msg)
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload_bytes = _b64decode(payload_b64)
            sig = _b64decode(sig_b64)
        except ValueError as exc:
            msg = "malformed token"
            raise InvalidTokenError(msg) from exc

        expected = hmac.new(self._key, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            msg = "signature mismatch"
            raise InvalidTokenError(msg)

        payload = _decode_payload(token)
        if any(claim not in payload for claim in _REQUIRED_CLAIMS):
            msg = "token is missing required claims"
            raise InvalidTokenError(msg)
        if payload.get("typ", ADMIT_PURPOSE) != purpose:
            msg = "token purpose mismatch"
            raise InvalidTokenError(msg)

        try:
            expires_at = float(payload["exp"])
            issued_at = float(payload["iat"])
        except (TypeError, ValueError) as exc:
            msg = "malformed token timestamps"
            raise InvalidTokenError(msg) from exc
        if time.time() > expires_at:
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
            issued_at=issued_at,
            expires_at=expires_at,
        )

    def mark_used(self, token: str) -> bool:
        """Atomically claim the token's nonce. Raises if it was already claimed.

        Without a Redis client (in-process tests, fail-open dev mode) we cannot
        guarantee single-use semantics across processes — return True so the
        host doesn't fail closed by accident, and surface the limitation in
        docs rather than silently rejecting valid traffic.
        """
        if self._redis is None:
            return True
        payload = _decode_payload(token)
        nonce = payload.get("nonce")
        if not nonce:
            msg = "cannot mark a token without a nonce as used"
            raise InvalidTokenError(msg)
        try:
            ttl = float(payload.get("exp", 0)) - time.time()
        except (TypeError, ValueError) as exc:
            msg = "malformed token timestamps"
            raise InvalidTokenError(msg) from exc
        if ttl <= 0:
            raise TokenExpiredError
        # SET ... NX returns None if the key already exists. The TTL caps the
        # storage cost: we only need to remember the nonce until the token expires.
        try:
            ok = self._redis.set(f"{self._prefix}:{nonce}", "1", nx=True, ex=int(ttl) + 1)
        except _redis_errors() as exc:
            raise BackendUnavailableError(str(exc)) from exc
        if not ok:
            raise TokenAlreadyUsedError
        return True


def _redis_errors() -> tuple[type[BaseException], ...]:
    try:
        import redis as redis_pkg
    except ImportError:
        return (OSError,)
    return (redis_pkg.RedisError, OSError)


__all__ = ["ADMIT_PURPOSE", "PASS_PURPOSE", "HMACTokenSigner"]
