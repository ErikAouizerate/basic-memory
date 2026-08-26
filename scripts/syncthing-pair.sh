#!/usr/bin/env bash
# Pair a client device (e.g. laptop) and share the notes folder with it.
# Runs inside the syncthing container: `docker compose exec -T syncthing sh
# /scripts/syncthing-pair.sh <client-device-id>` locally, or the Dokploy
# service terminal (`sh /scripts/syncthing-pair.sh <client-device-id>`).
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <client-device-id>" >&2
  exit 1
fi

CONFIG_DIR="${STHOMEDIR:-/var/syncthing/config}"
FOLDER_FILE="$CONFIG_DIR/syncthing-folder-id"
CLIENT_ID="$1"

[[ -f "$FOLDER_FILE" ]] || {
  echo "no folder configured yet — run syncthing-setup.sh first" >&2
  exit 1
}
FOLDER_ID=$(cat "$FOLDER_FILE")

cli() {
  key=$(sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' "$CONFIG_DIR/config.xml")
  syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
}

cli config devices add --device-id "$CLIENT_ID" --name laptop
cli config folders "$FOLDER_ID" devices add --device-id "$CLIENT_ID"

printf 'paired %s — folder "%s" shared\n' "$CLIENT_ID" "$FOLDER_ID"
