# Upstreaming

Consort is two forks: `zulip/zulip` (the server and web app) and
`zulip/zulip-desktop`. Some of what has been built here is a bug fix or a
feature upstream would plausibly want; most of it is a product direction they
would not. This is the sorting.

| | fork point | ours |
|---|---|---|
| `zulip/zulip` | `f6470bb`, 2026-07-31 | 60 commits · 108 files · +10,738 −150 |
| `zulip/zulip-desktop` | `228ac2b`, 2026-06-26 | 13 commits · 61 files · +1,531 −558 |

## Before any of it

Neither fork has an upstream remote — `origin` is our own fork in both. Step
zero for each:

```bash
git remote add upstream https://github.com/zulip/zulip.git && git fetch upstream
```

Every candidate below is cut against a fork point that is weeks old, so each
branch is `git rebase --onto upstream/main <fork-point>` before it is a pull
request. Rebase per candidate, not once for everything: the point of splitting
them up is that they land independently.

Zulip's contribution bar is high and specific — one logical change per commit,
an imperative subject under 72 characters, a body explaining *why*, tests, and
API documentation for anything that changes an endpoint. `zulip-server-patch/`
in this repository is an example of the shape they want, because it was written
to that standard.

## Candidates, in the order worth offering them

Ordered so that the smallest and most obviously-correct go first. Landing one
builds the credibility that the larger ones need.

### 1. Authenticated Jitsi calls scoped to a conversation — server

**Commits** `013f389`, `4d9faf7` · 11 files · +856 −1 · **no dependencies**

Zulip's video-call button generates a room name with `Math.random()` and applies
no access check, so any member of an organization can mint a link to any room.
This replaces that with a server-side membership check, a room name derived as
`HMAC(key, scope‖epoch)`, and a short-lived JWT scoped to exactly that room and
tenant.

The strongest candidate by a distance: it is a real gap, the fix is
self-contained, and it is already packaged with tests, API documentation and a
PR description in [`zulip-server-patch/`](../zulip-server-patch/).

Fold in `e0448ed208` ("make test_jitsi_jwt actually run") — it fixes a test in
this same code that never executed.

### 2. Screen share picker — desktop

**Commit** `8624d98` · 6 files · +343 −1 · **no dependencies**

Electron gives a web app no way to pick a screen or window without the host
supplying a handler, so screen sharing in a call simply does not work in Zulip
Desktop. This adds the picker.

Small, self-contained, and fixes something plainly broken rather than proposing
a direction. Best first offer to the desktop repository.

### 3. Camera and microphone permission prompt — desktop

**Commit** `7beee80` · 10 files · +434 −9 · **conflicts textually with #2**

Asks before a call uses the camera or microphone, and remembers the answer per
server. Both this and #2 touch `typed-ipc.ts`, `main/index.ts` and
`renderer/css/main.css`, so they need sequencing — offer one, land it, rebase
the other.

### 4. Web Push and an installable PWA — server

**Commits** `8c1e2af680` plus fourteen follow-ups · **no dependencies on the call work**

Web Push notifications with no FCM, no APNs and no bouncer, plus the manifest
and service worker that make the web app installable. Upstream's push story
currently routes through their bouncer, so this is the candidate most likely to
meet architectural opinions — but also the one a self-hosted deployment most
wants.

**Not offerable as-is.** Fifteen commits of which fourteen are "fix the thing
the first one added" is not a reviewable series; it needs collapsing into a
handful of commits that each do one thing: the model and migration, the sender,
the service worker, the permission UI. Budget real time for that rewrite, and
expect to open a discussion before the pull request.

### 5. Embedded call panel — server

**Commits** `d23d4ff9fb`, `8e2d6f23ec`, `6674fbd621`, `3b2a1e22d7`, `d7e19a884d`,
`7c4a41e695`, and the panel half of `b6a90848be` · **easier after #1**

The call opens in a draggable, minimisable panel inside the app rather than a
new browser tab, and survives switching channels. It does not need any of
Consort's external machinery.

This is a UX opinion rather than a bug fix, so it is worth raising as an issue
before writing the pull request. It reads better on top of #1, but can be
adapted to upstream's existing link generation if #1 stalls.

### 6. Per-channel voice and video setting — server

**Commits** `63a6d95238`, `e400f905a2`, and the `voice_video_enabled` half of
`d3f09ecdf9` · **depends on #1 landing to be meaningful**

A per-channel boolean for whether calls are allowed, plumbed through the API.
Defensible on its own terms — an administrator wanting calls off in an
announcement channel — provided it is offered as exactly that, and not as the
first step of the voice-channel work below.

## Staying here

Not because it is bad, but because it is Consort rather than Zulip.

| | why |
|---|---|
| Conferencing service hooks (`d84978ca12`, `5804e8694d`, `5b7110acfb`, `9a89384086`, `aab5289c08`) | Require an external service upstream does not run |
| Call-aware sidebar occupancy (~12 commits) | Same — the occupancy data has no source upstream |
| Voice channels as a channel type (`2273b553df`, `a9a893badb`, `d2343ae855`, `a22403a821`) | A product direction, not a gap |
| Lounges (`6cb28fee97`, `870415221a`, `cb5f5488c4`) | Same |
| Single-threaded channels (`b02b2c2b37`, `828fe29c7d`) | Same |
| Branding, both repositories | Ours by definition |
| Desktop packaging, release and update removal | Ours by definition |

## Sequencing

```
  #1 authenticated calls ──┬──▶ #5 embedded panel
                           └──▶ #6 per-channel setting
  #2 screen share ─────────▶ #3 permissions      (textual conflict, not logical)
  #4 web push               (independent, needs a rewrite first)
```

Offer #1 and #2 now: they are ready, small, and fix things rather than propose
things. #4 is the most valuable and the most work. #5 and #6 are worth an issue
before a pull request.
