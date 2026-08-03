# Zulip Meet

**Conversation-scoped Jitsi video calls in Zulip.** The channel or direct message you are in
decides which Jitsi room you can join — enforced end to end by short-lived, room-scoped tokens —
with the call embedded and minimizable *inside* Zulip and a live roster message posted into the
conversation.

Zulip ships a video-call button that opens a Jitsi room with a `Math.random()` name and no access
check: any member of the organization can mint a link to any room. That is harmless while the room
is unauthenticated anyway. This project replaces it with a real entitlement model and builds a
proper in-app calling experience on top.

## Demo

▶ **[Watch a walkthrough](docs/demo.mp4)** — starting a call from a conversation, the call embedded
in Zulip, and minimize / maximize / drag-resize.

<!-- To show it as an inline player at the top of this README on GitHub: drag docs/demo.mp4 into a
     new issue or release comment to get a https://github.com/user-attachments/assets/… URL, then
     paste that URL on its own line here. (GitHub renders an uploaded-attachment video inline; a
     plain repo-relative path only renders as a link.) -->

## What it does

- **The conversation is the room.** The server derives an unguessable room name from the
  channel/DM (`HMAC(key, scope‖epoch)`), mints a JWT scoped to exactly that room and tenant, and
  Prosody enforces it. You can only join the Jitsi room for a conversation you actually belong to,
  and the membership check happens on the server — not the client.
- **The call lives in Zulip.** Clicking call opens Jitsi in an embedded, minimizable panel (mount,
  minimize to a status bar, maximize, drag-resize) that survives switching channels — not a new tab.
- **A live roster in the conversation.** "📹 Call in progress — Ada, Bob" is posted into the
  channel (or the real DM/group) and edited as people join and leave, ending when the room tears
  down. It works across organizations and in DMs — things a bot could not do — because posting goes
  through a server-internal hook rather than a bot account.
- **Moderator from Zulip roles.** Mute-all / kick is granted only to a channel's admins, carried in
  the token and enforced in Jitsi's Prosody.
- **Rooms rotate.** Bumping an epoch gives a clean "start a fresh meeting" primitive and a recovery
  path if a link ever leaks; rotating the room key rekeys everything at once.

## How it fits together

```
  ┌── click "call" in a channel / DM
  ▼
Zulip server (patched)  ──mint room-scoped JWT──▶  browser ──join──▶  Jitsi / Prosody
  │  membership check                                                    │  validates room+tenant,
  │  derive room, mint token                                             │  grants moderator from token
  │                                                                      │
  │  ◀─────────── occupancy (event_sync) ──────────────────────────────┘
  ▼
core-hook  ◀── post / edit roster message ──  zulip-meet-conferencing  (state · render · timing)
```

1. Click call → `POST /calls/jitsi/create`: Zulip verifies you belong to the conversation, derives
   the room, and mints a short-lived JWT scoped to that room and tenant.
2. The embedded client joins the Jitsi room with the token; Prosody validates room + tenant and sets
   moderator from the token's claim.
3. Prosody's `event_sync` streams occupancy to **zulip-meet-conferencing**, which owns call state,
   renders the roster, and posts/edits the message into the conversation through Zulip's internal
   **core hook** (channels as a system bot; DMs/groups authored as the initiator, any org).

## Components

| Directory | What it is |
|---|---|
| [`zulip-meet-conferencing/`](zulip-meet-conferencing/) | The external service: occupancy, call state, roster rendering, posting via the hook. Its own repo, fully tested. |
| [`zulip-server-patch/`](zulip-server-patch/) | The Zulip server changes: the room-derivation + JWT mint, the membership-checked `calls/jitsi/create` endpoint, the internal core-hook endpoints, and the occupancy widget. |
| [`embedded-call/`](embedded-call/) | The in-Zulip embedded call: a `JitsiMeetExternalAPI` iframe mounted at the app root, minimize/maximize/drag-resize, plus the CSP notes. |
| [`jitsi-token-harness/`](jitsi-token-harness/) | A standalone harness that *proves* the isolation — a token for one room or tenant is refused at another — against a real Jitsi/Prosody stack. |
| [`docs/`](docs/) | Architecture (rev4), the Prosody `event_sync` runbook, and deployment notes. |

Prosody-side plugins used: `event_sync` (occupancy → the service), `muc_census` (reconciliation),
`token_affiliation_legacy` + `disable_cascading_set` (moderator strictly from the token), and Jitsi's
own token verification. See `zulip-meet-conferencing/deploy/prosody/` and `docs/`.

## Requirements

- A Zulip server built from [the fork carrying the patch](https://github.com/Dyslectric/zulip-meet-integration/tree/jitsi-jwt) (`zulip-server-patch/`).
- A token-authenticated Jitsi (docker-jitsi-meet) with JWT auth and the custom Prosody plugins.
- The conferencing service, reachable from both Zulip and Prosody on an internal network.

This is a working deployment, not a proof of concept — it runs in production. Deployment specifics
(networking, secrets, the custom images) are in `docs/` and each component's README.

## Status

Live: conversation-scoped calls, the embedded minimizable UI, channel and DM/group roster messages,
moderator-from-Zulip-roles, and the occupancy widget. Not built yet: the direct-message *ringing*
flow, and native mobile (a Flutter client is planned).

## License

[Apache-2.0](LICENSE). Zulip and Jitsi are trademarks of their respective owners; this project is an
independent integration and is not affiliated with either.
