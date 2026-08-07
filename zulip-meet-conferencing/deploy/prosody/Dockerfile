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
# For the OLDER token_affiliation_legacy plugin only. That one sets each
# occupant's affiliation once on join (disable_cascading_set=true by default),
# and Jicofo re-grants owner a split-second later, overwriting it — so a
# non-moderator keeps owner. Setting this to false makes it re-apply several
# times over ~1.5s and win the race. Prepended at global scope so the
# muc.meet.jitsi component's module reads it via config fallback.
#
# INERT with the mod_token_affiliation.lua bundled in unstable-2026-06-25, which
# writes the affiliation in muc-occupant-pre-join — before the join completes, so
# there is no race to win and no option to read. Kept because the correct value
# for a deployment that pins an older image is not obvious, and it costs nothing.
RUN sed -i '1i disable_cascading_set = false' /defaults/prosody.cfg.lua
USER 1000

# ---------------------------------------------------------------------------
# THIS FILE IS NOT ENOUGH ON ITS OWN. Three settings outside it decide whether
# the JWT's moderator claim means anything, and without all three, every
# moderator decision Zulip makes is decorative:
#
#   XMPP_MUC_MODULES=token_affiliation   # comma-separate to combine
#   ENABLE_AUTO_OWNER=false
#   JICOFO_ENABLE_AUTH=0
#
# The first loads the module that reads context.user.moderator; it ships in the
# image but is NOT in the MUC component's modules_enabled by default, so nothing
# reads the claim until you ask for it.
#
# The second stops Jicofo granting owner to whoever joins first.
#
# The third is the one that actually bit, and it is not obvious: Jicofo has its
# own authentication path, entirely separate from auto-owner, and
# JICOFO_ENABLE_AUTH defaults to ENABLE_AUTH. With AUTH_TYPE=jwt that makes
# Jicofo treat *every holder of a valid token* as an authenticated user to be
# granted ownership — anonymous guests included, since their tokens are valid by
# construction. The symptom was an unauthenticated guest able to end a meeting
# for everyone, having joined as PARTICIPANT and been promoted a second later:
#
#   ...onMemberJoined: Member joined:466949ed ... role=PARTICIPANT
#   ...AbstractAuthAuthority.authenticateJidWithSession: Authenticated jid: 466949ed...
#   ...end_conference: Room ... destroyed by occupant 466949ed...
#
# Turning it off costs nothing: Prosody's token_verification still gates entry on
# the token, and token_affiliation still decides moderator from the claim. It
# only stops Jicofo second-guessing both.
#
# See docker-compose.override.example.yml.
# ---------------------------------------------------------------------------
