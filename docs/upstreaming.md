# Upstreaming Consort

What it would take to offer the pieces of this project to the upstream **Zulip** and
**Jitsi** projects — component by component, with the honest triage of what is actually
upstreamable, what the receiving project already has, and the concrete gates each maintainer
group will hold you to.

> Status: **draft**. The per-project contribution mechanics (CLA, review stages) are being
> confirmed against the live contributing docs and will be tightened; the engineering triage
> below is grounded in `zulip-server-patch/`, `docs/architecture.md` (rev 4), and the component
> layout as built.
>
> §0–§6 were written when the calls patch was nearly all of the fork. It has grown a great deal
> since — Web Push, a call-aware sidebar, voice channels, lounges — and a **second** fork of
> `zulip/zulip-desktop` now exists too. §1.1 and §1.2 map that as it stands today; the triage,
> gates and process below still hold.

---

## 0. The one-paragraph version

Only **one** of this project's five components is a real upstream contribution: the **Zulip
calls patch** (`zulip-server-patch/`), which answers an open Zulip issue with maintainer
interest ([zulip#28657](https://github.com/zulip/zulip/issues/28657)) and is already written as
two review-ready commits. On the **Jitsi** side there is almost nothing to upstream — every
Prosody module the design relies on already ships in Jitsi or in `jitsi-contrib`; the design is
*configuration and token shape*, not new Jitsi code. The **embedded-call UI** is a fork surface
(and may be mooted by Zulip's own native-calling work), the **conferencing service** is an
external process that stays its own project by design, and the **Authentik identity layer** is
deployment configuration with no code to give anyone. So this document is mostly about doing the
Zulip PR *properly*, and being honest about the fact that the Jitsi contribution is documentation,
not a plugin.

---

## 1. Component triage

| Component | Upstream home | Verdict | Why |
|---|---|---|---|
| `zulip-server-patch/` — `POST /calls/jitsi/create` + compose change | **zulip/zulip** | **Upstream this.** | Fills an obvious gap in an existing four-provider pattern; answers open issue #28657; already written to Zulip's conventions. |
| `jitsi-token-harness/prosody-plugins-custom/` | jitsi-contrib | **Nothing new.** | The only file, `mod_token_no_wildcard.lua`, is a vendored copy of an existing `jitsi-contrib` module, not original work. |
| Prosody config / token schema (`docs/architecture.md`) | jitsi-meet handbook | **Docs, maybe.** | Multi-tenant token verification is thinly documented upstream; a handbook PR is the realistic contribution. No code. |
| `embedded-call/` — in-Zulip minimizable iframe | zulip/zulip (web) | **Probably not.** | Large frontend surface Zulip may replace with native calling (#28505); offer only if maintainers want it, and not before the calls patch lands. |
| `consort-conferencing/` — occupancy + roster service | — (its own project) | **Not upstreamable.** | Deliberately an external service; belongs outside the chat server. Publish it standalone; don't try to merge it into Zulip. |
| Authentik SSO / enrollment | — | **Nothing to upstream.** | Zulip already supports SAML natively; this is settings, not code. |

The rest of the document expands the two rows that have any upstream action: the Zulip calls
patch (the real one) and the Jitsi documentation (the honest one), then records why the others
stay where they are.

### 1.1 The server fork, commit by commit

Neither fork has an upstream remote — `origin` is our own fork in both. Step zero for each:

```bash
git remote add upstream https://github.com/zulip/zulip.git && git fetch upstream
```

| | fork point | ours |
|---|---|---|
| `zulip/zulip` (`~/zulip`, `main`) | `f6470bb`, 2026-07-31 | 60 commits · 108 files · +10,738 −150 |
| `zulip/zulip-desktop` (`Consort-Desktop`, `main`) | `228ac2b`, 2026-06-26 | 13 commits · 61 files · +1,531 −558 |

Every candidate is cut against a fork point that is weeks old, so each branch is
`git rebase --onto upstream/main <fork-point>` before it is a pull request. Rebase per
candidate, not once for everything — the point of splitting them is that they land
independently.

**Candidate A — authenticated Jitsi calls.** `013f389`, `4d9faf7`; 11 files, +856. This is §2,
already packaged in `zulip-server-patch/`. Fold in `e0448ed208` ("make test_jitsi_jwt actually
run"), which fixes the test file §2.2 flags as never having executed — that gate is now
closeable rather than open.

**Candidate B — Web Push and an installable PWA.** `8c1e2af680` plus fourteen follow-ups. No
dependency on the call work. The most valuable thing here for a self-hosted deployment, since
upstream's push story routes through their bouncer — and therefore the one most likely to meet
architectural opinions. **Not offerable as-is:** fifteen commits of which fourteen repair the
first is not a reviewable series. It needs collapsing into a handful that each do one thing —
the model and migration, the sender, the service worker, the permission UI — and a design
thread on CZO before the pull request, for the same reason the tenancy model needs one.

**Candidate C — the embedded call panel.** `d23d4ff9fb`, `8e2d6f23ec`, `6674fbd621`,
`3b2a1e22d7`, `d7e19a884d`, `7c4a41e695`, and the panel half of `b6a90848be`. §4 held this back
as "unproven"; it is now shipped and exercised daily, so that objection has expired. The other
two stand: it is a large frontend surface, and #28505 may moot it. Reads better on top of
Candidate A but does not require it.

**Candidate D — per-channel voice and video setting.** `63a6d95238`, `e400f905a2`, and the
`voice_video_enabled` half of `d3f09ecdf9`. A per-channel boolean plumbed through the API,
defensible on its own terms — an administrator wanting calls off in an announcement channel —
*provided* it is offered as exactly that, and not as the first step of the voice-channel work.

### 1.2 The desktop fork

Two of its thirteen commits are features rather than fork identity, and both fix something
plainly broken rather than proposing a direction. They are the easiest offers in this document.

**Candidate E — screen share picker.** `8624d98`; 6 files, +343. Electron gives a web app no way
to choose a screen or window unless the host supplies a handler, so screen sharing in a call
simply does not work in Zulip Desktop. This adds the picker.

**Candidate F — camera and microphone permission prompt.** `7beee80`; 10 files, +434. Asks
before a call uses the camera or microphone, remembered per server. Touches the same three files
as E, so the two conflict textually though not logically: offer one, land it, rebase the other.

---

## 2. Zulip track — the calls patch

This is the contribution. It is already written as two commits against `zulip/zulip` `main`
(backend + tests + API docs; frontend separately), with a drafted PR body in
[`zulip-server-patch/PR-description.md`](../zulip-server-patch/PR-description.md). The work
remaining is **not** more code — it is running the gates, closing the honestly-recorded
verification gaps, and navigating Zulip's process.

### 2.1 What the patch is

- `POST /calls/jitsi/create`: a server-brokered endpoint following the established pattern of
  the four existing providers (BigBlueButton, Webex, Nextcloud Talk, Constructor Groups), with
  **one deliberate difference — it performs an authorization check**. Channel subscription (or
  DM reachability) is the entitlement.
- Room names derived by `HMAC-SHA256(JITSI_ROOM_KEY, scope‖epoch)` instead of `Math.random()`,
  with a signed, scope-bound epoch (the same `Signer` trick `get_bigbluebutton_url` already
  uses) so it round-trips through an untrusted client.
- A short-lived (2-minute), single-room, tenant-scoped JWT that real Prosody has accepted and
  correctly refused cross-tenant and cross-room.
- Zero behaviour change for deployments that haven't set `JITSI_JWT_*`
  (`server_jitsi_jwt_enabled` is false; clients keep generating room names locally).

### 2.2 Gates to clear before opening the PR

These are the items the PR draft honestly marks as not-done. They are the real remaining work.

- [ ] **`./tools/test-backend zerver.tests.test_jitsi_jwt`** passes. The test file is written to
      convention but **has never executed** — Zulip's suite needs its full dev env (postgres,
      redis, rabbitmq). Treat every assertion as unproven until this is green.
- [ ] **`./tools/test-backend --coverage …`** confirms the new lines in `jitsi_token.py` and
      `create_jitsi_call` are covered.
- [ ] **`./tools/lint`** passes — this runs **mypy and the TypeScript checker**; the type
      annotations have not been machine-checked.
- [ ] **`./tools/test-js-with-node`** passes for the compose-side change.
- [ ] **Web build + manual UI verification** actually done: click-to-call in a channel, themes
      (light/dark), window sizes, i18n/string lengths, keyboard nav. None of this has been run.
- [ ] **Screenshots / screen capture** for the PR (the compose button is unchanged; the only
      visible difference is the inserted URL — capture it anyway, they ask for it).
- [ ] **Real API feature level** substituted for the `ZF-bdd936` placeholder in the OpenAPI
      changelog (`api_docs/unmerged.d/…` plus the matching **Changes** note), via
      `tools/create-api-changelog`.
- [ ] **Rebase** onto current `main` (patch was cut against `f6470bb`) and re-verify `git am`
      applies clean.

### 2.3 Process mechanics (Zulip)

Zulip's review process is deliberate and multi-stage; a large-ish feature is not a
throw-it-over-the-wall PR. The order that avoids wasted work:

1. **Signal on the issue first.** Comment on
   [#28657](https://github.com/zulip/zulip/issues/28657) that you have a working implementation
   and intend to PR, to avoid colliding with anyone else and to surface maintainer preferences
   *before* review. The issue is open with maintainer interest and no PR — good, but confirm no
   one has started since.
2. **Design discussion on chat.zulip.org (CZO).** Non-trivial features are expected to be
   discussed in the development community stream before or alongside the PR. The tenancy model
   (§2.4) is exactly the kind of thing to raise there rather than decide silently in a diff.
3. **CLA.** Zulip requires a signed Contributor License Agreement; their bot enforces it on the
   PR. Sign it before or at PR time. *(Confirming the exact current mechanism.)*
4. **AI-use policy.** Zulip has an explicit AI-use policy for contributions
   ([contributing docs](https://zulip.readthedocs.io/en/latest/contributing/contributing.html#ai-use-policy-and-guidelines)).
   Given how much of this was AI-assisted, this is a real gate: you must fully understand and
   stand behind every line, and disclose per their policy. The draft PR title even carries an
   `[ai]` marker. **Read the policy and be able to defend the code as your own understanding —
   not "the model wrote it".**
5. **Commit discipline.** One coherent idea per commit, messages explaining reasoning
   ([commit-discipline](https://zulip.readthedocs.io/en/latest/contributing/commit-discipline.html)).
   The two-commit split already respects this.
6. **Self-review + submit**, then expect **multiple review rounds**. Be responsive; Zulip values
   contributors who iterate.

### 2.4 Design decisions maintainers will push on

These are already surfaced honestly in the PR draft's "Decisions I would like input on" — carry
them into the CZO discussion so they're settled *before* review friction, not during it:

- **Tenancy (`JITSI_TENANT_BY_GROUP`).** Resolving a Jitsi tenant from Zulip user-group
  membership may read as "more policy than belongs in core." **Mitigation already built in:** the
  setting degrades cleanly to a single default tenant / realm subdomain, and the patch still
  stands as plain "authenticate Jitsi calls." Be ready to drop it on request.
- **Subscription as the entitlement.** A user who can *read* a public channel without being
  subscribed is refused a token. Deliberate, but a product decision — the content-access
  alternative is a one-line change. Let maintainers choose.
- **Moderator mapping.** From `can_administer_channel_group` / realm admin; DM calls give nobody
  moderator (Jitsi's first-joiner-owner applies). Agree the policy rather than guess it.
- **Rooms must exist before join (Jicofo).** `token_util:verify_room` returns
  `room-does-not-exist` before reading any claim, and every epoch rotation makes a brand-new
  room. This is the part **least verified** (no test against a Jicofo-present deployment). Expect
  a maintainer to ask about it; have the rotation-under-Jicofo test done before you do.

### 2.5 Honest risk on the Zulip PR

The review pipeline is long and the appetite for the tenancy model may be lower than for plain
JWT support. The patch is written to survive that (tenancy is severable). The bigger risk is the
**AI-use gate** combined with the **unrun test suite**: do not open the PR until the tests and
lint are green on your own machine and you can speak to every line. An AI-authored security patch
with unexecuted tests is the exact thing that gets a security-sensitive PR closed.

---

## 3. Jitsi track — the honest version

**There is essentially no code to upstream to Jitsi.** This is the most important thing the
document can tell you, because it's tempting to manufacture a plugin PR and there isn't one.

### 3.1 Why there's nothing to send

Everything the design leans on already exists:

- **`token_affiliation`** — ships **inside the Jitsi distribution** now (rev 4 corrected this;
  `jitsi-contrib` renamed theirs to `token_affiliation_legacy` precisely because Jitsi provides
  the official one). Nothing to add.
- **`event_sync`, `muc_census`, `token_no_wildcard`** — existing **`jitsi-contrib`** community
  modules. Your `prosody-plugins-custom/mod_token_no_wildcard.lua` is a **vendored copy**, not
  original work.
- **Token/JWT auth, tenant verification, `muc_domain_mapper`** — all upstream Jitsi behaviour.
  The design *uses* them correctly; it doesn't extend them.

The novelty in this project lives on the **Zulip** side (deriving rooms from conversations,
membership-checked minting) and in the **token shape / config**, not in Jitsi's Lua.

### 3.2 Where a Jitsi contribution could realistically land

If you want to give something back to Jitsi, it's **documentation**, and possibly small fixes:

- **A handbook / docs PR on multi-tenant token verification.** Rev 4 documents several things
  the official docs get wrong or omit and that cost real time:
  - the token travels in the **BOSH query string**, not as a SASL password (SASL is ANONYMOUS);
  - `muc_mapper_domain_base` is **required** or every tenant join is refused;
  - tenant enforcement is on the **MUC component**, not at SASL;
  - `asap_key_server` is a **static `sha256(kid).pem` file server, not a JWKS endpoint**;
  - `mod_auth_token` **depends on `jitsi_session`** and fails *silently* if it can't load.

  These are exactly the corrections in `docs/architecture.md` §2.3–§2.9. Written up as a docs PR
  to the jitsi-meet handbook, they are genuinely useful and low-risk to land.
- **Fixes upstreamed to `jitsi-contrib`** *only if* you actually hit and fixed a bug in
  `event_sync` / `muc_census` during phase three. Don't invent one; if it happens, that repo
  (community-governed, lighter process) is the target — **not** the main `jitsi/jitsi-meet` repo.

### 3.3 Process mechanics (Jitsi)

- **CLA.** 8x8 requires a signed CLA (ICLA/CCLA) for contributions to the core Jitsi repos
  (`jitsi-meet`, `docker-jitsi-meet`, `lib-jitsi-meet`). *(Confirming exact current form and
  which repos it gates.)*
- **`jitsi-contrib`** is a separate, community-run org with its own, lighter contribution norms —
  relevant only if you upstream a module fix there.
- A **documentation** PR still goes through the same repo/CLA process but is far lower-friction
  to review than code.

**Recommendation:** treat Jitsi as "contribute the multi-tenancy documentation if you have the
appetite; otherwise nothing." Do not gate anything else on it.

---

## 4. The components that stay where they are

Recording *why* these aren't upstream contributions, so it's a decision on record, not an omission.

- **`embedded-call/` (in-Zulip minimizable iframe).** This is a fork surface — frontend + CSP
  changes to the Zulip web app. Two reasons to hold it back: (1) it's a large, churn-exposed
  frontend piece that upstream may replace with **native calling** ([#28505](https://github.com/zulip/zulip/issues/28505));
  (2) it was **unproven** when this was written. ~~(2)~~ has since expired — it ships and is used
  daily — so it is now Candidate C in §1.1 rather than a flat no. (1) still stands: offer it
  *after* the calls patch lands and only if maintainers signal they want the embedded UX rather
  than their own.
- **The call-aware sidebar** (~12 commits: occupancy rows, speaker and lock badges, per-user
  speaking rings). Not upstreamable for a structural reason rather than a taste one: the
  occupancy it renders has no source upstream. It is fed by `consort-conferencing` through an
  internal hook, and without that service every one of these rows is empty.
- **Voice channels as a channel type**, **lounges**, and **single-threaded channels**
  (`2273b553df`, `a9a893badb`, `d2343ae855`, `a22403a821`, `6cb28fee97`, `870415221a`,
  `cb5f5488c4`, `b02b2c2b37`, `828fe29c7d`). These are a product direction — Consort deciding
  what a channel *is* — not gaps in Zulip. Offering them would be proposing a different product.
- **Branding, in both repositories**, and the desktop fork's packaging, release workflow and
  removal of auto-updates. Ours by definition.
- **`consort-conferencing/` (occupancy + roster service).** Deliberately external — token
  minting must be in-process in Zulip, but state machines, timers, occupancy, and message edits
  belong outside where they deploy without restarting the chat server. This is an architectural
  choice (architecture.md §5.1), not a limitation. **Publish it as its own open-source project**
  (it already has its own repo and full tests); don't try to merge it into anyone.
- **Authentik identity / enrollment.** Zulip supports SAML natively; this is `/etc/zulip/settings.py`
  configuration plus an Authentik flow. Nothing to upstream. If anything, a short **integration
  guide** (Authentik → Zulip via SAML) could go to Authentik's or Zulip's docs, but that's a
  how-to, not a contribution to either codebase.

---

## 5. Recommended sequence

0. **Offer the two desktop candidates** (§1.2, E and F). They are small, they fix things that
   are broken rather than proposing anything, and they are gated on none of the below. Landing
   one is also the cheapest way to learn how these maintainers review before spending the
   calls patch's credibility.
1. **Finish the Zulip calls patch gates** (§2.2) — tests green, lint clean, UI verified,
   real API feature level, rebased. This is the critical path and the only thing with a
   maintained upstream target waiting for it.
2. **Rotation-under-Jicofo test** (§2.4) — close the one design gap you're least confident in
   before a maintainer asks.
3. **CZO design thread + issue comment** referencing #28657, raising the tenancy/subscription/
   moderator decisions.
4. **Sign the Zulip CLA, satisfy the AI-use policy, open the two-commit PR.**
5. **Iterate through review.** Expect several rounds; be ready to sever tenancy.
6. **(Optional, independent) Jitsi multi-tenancy docs PR** — any time; gated on nothing.
7. **(Later, only if wanted) embedded-call** — Candidate C. After the calls patch lands and only
   on maintainer signal; otherwise keep in the fork.
8. **Publish the conferencing service standalone** — parallel to all of the above; it's not an
   upstream PR, it's a release.
9. **Web Push** (Candidate B) — deliberately last despite being the most valuable, because it
   needs the fifteen-commit series rewritten before anyone can review it, and a design thread
   before that. Start the thread early; it can run while the calls patch is in review.
10. **(Only after the calls patch lands) per-channel voice setting** — Candidate D. It is
    meaningless upstream until there is something to switch off.

---

## 6. Open questions to resolve before submitting

- Has anyone started a PR against #28657 since this was written? (Check before investing in the
  CZO thread.)
- Does Zulip's current AI-use policy permit a substantially AI-assisted security patch at all,
  and under what disclosure? This gates whether the Zulip PR is viable in its current form.
- Is Zulip's native-calling work (#28505) far enough along that the calls patch would be seen as
  redundant? If native calling is imminent, the JWT-auth *primitive* may still be wanted even if
  the UI is not.
- For the Jitsi docs PR: is the jitsi-meet handbook the right target, and does the CLA apply to
  docs-only changes? (Confirming.)
