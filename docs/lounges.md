# Lounges and rooms

A **lounge** is a third kind of channel beside text channels and voice channels.
Its conversations are **rooms**: a room exists only while somebody is in it, so a
lounge stores nothing and an idle one is empty.

This document is the working state of that feature. It covers what is built, what
is not, the decisions that should not be re-litigated, and the traps that have
already cost time.

Everything below lives on the Zulip fork branch `lounges` (off `web-push-pwa`)
plus the working tree of this repository. Nothing is committed yet.

---

## The three kinds of channel

| | text channel | voice channel | lounge |
|---|---|---|---|
| conversations are | topics | one always-on room | many ephemeral rooms |
| text | yes | single thread, or none | none |
| stored when idle | messages | the channel | **nothing** |

They are mutually exclusive, chosen from one dropdown in channel settings
(`channel_kind`, a synthetic settings property that expands into
`voice_video_enabled` + `is_lounge` on save — the same trick `channel_privacy`
uses). A voice channel and a lounge are both "you talk here"; what keeps them
from collapsing into one concept is that an idle lounge is empty and a voice
channel's room is always there.

## Decisions — settled, do not re-open

- **Naming.** Channel kind = *lounge*; the unit inside = *room*. Avoid "huddle":
  Zulip used it internally for group DMs for years.
- **Icon.** Waveform bars in the sidebar glyph slot. The speaker is taken twice
  over already — by voice channels, and by the per-user speaking indicator.
  A sidebar row is a pair: a main glyph for what kind of channel it is, and a
  corner badge for the qualifier.

  | | glyph | badge |
  |---|---|---|
  | public lounge | bars | none |
  | private lounge | bars | lock |
  | voice channel | speaker | none |
  | private voice channel | speaker | lock |
  | voice channel with text | speaker | hash |
  | **web-public lounge** | **globe** | **bars** |
  | **web-public voice channel** | **globe** | **speaker** |

  **Web-public outranks everything.** Such a channel takes the globe and drops
  what it otherwise would have worn into the badge. Being readable by the entire
  internet is the louder fact about a channel than what shape its conversations
  take, and it is the one that has to be legible at a glance down a list of rows;
  the pair still says both things, in the order that matters. Before this, a
  web-public lounge and a web-public voice channel each rendered identically to
  their non-web-public counterparts — the loudest fact about them was the one not
  being shown.

  A plain public channel carries no badge: there is nothing to qualify, and a
  badge that only ever means "ordinary" is noise on every row that has one.
- **Desktop:** selecting a lounge is a *disclosure toggle*, not a narrow. The
  centre pane keeps showing whatever channel you were reading. **Mobile** gets a
  real centre-pane lounge view; desktop does not.
- **Idle lounges start empty.** No standing rooms. This is what keeps lounges and
  voice channels distinct.
- **Private rooms are visible but locked.** Everyone who can see the lounge sees
  the room and who is in it; only joining is restricted.
- **Nothing about a room's settings ejects anybody.** Locking a room, dropping
  somebody from the invited set, or the last moderator leaving all change who may
  come in *from now on*. The people already talking are left where they are.
- **Substrate:** a lounge is a `Stream` row with `is_lounge`, not a new table.
  Standalone in the product, not in the schema — this reuses Zulip's subscription
  and access control rather than rewriting it.
- **Rooms live in Zulip, not the conferencing service.** The two questions a room
  answers — may this user join, are they a moderator here — are answered where
  subscriptions are, and neither can afford a network hop at token-mint time.
- **Web-public voice channels and lounges are allowed, and guests may join their
  calls anonymously.** This reverses the earlier rule that "a call is for a known
  set of people". The channel administrator's web-public toggle is now the
  control: anyone who can see such a channel can be heard in it.
  `access_lounge_by_id` had a leftover `or stream.is_web_public` that rejected a
  web-public lounge outright, making it unusable even for its own subscribers;
  removed, and it now makes the same subscription exception a web-public voice
  channel does. Holding a logged-in reader to a stricter bar than an anonymous
  visitor would be incoherent.
- **"No moderator present" is a door policy, not a kill switch.** Once no
  moderator is in a call, nobody new may enter; the people already talking are
  never cut off. Ending a conversation somebody is having is a worse failure than
  letting it finish unattended.

## A room ends on `active: false`, never on `count == 0`

This one is worth its own heading because getting it wrong cost hours and the
symptom was baffling — rooms vanishing seconds after being started, with the user
appearing for a single frame first.

A room that has been minted and not yet entered is *active with nobody in it*. So
is the moment between the last person leaving and the next arriving. Deleting on
an empty roster kills a room before anyone walks through the door.

The conferencing service already documents the trap in `on_occupancy_change`
("occupancy is None, not count == 0"). Read it before touching this. Both
`zerver/views/jitsi_hook.py` and the client's `apply_pushed_occupancy` had the
bug; both are fixed and have regression tests.

## Three ways a room dies — all three are needed

1. **The service reports it inactive.** `jitsi_hook_occupancy` deletes the row.
2. **Started but never entered.** No such report is ever coming, so it is reaped
   on a timer (`LoungeRoom.UNJOINED_GRACE_SECONDS`, 120s).
3. **The service forgot it.** `reconcile_lounge_rooms` deletes rows missing from
   the service's live-room feed. Without this, a room whose end-report never
   arrives is stranded permanently — which happened twice in testing.

Rule 3 runs from `get_jitsi_occupancy_all`, on the **unfiltered** feed, and
**only when the fetch succeeded**. Treating a failed fetch as an empty feed would
delete every live room in the deployment at the moment the service is least able
to say otherwise.

The client had the mirror-image gap: the room list only changed on a pushed
event, so a missed push left a dead room in the sidebar forever.
`jitsi_sidebar.ingest` now calls `lounge_rooms.reconcile_live_rooms`, refetching
when the live set changes. `POLL_MS` is 2s in development, 15s in production.

---

## What is built

### Backend

- `Stream.is_lounge` (migration `0811`), mutually exclusive with
  `voice_video_enabled`, enforced on create and update.
- `LoungeRoom` (migration `0812`): channel, name, creator, `is_private`,
  `date_created`, `first_joined_at`.
- `LoungeRoom.knockable_by_users`, `.knockable_by_guests`, `.invited_users`;
  `Stream.require_moderator_to_join`; `Stream.can_create_rooms_group`
  (migrations `0813`–`0815`).
- Scope `realm:{r}|lounge:{s}|room:{id}` in `zerver/lib/jitsi_token.py`, mirrored
  into `conferencing/rooms.py`.
- `POST /json/lounges/<id>/rooms` (start), `GET /json/lounges/rooms` (list),
  `PATCH /json/lounges/rooms/<id>` (settings), `POST .../knock`, `POST .../admit`,
  and `create_jitsi_call` takes `lounge_room_id` (join). Starting and joining are
  separate on purpose: one join path, so there is only one thing to get wrong.
- All the join rules in one place, `zerver/lib/lounges.py`:
  `user_may_join_room`, `user_may_knock`, `user_may_start_room`,
  `user_moderates_room`, `channel_moderator_ids`, `room_moderator_ids`,
  `access_lounge_room_by_id`, `reconcile_lounge_rooms`,
  `reap_unjoined_lounge_rooms`.
- Web-public voice channels and lounges permitted throughout.
- `POST /json/calls/jitsi/create_as_guest`, reachable without logging in, for a
  web-public channel or a public room in a web-public lounge. `GET
  /json/lounges/rooms` and `GET /json/calls/jitsi/occupancy_all` are readable by
  visitors too, scoped to web-public, so there is something for that token to
  point at.
- `require_moderator_to_join` enforced at the mint, from occupant ids cached by
  the occupancy hook (`zerver/lib/call_presence.py`). Settable through
  `PATCH /json/streams/<id>`.

### Frontend

- Waveform-bars glyph; lounge rows expand in place; rooms render with occupants
  listed beneath, sharing the row builder voice channels use.
- Each room row carries a call button — **clicking the row does not join**.
  Joining takes over your microphone and announces you; it needs its own target.
- A **+ button on the lounge row** ("Create a room"), matching the new-topic plus.
- A **settings cog** on rooms you moderate: privacy, the two knock switches, and
  an invited-users pill box.
- A locked room you may ask about carries an **ask-to-join** control instead of
  the call button, and says so once asked. Whoever moderates the room sees the
  knocker appear beneath it, dimmed, with an admit control — at the door rather
  than in a modal, because the sidebar is already where you see who is around.
- `lounge_rooms.ts` (data, including live knocks) and `lounge_rooms_ui.ts`
  (dialogs, joining, knocking, admitting). Click handlers live in `ui_init.js`
  because `jitsi_sidebar` must not import the call code — there is an import
  cycle.
- `guest_call.ts`: a visitor is asked once what to call them, then joins through
  the guest endpoint. Call buttons stay visible to visitors on web-public
  channels and lounges, and only there.
- Channel settings gained a **"Who must be in a call for others to join"**
  dropdown (anarchy / an account holder / a moderator), shown for voice channels
  and lounges, and a **"Who can start a room in this lounge"** group-setting pill
  row, shown for lounges. Both follow the channel-kind dropdown live; the
  dropdown is offered only when editing, since creation does not send it.

### Conferencing service

`Call.lounge_room_id`; `active_call_for_stream` skips lounge rooms (otherwise the
first room started answers for the whole lounge); occupancy pushes carry the room
id.

### Dev environment

- `zulip-meet-conferencing/scripts/stub_conferencing_service.py` — stdlib-only
  stand-in with a control panel on :8080. It implements Prosody's four
  `event_sync` sinks, so with a local Jitsi the occupancy is **real**.
  Run it `--bind 0.0.0.0 --no-auto-join`. Its `--auto-join` default pretends
  whoever mints a token joined, which is what makes rooms appear to hold people
  who are not in them.
- `zulip-meet-conferencing/deploy/dev-jitsi/` — local docker-jitsi-meet, with a
  runbook. **HTTPS is mandatory** even locally: `external_api.js` hardcodes
  `https://${domain}` for the iframe and takes a bare host, so an http-only Jitsi
  cannot be embedded at all.

---

## A knock is a live request, not a record

Nothing about a knock is stored, on the server or in the client. It is delivered
as a `lounge_knock` event to whichever of the room's moderators are listening,
stands for two minutes, and is then over. An unanswered knock goes unanswered
rather than becoming a queue of stale requests to work through later, which is
the same rule the room itself follows.

Admitting somebody widens `invited_users`, so the ordinary join path is what
opens; there is no second answer to "may this user enter" to keep in step with
the first. `POST .../admit` is additive rather than a whole-set PATCH so two
moderators answering two knocks at once cannot undo each other.

**Visitors knock differently, and it took a second mechanism.** Being admitted as
an account holder means being added to a set of `UserProfile`s, which a visitor
cannot be in — so for a while this was documented as impossible. What was missing
is that being admitted does not have to mean *becoming a member of a set*; it only
has to mean *this one person may come through, now*.

So a visitor's knock is a short-lived capability (`zerver/lib/guest_knocks.py`):
an unguessable id, minted when they ask, marked admitted when a moderator says
yes, and **spent on the way in**. It is good for one room and one entry, and it
expires on its own — a moderator who says yes is saying yes to the person they
looked at, not to whoever that id gets passed to.

Two consequences worth knowing:

- **A visitor is asked for a name before they may knock.** A moderator deciding
  whether to admit "Guest" has been told nothing at all. The name is marked as a
  guest's once, when the knock is made, and that same string is what the room
  shows if they are let in — so a moderator cannot admit "Sam (guest)" and get
  somebody else's wording in the call. (The first version marked it twice and
  produced "Sam (guest) (guest)"; a test now pins it.)
- **The visitor polls for the answer** (`GET /calls/jitsi/knock_status`). They
  hold no Zulip account and so have no event queue to push to. The `lounge_knock`
  event carries `guest_knock_id` and `guest_name` instead of a `user_id`, which is
  how a client tells the two kinds of knocker apart.

`knockable_by_guests` governs both: it now genuinely means "unauthenticated
visitors may ask", which is what the model comment originally claimed and the code
did not yet do.

## Three doors, and presence is cached rather than stored

`Stream.call_door_policy` says who must already be in a call before anyone else
may come in. It replaced a `require_moderator_to_join` boolean, which could only
express two of the three useful answers (migration `0816`).

| policy | who holds the door open | refuses |
|---|---|---|
| `anarchy` | nobody — the default | nobody |
| `authenticated_user` | anybody with a Zulip account | anonymous visitors only |
| `moderator` | somebody who moderates the call | everyone else |

`authenticated_user` is the one aimed at web-public channels: visitors may join a
conversation members are having, and cannot hold one among themselves. It never
refuses an account holder, so its whole effect falls on visitors — and it needs
no id list to check against, because the roster is already only account holders
(the occupancy hook keeps integer user ids and drops the rest, so a visitor
cannot be mistaken for the doorman).

Whichever is chosen, **whoever holds the door open is never refused by it**, or a
channel could never have a first person in it.

The presence this needs is read at mint time. Decided in favour of tracking it in
Zulip rather than asking the conferencing service synchronously — a call people
are trying to get into is the wrong place to add a network round trip and a new
failure mode.

It lives in a **cache** (`zerver/lib/call_presence.py`), not a table: it is call
state, which is not what `Stream` or `LoungeRoom` hold, and a cache gets the
failure mode right for free. Only the raw occupant ids are stored, never a
verdict — occupancy reports arrive on every join and leave while a token is
minted rarely, so the "who counts as a moderator" queries belong at the mint.

**A moderator has to be positively visible.** Anything else — a call nobody has
joined, a call whose occupants include no moderator, a call we have no report for
— turns a non-moderator away.

This reverses the fail-open choice made when the feature was designed, and it was
testing that reversed it. "No report means we do not know, so let them in" sounds
like the cautious reading, but **a call nobody has started has no report either**,
so the first person into a channel with the setting on was always admitted,
moderator or not, and the setting did nothing whatsoever.

What makes failing closed safe is that **a moderator is never refused**. If a
report really has been lost under a live call, a moderator walking in re-reports
the roster and reopens the door for everyone. There is no state a non-moderator
can be stuck in that a moderator cannot clear by doing the obvious thing.

**The shut door is reported, not just enforced.** A room dict carries
`waiting_for_moderator` and the occupancy feed carries `closed_channel_ids`, both
computed server-side, so the sidebar withholds the way in rather than leaving
somebody to discover the rule by bouncing off it. Kept separate from `can_join`
on purpose: that one is shut *to you* and stays shut, this one is shut to
*everyone* until somebody arrives, and rendering them alike would put a padlock
on a public room and hide that waiting would help.

`closed_channel_ids` covers channels with no live call too — that is exactly when
a non-moderator cannot start one — and rides the failure paths of the occupancy
fetch as well, since which doors are shut does not depend on that fetch.

**A refused join now says why.** Zulip's default handling of a 400 on this path
is silence, which is the worst option available: the door being shut is a state
the user can act on, and a control that simply fails to respond teaches them only
that the button is broken.

**The room list refetches on roster changes, not just on rooms appearing and
disappearing.** `reconcile_live_rooms` used to key on the set of live rooms,
which misses the case this feature turns on: a moderator leaving a room that
still has people in it changes no room's existence, so the sidebar went on
offering a way in that the mint had already started refusing. The key now
includes which *members* are in each room.

Two things about this that look like bugs and are not:

- **The policy is per-channel and defaults to `anarchy`**, so a web-public lounge
  admits guests freely until somebody changes it. "A guest got in" is only a
  fault if that channel asked for a doorman — check the channel, not the code,
  first.
- **Guests carry no user id**, so they never appear in the roster the moderator
  check reads. A call containing nothing but guests therefore counts as having no
  moderator, and stays shut to further guests. That is the intended reading: an
  anonymous visitor cannot be the one holding a room open.

Enforced at the mint and nowhere else, which is what makes it a door policy: an
established session keeps its Prosody connection, and nothing reaches into a call
to end it. The client never re-mints mid-call, so this is genuinely only the door.

## What is not built

Nothing from the original list. Remaining known gaps:

- **Group-based tenanting and anonymous guests do not compose.**
  `resolve_guest_jitsi_tenant` gives a visitor the deployment default, because
  they are in no groups to map. On a deployment that sets
  `JITSI_TENANT_BY_GROUP` and puts a web-public channel's whole membership in a
  mapped group, the visitor lands in a tenant the members are not in. Fixing it
  means deriving the tenant from the channel rather than from who is joining,
  which changes how every existing call is routed.
- **A guest's knock lives in one browser tab.** The knock id is held in memory
  and nowhere else, so a visitor who reloads while waiting has to ask again. That
  is the same rule every other knock follows, but it is more visible here because
  a visitor may be waiting a while.
- **Occupancy for visitors is filtered to web-public channels**, and
  `reconcile_lounge_rooms` deliberately does not run on the anonymous path: it
  deletes rows, and an unauthenticated request should not be what causes that.
  Anybody logged in looking at the same deployment does it instead.

---

## Traps

- **A bad `$ref` in `zulip.yaml` poisons the whole document** and fails ~270
  unrelated tests with no mention of your field. If a run explodes across suites
  you did not touch, validate the YAML first.
- **Adding a channel group setting is a twelve-site slog**: model FK +
  `stream_permission_group_settings`, three migrations (add nullable, backfill,
  alter to non-null), `lib/types.py` (×5), `lib/streams.py` (StreamDict,
  `create_stream_if_needed`, `create_streams_if_needed`, `stream_to_dict`,
  the group-settings map), `lib/subscription_info.py` (×4 builders),
  `actions/streams.py` (×2), `lib/event_types.py`, and six spots in `zulip.yaml`.
  Miss one and the failure is a `test_events` "Mismatching states" diff that
  never names the field.
- **…and six more sites on the client, which no test reaches.** The server
  advertises the setting in `server_supported_permission_settings.stream`, and
  several places parse that list against hand-written enums:

  1. `group_permission_settings.stream_group_setting_name_schema`
  2. `settings_config.stream_group_permission_settings`
  3. `settings_config.all_group_setting_labels.stream` (a label, or the panel
     renders a blank one)
  4. `stream_types.stream_permission_group_settings_schema`
  5. `stream_settings_components.new_stream_group_setting_widget_map`
  6. `settings_components.group_setting_widget_map`

  …plus a pill row in `channel_permissions.hbs`, or the setting exists and cannot
  be set. Miss 1–3 and the **whole web app fails to boot**; miss 4–6 and the
  channel-creation and channel-settings pages throw when opened. `ZodError` names
  an array index or nothing at all, never the field. `can_create_rooms_group` hit
  every one of these. **`test-backend` and `test-js-with-node` are both blind to
  all of it** — neither boots the app — so the only way to find these is to open
  the page: the app, then create-channel, then channel settings.
- **Query-count assertions in `test_subs.py`** shift when you add a setting.
  Check the actual number rather than assuming +1 — threading the setting
  explicitly through creation can remove the extra query again.
- **Docker creates missing bind mounts as root**, after which every Jitsi
  container refuses to start. Fix without sudo by borrowing Docker's root:
  `docker run --rm -v ~/.jitsi-meet-cfg:/cfg alpine:3 chown -R 1000:1000 /cfg`.
- **A token claim only means something if the stack was told to read it, and told
  not to override it.** Setting `AUTH_TYPE=jwt` gets the signature verified and
  nothing else. Five settings, all missing on first setup, and the symptoms all
  read as application bugs:
  `XMPP_MUC_MODULES=token_affiliation` (the module that reads
  `context.user.moderator` ships in the image but is not enabled),
  `ENABLE_AUTO_OWNER=false`, **`JICOFO_ENABLE_AUTH=0`**, `DISABLE_PROFILE=true`
  and `HIDE_PREJOIN_DISPLAY_NAME=true`. See `deploy/dev-jitsi/README.md`.

  The third is the one that cost the most. **Jicofo has a second, independent
  path to granting ownership** — its own authentication, nothing to do with
  auto-owner — and it defaults to on whenever `ENABLE_AUTH` is. Under
  `AUTH_TYPE=jwt` it grants ownership to *every holder of a valid token*, and an
  anonymous guest's token is valid by construction. The guest joined as
  PARTICIPANT and was promoted a second later, which is why the Jicofo join line
  looked correct and the behaviour was not.
- **Affiliation is not role, and only role is checked.** `token_affiliation` logs
  `set affiliation=member`, which proves nothing about what the occupant can do:
  `mod_end_conference` gates on `occupant.role == 'moderator'`. A correct-looking
  affiliation log next to a guest ending everyone's call is not a contradiction —
  it is the two being different fields.
- **Migrations are not applied by running tests.** The test runner builds its own
  database, so a fully green suite says nothing about the dev server. Run
  `./manage.py migrate zerver`.
- **A JSON-encoded list parameter needs an `encoding:` block in `zulip.yaml`**,
  or `validate_test_request` fails with "Failed to cast value to integer type:
  [8]" and no hint about what to add:

  ```yaml
  encoding:
    invited_user_ids:
      contentType: application/json
  ```

- **`./tools/lint` only lints git-tracked files**, so every new file in this
  feature was invisible to it until added. Run `eslint`/`prettier`/`ruff` on new
  paths directly before believing a clean lint run.
- **A `Stream` field is not settable just because it is plumbed.**
  `require_moderator_to_join` reached the client, the events system and
  `stream_to_dict`, but `update_stream_backend` never accepted it and no UI set
  it — so the setting could not be turned on at all. Check the view's parameter
  list, not just the model.
- **`hidden-for-spectators` is applied per control, so a new one starts hidden.**
  The join button was given a web-public exception when guest access landed; the
  ask-to-join button was not, so visitors could be told `can_knock: true` by the
  server and still see nothing. The server's `can_join`/`can_knock` already
  account for who is asking, so the class is now derived once
  (`spectator_suffix`) rather than written at each control — writing it twice is
  exactly how the two came apart.
- **A `<select>` rendered by `dropdown_options_widget` has no selected option.**
  The current value is put in afterwards, by hand, next to
  `$("#id_topics_policy").val(...)` in `stream_edit.ts`. Miss it and the control
  opens showing the *first* option, which then silently saves if anything else in
  the subsection is touched. The property name itself comes from the element
  **id** (`id_call_door_policy` → `call_door_policy`), not from its `name`.
- **The creation form renders `channel_permissions.hbs` too**, so a control added
  there appears while creating a channel — and does nothing, because
  `create_streams_backend` does not accept it. Wrap it in `{{#if is_stream_edit}}`
  unless you also plumb creation.
- **A mint notifies the conferencing service, which pushes occupancy straight
  back.** With the stub that resets the roster to empty, so a script that mints
  twice in a row finds its second check reading a roster its own first call
  wiped. Re-report occupancy before each mint when testing the door by hand; a
  confusing "a member was refused while a moderator was present" was this and
  not a bug.

## Known pre-existing failures on `web-push-pwa`

Not caused by this work; verified by stashing.

- `web/src/compose_call_ui.ts:255` — tsc error under `exactOptionalPropertyTypes`.
- `zerver.tests.test_events.RealmPropertyActionTest.test_change_realm_property`.
- ruff wants to reformat `zproject/dev_settings.py` (a local edit).

Further lint failures on the branch, in code this work did not touch, found by
running `./tools/lint` for the first time:

- ruff `I001` (unsorted imports) in `zerver/lib/events.py`.
- ruff-format wants to reformat `zerver/lib/streams.py`,
  `zerver/lib/subscription_info.py`, `zerver/tests/test_pwa.py`, three
  `zerver/migrations/080*` files, and several `docs/`/`api_docs/` code blocks.
- eslint: import order in `web/src/compose_call_ui.ts` and
  `web/src/message_view.ts`; `URL#href` in `compose_call_ui.ts`.
- stylelint: 24 hex-colour/notation errors in `web/styles/embedded_call.css`.
- Zulip's own `py` linter: `assert_length` in `zerver/tests/test_jitsi_jwt.py:54`,
  and two `Stream.objects.get` calls in `zerver/views/jitsi_hook.py` (deliberate —
  it is an internal secret-authed hook with no acting user).
