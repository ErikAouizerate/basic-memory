# Syncthing sync for the notes volume — design

Date: 2026-08-26
Status: approved by user (design review), pending spec review

## Goal

Let the user edit the Basic Memory notes (volume `notes`, mounted at
`/app/data` in the `basic-memory` service) on their laptop — in Obsidian or
VS Code — with bidirectional, real-time sync back to the Dokploy server.
UI of Syncthing is never exposed; all server-side configuration happens via
the `syncthing cli` inside the container.

## Constraints

- Dokploy shared host: no `ports:` mappings in `docker-compose.yml`
  (per user policy "Devcontainer + Docker Compose Pattern for Dokploy
  Deployments"). This means the Syncthing GUI port (8384) and the sync
  protocol port (22000) are not published.
- The `basic-memory` image runs as `appuser` with UID/GID 1000
  (Dockerfile defaults). The `notes` volume inherits that ownership, so the
  Syncthing container must write as UID/GID 1000.
- No GUI exposure anywhere: `STGUIADDRESS` bound to loopback inside the
  container, no `expose:`, no `ports:`.
- User choice: random folder ID, not fixed.

## Architecture

### docker-compose.yml — new `syncthing` service

```yaml
  syncthing:
    image: syncthing/syncthing:latest
    hostname: basic-memory
    environment:
      PUID: "1000"
      PGID: "1000"
      STGUIADDRESS: "127.0.0.1:8384"
    volumes:
      - syncthing-config:/var/syncthing/config
      - notes:/var/syncthing/data
    restart: unless-stopped
```

- `syncthing-config` named volume: persists `key.pem` (the device identity)
  and `config.xml` across Dokploy redeploys. A fresh volume would generate a
  new device ID and force re-pairing on every redeploy.
- `notes` volume shared with `basic-memory`. The folder shared with the
  laptop is `/var/syncthing/data/basic-memory` (the whole project tree).
  `memory.db` lives in the separate `config` volume, so no SQLite file is
  ever synced.
- No `depends_on` needed: both services mount the same volume and neither
  depends on the other's startup.

### volumes: section

```yaml
volumes:
  notes:
  config:
  syncthing-config:
```

## Pairing workflow (server side, 100% CLI)

Verified against Syncthing ≥1.27 docs/forums: `syncthing cli` configures
devices, folders and sharing through the local REST API. The API key is
generated on first run and stored in the config volume; `docker compose
exec` runs the CLI inside the container so no API key handling is needed.

Three helper scripts in `scripts/` (alpine image has no `jq`, so use
`grep`/`sed`):

1. `scripts/syncthing-device-id.sh`
   - `docker compose exec syncthing syncthing cli show system` → extract
     `myID`. Prints the server device ID (input for the laptop side).
2. `scripts/syncthing-setup.sh`
   - Creates the shared folder if missing:
     `syncthing cli config folders add --id <random-id> --label basic-memory
     --path /var/syncthing/data/basic-memory --type sendreceive`
   - Random folder ID generated from `/dev/urandom` (e.g. hex digest).
   - Prints server device ID + folder ID (laptop needs both).
3. `scripts/syncthing-pair.sh <laptop-device-id>`
   - `syncthing cli config devices add --device-id <laptop-id> --name laptop`
   - `syncthing cli config folders <folder-id> devices add
     --device-id <laptop-id>`
   - Prints confirmation; initial sync then flows server → laptop.

Device connection: since 22000 is not published, the laptop initiates the
connection; if that fails, Syncthing falls back to global relays (slower,
still E2E encrypted). Accepted trade-off on a shared Dokploy host.

## Client side (laptop, documented in README)

1. Install Syncthing desktop app.
2. Add device with the server's device ID (from `syncthing-device-id.sh`
   or `syncthing-setup.sh`).
3. Add folder: same ID as printed by `syncthing-setup.sh`, path e.g.
   `~/Notes/basic-memory`, type send & receive, share it with the server
   device.
4. Run `scripts/syncthing-pair.sh <laptop-device-id>` on the server.
5. First sync: existing notes appear in the local folder; afterwards both
   sides edit in real time. Conflicts produce `.sync-conflict-*` files.

## Security

- GUI bound to `127.0.0.1:8384` inside the container, no published ports,
  no `expose:` — unreachable from outside the container network namespace.
  Nothing needs to reach it: all management goes through `docker compose
  exec`.
- Device IDs are the only trust anchor; relay traffic is E2E encrypted.
- The notes volume already contains sensitive data (AI memory) — no new
  exposure added.

## Docs

README.md gains a "Synchroniser les notes" section (pairing workflow above,
troubleshooting: `docker compose exec syncthing syncthing cli ...`).
`.env.example` unchanged.

## Out of scope

- Publishing port 22000 for direct connections (blocked by policy).
- Syncthing on phone/other devices (same workflow, later).
- GUI password / TLS for the UI (UI never reachable anyway).
- Basic Memory Cloud sync (paid, different mechanism).
