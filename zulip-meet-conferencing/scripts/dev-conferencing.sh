#!/usr/bin/env bash
# Run the conferencing service locally to dev-test the call-aware sidebar.
#
# It serves the occupancy sinks on :8080 with NO Jitsi/Prosody behind it — you
# feed it fake occupancy with dev-seed-occupancy.sh, which plays the part of
# event_sync. Posting to Zulip is OFF (no ZULIP_HOOK_URL), so it only tracks
# occupancy and answers the widget/sidebar.
#
# DEV ONLY. Never point this at prod, and give it a dev EVENT_SYNC_SECRET that is
# distinct from the prod one so the two environments can never talk to each other.
set -euo pipefail

# Dev config — override by exporting any of these before you run this.
export EVENT_SYNC_SECRET="${EVENT_SYNC_SECRET:-devsecret}"  # must match JITSI_CONFERENCING_SECRET in dev_settings.py
export ZULIP_SITE="${ZULIP_SITE:-http://localhost:9991}"    # run-dev; the event loop pokes it (auth errors here are harmless)
export ZULIP_EMAIL="${ZULIP_EMAIL:-bot@localhost}"
export ZULIP_API_KEY="${ZULIP_API_KEY:-dummy}"
export BIND_PORT="${BIND_PORT:-8080}"
# The occupancy push — the sidebar's fast path (a jitsi_occupancy server event) —
# goes through Zulip's internal hook, so point it at run-dev. It is bearer-authed
# by EVENT_SYNC_SECRET, not the bot, so the dummy ZULIP_API_KEY above is fine: the
# event-loop thread just logs harmless auth errors. Unset ZULIP_HOOK_URL to go back
# to poll-only mode (the sidebar then refreshes on the slow 15s backstop instead).
export ZULIP_HOOK_URL="${ZULIP_HOOK_URL:-http://localhost:9991/api/internal/jitsi}"

# Run from the package root so `python -m conferencing` resolves, and prefer a
# local virtualenv if one exists.
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
if [[ -x .venv/bin/python ]]; then
    PYTHON=.venv/bin/python
fi

echo "conferencing (dev) -> http://localhost:${BIND_PORT}  secret=${EVENT_SYNC_SECRET}"
echo "occupancy push -> ${ZULIP_HOOK_URL}  (unset ZULIP_HOOK_URL for poll-only)"
echo "seed it:  bash scripts/dev-seed-occupancy.sh <stream_id> <user_id>:<name> ..."
exec "$PYTHON" -m conferencing
