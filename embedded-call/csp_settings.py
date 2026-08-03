# Widening Zulip's Content-Security-Policy for the embedded Jitsi call.
#
# The embedded iframe (embedded_call.js) will not run under Zulip's stock CSP:
# the browser blocks the meet iframe, external_api.js, and the call's XMPP
# transport unless the meet origin is explicitly allowed. This is the fork
# settings change that allows exactly that origin and nothing more.
#
# VERIFY IN THE DEV ENV — django-csp changed its settings shape across versions
# and Zulip 12.1's exact form must be confirmed:
#   * Older Zulip / django-csp 3.x: individual CSP_* settings, values are lists.
#     Widen them by appending the meet origin.
#   * Newer django-csp 4.x: a single CONTENT_SECURITY_POLICY = {"DIRECTIVES": {...}}
#     dict. Widen the directive lists inside it.
# Grep the fork for `CSP_` / `CONTENT_SECURITY_POLICY` in zproject/ to see which
# is in use, then apply the matching block below. Confirm with the browser
# console (a blocked resource logs a precise CSP violation naming the directive).

# The one origin we are opening up. Keep it a single explicit host — never a
# wildcard — so this stays a hole exactly one service wide.
JITSI_MEET_ORIGIN = "https://meet.zulip.davig01.net"
JITSI_MEET_WS_ORIGIN = "wss://meet.zulip.davig01.net"

# What each directive is for, so a future reviewer can tighten rather than guess:
#   frame-src   : the Jitsi iframe itself.
#   script-src  : external_api.js — ONLY needed if it is loaded cross-origin. If
#                 you self-host a version-pinned copy under Zulip's own origin
#                 (recommended, see README), external_api.js is same-origin and
#                 this entry can be dropped.
#   connect-src : the call's signalling (BOSH/WebSocket) back to the meet origin.
#   child-src   : older browsers still consult it for framed content.
#
# --- django-csp 3.x style (individual list settings) ------------------------
#
# Append in zproject/computed_settings.py AFTER the base CSP_* lists are defined:
#
#     CSP_FRAME_SRC = (*CSP_FRAME_SRC, JITSI_MEET_ORIGIN)
#     CSP_CHILD_SRC = (*CSP_CHILD_SRC, JITSI_MEET_ORIGIN)
#     CSP_CONNECT_SRC = (*CSP_CONNECT_SRC, JITSI_MEET_ORIGIN, JITSI_MEET_WS_ORIGIN)
#     # Only if external_api.js is loaded cross-origin (not self-hosted):
#     CSP_SCRIPT_SRC = (*CSP_SCRIPT_SRC, JITSI_MEET_ORIGIN)
#
# --- django-csp 4.x style (CONTENT_SECURITY_POLICY dict) --------------------
#
#     _d = CONTENT_SECURITY_POLICY["DIRECTIVES"]
#     def _allow(directive, *origins):
#         _d[directive] = [*_d.get(directive, ["'self'"]), *origins]
#     _allow("frame-src", JITSI_MEET_ORIGIN)
#     _allow("child-src", JITSI_MEET_ORIGIN)
#     _allow("connect-src", JITSI_MEET_ORIGIN, JITSI_MEET_WS_ORIGIN)
#     # Only if external_api.js is loaded cross-origin (not self-hosted):
#     _allow("script-src", JITSI_MEET_ORIGIN)
#
# Note: if the meet origin ever serves the call over a different websocket host
# (some docker-jitsi-meet setups use the same host, others a colibri/JVB host),
# add that host to connect-src too — the console violation will name it.
