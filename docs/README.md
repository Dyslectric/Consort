# Design notes & runbooks

Design documents and deployment runbooks written during the build, kept as reference. They go into
more depth than the top-level README and reference internal component names and development
milestones from when they were written — so treat the [top-level README](../README.md) as the
current overview, and these as the "why it's built this way" underneath it.

- **[architecture.md](architecture.md)** — the design of record: the entitlement model, room
  derivation and token shape, the conferencing service, and the Prosody-side enforcement. Detailed,
  and evolved through implementation.
- **[embedded-call-design.md](embedded-call-design.md)** — the embedded, minimizable in-Zulip call,
  and the core-hook messaging design that makes DM/group and multi-realm call messages possible.
- **[prosody-event-sync.md](prosody-event-sync.md)** — the Prosody side: the `event_sync` component
  that streams occupancy to the conferencing service, `muc_census` for reconciliation, and how the
  plugins are delivered via a custom Prosody image.
