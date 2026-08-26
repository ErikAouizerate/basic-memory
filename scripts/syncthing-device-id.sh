#!/usr/bin/env bash
# Print the server's Syncthing device ID (input for pairing a client).
# Runs inside the syncthing container: `docker compose exec -T syncthing sh
# /scripts/syncthing-device-id.sh` locally, or the Dokploy service terminal
# (`sh /scripts/syncthing-device-id.sh`).
set -euo pipefail

CONFIG_DIR="${STHOMEDIR:-/var/syncthing/config}"

cli() {
  key=$(sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' "$CONFIG_DIR/config.xml")
  syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
}

DEVICE_ID=$(cli show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[[ -n "$DEVICE_ID" ]] || {
  echo "failed to read the server device ID" >&2
  exit 1
}

printf '%s\n' "$DEVICE_ID"
