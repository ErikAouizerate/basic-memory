#!/usr/bin/env sh
# Local dev only: create the bind-mount targets used by docker-compose.override.yml.
#
# Each named volume in docker-compose.yml is mapped to ./volumes/<volume-name>
# in the override, so the data is directly editable and navigable on the host.
# Bind mounts do not get Docker's "copy up" or chown, so the folders must exist
# with the right ownership BEFORE `docker compose up`.
#
# The basic-memory container runs as appuser (UID 1000) and syncthing as
# PUID/PGID 1000; ./volumes/notes and ./volumes/config must therefore be owned
# by UID 1000. ./volumes/syncthing-config is chowned by syncthing's entrypoint
# and ./volumes/todo-agent-state is written as root.
#
# Idempotent: safe to re-run before every `docker compose up`.
#
#   sh scripts/init-local-volumes.sh
set -eu

VOL_DIR="volumes"

mkdir -p \
	"$VOL_DIR/notes" \
	"$VOL_DIR/config" \
	"$VOL_DIR/syncthing-config" \
	"$VOL_DIR/todo-agent-state"

case "$(id -u)" in
	0)
		chown -R 1000:1000 "$VOL_DIR/notes" "$VOL_DIR/config"
		;;
	1000)
		# Running user already matches the container UID: no-op.
		;;
	*)
		printf '%s\n' \
			"WARNING: $VOL_DIR/notes and $VOL_DIR/config must be owned by UID 1000" \
			"for the basic-memory and syncthing containers to write them." \
			"Run: sudo chown -R 1000:1000 $VOL_DIR/notes $VOL_DIR/config" >&2
		;;
esac