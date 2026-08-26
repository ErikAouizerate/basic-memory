#!/usr/bin/env bash
# Pair a client device (e.g. laptop) and share the notes folder with it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <client-device-id>" >&2
  exit 1
fi

CLIENT_ID="$1"
FOLDER_FILE=".syncthing-folder-id"

[[ -f "$FOLDER_FILE" ]] || {
  echo "no folder configured yet — run scripts/syncthing-setup.sh first" >&2
  exit 1
}
FOLDER_ID=$(cat "$FOLDER_FILE")

cli() {
  docker compose exec -T syncthing sh -c '
    key=$(sed -n "s/.*<apikey>\\([^<]*\\)<\\/apikey>.*/\\1/p" /var/syncthing/config/config.xml)
    exec syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
  ' sh "$@"
}

cli config devices add --device-id "$CLIENT_ID" --name laptop
cli config folders "$FOLDER_ID" devices add --device-id "$CLIENT_ID"

printf 'paired %s — folder "%s" shared\n' "$CLIENT_ID" "$FOLDER_ID"
