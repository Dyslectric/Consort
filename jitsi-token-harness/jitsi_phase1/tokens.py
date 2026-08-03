"""
Jitsi capability-token minting and inspection.

This module is deliberately small and dependency-light so that the same logic can
later be lifted into the Zulip `calls` patch (rev 3 section 5.4) without carrying
anything Zulip-specific back into it.

Design constraints, all of which come from rev 3 section 2:

* A token is a capability, not an identity. It authorises entry to exactly one
  room in exactly one tenant, and it expires quickly.
* Every field inside `context.user` must be a string. Prosody throws on a null or
  a numeric value rather than degrading, so we refuse to mint such a token here
  instead of discovering it at join time.
* `room = "*"` is a skeleton key for the whole deployment. Minting one requires an
  explicit opt-in, and the only legitimate caller of that opt-in is the test that
  proves `token_no_wildcard` is doing its job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import jwt

Algorithm = Literal["HS256", "RS256"]

#: Rev 3 section 5.3: the token's only job is to get you through the door.
DEFAULT_LIFETIME_SECONDS = 120

#: Small allowance for clock skew between the minting host and Prosody.
DEFAULT_NOT_BEFORE_SKEW_SECONDS = 5


class TokenError(ValueError):
    """Raised when a token would be malformed in a way Prosody handles badly."""


@dataclass(frozen=True)
class JitsiUser:
    """The `context.user` object.

    `id` is typed as `str` rather than `int` on purpose. Zulip user IDs are
    integers and the natural mistake is to pass one through unchanged.
    """

    id: str
    name: str
    email: str = ""
    avatar: str = ""
    moderator: bool = False

    def to_claim(self) -> dict[str, str]:
        claim: dict[str, str] = {
            "id": _require_str("context.user.id", self.id),
            "name": _require_str("context.user.name", self.name),
            # Prosody throws on null; an empty string is the safe way to say
            # "not supplied". Fields that are empty are omitted entirely rather
            # than sent as "", which keeps the token small and unambiguous.
            "moderator": "true" if self.moderator else "false",
        }
        if self.email:
            claim["email"] = _require_str("context.user.email", self.email)
        if self.avatar:
            claim["avatar"] = _require_str("context.user.avatar", self.avatar)
        return claim


@dataclass(frozen=True)
class JitsiFeatures:
    """The `context.features` object.

    Everything defaults to False. Rev 3 section 5.3: defaulting them off means a
    bug in issuance logic cannot silently enable recording.
    """

    recording: bool = False
    livestreaming: bool = False
    transcription: bool = False
    outbound_call: bool = False

    def to_claim(self) -> dict[str, bool]:
        return {
            "recording": self.recording,
            "livestreaming": self.livestreaming,
            "transcription": self.transcription,
            # Hyphenated on the wire, matching what JaaS documents.
            "outbound-call": self.outbound_call,
        }


@dataclass(frozen=True)
class SigningConfig:
    """How to sign, and what to claim as issuer and audience.

    For HS256, `secret` is the Prosody `app_secret`. For RS256, `private_key_pem`
    is the key whose public half is served by the ASAP key server as
    `sha256(kid).pem` — see keys.py, and rev 3 section 2.7 for why that is not a
    JWKS endpoint.
    """

    algorithm: Algorithm = "HS256"
    issuer: str = "zulip"
    audience: str = "jitsi"
    secret: str | None = None
    private_key_pem: bytes | None = None
    key_id: str | None = None

    def __post_init__(self) -> None:
        if self.algorithm == "HS256":
            if not self.secret:
                raise TokenError("HS256 signing requires `secret`")
        elif self.algorithm == "RS256":
            if not self.private_key_pem:
                raise TokenError("RS256 signing requires `private_key_pem`")
            if not self.key_id:
                raise TokenError(
                    "RS256 signing requires `key_id`; Prosody locates the public "
                    "key by fetching sha256(kid).pem from asap_key_server"
                )
        else:  # pragma: no cover - guarded by the Literal type
            raise TokenError(f"unsupported algorithm {self.algorithm!r}")


@dataclass(frozen=True)
class TokenRequest:
    """Everything that varies per token."""

    tenant: str
    room: str
    user: JitsiUser
    features: JitsiFeatures = field(default_factory=JitsiFeatures)
    group: str | None = None
    lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS
    allow_wildcard_room: bool = False


def mint(
    request: TokenRequest,
    signing: SigningConfig,
    *,
    now: int | None = None,
) -> str:
    """Mint a single-room, short-lived Jitsi capability token."""
    issued_at = int(time.time()) if now is None else now

    tenant = _require_str("tenant", request.tenant)
    if tenant != tenant.lower():
        # Rev 3 section 2.4: `sub` must be the lowercase tenant, and Prosody's
        # domain verification compares it against the URL path segment.
        raise TokenError(f"tenant must be lowercase, got {tenant!r}")

    room = _require_str("room", request.room)
    if room == "*" or "*" in room:
        if not request.allow_wildcard_room:
            raise TokenError(
                "refusing to mint a wildcard room token; it is a skeleton key for "
                "the whole deployment. Pass allow_wildcard_room=True only from a "
                "test that asserts token_no_wildcard rejects it."
            )

    if request.lifetime_seconds <= 0:
        raise TokenError("lifetime_seconds must be positive")

    payload: dict[str, Any] = {
        "iss": signing.issuer,
        "aud": signing.audience,
        "sub": tenant,
        "room": room,
        "iat": issued_at,
        "nbf": issued_at - DEFAULT_NOT_BEFORE_SKEW_SECONDS,
        "exp": issued_at + request.lifetime_seconds,
        "context": {
            "user": request.user.to_claim(),
            "features": request.features.to_claim(),
        },
    }
    if request.group is not None:
        payload["context"]["group"] = _require_str("context.group", request.group)

    headers: dict[str, Any] = {}
    if signing.algorithm == "RS256":
        headers["kid"] = signing.key_id
        key: Any = signing.private_key_pem
    else:
        key = signing.secret

    return jwt.encode(payload, key, algorithm=signing.algorithm, headers=headers)


def inspect(token: str) -> dict[str, Any]:
    """Decode without verifying. For diagnostics and tests only.

    Never make an authorisation decision from this; it does not check the
    signature. Prosody is the only thing whose opinion of a token matters.
    """
    return jwt.decode(token, options={"verify_signature": False})


def verify(
    token: str,
    signing: SigningConfig,
    *,
    public_key_pem: bytes | None = None,
    expected_tenant: str | None = None,
    expected_room: str | None = None,
) -> dict[str, Any]:
    """Verify a token the way Prosody would, for local round-trip testing.

    This mirrors `mod_auth_token`'s checks closely enough to catch our own
    mistakes, but it is not a substitute for testing against real Prosody. The
    live probe in bosh.py is what actually proves the deployment enforces this.
    """
    if signing.algorithm == "RS256":
        if public_key_pem is None:
            raise TokenError("RS256 verification requires `public_key_pem`")
        key: Any = public_key_pem
    else:
        key = signing.secret

    claims = jwt.decode(
        token,
        key,
        algorithms=[signing.algorithm],
        audience=signing.audience,
        issuer=signing.issuer,
    )

    if expected_tenant is not None and claims.get("sub") != expected_tenant:
        raise TokenError(
            f"sub {claims.get('sub')!r} does not match tenant {expected_tenant!r}"
        )
    if expected_room is not None and claims.get("room") not in (expected_room, "*"):
        raise TokenError(
            f"room {claims.get('room')!r} does not match {expected_room!r}"
        )
    return claims


def _require_str(label: str, value: Any) -> str:
    if value is None:
        raise TokenError(
            f"{label} is None; Prosody throws on a null inside the user context "
            "rather than degrading gracefully"
        )
    if not isinstance(value, str):
        raise TokenError(
            f"{label} must be a string, got {type(value).__name__}. Zulip user IDs "
            "are integers and must be coerced before they reach the token."
        )
    if not value:
        raise TokenError(f"{label} must not be empty")
    return value
