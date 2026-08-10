# A local Jitsi for development

Runs docker-jitsi-meet on your own machine so that occupancy is **real**: joining
and leaving a conference in the browser drives the Zulip sidebar, because Prosody
reports it, exactly as it will in production.

Without this, the stub service has to guess. Its guess — that whoever mints a
token walks through the door — is what makes a room appear to hold somebody who
is not in it, and it can never notice a hangup at all.

The stub does not become unnecessary here. It keeps standing in for the real
conferencing service's *bookkeeping* half (which conversation a room belongs to,
and telling Zulip about it); what changes is that its idea of who is in a room
stops being invented and starts coming from Prosody.

## What you need to do first

**Enable Docker Desktop's WSL integration for `Ubuntu-24.04`.** Docker is not on
the PATH in that distro right now, so nothing below will run. Docker Desktop →
Settings → Resources → WSL integration.

## The sequence

### 1. Get docker-jitsi-meet

Releases carry no zip asset, so take the source archive for a tag:

```bash
cd ~ && curl -fsSL https://github.com/jitsi/docker-jitsi-meet/archive/refs/tags/stable-11146-1.tar.gz | tar xz && mv docker-jitsi-meet-stable-11146-1 dev-jitsi && cd dev-jitsi && cp env.example .env && ./gen-passwords.sh
```

**The release tag is not an image tag.** There is no `jitsi/prosody:stable-11146-1`
on Docker Hub — the published tags are `unstable-YYYY-MM-DD`. Use the same one
the production companion pins, so dev and prod run the same Prosody:

```
JITSI_IMAGE_VERSION=unstable-2026-06-25
```

### 1b. Create the config directories *before the first start*

This one costs half an hour if you skip it. Docker creates missing bind-mount
sources **as root**, and every container then refuses to start with
`FATAL ERROR: directory '/var/lib/prosody' is not writable by the container user
(uid 1000)`. The paths are not all obvious either — the storage mounts live under
`${CONFIG}/storage/`, not under `${CONFIG}/prosody/`.

```bash
cd ~/dev-jitsi && grep -oP '\$\{CONFIG\}/\K[^:]+' docker-compose.yml | sort -u | xargs -I{} mkdir -p ~/.jitsi-meet-cfg/{}
```

If you have already started it and hit that error, you cannot fix the ownership
with `chown` unless you have sudo — but Docker runs as root, so borrow it:

```bash
docker run --rm -v ~/.jitsi-meet-cfg:/cfg alpine:3 chown -R 1000:1000 /cfg
```

### 2. Point it at the same JWT settings Zulip's dev server uses

Edit `.env`. These must match `zproject/default_settings.py` and
`zproject/dev-secrets.conf` exactly — a token Prosody will not accept looks
identical to a broken room name from the outside.

```
HTTPS_PORT=8443
PUBLIC_URL=https://localhost:8443
ENABLE_LETSENCRYPT=0
ENABLE_HTTP_REDIRECT=0
ENABLE_AUTH=1
ENABLE_GUESTS=0
AUTH_TYPE=jwt
JWT_APP_ID=zulip
JWT_ACCEPTED_ISSUERS=zulip
JWT_ACCEPTED_AUDIENCES=jitsi
```

**Setting `AUTH_TYPE=jwt` does not make the token authoritative.** Prosody will
verify the signature and then ignore almost everything the token says, and Jicofo
will actively override it. Five more lines are what make it mean something, and
without them the whole moderator design is decorative:

```
XMPP_MUC_MODULES=token_affiliation
ENABLE_AUTO_OWNER=false
JICOFO_ENABLE_AUTH=0
DISABLE_PROFILE=true
HIDE_PREJOIN_DISPLAY_NAME=true
```

- `XMPP_MUC_MODULES=token_affiliation` loads the module that reads
  `context.user.moderator` and sets the occupant's affiliation from it. The
  module **ships in the image but is not enabled**, so until you ask for it
  nothing anywhere reads the claim Zulip mints.
- `ENABLE_AUTO_OWNER=false` stops Jicofo granting owner to whoever joins first.
  It defaults to *true* when unset, which is precisely the "hands the room to a
  passer-by" behaviour the design refuses.
- `JICOFO_ENABLE_AUTH=0` is the one that actually bit, and it is the least
  obvious. **Jicofo has a second, independent path to granting ownership** — its
  own authentication — and `JICOFO_ENABLE_AUTH` defaults to `ENABLE_AUTH`. With
  `AUTH_TYPE=jwt` that means Jicofo treats *every holder of a valid token* as an
  authenticated user to be made owner. Anonymous guests hold valid tokens by
  construction, so they were promoted about a second after joining:

  ```
  onMemberJoined: Member joined:466949ed ... role=PARTICIPANT
  AbstractAuthAuthority.authenticateJidWithSession: Authenticated jid: 466949ed...
  end_conference: Room ... destroyed by occupant 466949ed...
  ```

  Turning it off costs nothing. Prosody's `token_verification` still gates entry
  on the token and `token_affiliation` still decides moderator from the claim;
  this only stops Jicofo overriding both.
- `DISABLE_PROFILE=true` makes the display name read-only. It is what
  jitsi-meet's `isNameReadOnly()` actually reads. Without it a participant can
  rename themselves in the Jitsi UI, which makes a guest's "(guest)" marker
  worthless — they can simply type a colleague's name.
- `HIDE_PREJOIN_DISPLAY_NAME=true` stops the prejoin screen offering a name field
  that no longer does anything.

All five were missing on first setup, and the symptoms read as application bugs
rather than deployment ones: editable names, and guests holding moderator — up to
and including ending a meeting for everyone.

**Debugging this class of problem.** Set `LOG_LEVEL=debug` in `.env` and Prosody
will log what `token_affiliation` decided per join
(`set affiliation=member for <jid>`). Jicofo's log shows the role it saw
(`Member joined:... role=PARTICIPANT`) and, crucially, anything it did
*afterwards*. The affiliation line alone proves nothing: affiliation and role are
different things, and `mod_end_conference` checks the role.

**HTTPS is not optional, even locally.** Jitsi's `external_api.js` builds the
iframe URL as `` `https://${domain}/…` `` with the scheme hardcoded, and takes a
bare host rather than a URL — there is no way to tell it otherwise. Point Zulip
at an http deployment and the embedded call loads its script and then frames a
URL nothing is listening on. `ENABLE_LETSENCRYPT=0` makes the stack generate a
self-signed certificate, which is all a dev box needs.

Then two secrets copied out of `~/zulip/zproject/dev-secrets.conf`:

```
JWT_APP_SECRET=<the value of jitsi_jwt_app_secret>
EVENT_SYNC_SECRET=<the value of jitsi_conferencing_secret>
```

`EVENT_SYNC_SECRET` is the same secret the stub authenticates Prosody with; it
reads it from `dev-secrets.conf` itself, so copying it here is what makes the two
sides agree.

### 3. Add the event_sync component

```bash
cp ~/zulip-jitsi-authentik/consort-conferencing/deploy/dev-jitsi/docker-compose.override.yml . && mkdir -p prosody-custom && cp ~/zulip-jitsi-authentik/consort-conferencing/deploy/prosody/Dockerfile prosody-custom/ && cp ~/zulip-jitsi-authentik/consort-conferencing/deploy/dev-jitsi/event_sync.cfg.lua ~/.jitsi-meet-cfg/prosody/prosody-plugins-custom/
```

Then fetch the plugin itself, pinned rather than tracking `main` — a dev
environment that changes because somebody pushed upstream is worse than one that
is slightly old:

```bash
curl -L https://raw.githubusercontent.com/jitsi-contrib/prosody-plugins/fe4532b8ef51bfbb74119928ffe8c683659c1d2d/event_sync/mod_event_sync_component.lua -o ~/.jitsi-meet-cfg/prosody/prosody-plugins-custom/mod_event_sync_component.lua
```

That commit is `fe4532b8` — the tip of `event_sync/mod_event_sync_component.lua`
as of 2026-08-06, sha256 `c4ea639f9b61dfa8…`.

### 4. Move JVB's debug port off 8080

The stub listens on 8080, and so does JVB's colibri debug endpoint. Whichever
starts second fails with `ports are not available`. Move JVB's — it is a
localhost-only debug surface, whereas the stub's port is baked into
`dev_settings.py` and `event_sync.cfg.lua`:

```
JVB_COLIBRI_PORT=8090
```

### 5. Start it

```bash
docker compose build prosody && docker compose up -d
```

### 6. Run the stub so containers can reach it

Bound to `127.0.0.1` it is invisible from inside Docker, and the only symptom is
Prosody logging a failed POST on every join.

```bash
cd ~/zulip-jitsi-authentik/consort-conferencing && python3 scripts/stub_conferencing_service.py --bind 0.0.0.0 --no-auto-join
```

`--no-auto-join` is the point of this whole exercise: with Prosody reporting real
occupancy, the stub must stop pretending.

### 7. Point Zulip's dev server at it

In `zproject/dev_settings.py`:

```python
JITSI_SERVER_URL = "https://localhost:8443"
```

`JITSI_DEFAULT_TENANT` stays `"root"`, so minted URLs are
`https://localhost:8443/root/c-…`.

**Then visit https://localhost:8443 once in the browser you will test with and
accept the certificate warning.** The certificate is self-signed, and a browser
will not prompt about a bad certificate inside an iframe — it refuses it
silently, so the call area just stays blank with nothing useful in the console.
Accepting it once for that origin is what makes the embed work.

## Checking it actually works

The failure mode to worry about is the quiet one, where everything starts
cleanly and no events ever arrive.

```bash
docker compose logs prosody | grep -iE "esync|Danger"
```

You want a line showing `esync.meet.jitsi` loaded against `muc.meet.jitsi`, and
no "Danger, Will Robinson" (that means Prosody is running as root and will
refuse to start properly).

Then join a call from Zulip and watch the stub's output. A real join prints:

```
[prosody] join c-xxxxxxxxxxxxxxxx: King Hamlet (now 1)
  -> zulip 200
```

If you instead see `for unknown room ... ignored`, Prosody is reporting a room
the stub was never told about — the `calls/created` notice from Zulip did not
arrive, so check `JITSI_CONFERENCING_URL` in `dev_settings.py`.

If you see nothing at all, the events are not leaving Prosody: check the secret
matches and that the stub is bound to `0.0.0.0`.

## What has and has not been proven

Verified against this stack running:

- All four containers healthy, with `esync.meet.jitsi … Component loaded
  muc.meet.jitsi` in the Prosody log.
- A container can reach the stub at `host.docker.internal:8080`.
- Zulip mints `http://localhost:8000/root/c-…` with `iss=zulip`, `aud=jitsi`,
  `sub=root` and the right room.
- **Jitsi accepts the token**: the prejoin screen shows the joiner's name, which
  it can only have read out of the JWT.
- The stub's four sinks behave correctly when sent real event_sync payloads
  (join, leave, room destroyed, bad bearer, unknown room).

**Not yet proven: a real MUC join firing event_sync.** That needs a browser that
can grant microphone access — Jitsi's prejoin screen will not let you past
without it. Open a minted call in a normal browser, allow the microphone, and
watch the stub:

```
[prosody] join c-xxxxxxxxxxxxxxxx: King Hamlet (now 1)
  -> zulip 200
```

If that appears, the loop is closed and the sidebar is being driven by real
occupancy. If it does not, the events are not leaving Prosody: check the secret
and that the stub is bound to `0.0.0.0`.

## If the call area stays blank

In order of likelihood:

1. **The self-signed certificate has not been accepted.** Visit
   https://localhost:8443 directly, click through the warning, and reload Zulip.
   This produces no useful console error, which is why it is first.
2. **Content-Security-Policy.** The embedded call iframes the Jitsi origin, and
   the dev server pointed at a different one until now. A CSP violation *does*
   show in the console.
3. **`external_api.js` failed to load.** The console names the URL it tried. It
   should be `https://localhost:8443/external_api.js`; if it is anything else,
   the minted URL and `JITSI_SERVER_URL` disagree.
