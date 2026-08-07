# Deploying lounges, guest access and call door policies

The batch built 2026-08-06/07: lounges and rooms, room settings, knock/admit,
anonymous guest tokens, and `call_door_policy`. Three things move, and one of
them is a set of Jitsi settings that no file in this repository can carry for
you.

Read [`lounges.md`](lounges.md) for what the feature *is*. This is only how to
get it onto the live stack.

**Nothing is committed yet.** Both repositories have uncommitted work; step 1 is
committing it, and nothing else can proceed until that is done.

---

## Do this first, before anything else

**Check whether the JWT's moderator claim is actually enforced in production.**
It very probably is not, and has not been since moderator-from-Zulip-roles was
first shipped.

```bash
cd /srv/davig01.asuscomm.com/meet-zulip
grep -E '^(XMPP_MUC_MODULES|ENABLE_AUTO_OWNER|JICOFO_ENABLE_AUTH)=' .env
docker compose exec prosody sh -c \
  'grep -A 12 "Component \"muc.meet.jitsi\"" /run/prosody/config/conf.d/jitsi-meet.cfg.lua'
```

If `token_affiliation` is missing from that component's `modules_enabled`, then
nothing in the deployment has ever read `context.user.moderator`, and Jicofo has
been granting ownership to every holder of a valid token — anonymous guests
included, once guest access goes live. On the dev stack that let an
unauthenticated visitor **end a meeting for everyone**.

The fix is step 3 below. It is independent of the rest of this batch and can be
applied on its own, today.

---

## 1. Commit

Two repositories, neither pushed.

**The Zulip fork** (`~/zulip`, branch `lounges` off `web-push-pwa`) — 59 files.
Note two things before you commit:

- `zproject/dev_settings.py` is a **local edit** pointing at `localhost:8443` and
  `localhost:8080`. It is dev-only and never read in production, but committing
  it puts a developer's machine into the image. Leave it out:
  `git restore zproject/dev_settings.py` or keep it unstaged permanently.
- Migrations `0811`–`0816` must go in the same commit series as the model
  changes, or an intermediate checkout will not migrate.

**The monorepo** — the conferencing service (`lounge_room_id` support), the
prosody deploy files, `docs/`, the dev-jitsi runbook and the stub service.
`conferencing.zip` at the top level is a build artifact; do not commit it.

I can prepare both commit series on request — ask, and I will stage and write
them, but the `git push` is yours to run.

## 2. Rebuild the Zulip image

Backend and frontend both changed, so this is a full rebuild of the custom
image, as with every previous fork change:

```bash
cd /srv/davig01.asuscomm.com/zulip
docker compose build --build-arg ZULIP_GIT_URL=https://github.com/Dyslectric/zulip-meet-integration --build-arg ZULIP_GIT_REF=lounges zulip
```

Bump the `image:` tag in `compose.override.yaml` (it was `zulip-jitsi:12.1-3`;
this batch makes it `12.1-4`) so the running stack picks up the new build rather
than the cached one.

The six migrations run on container start. `0816` is add-backfill-drop: it
converts `require_moderator_to_join=True` into `call_door_policy=moderator` and
then drops the boolean. It is reversible, and the reverse degrades
`authenticated_user` to the strict value rather than the loose one.

### You are not deploying one feature — read this before you build

`lounges` sits on `web-push-pwa`, which is **34 commits ahead of `main`**, and
`main` (`5086437`) is what production runs. So this build ships three unreleased
bodies of work at once:

| | commits | state |
|---|---|---|
| Web Push + installable PWA | 13 | never deployed; **dormant without VAPID keys** |
| Voice channels, channel kinds, sidebar call affordances | ~13 | never deployed |
| Lounges, guests, door policies | uncommitted | this batch |

Web Push is the safe one: `WEB_PUSH_ENABLED` is
`VAPID_PUBLIC_KEY is not None and VAPID_PRIVATE_KEY is not None`
(`zproject/computed_settings.py`), so with no `vapid_public_key` /
`vapid_private_key` in `zulip-secrets.conf` the whole feature stays off and
nothing regresses. Turn it on deliberately, later, by generating a keypair — not
as a side effect of this deploy.

The voice-channel and sidebar commits are the ones to be careful about: they
change every channel row in the sidebar and add a channel-kind concept, and they
have never run in production. Nothing about them is opt-in the way lounges are.

If you want lounges *without* the other two, that is a rebase onto `main` and it
will not be clean — lounges builds directly on voice channels. Say so and I will
do it, but the honest recommendation is to ship the stack and test the sidebar
carefully rather than untangle it.

## 3. The Jitsi settings — the part no file here can do for you

In the meet stack's `.env`. All five, and the deployment is only as good as the
weakest:

```
XMPP_MUC_MODULES=token_affiliation
ENABLE_AUTO_OWNER=false
JICOFO_ENABLE_AUTH=0
DISABLE_PROFILE=true
HIDE_PREJOIN_DISPLAY_NAME=true
```

- `XMPP_MUC_MODULES` loads the module that reads `context.user.moderator`. It
  ships in the image but is **not enabled by default**, so until you ask for it
  nothing reads the claim Zulip mints.
- `ENABLE_AUTO_OWNER=false` stops Jicofo handing owner to whoever joins first.
  It defaults to *true*.
- `JICOFO_ENABLE_AUTH=0` is the one that actually bit. Jicofo has a second,
  independent path to granting ownership — its own authentication — and it
  defaults to `ENABLE_AUTH`. Under `AUTH_TYPE=jwt` it grants ownership to every
  holder of a valid token. Prosody still gates entry and still decides moderator;
  this only stops Jicofo overriding both.
- `DISABLE_PROFILE=true` makes the display name read-only. Without it a guest can
  rename themselves past the `(guest)` marker and type a colleague's name.
- `HIDE_PREJOIN_DISPLAY_NAME=true` stops the prejoin screen offering a field that
  no longer does anything.

Then `docker compose up -d prosody jicofo web` and verify:

```bash
docker compose exec prosody sh -c 'grep -A 12 "Component \"muc.meet.jitsi\"" /run/prosody/config/conf.d/jitsi-meet.cfg.lua' | grep token_affiliation
docker compose exec jicofo sh -c 'grep -c "authentication {" /run/jicofo/config/jicofo.conf'   # want 0
docker compose exec jicofo sh -c 'grep enable-auto-owner /run/jicofo/config/jicofo.conf'       # want false
docker compose exec web sh -c 'grep disableProfile /run/web/config/config.js'                  # want true
```

`deploy/prosody/Dockerfile` and `docker-compose.override.example.yml` now
document all of this; the Dockerfile itself needs no rebuild for these settings.

## 4. Redeploy the conferencing service

`Call.lounge_room_id` and the occupancy plumbing for rooms. Sync the **whole**
`conferencing/` directory and rebuild `--no-cache` — a partial sync has bitten
this project before, leaving one module new and another old with no error.

```bash
cd /srv/davig01.asuscomm.com/meet-zulip
docker compose build --no-cache conferencing && docker compose up -d conferencing
```

Order matters: the service must understand `lounge_room_id` before Zulip starts
minting lounge rooms, so do this before or with the Zulip rebuild, not after.

## 5. Turn it on

Nothing here changes an existing channel. Lounges are opt-in per channel, from
**Channel settings → Channel kind → Lounge**, and `call_door_policy` defaults to
`anarchy` on everything.

For web-public channels, decide the door policy deliberately: `anarchy` means an
anonymous visitor may start a call alone in your organization's channel.
`authenticated_user` is the usual answer — visitors may join a conversation
members are having, and cannot hold one among themselves.

---

## Verifying it took

0. The sidebar still looks right for ordinary text channels, and channel settings
   opens without error. These come from the unreleased voice-channel commits, not
   from lounges, and they touch every row.
1. A moderator and a non-moderator join the same call. Only the moderator has
   moderator controls. **This is the one that has silently never worked.**
2. A logged-out visitor on a web-public voice channel: name is not editable, and
   "End meeting for all" is absent.
3. Set a web-public channel to `authenticated_user`, have a visitor try to join
   with nobody inside (refused, with a reason shown), then with a member inside
   (admitted).
4. A lounge: start a room, check it appears in the sidebar with its occupants,
   and that it disappears when the last person leaves.

## What is not covered

- **Mobile.** Lounges are a desktop/web sidebar feature; the Flutter client knows
  nothing about them.
- **Anonymous guests cannot knock.** Being admitted means being added to a set of
  Zulip accounts, so a visitor with no account cannot be admitted at all.
- **Group-based tenanting and anonymous guests do not compose.** If
  `JITSI_TENANT_BY_GROUP` is set, a visitor lands in the default tenant and may
  not be where the members are. See `lounges.md`.
