# Syncthing notes sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user edit the Basic Memory notes on their laptop (Obsidian/VS Code) with bidirectional real-time sync, via a Syncthing container sharing the `notes` volume.

**Architecture:** A `syncthing` service in `docker-compose.yml` mounts the existing `notes` named volume plus a new `syncthing-config` volume (device identity). The management GUI binds loopback only and is never published; all server-side configuration runs through `syncthing cli` via `docker compose exec`. Three helper scripts cover device ID retrieval, folder setup, and pairing.

**Tech Stack:** docker compose, Syncthing official image (`syncthing/syncthing:latest`, v2.x), bash.

## Global Constraints

- No `ports:` mappings in `docker-compose.yml` (Dokploy shared host, user policy). `expose:` only where another service needs the port — here nothing does, so neither.
- Syncthing must run as UID/GID 1000 (`PUID`/`PGID`), matching `appuser` in the basic-memory image, so both containers can write the `notes` volume.
- Management GUI bound to `127.0.0.1:8384` (`STGUIADDRESS`), never published or exposed.
- Folder ID is random, generated once and stored in gitignored `.syncthing-folder-id` at repo root.
- All code, comments, and documentation in English (user convention). Chat in French.
- Verified CLI behaviors (Syncthing v2.1.3): `cli` requires `--gui-address` + `--gui-apikey` flags; the API key is read from `config.xml` (`<apikey>`); `config devices add` and `config folders <id> devices add` are idempotent (rc=0 on duplicates); `config folders add` fails with rc=1 on duplicate ID; `config folders list` prints one ID per line; `config devices list` prints one ID per line (includes self); `show system` prints JSON with `"myID"`; `config devices <id> addresses add <value>` takes a positional address.

---

### Task 1: Add the `syncthing` service to docker-compose.yml

**Files:**
- Modify: `docker-compose.yml` (service block + volumes section)
- Modify: `.gitignore`

**Interfaces:**
- Produces: a running `syncthing` container on the compose `default` network, with `/var/syncthing/config` (identity) and `/var/syncthing/data` (notes) volumes; container reachable at `syncthing` from other containers on the network (used by the Task 2 E2E test).

- [ ] **Step 1: Add the service block**

Insert between the `basic-memory` service (ends line 38 with `restart: unless-stopped`) and the `gateway` service (line 40):

```yaml
  syncthing:
    image: syncthing/syncthing:latest
    hostname: basic-memory
    environment:
      # Match the basic-memory image's appuser (UID/GID 1000) so both
      # containers can write the shared notes volume.
      PUID: "1000"
      PGID: "1000"
      # Management UI stays on loopback; it is never published or exposed.
      STGUIADDRESS: "127.0.0.1:8384"
    volumes:
      # Device identity (key.pem) + config.xml. Must persist across
      # redeploys or the device ID changes and clients have to re-pair.
      - syncthing-config:/var/syncthing/config
      # Same notes volume as basic-memory; the shared folder is
      # /var/syncthing/data/basic-memory.
      - notes:/var/syncthing/data
    restart: unless-stopped
```

- [ ] **Step 2: Register the new volume**

In the `volumes:` section (line 53-55), add `syncthing-config:`:

```yaml
volumes:
  notes:
  config:
  syncthing-config:
```

- [ ] **Step 3: Add the folder-ID state file to .gitignore**

`.gitignore` currently contains only `.env`. Append:

```
.syncthing-folder-id
```

- [ ] **Step 4: Validate and start locally**

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d syncthing
sleep 5
docker compose exec syncthing ls -la /var/syncthing/config/config.xml
```

Expected: first command exits 0 (valid compose); config.xml exists and is owned by `1000 1000`; `docker compose logs syncthing` shows no startup errors.

- [ ] **Step 5: Verify identity persistence and loopback GUI**

```bash
docker compose exec syncthing ps aux | grep syncthing
```

Expected: the daemon process runs as uid 1000 (first column `1000`), and the command line has no `-gui-address` override (the entrypoint applied `STGUIADDRESS=127.0.0.1:8384` — check `docker compose exec syncthing env | grep STGUI`).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .gitignore
git commit -m "feat: add syncthing service for notes volume sync"
```

---

### Task 2: Add the syncthing helper scripts and prove end-to-end sync

**Files:**
- Create: `scripts/syncthing-device-id.sh`
- Create: `scripts/syncthing-setup.sh`
- Create: `scripts/syncthing-pair.sh`

**Interfaces:**
- Consumes: the `syncthing` service from Task 1 (running locally).
- Produces:
  - `scripts/syncthing-device-id.sh` — prints the server device ID (stdout, one line).
  - `scripts/syncthing-setup.sh` — creates the shared folder if needed, writes `.syncthing-folder-id`, prints `server device ID:` and `folder ID:`.
  - `scripts/syncthing-pair.sh <client-device-id>` — idempotently adds the client device and shares the folder with it; exit 1 with usage on missing arg, exit 1 with guidance if `.syncthing-folder-id` is absent.

All three scripts share one `cli()` helper that runs `syncthing cli` inside the container, reading the API key from the container's own `config.xml`:

```bash
cli() {
  docker compose exec -T syncthing sh -c '
    key=$(sed -n "s/.*<apikey>\\([^<]*\\)<\\/apikey>.*/\\1/p" /var/syncthing/config/config.xml)
    exec syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$key" "$@"
  ' sh "$@"
}
```

- [ ] **Step 1: Write `scripts/syncthing-device-id.sh`**

```bash
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

cli show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
```

- [ ] **Step 2: Write `scripts/syncthing-setup.sh`**

```bash
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
```

- [ ] **Step 3: Write `scripts/syncthing-pair.sh`**

```bash
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
```

- [ ] **Step 4: Make executable and lint if shellcheck is available**

```bash
chmod +x scripts/syncthing-device-id.sh scripts/syncthing-setup.sh scripts/syncthing-pair.sh
command -v shellcheck >/dev/null && shellcheck scripts/syncthing-*.sh || echo "shellcheck not installed — skip"
```

- [ ] **Step 5: Verify the scripts against the running container**

The `syncthing` service from Task 1 must be up:

```bash
./scripts/syncthing-device-id.sh
./scripts/syncthing-setup.sh
./scripts/syncthing-setup.sh   # second run: idempotent, same IDs
./scripts/syncthing-pair.sh    # expect usage error, exit 1
./scripts/syncthing-pair.sh INVALID-ID-...  # expect syncthing error, exit 1
```

Expected: device ID is 56 chars in `XXXXXXX-XXXXXXX-...-XXXXXXX` form; both setup runs print the identical `folder ID` (16 hex chars); `.syncthing-folder-id` contains it; `scripts/syncthing-pair.sh` without args exits 1 with usage; with a malformed ID exits 1 with a syncthing validation error.

- [ ] **Step 6: End-to-end sync test with a throwaway second device**

The compose network is named `basic-memory_default` (project name from the repo directory). Run a second Syncthing container on that network to act as the laptop:

```bash
rm -rf /tmp/opencode/syncthing-laptop /tmp/opencode/syncthing-laptop-data
docker run -d --name syncthing-laptop --network basic-memory_default \
  -e PUID=1000 -e PGID=1000 \
  -v /tmp/opencode/syncthing-laptop:/var/syncthing/config \
  -v /tmp/opencode/syncthing-laptop-data:/var/syncthing/data \
  syncthing/syncthing:latest
sleep 4
```

Read the laptop's own device ID and API key from its config.xml:

```bash
LAPTOP_KEY=$(sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' /tmp/opencode/syncthing-laptop/config.xml)
LAPTOP_ID=$(docker exec syncthing-laptop syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$LAPTOP_KEY" show system | sed -n 's/.*"myID"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
SERVER_ID=$(./scripts/syncthing-device-id.sh)
FOLDER_ID=$(cat .syncthing-folder-id)
echo "server=$SERVER_ID laptop=$LAPTOP_ID folder=$FOLDER_ID"
```

Pair both sides and pin static addresses (no discovery needed — DNS names resolve on the network):

```bash
./scripts/syncthing-pair.sh "$LAPTOP_ID"
docker exec syncthing-laptop syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$LAPTOP_KEY" config devices add --device-id "$SERVER_ID" --name server
docker exec syncthing-laptop syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$LAPTOP_KEY" config devices "$SERVER_ID" addresses add tcp://syncthing:22000
docker exec syncthing-laptop syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$LAPTOP_KEY" config folders add --id "$FOLDER_ID" --label basic-memory --path /var/syncthing/data/basic-memory
docker exec syncthing-laptop syncthing cli --gui-address 127.0.0.1:8384 --gui-apikey "$LAPTOP_KEY" config folders "$FOLDER_ID" devices add --device-id "$SERVER_ID"
```

Wait for initial sync, then verify the notes arrived on the "laptop" and a reverse write flows back:

```bash
sleep 20
ls /tmp/opencode/syncthing-laptop-data/basic-memory        # expect the project tree (e.g. main/)
echo "reverse test" > /tmp/opencode/syncthing-laptop-data/basic-memory/reverse-test.md
sleep 10
docker compose exec basic-memory ls /app/data/basic-memory  # expect reverse-test.md present
```

Expected: `ls` shows the existing project directory (inherited from the image into the notes volume); `reverse-test.md` appears in the server container after the second sleep (folder sync is bidirectional, `sendreceive`). Remove the throwaway container and the test file:

```bash
docker rm -f syncthing-laptop
docker compose exec basic-memory rm /app/data/basic-memory/reverse-test.md
```

- [ ] **Step 7: Commit**

```bash
git add scripts/syncthing-device-id.sh scripts/syncthing-setup.sh scripts/syncthing-pair.sh
git commit -m "feat: add syncthing pairing scripts"
```

---

### Task 3: Document the sync workflow in the README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: scripts and service from Tasks 1-2.

- [ ] **Step 1: Update the file/role table**

In the table at lines 31-37, update the `docker-compose.yml` row and add a scripts row:

```markdown
| `docker-compose.yml` | The MCP server (never exposed) + the Caddy gateway + the Syncthing sync service |
| `scripts/syncthing-*.sh` | Device ID, folder setup and pairing for Syncthing |
```

- [ ] **Step 2: Add a "Syncing the notes (Syncthing)" section**

Insert after the "Connect a client" section (ends line 98) and before "Rotating the token":

```markdown
## Syncing the notes (Syncthing)

The `syncthing` service shares the `notes` volume with the MCP server, so you
can edit the knowledge base on your laptop (Obsidian, VS Code, ...) and have
changes flow both ways in real time. The management UI is bound to loopback
and never published; all server-side configuration happens through the CLI
via `docker compose exec`. Nothing needs to be exposed: Syncthing devices
connect outbound or fall back to its encrypted relay network.

### First-time pairing

1. Make sure the stack is up: `docker compose -f docker-compose.yml
   -f docker-compose.local.yml up -d`.
2. On the server: `./scripts/syncthing-setup.sh` — creates the shared folder
   (random ID, stored in `.syncthing-folder-id`) and prints the server device
   ID and the folder ID.
3. On the laptop: install the Syncthing desktop app, add the server device
   (paste the printed device ID), add a folder with the **same folder ID**
   pointing at e.g. `~/Notes/basic-memory` (send & receive), and share it
   with the server device.
4. On the server: `./scripts/syncthing-pair.sh <laptop-device-id>` — adds the
   laptop and shares the folder back.
5. The first sync pushes the existing notes to the laptop; after that both
   sides edit in real time. Conflicts are kept as `.sync-conflict-*` files.

`./scripts/syncthing-device-id.sh` prints the server device ID again if you
lost it.

### Troubleshooting

```bash
# Is the daemon healthy?
docker compose exec syncthing syncthing cli --gui-address 127.0.0.1:8384 \
  --gui-apikey "$(docker compose exec -T syncthing sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' /var/syncthing/config/config.xml)" show system
```

The API key is always read from the container's own `config.xml`; the helper
scripts wrap this, so prefer them over raw `cli` calls.
```

- [ ] **Step 3: Verify the docs and the smoke test still pass**

```bash
docker compose -f docker-compose.yml config --quiet
set -a && source .env && set +a
BASE_URL=http://localhost:8080 ./scripts/smoke-test.sh
```

Expected: compose valid; all smoke-test checks `ok` (the gateway and basic-memory services are unaffected).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document syncthing notes sync"
```

---

## Self-Review Notes

- **Spec coverage:** compose service + volume (Task 1), three scripts with exact names/behavior from the spec (Task 2), README section (Task 3), `.env.example` untouched, no ports published, GUI loopback, PUID/PGID 1000, random folder ID stored gitignored — all spec requirements map to a task.
- **Placeholders:** none — every step carries concrete commands, expected output, and full script bodies.
- **Type consistency:** `FOLDER_FILE=".syncthing-folder-id"`, `FOLDER_PATH="/var/syncthing/data/basic-memory"`, folder type `sendreceive`, and the `cli()` helper are identical across the three scripts; `syncthing-setup.sh` output labels match the E2E step (`cat .syncthing-folder-id`, `syncthing-device-id.sh`).
