"""The phase one gate, as pytest.

Skipped unless a stack is reachable, so `make test` stays fast and offline. Run
it against the staging stack with `make check-pytest`.

Rev 3 section 8: *do not proceed until you have proven that a token for tenant A
cannot open a room in tenant B, because that is the assumption everything else
rests on.* This file is that proof, in a form CI can run.

Each test asserts **admission** — was this token let in or not — rather than
which module did the rejecting. The enforcement point legitimately varies with
deployment shape: in Jitsi's standard single-virtual-host layout a cross-tenant
token is caught by `token_verification` on the MUC component, not by
`mod_auth_token` at SASL. Asserting the point rather than the outcome produces a
gate that fails on correct deployments, which teaches people to ignore it.
"""

from __future__ import annotations

import os
import time

import pytest
import requests

from jitsi_phase1.bosh import BoshProbe, Hosts, Outcome
from jitsi_phase1.cli import _hosts_for, _probe_kwargs
from jitsi_phase1.tokens import JitsiUser, SigningConfig, TokenRequest, mint

BASE_URL = os.environ.get("JITSI_BASE_URL", "https://localhost:8443")
VERIFY_TLS = os.environ.get("JITSI_VERIFY_TLS", "0") not in ("0", "false", "no", "")
TENANT_A = os.environ.get("JITSI_TENANT_A", "engineering")
TENANT_B = os.environ.get("JITSI_TENANT_B", "design")
ROOM = os.environ.get("JITSI_TEST_ROOM", "c-phase1-probe")
OTHER_ROOM = f"{ROOM}-other"

USER = JitsiUser(id="31", name="Phase One Probe")


def _stack_is_up() -> bool:
    try:
        requests.post(f"{BASE_URL}/http-bind", timeout=3, verify=VERIFY_TLS)
        return True
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_is_up(),
    reason=f"no Jitsi stack at {BASE_URL}; run `make up` first",
)


@pytest.fixture(scope="session")
def signing() -> SigningConfig:
    secret = os.environ.get("JITSI_JWT_APP_SECRET")
    if not secret:
        pytest.skip("JITSI_JWT_APP_SECRET not set")
    return SigningConfig(algorithm="HS256", secret=secret)


@pytest.fixture(scope="session")
def hosts_a() -> Hosts:
    return _hosts_for(TENANT_A)


@pytest.fixture(scope="session")
def hosts_b() -> Hosts:
    return _hosts_for(TENANT_B)


def token(signing: SigningConfig, tenant: str, room: str, **kwargs) -> str:
    return mint(
        TokenRequest(tenant=tenant, room=room, user=USER, group=tenant, **kwargs),
        signing,
    )


def join(tenant: str, hosts: Hosts, tok: str, room: str):
    return BoshProbe(BASE_URL, hosts, tenant=tenant, **_probe_kwargs()).attempt_join(
        tok, room
    )


def assert_refused(result) -> None:
    """Refused by Prosody — and not merely broken in transit.

    A transport error must never satisfy a negative assertion. That is how a
    security test ends up green while testing nothing at all.
    """
    assert result.outcome is not Outcome.TRANSPORT_ERROR, (
        f"transport failure, not a rejection: {result.detail}"
    )
    assert not result.outcome.admitted, result


def test_a_correctly_scoped_token_is_admitted(signing, hosts_a):
    """Must pass first. If it does not, no other result here means anything.

    Note the room has to exist: `token_util:verify_room` returns
    `room-does-not-exist` before it ever looks at the token's claims, and in a
    real deployment Jicofo is what creates rooms.
    """
    result = join(TENANT_A, hosts_a, token(signing, TENANT_A, ROOM), ROOM)
    assert result.outcome is Outcome.JOINED, result


def test_a_tenant_a_token_cannot_open_a_tenant_b_room(signing, hosts_b):
    """The load-bearing assertion. Everything downstream assumes this."""
    assert_refused(join(TENANT_B, hosts_b, token(signing, TENANT_A, ROOM), ROOM))


def test_a_token_cannot_open_a_different_room_in_its_own_tenant(signing, hosts_a):
    """OTHER_ROOM must exist too, or this passes without the room claim being read."""
    assert_refused(join(TENANT_A, hosts_a, token(signing, TENANT_A, ROOM), OTHER_ROOM))


def test_an_expired_token_is_refused(signing, hosts_a):
    stale = mint(
        TokenRequest(tenant=TENANT_A, room=ROOM, user=USER),
        signing,
        now=int(time.time()) - 3600,
    )
    assert_refused(join(TENANT_A, hosts_a, stale, ROOM))


def test_an_empty_token_is_refused(signing, hosts_a):
    """Proves allow_empty_token is actually false."""
    assert_refused(join(TENANT_A, hosts_a, "", ROOM))


def test_a_forged_signature_is_refused(signing, hosts_a):
    forged = mint(
        TokenRequest(tenant=TENANT_A, room=ROOM, user=USER),
        SigningConfig(algorithm="HS256", secret="not-the-app-secret-but-long-enough!!"),
    )
    assert_refused(join(TENANT_A, hosts_a, forged, ROOM))


def test_a_wildcard_room_token_is_refused(signing, hosts_a):
    """Proves token_no_wildcard is loaded and doing its job.

    Without that module a wildcard token is a skeleton key for the whole
    deployment, and the failure is completely silent — the worst combination
    available.
    """
    skeleton = token(signing, TENANT_A, "*", allow_wildcard_room=True)
    assert_refused(join(TENANT_A, hosts_a, skeleton, ROOM))
