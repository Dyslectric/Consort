"""Exercise the BOSH probe's state machine against a scripted fake Prosody.

The live matrix in `python -m jitsi_phase1 check` is the real test, but it needs
a running stack. This proves the probe reports the right outcome for each kind
of rejection, so that when the live run says AUTH_REJECTED you know the probe
understood what it saw rather than tripping over its own plumbing.

The distinction between AUTH_REJECTED and ROOM_REJECTED matters: they are
different enforcement points (`mod_auth_token` on the `sub` claim versus
`token_verification` on the `room` claim) and a test that conflates them can
pass while one of the two controls is switched off.
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jitsi_phase1.bosh import BoshProbe, Hosts, Outcome

BOSH_NS = "http://jabber.org/protocol/httpbind"
SASL_NS = "urn:ietf:params:xml:ns:xmpp-sasl"
BIND_NS = "urn:ietf:params:xml:ns:xmpp-bind"
CLIENT_NS = "jabber:client"


class FakeProsody(ThreadingHTTPServer):
    """A BOSH endpoint that fails wherever you tell it to."""

    allow_reuse_address = True

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.seen_paths: list[str] = []
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output readable
        pass

    def do_GET(self):
        # config.js discovery
        body = b"hosts: { domain: 'meet.jitsi', muc: 'muc.meet.jitsi' },"
        self._respond(body, content_type="application/javascript")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        request = self.rfile.read(length).decode()
        self.server.seen_paths.append(self.path)  # type: ignore[attr-defined]
        self._respond(self._reply_to(request).encode())

    def _reply_to(self, request: str) -> str:
        scenario = self.server.scenario  # type: ignore[attr-defined]

        if "sid=" not in request:
            return (
                f"<body xmlns='{BOSH_NS}' sid='fake-session' wait='30' requests='2' "
                f"xmlns:stream='http://etherx.jabber.org/streams'>"
                f"<stream:features><mechanisms xmlns='{SASL_NS}'>"
                f"<mechanism>PLAIN</mechanism></mechanisms></stream:features></body>"
            )

        if "<auth " in request:
            if "ANONYMOUS" not in request:
                # Jitsi's mod_auth_token only registers ANONYMOUS; its
                # provider.test_password returns "Password based auth not
                # supported". A PLAIN attempt gets exactly this.
                return (
                    f"<body xmlns='{BOSH_NS}'><failure xmlns='{SASL_NS}'>"
                    f"<invalid-mechanism/></failure></body>"
                )
            if scenario == "no_token_seen":
                return (
                    f"<body xmlns='{BOSH_NS}'><failure xmlns='{SASL_NS}'>"
                    f"<invalid-mechanism/></failure></body>"
                )
            if scenario == "auth_fail":
                return (
                    f"<body xmlns='{BOSH_NS}'><failure xmlns='{SASL_NS}'>"
                    f"<not-authorized/></failure></body>"
                )
            if scenario == "auth_terminate":
                return f"<body xmlns='{BOSH_NS}' type='terminate' condition='policy-violation'/>"
            return f"<body xmlns='{BOSH_NS}'><success xmlns='{SASL_NS}'/></body>"

        if "restart='true'" in request:
            return f"<body xmlns='{BOSH_NS}'/>"

        if "<bind " in request:
            if scenario == "bind_fail":
                return f"<body xmlns='{BOSH_NS}' type='terminate' condition='internal-server-error'/>"
            return (
                f"<body xmlns='{BOSH_NS}'><iq xmlns='{CLIENT_NS}' type='result' id='bind_1'>"
                f"<bind xmlns='{BIND_NS}'><jid>probe@meet.jitsi/probe</jid></bind></iq></body>"
            )

        if "<session " in request:
            return f"<body xmlns='{BOSH_NS}'/>"

        if "<presence " in request:
            match = re.search(r"to='([^']+)'", request)
            full_jid = match.group(1) if match else "room@muc/probe"
            if scenario == "room_fail":
                return (
                    f"<body xmlns='{BOSH_NS}'><presence xmlns='{CLIENT_NS}' "
                    f"from='{full_jid}' type='error'><error type='auth'>"
                    f"<not-allowed xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>"
                    f"</error></presence></body>"
                )
            return (
                f"<body xmlns='{BOSH_NS}'><presence xmlns='{CLIENT_NS}' from='{full_jid}'>"
                f"<x xmlns='http://jabber.org/protocol/muc#user'/></presence></body>"
            )

        return f"<body xmlns='{BOSH_NS}'/>"

    def _respond(self, body: bytes, content_type: str = "text/xml; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_probe(scenario: str, *, tenant: str | None = "engineering", **kwargs):
    """Returns (outcome, paths the server saw) so transport can be asserted."""
    server = FakeProsody(scenario)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        probe = BoshProbe(
            server.base_url,
            Hosts(domain="meet.jitsi", muc="muc.meet.jitsi"),
            tenant=tenant,
            timeout=3.0,
            tenant_in_path=False,
            **kwargs,
        )
        outcome = probe.attempt_join("a.token.value", "c-phase1-probe").outcome
        return outcome, list(server.seen_paths)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("ok", Outcome.JOINED),
        ("auth_fail", Outcome.AUTH_REJECTED),
        ("auth_terminate", Outcome.AUTH_REJECTED),
        ("room_fail", Outcome.ROOM_REJECTED),
        ("bind_fail", Outcome.TRANSPORT_ERROR),
        # The token never reached Prosody. This must NOT read as a rejection:
        # a live run of this harness once reported four PASSes on the strength
        # of exactly this failure, every one of them false.
        ("no_token_seen", Outcome.TRANSPORT_ERROR),
    ],
)
def test_probe_classifies_each_failure_point(scenario, expected):
    outcome, _ = run_probe(scenario)
    assert outcome is expected


def test_works_without_a_tenant():
    outcome, _ = run_probe("ok", tenant=None)
    assert outcome is Outcome.JOINED


def test_token_travels_in_the_query_string_not_as_a_sasl_password():
    """`mod_jitsi_session` reads ?token= off the BOSH URL.

    It hooks the `bosh-session` event, so the token has to be on the request
    that creates the session — not merely on a later one.
    """
    _, paths = run_probe("ok")
    assert paths, "no requests reached the server"
    assert "token=a.token.value" in paths[0]
    assert "prefix=engineering" in paths[0]
    assert "room=c-phase1-probe" in paths[0]


def test_bearer_header_transport_is_available():
    outcome, paths = run_probe("ok", token_transport="header")
    assert outcome is Outcome.JOINED
    assert "token=" not in paths[0]


def test_transport_failure_is_not_mistaken_for_a_rejection():
    # A dead endpoint must never look like a security control doing its job.
    probe = BoshProbe(
        "http://127.0.0.1:1",
        Hosts(domain="meet.jitsi", muc="muc.meet.jitsi"),
        tenant="engineering",
        timeout=1.0,
    )
    result = probe.attempt_join("token", "room")
    assert result.outcome is Outcome.TRANSPORT_ERROR
    assert not result


def test_hosts_discovery_reads_config_js():
    server = FakeProsody("ok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hosts = Hosts.discover(server.base_url, "engineering")
        assert hosts.domain == "meet.jitsi"
        assert hosts.muc == "muc.meet.jitsi"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_endpoint_includes_the_tenant_path_segment():
    # If tenant routing is not in the URL, Prosody never sees a tenant and the
    # cross-tenant test would pass for entirely the wrong reason.
    probe = BoshProbe("https://jitsi.example", Hosts("meet.jitsi", "muc.meet.jitsi"), tenant="engineering")
    assert probe.endpoint == "https://jitsi.example/engineering/http-bind"

    untenanted = BoshProbe("https://jitsi.example", Hosts("meet.jitsi", "muc.meet.jitsi"))
    assert untenanted.endpoint == "https://jitsi.example/http-bind"
