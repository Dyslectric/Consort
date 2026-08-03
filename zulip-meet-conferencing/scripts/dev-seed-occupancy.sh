#!/usr/bin/env bash
# Seed the DEV conferencing service (scripts/dev-conferencing.sh) with a fake call
# and occupants, so the call-aware sidebar has something to render without a real
# Jitsi. This plays the part of Prosody's event_sync. DEV ONLY.
#
# Use real dev channel stream_ids and real user_ids so avatars resolve via Zulip's
# /avatar/<user_id>/medium. Env: CONFERENCING_URL (default http://localhost:8080),
# EVENT_SYNC_SECRET (default devsecret, must match the running dev service).
set -euo pipefail

BASE="${CONFERENCING_URL:-http://localhost:8080}/api/v1/jitsi"
SECRET="${EVENT_SYNC_SECRET:-devsecret}"

usage() {
    cat <<'EOF'
Seed the dev conferencing service with a fake call (dev only).

  dev-seed-occupancy.sh <stream_id> <user_id>:<name> [<user_id>:<name> ...]
      Start a call in channel <stream_id> with these people in it.
  dev-seed-occupancy.sh --join  <stream_id> <user_id>:<name>
      Add one more person to that call.
  dev-seed-occupancy.sh --leave <stream_id> <user_id>
      One person leaves.
  dev-seed-occupancy.sh --end   <stream_id>
      End the call (destroy the room) — the sidebar row clears.

Env: CONFERENCING_URL (default http://localhost:8080), EVENT_SYNC_SECRET (default devsecret).
EOF
}

post() { # post <path> <json-body>
    curl -fsS -XPOST "$BASE/$1" \
        -H "Authorization: Bearer $SECRET" \
        -H "Content-Type: application/json" \
        -d "$2" >/dev/null
}

room_for() { printf 'c-dev%s' "$1"; }

join() { # join <room> <user_id>:<name>
    local room="$1" uid name
    IFS=: read -r uid name <<<"$2"
    post "events/occupant/joined" \
        "{\"room_name\":\"$room\",\"occupant\":{\"occupant_jid\":\"$room/$uid\",\"id\":\"$uid\",\"name\":\"$name\"}}"
    echo "  + $uid ($name)"
}

case "${1:-}" in
    "" | -h | --help)
        usage
        ;;
    --end)
        room="$(room_for "$2")"
        post "events/room/destroyed" "{\"room_name\":\"$room\"}"
        echo "ended call in stream $2 (room $room)"
        ;;
    --leave)
        room="$(room_for "$2")"
        post "events/occupant/left" \
            "{\"room_name\":\"$room\",\"occupant\":{\"occupant_jid\":\"$room/$3\"}}"
        echo "user $3 left stream $2"
        ;;
    --join)
        room="$(room_for "$2")"
        join "$room" "$3"
        ;;
    *)
        stream="$1"
        shift
        room="$(room_for "$stream")"
        post "calls/created" "{\"room\":\"$room\",\"stream_id\":$stream,\"scope\":\"dev\"}"
        echo "started call in stream $stream (room $room)"
        for person in "$@"; do
            join "$room" "$person"
        done
        ;;
esac
