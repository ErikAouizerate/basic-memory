#!/usr/bin/env bash
# Enable a direct QUIC connection to the server on UDP 443 (the sync default
# is port 22000) so paired devices sync over a direct encrypted link instead
# of the relay network. Idempotent: re-running is a no-op once the address is
# in the listen list. The default listeners (TCP 22000 + relay fallback) are
# left untouched; UDP 443 must be published to the container and allowed in
# the host firewall for the address to be reachable.
# Runs inside the syncthing container: `docker compose exec -T syncthing sh
# /scripts/syncthing-direct.sh` locally, or the Dokploy service terminal
# (`sh /scripts/syncthing-direct.sh`).
set -euo pipefail

CONFIG_DIR="${STHOMEDIR:-/var/syncthing/config}"
QUIC_ADDRESS="quic://0.0.0.0:443"

cli() {
  key=$(sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' "$CONFIG_DIR/config.xml")
  syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
}

present=0
for i in $(cli config options raw-listen-addresses list); do
  if [[ "$(cli config options raw-listen-addresses "$i" get)" == "$QUIC_ADDRESS" ]]; then
    present=1
  fi
done

if [[ "$present" -eq 0 ]]; then
  cli config options raw-listen-addresses add "$QUIC_ADDRESS"
  echo "added $QUIC_ADDRESS to listen addresses"
else
  echo "$QUIC_ADDRESS already in listen addresses"
fi

echo "listen addresses:"
for i in $(cli config options raw-listen-addresses list); do
  printf '  %s\n' "$(cli config options raw-listen-addresses "$i" get)"
done