#!/usr/bin/env bash
# Print the server's Syncthing device ID (input for pairing a client).
set -euo pipefail
cd "$(dirname "$0")/.."

cli() {
  docker compose exec -T syncthing sh -c '
    key=$(sed -n "s/.*<apikey>\\([^<]*\\)<\\/apikey>.*/\\1/p" /var/syncthing/config/config.xml)
    exec syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
  ' sh "$@"
}

DEVICE_ID=$(cli show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[[ -n "$DEVICE_ID" ]] || {
  echo "failed to read the server device ID" >&2
  exit 1
}

printf '%s\n' "$DEVICE_ID"
