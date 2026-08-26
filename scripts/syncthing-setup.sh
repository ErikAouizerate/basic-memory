#!/usr/bin/env bash
# Create the shared Syncthing folder (once) and print the IDs needed to pair.
# Runs inside the syncthing container: `docker compose exec -T syncthing sh
# /scripts/syncthing-setup.sh` locally, or the Dokploy service terminal
# (`sh /scripts/syncthing-setup.sh`).
set -euo pipefail

CONFIG_DIR="${STHOMEDIR:-/var/syncthing/config}"
FOLDER_FILE="$CONFIG_DIR/syncthing-folder-id"
FOLDER_PATH="/var/syncthing/data/basic-memory"
FOLDER_LABEL="basic-memory"

cli() {
  key=$(sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' "$CONFIG_DIR/config.xml")
  syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
}

DEVICE_ID=$(cli show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[[ -n "$DEVICE_ID" ]] || {
  echo "failed to read the server device ID" >&2
  exit 1
}

FOLDER_ID=""
[[ -f "$FOLDER_FILE" ]] && FOLDER_ID=$(cat "$FOLDER_FILE")

if [[ -z "$FOLDER_ID" ]]; then
  # No state file (lost volume, re-created container): reuse the sole
  # existing folder instead of minting an orphan, else generate a fresh ID.
  EXISTING=$(cli config folders list)
  if [[ -n "$EXISTING" ]] && [[ "$(printf '%s\n' "$EXISTING" | wc -l)" -eq 1 ]]; then
    FOLDER_ID="$EXISTING"
  else
    FOLDER_ID=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  fi
  printf '%s\n' "$FOLDER_ID" > "$FOLDER_FILE"
fi

if ! cli config folders list | grep -qx "$FOLDER_ID"; then
  cli config folders add --id "$FOLDER_ID" --label "$FOLDER_LABEL" --path "$FOLDER_PATH" --type sendreceive
fi

printf 'server device ID: %s\n' "$DEVICE_ID"
printf 'folder ID:        %s\n' "$FOLDER_ID"
