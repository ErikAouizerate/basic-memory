#!/usr/bin/env bash
# Create the shared Syncthing folder (once) and print the IDs needed to pair.
set -euo pipefail
cd "$(dirname "$0")/.."

FOLDER_FILE=".syncthing-folder-id"
FOLDER_PATH="/var/syncthing/data/basic-memory"
FOLDER_LABEL="basic-memory"

cli() {
  docker compose exec -T syncthing sh -c '
    key=$(sed -n "s/.*<apikey>\\([^<]*\\)<\\/apikey>.*/\\1/p" /var/syncthing/config/config.xml)
    exec syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
  ' sh "$@"
}

DEVICE_ID=$(cli show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

FOLDER_ID=""
[[ -f "$FOLDER_FILE" ]] && FOLDER_ID=$(cat "$FOLDER_FILE")

if [[ -z "$FOLDER_ID" ]]; then
  FOLDER_ID=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  printf '%s\n' "$FOLDER_ID" > "$FOLDER_FILE"
fi

if ! cli config folders list | grep -qx "$FOLDER_ID"; then
  cli config folders add --id "$FOLDER_ID" --label "$FOLDER_LABEL" --path "$FOLDER_PATH" --type sendreceive
fi

printf 'server device ID: %s\n' "$DEVICE_ID"
printf 'folder ID:        %s\n' "$FOLDER_ID"
