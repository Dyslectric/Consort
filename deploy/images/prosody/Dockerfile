# Prosody, with everything Consort needs baked in.
#
# The deployment this grew out of mounted the Lua from a host directory. That is
# what made a fresh install fragile: Docker creates a missing bind-mount source
# as root, and every Jitsi container then refuses to start with "directory is not
# writable by the container user (uid 1000)". Baking removes the mount, and with
# it the whole failure mode.
#
# WHY the Include exists: docker-jitsi-meet regenerates /run/prosody/config from
# /defaults on every start, and its config generator writes a FIXED set of files
# into the runtime conf.d — it does NOT glob custom files dropped in
# /defaults/conf.d. So the documented "mount into /defaults/conf.d" trick never
# reaches the runtime config. One Include appended to the main template does.
#
# The USER dance matters: the build runs as the image's non-root user (uid 1000),
# which cannot write the root-owned /defaults, so switch to root for the edit —
# then switch BACK to 1000, or Prosody refuses to run ("Danger, Will Robinson!
# Prosody doesn't need to be run as root").

# Defaults match deploy/.env.example. ghcr.io rather than Docker Hub for the same
# reason the compose files use it: it is where docker-jitsi-meet publishes, and it
# has no anonymous pull limit to trip over in CI. Note that a docker-jitsi-meet
# *release* tag is not an *image* tag — the published ones are dated.
ARG JITSI_IMAGE_REPO=ghcr.io/jitsi
ARG JITSI_IMAGE_VERSION=unstable-2026-06-25
FROM ${JITSI_IMAGE_REPO}/prosody:${JITSI_IMAGE_VERSION}

USER root

# Three modules upstream does not ship, pinned twice over: by the commit they
# came from, and by the hash of what that commit actually served. The second is
# the stronger claim — it pins what is running rather than what a ref pointed at
# when someone looked — and a mismatch fails the build rather than shipping
# quietly.
#
#   mod_event_sync_component      streams occupancy to the conferencing service
#   mod_token_affiliation_legacy  sets affiliation from the token's moderator
#                                 claim on images whose bundled module predates
#                                 the pre-join write (see the note below)
#   mod_token_no_wildcard         refuses a token claiming every room, which is a
#                                 skeleton key for the deployment and a silent
#                                 failure without it
#
# Fetched rather than copied out of jitsi-token-harness/prosody-plugins-custom/,
# where a working copy of the third one usually sits: that directory is
# .gitignore'd on purpose, because its contents are fetched third-party code, so
# it is empty in a fresh clone and in every CI checkout.
#
# mod_muc_census and mod_token_affiliation are already in the image; enabling
# them in XMPP_MUC_MODULES is enough, and vendoring them would only create a
# second, staler copy.
ARG PROSODY_PLUGINS_REF=fe4532b8ef51bfbb74119928ffe8c683659c1d2d
RUN set -eux; \
    mkdir -p /prosody-plugins-custom; \
    base="https://raw.githubusercontent.com/jitsi-contrib/prosody-plugins/${PROSODY_PLUGINS_REF}"; \
    curl -fsSL "$base/event_sync/mod_event_sync_component.lua" \
        -o /prosody-plugins-custom/mod_event_sync_component.lua; \
    curl -fsSL "$base/token_affiliation_legacy/mod_token_affiliation_legacy.lua" \
        -o /prosody-plugins-custom/mod_token_affiliation_legacy.lua; \
    curl -fsSL "$base/token_no_wildcard/mod_token_no_wildcard.lua" \
        -o /prosody-plugins-custom/mod_token_no_wildcard.lua; \
    { \
      echo "c4ea639f9b61dfa8548e84f535953967c4139404d331a7abfe86e83d558c4024  /prosody-plugins-custom/mod_event_sync_component.lua"; \
      echo "02b4fca21a01397e05b86c4e4043b6ac8638f31aed0439b06e09f958d1e076a9  /prosody-plugins-custom/mod_token_affiliation_legacy.lua"; \
      echo "6d4a1bffb5fdeddfaf769b22cd9e88b103bfe8a5d5e910c8dde307a937db0407  /prosody-plugins-custom/mod_token_no_wildcard.lua"; \
    } > /tmp/sums; \
    sha256sum -c /tmp/sums; \
    rm /tmp/sums

# Ours: the event_sync component's own config, which reads its bearer secret from
# the environment rather than being rendered with a secret baked in.
COPY consort-conferencing/deploy/prosody/event_sync.cfg.lua \
     /prosody-plugins-custom/event_sync.cfg.lua

RUN chown -R 1000:1000 /prosody-plugins-custom \
 && printf '\nInclude "/prosody-plugins-custom/*.cfg.lua"\n' >> /defaults/prosody.cfg.lua

# For mod_token_affiliation_legacy only. That module sets each occupant's
# affiliation once on join (disable_cascading_set=true by default), and Jicofo
# re-grants owner a split second later, overwriting it — so a non-moderator keeps
# owner. False makes it re-apply over ~1.5s and win the race. Prepended at global
# scope so the muc.meet.jitsi component reads it by config fallback.
#
# Inert with the mod_token_affiliation bundled in recent images, which writes the
# affiliation in muc-occupant-pre-join — before the join completes, so there is no
# race. Kept because the right value for a deployment pinning an older image is
# not obvious, and it costs nothing.
RUN sed -i '1i disable_cascading_set = false' /defaults/prosody.cfg.lua

USER 1000

# ---------------------------------------------------------------------------
# THIS IMAGE IS NOT ENOUGH ON ITS OWN. Settings outside it decide whether the
# JWT's moderator claim means anything; without them every moderator decision
# Zulip makes is decorative. They live in .env and overlay.yaml:
#
#   XMPP_MUC_MODULES=...,token_affiliation   ENABLE_AUTO_OWNER=false
#   JICOFO_ENABLE_AUTH=0                     DISABLE_PROFILE=true
#
# `consort verify` asserts all of them against the running stack, because
# every one of them fails silently.
# ---------------------------------------------------------------------------
