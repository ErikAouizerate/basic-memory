# Syncthing plus the helper scripts baked in at /scripts. Built from this
# directory (the compose service uses `build: ./scripts`), so the build context
# stays tiny and the scripts survive Dokploy re-cloning the repository.
FROM syncthing/syncthing:latest

COPY syncthing-*.sh /scripts/
