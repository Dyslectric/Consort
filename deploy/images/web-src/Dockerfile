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

# --platform=$BUILDPLATFORM pins the compile to the machine doing the building,
# never to the architecture being built for. What comes out of it is JavaScript,
# CSS and sounds — the same bytes whatever runs them — so compiling it once per
# target architecture buys nothing at all, and buys it at the price of running
# webpack under QEMU, which turns ten minutes into most of an afternoon. The
# per-architecture part of this image is the `base` layer above, which upstream
# already publishes for both. Remove this and a multi-arch build looks like it
# has hung.
FROM --platform=$BUILDPLATFORM node:${NODE_VERSION}-bookworm AS build
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
ARG JM_REF
ARG LJM_REF
USER root
COPY --from=build /src/jitsi-meet/libs /usr/share/jitsi-meet/libs
COPY --from=build /src/jitsi-meet/css /usr/share/jitsi-meet/css
COPY --from=build /src/jitsi-meet/static /usr/share/jitsi-meet/static
COPY --from=build /src/jitsi-meet/lang /usr/share/jitsi-meet/lang
COPY --from=build /src/jitsi-meet/sounds /usr/share/jitsi-meet/sounds
COPY --from=build /src/jitsi-meet/images /usr/share/jitsi-meet/images
COPY --from=build /src/jitsi-meet/fonts /usr/share/jitsi-meet/fonts
COPY --from=build /src/jitsi-meet/index.html /usr/share/jitsi-meet/index.html

# The bundles are asked for as `libs/app.bundle.min.js?v=139`, and nginx gives
# anything carrying a `v` parameter `expires 1y`. The number is written into
# index.html by hand and upstream bumps it per release — which is fine for a
# release and useless for us, because a build from a moved fork produces new
# bytes at a URL every client already holds for a year. Rebuild, pull, restart,
# and the app that runs is still the one from before: no request is made, so
# there is nothing for the new image to answer.
#
# Stamping the refs onto that number makes the URL change exactly when the code
# does, which is what the parameter was for. The value stays cacheable — it is
# still a `v`, so the year still applies, now to bytes that will not change
# under it.
#
# The two commits are the whole of what the compile above depends on, so a build
# that produces different JavaScript cannot produce the same stamp. The grep is
# the usual guard: if upstream ever stops writing `?v=` into index.html, this
# silently stops busting anything, and the failure looks exactly like the
# problem it fixes.
RUN set -eux; \
    stamp="$(printf '%s-%s' "${JM_REF}" "${LJM_REF}" | tr -cd 'A-Za-z0-9._-')"; \
    for page in /usr/share/jitsi-meet/index.html /usr/share/jitsi-meet/static/*.html; do \
        [ -f "$page" ] || continue; \
        sed -i "s#\(libs/[A-Za-z0-9._-]*\.js?v=[0-9]*\)#\1-${stamp}#g" "$page"; \
    done; \
    grep -q "app\.bundle\.min\.js?v=[0-9]*-${stamp}" /usr/share/jitsi-meet/index.html

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

# Without this, the sound of a screen share is mixed into the sharer's microphone
# and the two arrive as one track — no separate volume for the shared
# application, no way to stop its sound short of muting the person, and nothing
# at all sent by a sharer whose microphone is muted.
#
# It is lib-jitsi-meet's own flag, guarding JitsiConference.addTrack against a
# second audio source, and this is the only route to it: setting it in the URL
# hash does NOT reach the library, because jitsi-meet does not carry `testing`
# through its config override whitelist. config.js does reach it, since
# getConferenceOptions spreads the whole config into the options the conference
# is built with.
#
# Assigned around whatever is already there rather than into it: unlike
# audioQuality above, nothing in the templates is guaranteed to have created
# config.testing first, and a deployment where it happens to exist must not lose
# what it holds.
RUN set -eux; \
    printf '\nconfig.testing = Object.assign({}, config.testing, { allowMultipleTracks: true });\n' \
        >> /defaults/settings-config.js

# Twelve megabits for a screen share, at every resolution it is sent at.
#
# lib-jitsi-meet gives a desktop track a flat ceiling — `ssHigh`, 2.5 Mbps for
# all four codecs — rather than one from the quality ladder the camera uses, so
# this is not a per-resolution table to edit: the one number is what a share gets
# whether it is 1920x804 or 640x360. It is deliberately low for a slideshow of a
# document, and low is what a screen full of moving text or a terminal looks
# like when the encoder has to fit inside it.
#
# Only ssHigh is written. The parser copies across whichever of the bitrate keys
# are present and leaves the rest at the library's defaults, so nothing here
# touches camera video — the concern that made this a config edit rather than a
# change to the four tables in the fork's StandardVideoQualitySettings.
#
# Every codec, because the codec in use is decided per call by
# codecPreferenceOrder and by what the far end supports: raising one of them is a
# setting that appears to work and then does nothing on the next call.
#
# Twelve rather than the five it was before, because five was being reached
# rather than spent: a 1920x804 share of moving content measured 5.4-6.0 Mbps
# against it, in bursts that line up with the busiest seconds. The number is
# also shared rather than per-layer -- all three spatial layers of a K-SVC
# stream live inside one encoding, and a measured split put 2519, 667 and 204
# kbps across them, so roughly a quarter of the ceiling is spent on two layers
# that a single watcher never sees.
#
# Assigned around what is already there, for the reason config.testing is above:
# the templates create config.videoQuality.<codec> as an empty object today, and
# a deployment whose environment fills it in must keep what it holds. The grep
# fails the build if that stops being true, because an assignment into an object
# that does not exist takes the whole of config.js down with it — a blank call
# and a console error, from a bitrate change.
RUN set -eux; \
    grep -q 'config.videoQuality.vp9' /defaults/settings-config.js; \
    printf '\n%s\n%s\n%s\n%s\n%s\n' \
        "for (const codec of ['av1', 'h264', 'vp8', 'vp9']) {" \
        "    config.videoQuality[codec] = Object.assign({}, config.videoQuality[codec]);" \
        "    config.videoQuality[codec].maxBitratesVideo =" \
        "        Object.assign({}, config.videoQuality[codec].maxBitratesVideo, { ssHigh: 12000000 });" \
        "}" \
        >> /defaults/settings-config.js

# Sixty frames a second for a screen share, and the encoder told to keep them.
#
# Unset, as it was, this is not "whatever the capturer manages". It is the switch
# lib-jitsi-meet reads to decide that a share is a slideshow, and it decides four
# things at once — every one of them against a share of something that moves:
#
#   * getDisplayMedia is given no frame rate at all, so Chromium captures at its
#     default 30. Measured on a 3440x1440 panel: 21.5 fps delivered, against 35
#     with a ceiling of 60. `max` is the whole of what is needed here — an
#     `ideal` alongside it changed nothing.
#   * setContentHint reads `max > 5` and settles on 'detail' rather than
#     'motion', which tells the encoder to spend its budget on a sharp still.
#   * JingleSessionPC turns the same comparison into capScreenshareBitrate,
#     which makes the degradation preference 'maintain-resolution': under load
#     the frame rate is what gets dropped, so the 60 above would collapse to a
#     slideshow exactly when it mattered. Above 5 it becomes
#     'maintain-framerate', which is what "60 when possible" has to mean.
#   * The same flag disables the lower simulcast layers for a share and routes
#     the bitrate through `desktopbitrate` instead of the `ssHigh` raised above.
#
# So this is one number and four behaviours, and the four only agree with each
# other at the ends of the range. Anything above 5 gets the motion half of them.
#
# `min` stays at 5, which is not a floor on what is sent: it is deleted before
# capture, and used only for the applyConstraints call that keeps Chromium's 0Hz
# mode from parking a still screen at no frames at all. Raising it would demand
# frames of a screen that is not changing.
#
# The cost is CPU on the sharer, at 60 fps and up to 1920x1080. config.videoQuality
# .enableAdaptiveMode is on, which walks the codec down AV1 -> VP9 -> VP8 when the
# encoder cannot keep up, so the failure mode is a softer picture rather than a
# stalled one.
#
# DO NOT EXPECT SIXTY. It is a ceiling, and on Windows the capturer does not
# reach it: measured on Electron 42 against a 3440x1440 panel, getSettings
# negotiates 60 and the track delivers 27-38 fps, wandering with the state of the
# machine. What that number is NOT, each measured rather than assumed:
#
#   not the encoder    AV1 and VP9 both sent every frame at 1920x804 with
#                      qualityLimitationReason 'none', at 3.4-3.7 Mbps against
#                      the 5 Mbps ceiling above.
#   not the downscale  3440x1440 uncapped, 1920x804, 1280x720 and 640x360 all
#                      delivered the same rate in one run.
#   not the source     window capture measured the same as screen capture.
#   not our own load   a heavy animation and a near-idle one measured the same.
#   not the screen     the captured desktop was changing at 96-99 Hz throughout.
#   not a switch       --webrtc-max-cpu-consumption-percentage=100, the WGC
#                      capturer features and --disable-frame-rate-limit all
#                      landed inside the run-to-run noise.
#
# The ceiling is Chromium's Windows desktop capture path, so raising it is an
# Electron question rather than a Jitsi one. The setting still earns its place:
# against a max of 30 the same machine delivered 29.6 fps, so this is the
# difference between a hard 30 and the mid 30s -- and it is what buys the motion
# semantics and the ssHigh path described above, which is most of the point.
RUN set -eux; \
    printf '\nconfig.desktopSharingFrameRate = { min: 5, max: 60 };\n' \
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
