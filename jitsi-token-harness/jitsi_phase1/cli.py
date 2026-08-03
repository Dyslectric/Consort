"""
Command line entry points for the phase one harness.

    python -m jitsi_phase1 keygen --kid zulip-jitsi-2026-07
    python -m jitsi_phase1 mint --tenant engineering --room c-7f3a91b2e4c8d5a6
    python -m jitsi_phase1 probe --tenant engineering --room c-7f3a91b2e4c8d5a6
    python -m jitsi_phase1 check

`check` is the one that matters. It runs the full isolation matrix and exits
non-zero unless every case behaves as it must. Rev 3 section 8 says do not
proceed to phase two until that passes, so make it something you can run in CI
rather than something you remember to do.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from .bosh import BoshProbe, Hosts, Outcome, ProbeResult
from .keys import generate_keypair, keyfile_name
from .tokens import (
    JitsiUser,
    SigningConfig,
    TokenError,
    TokenRequest,
    inspect,
    mint,
)


def _signing_from_env() -> SigningConfig:
    algorithm = os.environ.get("JITSI_JWT_ALGORITHM", "HS256").upper()
    issuer = os.environ.get("JITSI_JWT_ISSUER", "zulip")
    audience = os.environ.get("JITSI_JWT_AUDIENCE", "jitsi")

    if algorithm == "RS256":
        key_id = os.environ.get("JITSI_JWT_KEY_ID")
        key_path = os.environ.get("JITSI_JWT_PRIVATE_KEY")
        if not key_id or not key_path:
            _die("RS256 requires JITSI_JWT_KEY_ID and JITSI_JWT_PRIVATE_KEY")
        return SigningConfig(
            algorithm="RS256",
            issuer=issuer,
            audience=audience,
            private_key_pem=Path(key_path).read_bytes(),
            key_id=key_id,
        )

    secret = os.environ.get("JITSI_JWT_APP_SECRET")
    if not secret:
        _die("HS256 requires JITSI_JWT_APP_SECRET (must match JWT_APP_SECRET in .env)")
    return SigningConfig(algorithm="HS256", issuer=issuer, audience=audience, secret=secret)


def _user_from_env(moderator: bool = True) -> JitsiUser:
    return JitsiUser(
        id=os.environ.get("JITSI_PROBE_USER_ID", "31"),
        name=os.environ.get("JITSI_PROBE_USER_NAME", "Phase One Probe"),
        email=os.environ.get("JITSI_PROBE_USER_EMAIL", ""),
        moderator=moderator,
    )


def _base_url() -> str:
    return os.environ.get("JITSI_BASE_URL", "https://localhost:8443")


def _verify_tls() -> bool:
    return os.environ.get("JITSI_VERIFY_TLS", "0") not in ("0", "false", "no", "")


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default) not in ("0", "false", "no", "")


def _probe_kwargs() -> dict:
    """Deployment-shape knobs shared by `probe` and `check`."""
    kwargs = {
        "verify_tls": _verify_tls(),
        "tenant_in_path": _flag("JITSI_TENANT_IN_PATH"),
        # Off by default: Jitsi's standard layout has ONE virtual host, with the
        # tenant carried by the MUC address rather than the XMPP domain.
        "tenant_in_xmpp_host": _flag("JITSI_TENANT_IN_XMPP_HOST", "0"),
    }
    template = os.environ.get("JITSI_MUC_TEMPLATE")
    if template:
        kwargs["muc_template"] = template
    return kwargs


def _hosts_for(tenant: str | None) -> Hosts:
    """Use configured hosts if given, otherwise read them off the deployment.

    Discovery is the default because guessing these wrong produces a failure
    indistinguishable from a rejected token. Explicit configuration exists for
    deployments with no web container in front — talking to Prosody directly,
    for instance, where there is no config.js to read.
    """
    domain = os.environ.get("JITSI_XMPP_DOMAIN")
    if domain:
        return Hosts(domain=domain, muc=os.environ.get("JITSI_MUC_DOMAIN", f"muc.{domain}"))
    return Hosts.discover(_base_url(), tenant, verify_tls=_verify_tls())


# -- subcommands ----------------------------------------------------------


def cmd_keygen(args: argparse.Namespace) -> int:
    pair = generate_keypair(
        key_id=args.kid,
        private_key_dir=Path(args.private_dir),
        keyserver_dir=Path(args.keyserver_dir),
        overwrite=args.overwrite,
    )
    print(f"kid          {pair.key_id}")
    print(f"private key  {pair.private_key_path}")
    print(f"public key   {pair.public_key_path}")
    print()
    print("Prosody will request exactly this filename from asap_key_server:")
    print(f"    {keyfile_name(pair.key_id)}")
    print("It is not a JWKS endpoint. Serve that directory as static files.")
    return 0


def cmd_mint(args: argparse.Namespace) -> int:
    token = mint(
        TokenRequest(
            tenant=args.tenant,
            room=args.room,
            user=_user_from_env(moderator=args.moderator),
            group=args.tenant,
            lifetime_seconds=args.lifetime,
            allow_wildcard_room=args.allow_wildcard,
        ),
        _signing_from_env(),
    )
    if args.url:
        print(f"{_base_url()}/{args.tenant}/{args.room}?jwt={token}")
    else:
        print(token)
    if args.decode:
        import json

        print(json.dumps(inspect(token), indent=2), file=sys.stderr)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    signing = _signing_from_env()
    token = mint(
        TokenRequest(
            tenant=args.tenant,
            room=args.room,
            user=_user_from_env(),
            group=args.tenant,
        ),
        signing,
    )
    hosts = _hosts_for(args.tenant)
    probe = BoshProbe(_base_url(), hosts, tenant=args.tenant, **_probe_kwargs())
    print(
        f"endpoint={probe.endpoint} xmpp_host={probe.xmpp_host} muc_host={probe.muc_host}",
        file=sys.stderr,
    )
    result = probe.attempt_join(token, args.room)
    print(result)
    return 0 if result.outcome is Outcome.JOINED else 1


# -- the phase one gate ---------------------------------------------------


@dataclass
class Case:
    """One row of the gate.

    `admitted` is the assertion that matters and the only thing that can fail the
    gate: either Prosody let this token in or it did not.

    `typical` records where the rejection is *expected* to happen so that a shift
    is visible, but it is reported rather than asserted. Which enforcement point
    catches a cross-tenant token depends on deployment shape — in Jitsi's
    standard single-virtual-host layout it is `token_verification` on the MUC
    component (room_rejected), not `mod_auth_token` at SASL (auth_rejected).
    Asserting the point rather than the outcome makes the gate fail on correct
    deployments, which teaches people to ignore it.
    """

    name: str
    why: str
    admitted: bool
    typical: Outcome
    run: object  # Callable[[], ProbeResult]


def cmd_check(args: argparse.Namespace) -> int:
    signing = _signing_from_env()
    base = _base_url()
    verify_tls = _verify_tls()
    tenant_a = args.tenant_a
    tenant_b = args.tenant_b
    room = args.room
    other_room = f"{room}-other"

    hosts_a = _hosts_for(tenant_a)
    hosts_b = _hosts_for(tenant_b)

    def token_for(tenant: str, target_room: str, **kwargs) -> str:
        return mint(
            TokenRequest(
                tenant=tenant,
                room=target_room,
                user=_user_from_env(),
                group=tenant,
                **kwargs,
            ),
            signing,
        )

    def probe(tenant: str, hosts: Hosts, token: str, target_room: str) -> ProbeResult:
        return BoshProbe(base, hosts, tenant=tenant, **_probe_kwargs()).attempt_join(
            token, target_room
        )

    wrong_secret = SigningConfig(
        algorithm="HS256", issuer=signing.issuer, audience=signing.audience,
        secret="definitely-not-the-app-secret",
    )

    cases: list[Case] = [
        Case(
            "happy path",
            "a correctly scoped token opens its own room",
            True,
            Outcome.JOINED,
            lambda: probe(tenant_a, hosts_a, token_for(tenant_a, room), room),
        ),
        Case(
            "cross-tenant",
            "a tenant A token must not open a room in tenant B",
            False,
            Outcome.ROOM_REJECTED,
            lambda: probe(tenant_b, hosts_b, token_for(tenant_a, room), room),
        ),
        Case(
            "cross-room",
            "a token for one room must not open a different room",
            False,
            Outcome.ROOM_REJECTED,
            lambda: probe(tenant_a, hosts_a, token_for(tenant_a, room), other_room),
        ),
        Case(
            "expired",
            "an expired token must be refused",
            False,
            Outcome.AUTH_REJECTED,
            lambda: probe(
                tenant_a, hosts_a,
                mint(
                    TokenRequest(tenant=tenant_a, room=room, user=_user_from_env()),
                    signing,
                    now=int(__import__("time").time()) - 3600,
                ),
                room,
            ),
        ),
        Case(
            "empty token",
            "allow_empty_token must be false",
            False,
            Outcome.AUTH_REJECTED,
            lambda: probe(tenant_a, hosts_a, "", room),
        ),
        Case(
            "forged signature",
            "a token signed with the wrong key must be refused",
            False,
            Outcome.AUTH_REJECTED,
            lambda: probe(
                tenant_a, hosts_a,
                mint(
                    TokenRequest(tenant=tenant_a, room=room, user=_user_from_env()),
                    wrong_secret,
                ),
                room,
            ),
        ),
        Case(
            "wildcard room",
            "token_no_wildcard must reject a skeleton key",
            False,
            Outcome.ROOM_REJECTED,
            lambda: probe(
                tenant_a, hosts_a,
                token_for(tenant_a, "*", allow_wildcard_room=True),
                room,
            ),
        ),
    ]

    print(f"{'case':<20} {'want':<10} {'outcome':<16} result")
    print("-" * 72)
    failures = 0
    shifted = []
    for case in cases:
        result: ProbeResult = case.run()  # type: ignore[operator]

        # A transport error is never a pass. A broken environment must not be
        # mistakable for a security control doing its job.
        transport = result.outcome is Outcome.TRANSPORT_ERROR
        ok = (not transport) and (result.outcome.admitted == case.admitted)
        if not ok:
            failures += 1
        elif result.outcome is not case.typical:
            shifted.append((case.name, case.typical, result.outcome))

        want = "join" if case.admitted else "refuse"
        print(f"{case.name:<20} {want:<10} {result.outcome.value:<16} {'PASS' if ok else 'FAIL'}")
        if not ok or args.verbose:
            print(f"    {case.why}")
            print(f"    detail: {result.detail}")

    print()
    for name, typical, actual in shifted:
        print(
            f"note: '{name}' was refused at {actual.value} rather than the usual "
            f"{typical.value}. Not a failure — enforcement point varies with "
            f"deployment shape — but worth knowing which module caught it."
        )
    if shifted:
        print()

    if failures:
        print(f"{failures} of {len(cases)} cases failed. Phase one is NOT complete.")
        print("Do not start phase two until this is clean.")
        return 1
    print(f"All {len(cases)} cases behaved correctly. Phase one gate passed.")
    return 0


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jitsi_phase1", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="generate an RS256 keypair for the ASAP key server")
    keygen.add_argument("--kid", required=True)
    keygen.add_argument("--private-dir", default="./secrets")
    keygen.add_argument("--keyserver-dir", default="./keyserver")
    keygen.add_argument("--overwrite", action="store_true")
    keygen.set_defaults(func=cmd_keygen)

    mint_cmd = sub.add_parser("mint", help="mint a token by hand")
    mint_cmd.add_argument("--tenant", required=True)
    mint_cmd.add_argument("--room", required=True)
    mint_cmd.add_argument("--lifetime", type=int, default=120)
    mint_cmd.add_argument("--moderator", action="store_true", default=True)
    mint_cmd.add_argument("--no-moderator", dest="moderator", action="store_false")
    mint_cmd.add_argument("--allow-wildcard", action="store_true")
    mint_cmd.add_argument("--url", action="store_true", help="print a joinable URL")
    mint_cmd.add_argument("--decode", action="store_true", help="dump claims to stderr")
    mint_cmd.set_defaults(func=cmd_mint)

    probe_cmd = sub.add_parser("probe", help="attempt one join and report the outcome")
    probe_cmd.add_argument("--tenant", required=True)
    probe_cmd.add_argument("--room", required=True)
    probe_cmd.set_defaults(func=cmd_probe)

    check = sub.add_parser("check", help="run the phase one isolation matrix")
    check.add_argument("--tenant-a", default="engineering")
    check.add_argument("--tenant-b", default="design")
    check.add_argument("--room", default="c-phase1-probe")
    check.add_argument("--verbose", "-v", action="store_true")
    check.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except TokenError as exc:
        # These are refusals by design, not crashes. Print them as such.
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(f"error: could not reach the stack: {exc}", file=sys.stderr)
        print(f"       is it up, and is JITSI_BASE_URL ({_base_url()}) right?", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
