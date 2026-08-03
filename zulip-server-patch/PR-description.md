# Suggested pull request

Title: `[ai] video_calls: Add authenticated Jitsi calls scoped to a conversation.`

Base: `upstream/main`

**Do not open this PR until `./tools/test-backend zerver.tests.test_jitsi_jwt` and
`./tools/lint` pass.** Neither was run — see "How changes were tested" below, which is
written honestly and should be edited once you have actually run them.

---

```markdown
Fixes: #28657

Zulip generates Jitsi room names in the browser with `Math.random()` and does not
check who joins; anyone with the link is in. That is fine for `meet.jit.si`, but a
self-hosted deployment currently has no way to express "only the people in this
conversation can join this call", which is the thing #28657 asks for.

This adds `POST /calls/jitsi/create`, following the pattern already established by
the BigBlueButton, Webex, Nextcloud Talk and Constructor Groups endpoints, with one
deliberate difference: it performs an authorization check. Membership of the
conversation is what entitles someone to the call, so the endpoint requires a channel
subscription, or that the direct message recipients are users the caller may reach.
The four existing endpoints check nothing, which is harmless only because the rooms
they hand out are unauthenticated anyway; once Prosody trusts our signature it stops
being harmless.

Room names are derived by HMAC over the conversation plus a rotatable epoch, rather
than generated randomly, because a room name is functionally a secret once it can be
pasted into a chat, and because rotation gives a recovery path when one leaks. The
epoch is signed and bound to its conversation — the same approach
`get_bigbluebutton_url` already uses for its moderator flag — so it can round-trip
through an untrusted client without becoming a way to reach into someone else's
conversation.

Tokens are scoped to a single room and expire in two minutes. Prosody validates a
token when the client connects and again at MUC join and never afterwards, so a short
lifetime costs the user nothing and bounds how long a leaked token is useful for
joining. It does nothing about an already-established session; there is no revocation
in this model, and the commit message says so rather than implying otherwise.

Servers that have not configured the `JITSI_JWT_*` settings are entirely unaffected:
`server_jitsi_jwt_enabled` is false and clients keep generating room names locally.

**How changes were tested:**

- [ ] `./tools/test-backend zerver.tests.test_jitsi_jwt`
- [ ] `./tools/test-backend --coverage zerver.tests.test_jitsi_jwt` — confirm the new
      lines in `zerver/lib/jitsi_token.py` and `create_jitsi_call` are covered
- [ ] `./tools/lint`
- [ ] `./tools/test-js-with-node`
- [x] Verified end-to-end against a real Prosody running Jitsi's `mod_auth_token`,
      `mod_jitsi_session`, `token_verification`, `token_no_wildcard` and
      `muc_domain_mapper`: a token minted by this code was admitted to its own room,
      and tokens for another tenant and another room were both refused by Prosody.
- [x] Verified the room derivation and token claim shape directly (stability,
      channel/realm/epoch scoping, key rotation, and the refusals for wildcard rooms,
      uppercase tenants and non-string user context fields).

**Screenshots and screen captures:**

The compose-box call button is unchanged; the only user-visible difference is the URL
it inserts. Screenshots pending manual verification.

<details>
<summary>Self-review checklist</summary>

- [x] [Self-reviewed](https://zulip.readthedocs.io/en/latest/contributing/code-reviewing.html#how-to-review-code) the changes for clarity and maintainability
      (variable names, code reuse, readability, etc.).
- [ ] Followed the [AI use policy](https://zulip.readthedocs.io/en/latest/contributing/contributing.html#ai-use-policy-and-guidelines).

Communicate decisions, questions, and potential concerns.

- [x] Explains differences from previous plans (e.g., issue description).
- [x] Highlights technical choices and bugs encountered.
- [x] Calls out remaining decisions and concerns.
- [x] Automated tests verify logic where appropriate.

Individual commits are ready for review (see [commit discipline](https://zulip.readthedocs.io/en/latest/contributing/commit-discipline.html)).

- [x] Each commit is a coherent idea.
- [x] Commit message(s) explain reasoning and motivation for changes.

Completed manual review and testing of the following:

- [ ] Visual appearance of the changes.
- [ ] Responsiveness and internationalization.
- [ ] Strings and tooltips.
- [ ] End-to-end functionality of buttons, interactions and flows.
- [ ] Corner cases, error conditions, and easily imagined bugs.

</details>

## Decisions I would like input on

**Tenancy.** Tenant-style Jitsi URLs (`https://jitsi.example/TENANT/ROOM`) let Prosody
refuse a token whose `sub` does not match the tenant in the URL, so isolation between
groups becomes structural rather than a property of this code being correct. I resolve
the tenant from Zulip user group membership via `JITSI_TENANT_BY_GROUP`, falling back
to the realm subdomain. That may be more policy than belongs in core. If so, the
setting can be dropped and the patch still stands on its own as "authenticate Jitsi
calls" — everything else is unchanged.

**Subscription as the entitlement.** A user who can *read* a public channel without
being subscribed is refused a call token. That is deliberate but it is a product
decision, and the alternative (content access rather than subscription) is a one-line
change.

**Moderator mapping.** `context.user.moderator` is set from
`can_administer_channel_group`, or realm admin. Direct message calls give nobody
moderator, which means Jitsi's default first-joiner-becomes-owner behaviour applies
there unless the deployment configures otherwise. I would rather agree the policy than
guess it.

**Rooms must exist before anyone can join.** Jitsi's `token_util:verify_room` returns
`room-does-not-exist` before it reads any claim, and Jicofo is what creates rooms
during conference allocation. Every epoch rotation produces a brand-new room, so this
design exercises that path constantly rather than rarely. I have not been able to test
rotation against a deployment with Jicofo present, and that is the part of this change
I am least confident about.

**API feature level.** Uses the `ZF-bdd936` placeholder from
`tools/create-api-changelog`, per `docs/documentation/api.md`.
```
