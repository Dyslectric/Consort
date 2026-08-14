# Deploying Consort

```bash
./consort up
./consort trust
```

The first brings the whole stack up from nothing. The second tells you how to trust the certificate
authority it generated, which you need before a call will load.

| Command | |
|---|---|
| `up` | check, generate, pull, start, bootstrap, verify — idempotent, run it again after editing `.env` |
| `up --build` | the same, building the four images locally instead of pulling (remembered afterwards) |
| `trust` | print the certificate-authority import line for your platform |
| `verify` | re-run the checks against a running stack |
| `preflight` | check the machine, change nothing |
| `logs [service]` | follow logs |
| `down` | stop, keep all data |
| `destroy` | stop and delete every volume, secret and generated file |

## What `up` actually does

Preflight, then generate `.env` and the secrets, then pull, then start eleven containers. It waits
for Zulip to answer — the first boot runs every migration against an empty database and takes
minutes — then creates the organization, its owner, and the conferencing bot, writes the bot's
credentials back into `.env`, and restarts the conferencing service with its event loop on.

That order is forced. The bot is a Zulip account, so it cannot exist before Zulip does; the service
therefore starts with `EVENT_LOOP=0`, tracking occupancy and answering the sidebar without one, and
only picks up an account once there is one to pick up.

Finally it verifies, and prints a URL, an email and a password.

## How the files fit together

`COMPOSE_FILE` in `.env` merges four files in order:

| | |
|---|---|
| `base.yaml` | Exists only to fix the project directory. Compose resolves every relative path against the *first* file it is given, which would otherwise be the vendored upstream — and it does not check bind-mount sources, so that mistake survives `docker compose config` and shows up at runtime as empty mounts. |
| `vendor/docker-jitsi-meet/` | Upstream, verbatim, at release `stable-11146-1`. Never edited. |
| `vendor/docker-zulip/` | Upstream, verbatim, at `12.1-0`. Never edited. |
| `overlay.yaml` | Everything this project changes, plus Caddy and the conferencing service. This file *is* the diff against upstream, which is what makes bumping either of them a diff rather than a merge into someone else's 529-line compose. |

`build.yaml` joins them under `up --build`. It only adds `build:` blocks — the image names still come
from `overlay.yaml`, so a local build is tagged exactly as the pull would have been and everything
downstream is identical.

Removing something upstream declares needs `!reset` or `!override`: Compose *concatenates* `ports`
and `volumes` across files rather than replacing them. That is the reason for the 2.24 floor.

## Configuration

Everything is in one generated `.env`. Three values do the real work and each is consumed by more
than one service, so they appear once and are fanned out in `overlay.yaml` — the identity is
structural rather than something you have to keep true by hand:

| | |
|---|---|
| `SHARED_SECRET` | Prosody's `event_sync` → the conferencing service → Zulip's internal hook |
| `JITSI_JWT_SECRET` | Zulip mints tokens with it; Prosody validates them with it |
| `JITSI_ROOM_KEY` | derives room names; rotating it rekeys every room at once |

Zulip's own secrets are files under `secrets/`, not environment variables, because an environment
variable is visible in `docker inspect` to anything that can reach the daemon.

### Single sign-on

SAML against Authentik is off until `SAML_PROFILES` in `.env` names one of `saml/profiles/*.env`.
Nothing about it is transcribed:

| | |
|---|---|
| **Published, so fetched** | Entity ID, SSO URL and signing certificate come from the metadata document Authentik serves at `https://<host>/application/saml/<slug>/metadata/`. `generate` fetches it and renders `generated/saml/`. The certificate is not under `secrets/` — it is a public key, served at a URL to anyone who asks. |
| **Local, so never fetched** | Zulip derives `SOCIAL_AUTH_SAML_SP_ENTITY_ID` from its own hostname, and reads the optional keypair it signs requests with from `/etc/zulip/saml/zulip-cert.crt` inside the container. There is nothing in another environment that could correctly supply either. |
| **Agreed, so committed** | Which attribute is the email address, and which groups sync into Zulip user groups, is in `saml/mapping.conf`. Changing it is a commit, not a deployment step. |

A profile file holds the metadata URL and the login button's label, and is meant to be committed so
that pointing a machine at preview costs one word rather than three values out of someone else's
browser. `SAML_METADATA_URL_<PROFILE>` in `.env` overrides it for an environment not worth sharing.
More than one profile can be enabled at once; each becomes its own login button.

If the fetch fails, `generate` keeps the copy it rendered last and says so — a laptop with no route
to Authentik still brings the stack up. It only refuses when a profile is enabled, unreachable, and
has never been rendered, because leaving SSO quietly switched off is worse than stopping. A fetch
that *succeeds* and returns something else — an HTML login page, a service provider's metadata — is
a wrong URL rather than an unreachable one, and stops the run without reaching for the old copy.

`up` never overwrites an existing `.env` — it would take your secrets with it. So a setting added to
`.env.example` after you installed does **not** appear in your `.env`, and a `git pull` followed by
`up` will not pick it up. Copy the new line across by hand, then run `up` again:

```bash
grep DISABLE_DEEP_LINKING .env.example >> .env && ./consort up
```

## Running it for other people

The defaults are for one machine: `*.localhost` hostnames, a locally generated certificate
authority, and `JVB_ADVERTISE_IPS=127.0.0.1`. For a deployment others can reach, all of it is in
`.env` and `caddy/Caddyfile`:

1. Point `ZULIP_HOST` and `MEET_HOST` at real names, with DNS pointing at the host.
2. In `caddy/Caddyfile`, replace `local_certs` with an ACME issuer — everything else in that file is
   unchanged. The topology is deliberately the same either way.
3. Set `JVB_ADVERTISE_IPS` to the address participants can actually reach, and publish `JVB_PORT`
   through any firewall. This is UDP and it does not go through Caddy.
4. Consider `call_door_policy` on web-public channels: the default, `anarchy`, lets an anonymous
   visitor hold a call alone in your organization's channel. `authenticated_user` is usually right.

### Behind a Traefik that already exists

Add `traefik.yaml` to `COMPOSE_FILE` and fill in the `TRAEFIK_*` block in `.env`. Caddy stays in
the path — it is the only place that knows Zulip is on `:80`, the Jitsi web app is on `:8000`, and
that both must share a port so they share a cookie jar. Traefik terminates TLS and hands it both
names; Caddy does the split, exactly as it does on a laptop.

What the overlay changes, and why each one is not optional:

| | |
|---|---|
| Caddy joins Traefik's network | Traefik cannot route to a container it shares no network with. `traefik.docker.network` is set too, because Caddy is on three and Traefik picks among them arbitrarily otherwise — for a 502 that comes and goes with restarts. |
| A plain-HTTP listener on `CADDY_PROXY_PORT` | Nothing publishes it, so it is reachable only through Traefik. The TLS listener on `INGRESS_PORT` stays, which is how `verify` and the first-boot wait keep working without DNS or a certificate. |
| `trusted_proxies` in the Caddyfile | Caddy *overwrites* `X-Forwarded-*` for any peer not on that list, so without it Zulip is told every request arrived over plain HTTP from Traefik's address. |
| The port comes out of the public origins | `SETTING_EXTERNAL_HOST`, `PUBLIC_URL`, `ZULIP_SITE` and `SETTING_JITSI_SERVER_URL` are built as `HOST:INGRESS_PORT`, which is true on a laptop and not behind Traefik on 443. Left alone it does not fail — it generates links to a port nothing serves, and a Jitsi iframe whose origin does not match `PUBLIC_URL`. |
| `LOADBALANCER_IPS` gains Traefik's subnet | So nginx walks the `X-Forwarded-For` chain past *both* proxies to the real client. `EDGE_SUBNET` stays, because it is also how the conferencing service is allowed to call the internal hook. |

That last row is the one worth understanding. Traefik's subnet is added to `LOADBALANCER_IPS` and
deliberately **not** to `zulip/jitsi-hook.conf.template`, which is what finally makes that file's
claim true: `/api/internal/jitsi/` is reachable from the internal network and not from the ingress.
Until something sat in front of Caddy, Caddy was itself on `edge`, so a public request to that path
arrived from an allowed address and only the bearer secret stood in the way.

Media is not affected by any of this. JVB is UDP straight from the browser, does not pass through
Traefik, and still needs `JVB_ADVERTISE_IPS` and an open `JVB_PORT`.

## What `verify` asserts, and why those things

Everything it checks fails *silently*. A stack with all eleven containers running and every one of
these wrong looks, from the outside, exactly like a working one.

- **`token_affiliation` is loaded.** It ships in the Prosody image but is not enabled by default, so
  without it nothing reads the moderator claim Zulip mints and every moderator decision is decorative.
- **Jicofo's own authentication is off.** It is a second, independent path to granting ownership, and
  under `AUTH_TYPE=jwt` it grants it to every holder of a valid token — anonymous visitors included,
  theirs being valid by construction.
- **`enable-auto-owner` is false.** Otherwise the room goes to whoever joins first.
- **`disableProfile` is true.** Otherwise the display name in the token is a suggestion and a visitor
  can rename themselves past their `(guest)` marker.
- **Prosody can reach the conferencing service.** If it cannot, the sidebar is simply always empty.
- **`/saml/metadata.xml` serves XML**, when any SAML profile is enabled. Zulip answers that URL with
  an ordinary HTML page — status 200, so nothing looks wrong — if `SAMLAuthBackend` was never
  enabled, and with a 500 if python3-saml rejected the rendered settings. Either way the login page
  still shows the button, and the first person to find out is whoever clicks it.

## When something is wrong

**The call panel does nothing.** The certificate authority is not trusted. Run `./consort trust`.
Zulip itself will let you click through a warning; the iframe will not, and browsers do not prompt
about a certificate inside one. Zulip detects this case and says so rather than leaving the button
dead — if you see no message at all, the problem is something else.

**`port is already allocated` on the video bridge.** Something else holds `JVB_PORT` — another Jitsi
is the usual culprit. Change it in `.env` and run `up` again.

**`docker compose` is not found on Windows.** Use Git Bash. A WSL distro with its own `docker.io`
package shadows Docker Desktop's CLI and has no Compose plugin behind it.

**A URL does not resolve from curl or a script.** `*.localhost` is resolved by browsers, not by the
system resolver. Use `--resolve host:port:127.0.0.1`, as `verify` does.
