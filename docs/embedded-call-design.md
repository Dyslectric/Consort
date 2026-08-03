# Embedded calls, core-hook messaging, and moderator mapping — design

Status: **design (2026-08-02).** A new architectural + UX direction, decided after Phase 3
(bot-posted roster messages) shipped and worked live. This supersedes two earlier ideas: the
"voice-channel type" concept (dropped — no Zulip model change) and the bot-based message posting
(evolving into a core hook). It does **not** change what already works; it plans what comes next.

Design of record it builds on: `architecture.md`, `prosody-event-sync.md`.

---

## Decisions recorded

- **No voice-channel type.** Channels stay plain channels. Every channel, DM, and group chat keeps
  the navbar call button + occupancy widget (single-person indicator for a 1:1 DM). No create-channel
  dropdown, no speaker icon, no settings-axing. This was considered and deliberately dropped.
- **Embedded, minimizable call view.** Clicking call opens Jitsi *inside* Zulip; it minimizes to a
  **compact status bar** so the user keeps chatting/navigating during the call.
- **Message send/edit moves to a core hook** (Zulip internal functions in the fork), not the external
  bot — this is what makes DM/group and multi-realm possible at all.
- **Moderator mapping:** channels → `can_administer_channel_group` (as today); **DM/group → everyone
  is moderator (flat)**.
- **Target web first, plan for mobile** (Flutter).

---

## 1. Message delivery: bot → core hook (the enabling change)

### Why the bot can't do DM/group or multi-realm
The external conferencing bot posts through Zulip's **public API**, which always sends **as the bot
user**. Two hard walls follow:
- A Zulip DM/group is defined by its participant set, so a bot message lands in a *bot-inclusive*
  conversation, never the users' real DM/group.
- A bot belongs to one realm; cross-realm posting needs a bot per realm and per-realm credentials.

### The hook
Move send/edit into **secret-authed internal endpoints in the fork** that call Zulip's internal
message functions with server privileges:
- **Channels (any realm):** `internal_send_stream_message(sender=<system "Calls" bot>, stream, topic,
  content)` — no subscription required, works in every realm.
- **DM/group:** author **as the initiator** — `internal_send_private_message` /
  `internal_send_group_direct_message(sender=initiator, recipients, content)` — so the message appears
  in the *real* conversation. This is the capability the bot API simply does not have.
- **Edits (occupancy/end):** `do_update_message(...)` invoked internally on the stored `message_id`,
  regardless of author.

The conferencing service stays the brain — it owns occupancy, renders bodies (`render.py`), and
decides when to post/edit — but instead of a bot API key it calls these endpoints and stores the
returned `message_id`. Net effects: **no bot user, no subscription requirement, DM/group works,
multi-realm works.**

### Topology
```
Prosody event_sync ─► conferencing service (occupancy, render, timing)
                              │  secret-authed, proxy-bypassed (smokescreen), internal-network only
                              ▼
        Zulip fork internal hook:  send / update  (internal_send_*, do_update_message)
          channels → system Calls bot;  DM/group → initiator;  any realm
```
The Phase-3 occupancy **widget** path (Zulip view → service `GET /occupancy`) is unchanged.

### Security
These endpoints post/edit as **arbitrary senders in arbitrary conversations** — the most privileged
surface in the whole system. Requirements: shared-secret auth (constant-time), **never** on the public
route table, reachable only on the internal network, and the proxy bypass (`proxies={}`) that Phase 3
already needed for smokescreen.

### Authorship — DECIDED
- **Channels:** a system "Calls" bot (conventional, like Notification Bot; no membership issue).
- **DM/group:** the **initiator** — the user who started the call (minted the first token; "first in
  the call"). The message lands in the *real* conversation, attributed to them ("David started a
  call"). This is available at mint time in `create_jitsi_call` (the `user`), so the initial post can
  happen inline there (authored as the initiator, `message_id` handed to the service); occupancy edits
  then go service → the internal update endpoint.

---

## 2. Embedded minimizable call — web

Today the button does `window.open(url)`. Replace with Jitsi's **`JitsiMeetExternalAPI`** iframe:
```js
new JitsiMeetExternalAPI("meet.zulip.davig01.net", {
    roomName: `${tenant}/${room}`, jwt, parentNode: callContainer,
});
```

Rules that make "minimize while chatting" actually work:
- **The call container lives at the app root**, not in the message view. Zulip is a SPA; an iframe
  inside the narrow gets unmounted on channel switch, which **drops the call**. It must be a
  persistent, top-level element that survives narrow changes.
- **Minimize = CSS resize/reposition, never DOM removal.** Full view ↔ a **compact status bar**
  ("In call · #channel · mute · leave · restore"). Unmounting the iframe leaves the call.
- **Single active call** (v1): starting a call elsewhere prompts to leave the current one.
- **In-call roster is free** from the External API (`participantJoined/Left`) — the status bar doesn't
  need the service for its own occupancy.
- **CSP:** Zulip's Content-Security-Policy must allow the Jitsi origin in `frame-src` / `script-src`
  (for `external_api.js`) / `connect-src`. The fork widens Zulip's CSP settings. Consider self-hosting
  `external_api.js` (version-pinned) to avoid a cross-origin `script-src`.

---

## 3. Moderator mapping

The token carries `context.user.moderator`; Prosody's `token_affiliation` turns it into moderator
powers. Two work items:
- **Set it right in the mint** (`create_jitsi_call`): channels → realm admin **or**
  `can_administer_channel_group`; **DM/group → `is_moderator = True` for everyone** (flat). Each
  participant mints their own token, so each sets their own flag — flat means every mint sets `True`.
- **Verify the powers end to end** — that a moderator actually gets mute-all / kick / lobby controls
  in the call. Phase 1 proved token *isolation*, never moderator *authority*.

---

## 4. Mobile (Flutter) — plan, not v1

Zulip mobile is `zulip-flutter`. The embedded call there = the **Jitsi Meet Flutter SDK** (or a
webview + External API), launched by the same navbar button with the same token. **Minimize-while-
chatting is materially harder on mobile** — native picture-in-picture or a Flutter overlay, a separate
fork from the web client. Phasing: web gets the full minimizable experience first; mobile follows by
first launching the call full-screen, with PiP as its own later milestone. The **core-hook messaging**
and **moderator** work are platform-agnostic and benefit mobile for free.

---

## 5. Phasing

1. **Core-hook messaging.** Add the internal send/edit endpoints in the fork; point the service at
   them; drop the bot posting. Unlocks DM/group + multi-realm messages. (Backend, testable.)
2. **Embedded call, no minimize.** Swap `window.open` for the app-root iframe + leave button + CSP.
3. **Minimize/restore** compact status bar, persistent across narrows.
4. **Moderator** flat DM/group mint change + power verification.
5. **DM/group occupancy widget** — extend the widget/endpoint to answer by DM scope (user_ids), not
   just `stream_id`; single-person indicator for 1:1.
6. **Mobile** — Flutter SDK launch, then PiP.

---

## Open items
- Whether the conferencing service still needs a bot at all once posting is a hook (the event-queue
  loop for reconcile/private-call may still want one, or that too could become internal).
- CSP: self-host `external_api.js` vs. allow the cross-origin script.
- Single vs. multiple concurrent embedded calls (v1 assumes single).

*(Resolved: DM/group message authorship = the initiator — see §1.)*
