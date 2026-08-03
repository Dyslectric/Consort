"""Offline assertions about the shape of tokens we mint.

These catch the mistakes that are expensive to discover against real Prosody,
because Prosody's failure mode for most of them is a stack trace in a log you
are not tailing.
"""

from __future__ import annotations

import time

import pytest

from jitsi_phase1.keys import generate_keypair
from jitsi_phase1.tokens import (
    JitsiFeatures,
    JitsiUser,
    SigningConfig,
    TokenError,
    TokenRequest,
    inspect,
    mint,
    verify,
)

# At least 32 bytes: PyJWT warns below that, and `make secrets` generates 64.
SECRET = SigningConfig(algorithm="HS256", secret="staging-secret-of-at-least-32-bytes-length")


def request(**overrides) -> TokenRequest:
    defaults = dict(
        tenant="engineering",
        room="c-7f3a91b2e4c8d5a6",
        user=JitsiUser(id="31", name="David Green"),
        group="engineering",
    )
    defaults.update(overrides)
    return TokenRequest(**defaults)  # type: ignore[arg-type]


class TestClaimShape:
    def test_carries_the_claims_prosody_actually_checks(self):
        claims = inspect(mint(request(), SECRET))
        assert claims["iss"] == "zulip"
        assert claims["aud"] == "jitsi"
        assert claims["sub"] == "engineering"
        assert claims["room"] == "c-7f3a91b2e4c8d5a6"
        assert claims["exp"] > claims["iat"]

    def test_lifetime_defaults_to_two_minutes(self):
        claims = inspect(mint(request(), SECRET))
        assert claims["exp"] - claims["iat"] == 120

    def test_nbf_allows_for_clock_skew(self):
        claims = inspect(mint(request(), SECRET))
        assert claims["nbf"] < claims["iat"]

    def test_moderator_is_a_string_not_a_boolean(self):
        # JaaS documents context.user.moderator as a string, and
        # token_affiliation reads it from inside the user context rather than
        # from a top-level claim. Both details bite people.
        claims = inspect(mint(request(user=JitsiUser(id="31", name="D", moderator=True)), SECRET))
        assert claims["context"]["user"]["moderator"] == "true"
        assert "moderator" not in claims

    def test_features_default_to_all_false(self):
        features = inspect(mint(request(), SECRET))["context"]["features"]
        assert features == {
            "recording": False,
            "livestreaming": False,
            "transcription": False,
            "outbound-call": False,
        }

    def test_outbound_call_is_hyphenated_on_the_wire(self):
        features = JitsiFeatures(outbound_call=True).to_claim()
        assert features["outbound-call"] is True
        assert "outbound_call" not in features

    def test_empty_optional_user_fields_are_omitted_not_nulled(self):
        # A null inside the user context throws rather than degrading.
        user = inspect(mint(request(), SECRET))["context"]["user"]
        assert "email" not in user
        assert "avatar" not in user
        assert all(value is not None for value in user.values())


class TestRefusals:
    def test_refuses_a_non_string_user_id(self):
        # Zulip user IDs are integers. This is the mistake that will actually
        # happen when the calls patch is written.
        with pytest.raises(TokenError, match="must be a string"):
            mint(request(user=JitsiUser(id=31, name="D")), SECRET)  # type: ignore[arg-type]

    def test_refuses_a_none_user_field(self):
        with pytest.raises(TokenError, match="is None"):
            mint(request(user=JitsiUser(id=None, name="D")), SECRET)  # type: ignore[arg-type]

    def test_refuses_a_wildcard_room_by_default(self):
        with pytest.raises(TokenError, match="skeleton key"):
            mint(request(room="*"), SECRET)

    def test_refuses_a_partial_wildcard_room(self):
        with pytest.raises(TokenError, match="skeleton key"):
            mint(request(room="c-*"), SECRET)

    def test_allows_a_wildcard_only_with_explicit_opt_in(self):
        token = mint(request(room="*", allow_wildcard_room=True), SECRET)
        assert inspect(token)["room"] == "*"

    def test_refuses_an_uppercase_tenant(self):
        # sub must be the lowercase tenant; Prosody compares it against the URL
        # path segment and the mismatch is silent-looking.
        with pytest.raises(TokenError, match="lowercase"):
            mint(request(tenant="Engineering"), SECRET)

    def test_refuses_a_nonpositive_lifetime(self):
        with pytest.raises(TokenError, match="positive"):
            mint(request(lifetime_seconds=0), SECRET)

    def test_hs256_requires_a_secret(self):
        with pytest.raises(TokenError, match="requires `secret`"):
            SigningConfig(algorithm="HS256", secret=None)

    def test_rs256_requires_a_key_id(self):
        with pytest.raises(TokenError, match="requires `key_id`"):
            SigningConfig(algorithm="RS256", private_key_pem=b"x", key_id=None)


class TestVerification:
    def test_round_trips_under_hs256(self):
        claims = verify(
            mint(request(), SECRET),
            SECRET,
            expected_tenant="engineering",
            expected_room="c-7f3a91b2e4c8d5a6",
        )
        assert claims["sub"] == "engineering"

    def test_round_trips_under_rs256(self, tmp_path):
        pair = generate_keypair("zulip-jitsi-test", tmp_path / "priv", tmp_path / "pub")
        signing = SigningConfig(
            algorithm="RS256", private_key_pem=pair.private_key_pem, key_id=pair.key_id
        )
        token = mint(request(), signing)
        claims = verify(token, signing, public_key_pem=pair.public_key_pem)
        assert claims["room"] == "c-7f3a91b2e4c8d5a6"

    def test_rejects_a_tenant_mismatch(self):
        with pytest.raises(TokenError, match="does not match tenant"):
            verify(mint(request(), SECRET), SECRET, expected_tenant="design")

    def test_rejects_a_room_mismatch(self):
        with pytest.raises(TokenError, match="does not match"):
            verify(mint(request(), SECRET), SECRET, expected_room="c-something-else")

    def test_rejects_an_expired_token(self):
        import jwt

        stale = mint(request(), SECRET, now=int(time.time()) - 3600)
        with pytest.raises(jwt.ExpiredSignatureError):
            verify(stale, SECRET)

    def test_rejects_a_forged_signature(self):
        import jwt

        forged = mint(
            request(),
            SigningConfig(algorithm="HS256", secret="a-different-secret-of-32-bytes-or-more!!"),
        )
        with pytest.raises(jwt.InvalidSignatureError):
            verify(forged, SECRET)
