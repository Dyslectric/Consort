#!/usr/bin/env bash
#
# Fetch the jitsi-contrib Prosody modules this phase needs into
# ./prosody-plugins-custom, which the compose file mounts into the container.
#
# Rev 3 section 2.3: pin the exact version you deploy and read its Lua before
# trusting it. This script records the commit it fetched into
# prosody-plugins-custom/.pinned so that "which version am I running" has an
# answer. Once you are happy, set PROSODY_PLUGINS_REF to that SHA and commit it.

set -euo pipefail

REF="${PROSODY_PLUGINS_REF:-main}"
DEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/prosody-plugins-custom"
BASE="https://raw.githubusercontent.com/jitsi-contrib/prosody-plugins"

# Only what upstream does NOT already ship.
#
# `token_affiliation` used to live here and no longer does: jitsi-contrib renamed
# it to `token_affiliation_legacy` with the note "Jitsi officially provides a
# token_affiliation module now." The upstream module ships inside the jitsi/prosody
# image under /prosody-plugins, so enabling it in XMPP_MUC_MODULES is enough and
# nothing needs fetching. `make verify-modules` confirms it is actually present.
#
# If you ever need the old behaviour back, the legacy module is at
#   token_affiliation_legacy/mod_token_affiliation_legacy.lua
# and it additionally wants `wait_for_host_disable_auto_owners = true` on the MUC
# component. Note the filename changed too, not just the directory.
MODULES=(
  "token_no_wildcard/mod_token_no_wildcard.lua"
)

mkdir -p "$DEST"

if [[ "$REF" == "main" ]]; then
  cat >&2 <<'WARN'
warning: fetching from `main`, which is a moving target.
         Read the .pinned file this writes, then set PROSODY_PLUGINS_REF to that
         SHA and re-run, so that your deployment is reproducible.
WARN
fi

for module in "${MODULES[@]}"; do
  name="$(basename "$module")"
  echo "fetching ${name} @ ${REF}"
  curl -fsSL "${BASE}/${REF}/${module}" -o "${DEST}/${name}"
done

# Record content hashes rather than a commit SHA. api.github.com is not reachable
# from every environment, and a content hash is the stronger claim anyway: it
# pins what you are actually running, not what a ref pointed at when you looked.
{
  printf '# written by fetch-contrib-modules.sh\n'
  printf 'ref=%s\n' "$REF"
  printf 'fetched=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for module in "${MODULES[@]}"; do
    name="$(basename "$module")"
    printf 'sha256=%s  %s\n' "$(sha256sum "${DEST}/${name}" | cut -d' ' -f1)" "$name"
  done
} > "${DEST}/.pinned"

echo
echo "Modules in ${DEST}:"
ls -1 "${DEST}"/*.lua 2>/dev/null || echo "  (none)"
echo
cat "${DEST}/.pinned"
echo
echo "Read them before you trust them, and diff these hashes on every refetch."
