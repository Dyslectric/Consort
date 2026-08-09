# The Jitsi web app, with the per-participant speaking relay loaded into it.
#
# WHY it has to be inside the web app: the iframe API (JitsiMeetExternalAPI) only
# exposes the single dominant speaker, so it cannot drive a per-user glow in
# Zulip's sidebar. Per-participant audio levels exist only inside the app, where
# lib-jitsi-meet emits them — so the relay listens there, debounces to a set of
# who is speaking, and postMessages it out to the embedding Zulip page.
#
# The modification is done in a builder stage rather than in place, so the FINAL
# stage never changes USER. Recent jitsi/web images run as a non-root user, and
# /usr/share/jitsi-meet is root-owned, so the edit needs root — but switching to
# root and back in the shipped image means hardcoding whichever uid this image
# happens to use, which is exactly the kind of detail that breaks silently on an
# upstream bump. The builder is thrown away, so it can become root freely, and
# COPY --from writes as root regardless of the active USER. The result is that
# the published image's user is whatever upstream chose, untouched.

ARG JITSI_IMAGE_VERSION=unstable-2026-06-25
FROM jitsi/web:${JITSI_IMAGE_VERSION} AS base

FROM base AS builder
USER root
COPY embedded-call/jitsi-speaking-relay.js /usr/share/jitsi-meet/zulip-speaking-relay.js
# Inserted before </body> so it runs after the app's own scripts are present.
# The grep is the point of the whole RUN: a missing tag means the relay never
# loads, which looks exactly like "nobody is speaking" — no error, no console
# line, just a feature that quietly does nothing. Fail the build instead.
RUN set -eux; \
    sed -i 's#</body>#    <script src="/zulip-speaking-relay.js"></script>\n</body>#' \
        /usr/share/jitsi-meet/index.html; \
    grep -q 'zulip-speaking-relay.js' /usr/share/jitsi-meet/index.html

FROM base
COPY --from=builder /usr/share/jitsi-meet/zulip-speaking-relay.js /usr/share/jitsi-meet/zulip-speaking-relay.js
COPY --from=builder /usr/share/jitsi-meet/index.html /usr/share/jitsi-meet/index.html
