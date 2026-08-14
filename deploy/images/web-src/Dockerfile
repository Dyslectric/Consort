# The Jitsi web app, built from source rather than taken prebuilt.
#
# WHY this exists alongside images/web: that one layers edits onto the published
# app, which is enough for injecting a script and nothing more. Sending screen
# share audio as its own track changes lib-jitsi-meet — a second audio sender per
# peer connection — and jitsi-meet — no AudioMixerEffect, a separate track, and
# per-source volume. Neither is reachable in a built bundle, so the app has to be
# compiled here.
#
# Both repositories default to UPSTREAM. Build it that way FIRST and deploy it:
# an unmodified source build that behaves exactly like the published image proves
# the build itself, and every failure after that belongs to the change rather
# than to the sixty lines below. Then point the ARGs at the forks.
#
# It is not a cheap image. Two npm installs and a webpack build put it around ten
# minutes against seconds for images/web, which is the real cost of the fork and
# is worth knowing before it is in a hot loop.

ARG JITSI_IMAGE_REPO=ghcr.io/jitsi
ARG JITSI_IMAGE_VERSION=unstable-2026-06-25

# jitsi-meet's package.json asks for node >=24 and npm >=11. Below that, npm ci
# fails on lockfile syntax rather than on anything to do with this build.
ARG NODE_VERSION=24

# Pin by commit, not by branch. A branch means the image quietly changes under
# you between builds, which is the same class of problem as the three identically
# named tarballs that cost an afternoon.
ARG LJM_REPO=https://github.com/jitsi/lib-jitsi-meet.git
ARG LJM_REF=master
ARG JM_REPO=https://github.com/jitsi/jitsi-meet.git
ARG JM_REF=master

FROM ${JITSI_IMAGE_REPO}/web:${JITSI_IMAGE_VERSION} AS base

FROM node:${NODE_VERSION}-bookworm AS build
ARG LJM_REPO
ARG LJM_REF
ARG JM_REPO
ARG JM_REF

# make for jitsi-meet's build, git to fetch it, and a toolchain because some
# dependencies still build native code through node-gyp.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates git make python3; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src

# lib-jitsi-meet first: jitsi-meet consumes it as a package, so it has to exist
# as one before jitsi-meet is installed.
RUN set -eux; \
    git clone --filter=blob:none "${LJM_REPO}" lib-jitsi-meet; \
    cd lib-jitsi-meet; \
    git checkout "${LJM_REF}"; \
    npm ci; \
    npm run build; \
    npm pack

# jitsi-meet, with that build put in place of the released tarball its
# package.json names.
#
# The install happens AFTER npm ci on purpose: ci wipes node_modules and would
# undo it. --no-save leaves package.json alone, so the diff against upstream
# stays limited to the code that was actually changed — the Makefile copies out
# of node_modules either way, so nothing needs the manifest to agree.
RUN set -eux; \
    git clone --filter=blob:none "${JM_REPO}" jitsi-meet; \
    cd jitsi-meet; \
    git checkout "${JM_REF}"; \
    npm ci; \
    npm install --no-save ../lib-jitsi-meet/lib-jitsi-meet-*.tgz; \
    make

# The app payload, and only the app payload.
#
# config.js and interface_config.js are NOT here, deliberately. The container
# generates them from the environment on every start and the ones in the web root
# are symlinks into /config; copying a source tree over them replaces the links
# with files, and the result is a deployment that ignores every JITSI_* variable
# it is given while looking perfectly healthy.
FROM base AS app
USER root
COPY --from=build /src/jitsi-meet/libs /usr/share/jitsi-meet/libs
COPY --from=build /src/jitsi-meet/css /usr/share/jitsi-meet/css
COPY --from=build /src/jitsi-meet/static /usr/share/jitsi-meet/static
COPY --from=build /src/jitsi-meet/lang /usr/share/jitsi-meet/lang
COPY --from=build /src/jitsi-meet/sounds /usr/share/jitsi-meet/sounds
COPY --from=build /src/jitsi-meet/images /usr/share/jitsi-meet/images
COPY --from=build /src/jitsi-meet/fonts /usr/share/jitsi-meet/fonts
COPY --from=build /src/jitsi-meet/index.html /usr/share/jitsi-meet/index.html

# The speaking relay, exactly as images/web does it — the sidebar's per-user glow
# depends on it, and a source build that silently dropped it would look like the
# relay had broken rather than like it had never been loaded.
COPY embedded-call/jitsi-speaking-relay.js /usr/share/jitsi-meet/zulip-speaking-relay.js
RUN set -eux; \
    sed -i 's#</body>#    <script src="/zulip-speaking-relay.js"></script>\n</body>#' \
        /usr/share/jitsi-meet/index.html; \
    grep -q 'zulip-speaking-relay.js' /usr/share/jitsi-meet/index.html

# Without this, the audio settings a user chooses never reach capture.
# createLocalTracksF only reads features/settings.audioSettings when
# enableAdvancedAudioSettings is on, so with it off the microphone controls
# store a preference that nothing consults — the setting appears to work and
# changes nothing. docker-jitsi-meet has no environment variable for it, so it
# is appended to the template the container renders config.js from. The
# assignment sits after config.audioQuality is created by the template above it,
# and the grep fails the build if that template ever stops creating it.
RUN set -eux; \
    grep -q 'config.audioQuality' /defaults/settings-config.js; \
    printf '\nconfig.audioQuality.enableAdvancedAudioSettings = true;\n' \
        >> /defaults/settings-config.js

# Back to whatever user upstream chose, for the reason images/web spells out: the
# published image runs as non-root and hardcoding its uid here is the kind of
# detail that breaks silently on a bump.
FROM base
COPY --from=app /usr/share/jitsi-meet /usr/share/jitsi-meet
# The config template as well as the app: the edit above lives in /defaults,
# which this stage would otherwise take from the base image and lose. COPY
# --from writes as root whatever the active USER is, which is why it can reach
# a root-owned path without switching user here.
COPY --from=app /defaults/settings-config.js /defaults/settings-config.js
