# Custom docker-jitsi-meet Prosody image that loads the event_sync Component.
#
# WHY this exists: docker-jitsi-meet regenerates /run/prosody/config from
# /defaults on every start, and its config generator writes a FIXED set of files
# into the runtime conf.d (jitsi-meet, and conditionally brewery/visitors) — it
# does NOT glob custom files dropped in /defaults/conf.d. So the documented
# "mount into /defaults/conf.d" trick does not work on this image; the component
# never reaches the runtime config. (Verified 2026-08-02.)
#
# The fix is one Include appended to the main config template, pointing at the
# already-mounted, persistent prosody-plugins-custom dir. Put the rendered
# event_sync.cfg.lua in that dir (secret substituted) and it gets loaded.
#
# The USER dance matters: the build runs as the image's non-root user (uid 1000),
# which cannot write the root-owned /defaults, so switch to root for the edit —
# then switch BACK to 1000, or Prosody refuses to run ("Danger, Will Robinson!
# Prosody doesn't need to be run as root").

ARG JITSI_IMAGE_VERSION=unstable-2026-06-25
FROM jitsi/prosody:${JITSI_IMAGE_VERSION}

USER root
RUN printf '\nInclude "/prosody-plugins-custom/*.cfg.lua"\n' >> /defaults/prosody.cfg.lua
# Enforce moderator strictly from the JWT. token_affiliation_legacy sets each
# occupant's affiliation from the token's context.user.moderator, but only ONCE
# on join by default (disable_cascading_set=true) — and with JWT auth in Jicofo,
# Jicofo re-grants owner a split-second later, overwriting that single set, so a
# non-moderator keeps owner. Setting disable_cascading_set=false makes the plugin
# re-apply the correct affiliation several times over ~1.5s and win the race.
# Prepended at global scope so the muc.meet.jitsi component's module reads it via
# config fallback (a plugins-custom Include lands in the wrong scope).
RUN sed -i '1i disable_cascading_set = false' /defaults/prosody.cfg.lua
USER 1000
