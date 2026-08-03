"""
A minimal BOSH client that answers exactly one question:

    "Does this Prosody let this token into this room?"

Rev 3 section 8 makes phase one's exit criterion machine-checkable: *do not
proceed until you have proven that a token for tenant A cannot open a room in
tenant B*. A browser test can demonstrate that to a human, but it is slow and it
fails for boring reasons (a selector moved, a video device is missing). This
speaks BOSH directly to Prosody so the answer is deterministic.

How the token actually travels, which is not obvious and which this harness
originally got wrong: it is **not** a SASL password. Jitsi's `mod_jitsi_session`
reads it from the BOSH URL query string (`?token=`) or an
`Authorization: Bearer` header and stores it on the session; `mod_auth_token`
then registers a SASL **ANONYMOUS** mechanism whose callback validates that
stored token. `mod_auth_token`'s `provider.test_password` explicitly returns
"Password based auth not supported", so a PLAIN attempt fails with
`invalid-mechanism` — which looks exactly like a rejected token if you are not
reading carefully.

Two enforcement points are exercised, and they are genuinely different:

* **SASL authentication** — `mod_auth_token` verifies the signature, issuer,
  audience and expiry, and checks the `sub` claim against the virtual host. This
  is where a tenant mismatch is caught.
* **MUC join** — `token_verification` checks the `room` claim against the room
  actually being joined, and `token_no_wildcard` rejects wildcard rooms. This is
  where a room mismatch is caught.

A test that only reaches the first of those has not proven what it thinks it has.
"""

from __future__ import annotations

import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlencode

import requests

BOSH_NS = "http://jabber.org/protocol/httpbind"
SASL_NS = "urn:ietf:params:xml:ns:xmpp-sasl"
BIND_NS = "urn:ietf:params:xml:ns:xmpp-bind"
SESSION_NS = "urn:ietf:params:xml:ns:xmpp-session"
CLIENT_NS = "jabber:client"
MUC_NS = "http://jabber.org/protocol/muc"


class Outcome(Enum):
    """What Prosody decided, and at which enforcement point."""

    JOINED = "joined"
    AUTH_REJECTED = "auth_rejected"
    ROOM_REJECTED = "room_rejected"
    TRANSPORT_ERROR = "transport_error"

    @property
    def admitted(self) -> bool:
        return self is Outcome.JOINED


@dataclass
class ProbeResult:
    outcome: Outcome
    detail: str = ""
    stanzas: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.outcome.admitted

    def __str__(self) -> str:
        return f"{self.outcome.value}: {self.detail}" if self.detail else self.outcome.value


@dataclass(frozen=True)
class Hosts:
    """The XMPP domains a Jitsi deployment is actually using.

    Prefer `discover` over hand-writing these. Tenant routing changes them in
    ways that are easy to guess wrong, and guessing wrong produces a failure that
    looks exactly like the token being rejected — which would make this probe
    lie to you in the most damaging possible direction.
    """

    domain: str
    muc: str

    @classmethod
    def discover(
        cls,
        base_url: str,
        tenant: str | None = None,
        *,
        timeout: float = 10.0,
        verify_tls: bool = True,
    ) -> "Hosts":
        """Read `hosts.domain` and `hosts.muc` out of the deployment's config.js."""
        url = f"{base_url.rstrip('/')}/{tenant}/config.js" if tenant else f"{base_url.rstrip('/')}/config.js"
        response = requests.get(url, timeout=timeout, verify=verify_tls)
        response.raise_for_status()
        body = response.text

        domain = _extract_js_string(body, "domain")
        muc = _extract_js_string(body, "muc")
        if not domain or not muc:
            raise ValueError(
                f"could not find hosts.domain and hosts.muc in {url}. Pass a Hosts "
                "instance explicitly if your deployment templates config.js "
                "differently."
            )
        return cls(domain=domain, muc=muc)


class BoshProbe:
    """A single-use BOSH session against one Jitsi deployment."""

    #: How the MUC host is derived from the tenant. docker-jitsi-meet's default.
    DEFAULT_MUC_TEMPLATE = "conference.{tenant}.{domain}"

    def __init__(
        self,
        base_url: str,
        hosts: Hosts,
        *,
        tenant: str | None = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        tenant_in_path: bool = True,
        tenant_in_xmpp_host: bool = False,
        muc_template: str | None = None,
        token_transport: str = "query",
    ) -> None:
        """
        `tenant_in_path` selects how the deployment expresses tenancy.

        With nginx tenant routing in front (docker-jitsi-meet with
        ENABLE_SUBDOMAINS), BOSH lives at `/TENANT/http-bind` and this should be
        True. Talking to Prosody directly, BOSH is at `/http-bind` and the tenant
        is carried only by the `to` domain, so it should be False.

        Either way the tenant reaches Prosody as a virtual host, which is what
        `mod_auth_token` compares the `sub` claim against. Getting this wrong
        does not weaken the test — it makes every case fail — but it wastes an
        afternoon, so it is explicit rather than inferred.
        """
        self.base_url = base_url.rstrip("/")
        self.hosts = hosts
        self.tenant = tenant
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.tenant_in_path = tenant_in_path
        self.tenant_in_xmpp_host = tenant_in_xmpp_host
        self.muc_template = muc_template or self.DEFAULT_MUC_TEMPLATE

        self._token: str | None = None
        self._room: str | None = None
        self._token_transport = token_transport
        self._rid = int(uuid.uuid4().int % 1_000_000) + 1_000_000
        self._sid: str | None = None
        self._session = requests.Session()
        self.stanzas: list[str] = []

    @property
    def endpoint(self) -> str:
        if self.tenant and self.tenant_in_path:
            return f"{self.base_url}/{self.tenant}/http-bind"
        return f"{self.base_url}/http-bind"

    def _request_url(self) -> str:
        """The BOSH URL, carrying the token the way Jitsi's client carries it.

        `mod_jitsi_session` hooks the `bosh-session` event and reads `token`,
        `room` and `prefix` off the query string. The token must therefore be
        present on the request that creates the session, not merely on a later
        one.
        """
        if self._token_transport != "query" or self._token is None:
            return self.endpoint
        params = {"token": self._token}
        if self._room:
            params["room"] = self._room
        if self.tenant:
            params["prefix"] = self.tenant
        return f"{self.endpoint}?{urlencode(params)}"

    def _request_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "text/xml; charset=utf-8"}
        if self._token_transport == "header" and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @property
    def xmpp_host(self) -> str:
        """The virtual host we authenticate against.

        In Jitsi's multi-tenancy layout this is the *base* domain, not
        `<tenant>.<base>`: there is one virtual host and one MUC component, and
        the tenant is carried by the MUC address, which `muc_domain_mapper`
        rewrites into `[<tenant>]room@conference.<base>`. `token_verification`
        recovers the tenant from that mapped name and compares it to `sub`.

        Set `tenant_in_xmpp_host` for deployments that really do give each tenant
        its own virtual host.
        """
        if self.tenant and self.tenant_in_xmpp_host:
            return f"{self.tenant}.{self.hosts.domain}"
        return self.hosts.domain

    @property
    def muc_host(self) -> str:
        if self.tenant:
            return self.muc_template.format(tenant=self.tenant, domain=self.hosts.domain)
        return self.hosts.muc

    def attempt_join(
        self,
        token: str,
        room: str,
        *,
        nick: str | None = None,
    ) -> ProbeResult:
        """Authenticate with `token` and try to join `room`.

        Returns rather than raises for every outcome that represents a decision
        by Prosody. Only genuine transport problems produce TRANSPORT_ERROR, and
        that is deliberately distinguishable from a rejection so that a broken
        test environment is never mistaken for a passing security control.
        """
        self._token = token
        self._room = room
        # A unique nick per attempt. Reusing one means the second probe collides
        # with the first probe's still-present occupant and comes back
        # `conflict`, which is a MUC nickname error rather than anything to do
        # with the token — another way for this harness to report a failure that
        # is really its own.
        #
        # No hyphens: docker-jitsi-meet's `muc_resource_validate` module refuses
        # any MUC resource that does not match ^[a-zA-Z0-9][a-zA-Z0-9_]*$
        # (underscores allowed, hyphens not). A `phase1-probe-...` nick is
        # rejected as "invalid MUC resource" *before* token_verification runs —
        # which false-fails the happy path and false-passes the room cases.
        nick = nick or f"phase1probe_{uuid.uuid4().hex[:8]}"
        try:
            if not self._open_stream():
                return self._result(Outcome.TRANSPORT_ERROR, "no session id in BOSH response")

            auth = self._authenticate()
            if auth is not None:
                return auth

            if not self._restart_stream():
                return self._result(Outcome.TRANSPORT_ERROR, "stream restart failed")

            if not self._bind_and_session(nick):
                return self._result(Outcome.TRANSPORT_ERROR, "resource bind failed")

            return self._join_muc(room, nick)
        except requests.RequestException as exc:
            return self._result(Outcome.TRANSPORT_ERROR, f"{type(exc).__name__}: {exc}")
        finally:
            self._session.close()

    # -- BOSH mechanics ---------------------------------------------------

    def _open_stream(self) -> bool:
        to = self.xmpp_host
        body = (
            f"<body xmlns='{BOSH_NS}' xmlns:xmpp='urn:xmpp:xbosh' "
            f"content='text/xml; charset=utf-8' hold='1' rid='{self._next_rid()}' "
            f"to='{to}' ver='1.6' wait='30' xml:lang='en' xmpp:version='1.0'/>"
        )
        root = self._post(body)
        self._sid = root.get("sid")
        return self._sid is not None

    def _authenticate(self) -> ProbeResult | None:
        """Return None if authentication succeeded, else a terminal result.

        ANONYMOUS carries no credential: the token was already handed over on the
        BOSH request that opened the session. `invalid-mechanism` here almost
        always means the token never reached Prosody, not that it was rejected —
        so it is reported distinctly rather than folded into AUTH_REJECTED.
        """
        root = self._post(self._wrap(f"<auth xmlns='{SASL_NS}' mechanism='ANONYMOUS'/>"))

        deadline = time.monotonic() + self.timeout
        while True:
            if root.find(f"{{{SASL_NS}}}success") is not None:
                return None
            failure = root.find(f"{{{SASL_NS}}}failure")
            if failure is not None:
                condition = _condition(failure)
                if condition == "invalid-mechanism":
                    return self._result(
                        Outcome.TRANSPORT_ERROR,
                        "invalid-mechanism: Prosody offered no usable SASL mechanism. "
                        "The token almost certainly never reached it — check that "
                        "mod_jitsi_session is loaded and authentication = \"token\".",
                    )
                return self._result(Outcome.AUTH_REJECTED, condition)
            if root.get("type") == "terminate":
                return self._result(
                    Outcome.AUTH_REJECTED,
                    root.get("condition") or "stream terminated during SASL",
                )
            if time.monotonic() > deadline:
                return self._result(Outcome.TRANSPORT_ERROR, "timed out awaiting SASL result")
            root = self._post(self._wrap(""))

    def _restart_stream(self) -> bool:
        to = self.xmpp_host
        body = (
            f"<body xmlns='{BOSH_NS}' xmlns:xmpp='urn:xmpp:xbosh' "
            f"rid='{self._next_rid()}' sid='{self._sid}' to='{to}' "
            f"xml:lang='en' xmpp:restart='true'/>"
        )
        root = self._post(body)
        return root.get("type") != "terminate"

    def _bind_and_session(self, resource: str) -> bool:
        root = self._post(
            self._wrap(
                f"<iq xmlns='{CLIENT_NS}' type='set' id='bind_1'>"
                f"<bind xmlns='{BIND_NS}'><resource>{resource}</resource></bind></iq>"
            )
        )
        deadline = time.monotonic() + self.timeout
        while root.find(f".//{{{BIND_NS}}}jid") is None:
            if root.get("type") == "terminate" or time.monotonic() > deadline:
                return False
            root = self._post(self._wrap(""))

        self._post(
            self._wrap(
                f"<iq xmlns='{CLIENT_NS}' type='set' id='sess_1'>"
                f"<session xmlns='{SESSION_NS}'/></iq>"
            )
        )
        return True

    def _join_muc(self, room: str, nick: str) -> ProbeResult:
        room_jid = f"{room}@{self.muc_host}"
        root = self._post(
            self._wrap(
                f"<presence xmlns='{CLIENT_NS}' to='{room_jid}/{nick}'>"
                f"<x xmlns='{MUC_NS}'/></presence>"
            )
        )

        deadline = time.monotonic() + self.timeout
        while True:
            for presence in root.findall(f"{{{CLIENT_NS}}}presence"):
                sender = presence.get("from", "")
                if not sender.startswith(room_jid):
                    continue
                if presence.get("type") == "error":
                    error = presence.find(f"{{{CLIENT_NS}}}error")
                    return self._result(
                        Outcome.ROOM_REJECTED,
                        _condition(error) if error is not None else "presence error",
                    )
                self._leave(room_jid, nick)
                return self._result(Outcome.JOINED, f"joined {room_jid}")

            if root.get("type") == "terminate":
                return self._result(
                    Outcome.ROOM_REJECTED,
                    root.get("condition") or "stream terminated during MUC join",
                )
            if time.monotonic() > deadline:
                return self._result(
                    Outcome.TRANSPORT_ERROR, f"timed out awaiting presence from {room_jid}"
                )
            root = self._post(self._wrap(""))

    def _leave(self, room_jid: str, nick: str) -> None:
        """Leave the room and end the session.

        Best effort: a probe that succeeds should not leave a ghost occupant
        behind, both because it pollutes occupancy on a real deployment and
        because the next run would collide with it.
        """
        try:
            self._post(
                self._wrap(
                    f"<presence xmlns='{CLIENT_NS}' to='{room_jid}/{nick}' type='unavailable'/>"
                )
            )
            self._post(
                f"<body xmlns='{BOSH_NS}' rid='{self._next_rid()}' sid='{self._sid}' "
                f"type='terminate'/>"
            )
        except requests.RequestException:
            pass

    # -- plumbing ---------------------------------------------------------

    def _wrap(self, payload: str) -> str:
        return (
            f"<body xmlns='{BOSH_NS}' rid='{self._next_rid()}' sid='{self._sid}'>"
            f"{payload}</body>"
        )

    def _post(self, body: str) -> ET.Element:
        response = self._session.post(
            self._request_url(),
            data=body.encode("utf-8"),
            headers=self._request_headers(),
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        response.raise_for_status()
        self.stanzas.append(response.text)
        return ET.fromstring(response.text)

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    def _result(self, outcome: Outcome, detail: str = "") -> ProbeResult:
        return ProbeResult(outcome=outcome, detail=detail, stanzas=list(self.stanzas))


def _condition(element: ET.Element) -> str:
    """Extract the XMPP error condition local name, e.g. 'not-authorized'."""
    for child in element:
        tag = child.tag.split("}")[-1]
        if tag != "text":
            return tag
    return element.text or "unknown"


def _extract_js_string(body: str, key: str) -> str | None:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*['\"]([^'\"]+)['\"]", body)
    return match.group(1) if match else None
