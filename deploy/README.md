# Deploying Zulip Meet

```bash
./zulip-meet up
./zulip-meet trust
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

## When something is wrong

**The call panel does nothing.** The certificate authority is not trusted. Run `./zulip-meet trust`.
Zulip itself will let you click through a warning; the iframe will not, and browsers do not prompt
about a certificate inside one. Zulip detects this case and says so rather than leaving the button
dead — if you see no message at all, the problem is something else.

**`port is already allocated` on the video bridge.** Something else holds `JVB_PORT` — another Jitsi
is the usual culprit. Change it in `.env` and run `up` again.

**`docker compose` is not found on Windows.** Use Git Bash. A WSL distro with its own `docker.io`
package shadows Docker Desktop's CLI and has no Compose plugin behind it.

**A URL does not resolve from curl or a script.** `*.localhost` is resolved by browsers, not by the
system resolver. Use `--resolve host:port:127.0.0.1`, as `verify` does.
