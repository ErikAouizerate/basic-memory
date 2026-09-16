# Basic Memory MCP — authenticated remote gateway

A deployment of the [Basic Memory](https://github.com/basicmachines-co/basic-memory)
MCP server over plain HTTP, protected by a static bearer token.

Any MCP-capable client (Claude Code, Codex, Cursor, a custom agent) can read,
write and search a Basic Memory knowledge base from any machine — no local
Python, no Markdown files on the client, no stdio pipe.

## There is no application code here, on purpose

Basic Memory is abbreviated **`bm`** (product, CLI and this repo).

`basic-memory` already ships a complete MCP server that speaks streamable HTTP:

```
basic-memory mcp --transport streamable-http --host 0.0.0.0 --port 8000 --path /mcp
```

It natively exposes 21 tools — `write_note`, `read_note`, `edit_note`,
`search_notes`, `build_context`, `recent_activity`, `move_note`, `delete_note`,
project management, canvas. The `bm tool ...` CLI is itself a wrapper around
those same functions, so re-implementing them on top of the CLI would only add
subprocess overhead and human-formatted output.

The one thing upstream does not provide is authentication: the server is built
with `FastMCP(name=..., lifespan=...)` and no `auth=` provider, and FastMCP 3
dropped the `FASTMCP_SERVER_AUTH` environment variable from v2. Anything that
can reach the port can read and write the whole knowledge base.

So this repository is a deployment, not a program:

| File | Role |
| --- | --- |
| `Caddyfile` | Checks `Authorization: Bearer` and proxies to the MCP server |
| `docker-compose.yml` | The MCP server (never exposed) + the Caddy gateway + the Syncthing sync service |
| `docker-compose.override.yml` | Dev-only override, auto-merged by `docker compose up`: publishes the gateway on `127.0.0.1:8080` and binds the named volumes to `./volumes/` |
| `.env.example` | The one secret you must set |
| `scripts/smoke-test.sh` | Proves auth works and the tools are reachable |
| `scripts/syncthing-*.sh` | Device ID, folder setup, pairing and direct QUIC/443 for Syncthing |
| `scripts/Dockerfile` | Bakes `scripts/syncthing-*.sh` into the syncthing image at `/scripts` (a repo bind mount would be emptied by Dokploy's re-clone) |

The `basic-memory` service publishes no ports. Only `gateway` is routable, and
it forwards nothing without a valid token. TLS and the public domain are
handled upstream by Traefik (via Dokploy).

## Run it locally

```bash
# Dokploy provides this network on the server; create it once for local runs.
docker network create dokploy-network

cp .env.example .env
echo "MCP_TOKEN=$(openssl rand -base64 32)" > .env

# Create ./volumes/* and set ownership (see "Local volumes" below).
sh scripts/init-local-volumes.sh

# docker-compose.override.yml is merged automatically — no -f flag needed.
# --build (re)builds the syncthing image with the helper scripts baked in.
docker compose up -d --build

set -a && source .env && set +a
BASE_URL=http://localhost:8080 ./scripts/smoke-test.sh
```

Expected output:

```
Testing http://localhost:8080/mcp
  ok   unauthenticated request rejected with 401
  ok   invalid token rejected with 401
  ok   initialize succeeded (server: Basic Memory)
  ok   tools/list returned 21 tools, including write_note
All checks passed.
```

The smoke test fails loudly if an unauthenticated request is *not* rejected, so
it doubles as the security check.

## Local volumes

Locally, the named volumes of `docker-compose.yml` are replaced (dev-only) by
bind mounts on `volumes/<volume-name>` in this project — `volumes/notes`,
`volumes/config`, `volumes/syncthing-config`, `volumes/todo-agent-state` — so
the data is directly editable and navigable from the host file manager, an
editor, or Obsidian. `volumes/` is gitignored.

Two folders must be owned by UID 1000 (the `appuser` of the basic-memory image
and syncthing's `PUID`/`PGID`); bind mounts get no Docker "copy up" or chown:

```bash
sh scripts/init-local-volumes.sh   # idempotent; run before every compose up
```

The script creates the folders, chowns `notes` and `config` to UID 1000 when
run as root (no-op when your UID is already 1000), and warns with the exact
`sudo chown` command otherwise. Existing named volumes from a previous local
stack are abandoned when you switch to bind mounts — copy the data into
`volumes/` first if you want to keep it. In production Dokploy keeps the named
volumes, untouched by this override.

## Development (devcontainer)

This project ships a devcontainer based on the same two compose files, so the
containerized dev environment matches `docker compose up` exactly. It attaches
to a dedicated `dev` service (`mcr.microsoft.com/devcontainers/base:ubuntu`)
while the whole stack runs: the MCP server, the gateway, syncthing and the
todo agent. The repo — `volumes/` included — is the workspace.

- **VS Code**: open the repo and "Reopen in Container". `.env` must exist on
  the host (the compose stack runs on the host Docker), and the host needs the
  `dokploy-network` external network.
- **CLI**: `npm install -g @devcontainers/cli`, then
  `devcontainer up --workspace-folder .`.
- **Without VS Code**: `sh scripts/init-local-volumes.sh && docker compose up -d --build`.

Inside the container the smoke test targets the gateway by service name:

```bash
set -a && source .env && set +a
BASE_URL=http://gateway:8080 ./scripts/smoke-test.sh
```

## Deploy on Dokploy

1. Create a **Compose** application pointing at this repository.
2. In **Environment**, set `MCP_TOKEN` to a freshly generated value
   (`openssl rand -base64 32`). The deployment refuses to start without it.
3. In **Domains**, add your domain targeting service **`gateway`**, container
   port **8080**. Dokploy generates the Traefik labels and the certificate.
4. Deploy, then verify against the public URL:

   ```bash
   BASE_URL=https://<your-domain> MCP_TOKEN=<token> ./scripts/smoke-test.sh
   ```

   This also confirms Traefik forwards the `Authorization` header intact.

Dokploy uses `docker-compose.yml` alone — `docker-compose.override.yml` is never
applied there (nothing is published on the host, and the `./volumes/` bind
mounts and the `dev` service stay local).

## Connect a client

```bash
claude mcp add --transport http basic-memory https://<your-domain>/mcp \
  --header "Authorization: Bearer $MCP_TOKEN"
```

Then `/mcp` inside Claude Code should show the server connected with its tools
listed. Codex and Cursor take the same URL and header in their MCP config.

## Syncing the notes (Syncthing)

The `syncthing` service shares the `notes` volume with the MCP server, so you
can edit the knowledge base on your laptop (Obsidian, VS Code, ...) and have
changes flow both ways in real time. The management UI is bound to loopback
and never published; all configuration happens through the helper scripts,
which run **inside the container** — they are baked into the syncthing image
at `/scripts` at build time, so they survive Dokploy re-cloning the repository.

### First-time pairing

1. Make sure the stack is up.
2. On the server, open the Syncthing service terminal (Dokploy UI → service
   → Terminal) and run `sh /scripts/syncthing-setup.sh` — it creates the
   shared folder (random ID, stored in the `syncthing-config` volume) and
   prints the server device ID and the folder ID. If a folder already
   exists it reuses it, so re-runs are safe.
3. On the laptop: install the Syncthing desktop app, add the server device
   (paste the printed device ID), add a folder with the **same folder ID**
   pointing at e.g. `~/Notes/basic-memory` (send & receive), and share it
   with the server device.
4. Back in the server terminal: `sh /scripts/syncthing-pair.sh
   <laptop-device-id>` — adds the laptop and shares the folder back.
5. The first sync pushes the existing notes to the laptop; after that both
   sides edit in real time. On a conflict, Syncthing keeps both versions as
   `*.sync-conflict-*` files next to the original.

`sh /scripts/syncthing-device-id.sh` prints the server device ID again if
you lost it.

### Same pairing locally

Locally the same scripts run through compose instead of the Dokploy
terminal:

```bash
docker compose exec -T syncthing sh /scripts/syncthing-setup.sh
docker compose exec -T syncthing sh /scripts/syncthing-pair.sh <laptop-device-id>
```

### Connectivity

By default the server publishes **no port**: devices reach it through
Syncthing's encrypted relay network (slower transfers, zero host exposure —
only devices paired by device ID can sync, and the relay only ever sees
encrypted data). The notes volume is never exposed in clear text; the
management UI is never published.

For direct connections without opening a new port, expose the server on
**UDP 443** through Syncthing's QUIC transport. QUIC is already
end-to-end encrypted (device-ID authentication on top of TLS 1.3), so no
extra tunnel or TLS terminator is needed — the sync protocol has no
`https://` mode, but a hostname works in a QUIC address. This satisfies a
443-only firewall rule; relays remain the fallback.

Server-side setup:

1. Free UDP 443 by disabling Traefik's HTTP/3 (the only other consumer of
   that port): in `/etc/dokploy/traefik/traefik.yml` remove the `http3:`
   block under the `websecure` entrypoint, then restart `dokploy-traefik`.
   Browsers fall back to HTTP/2 — no functional loss.
2. In the Syncthing service terminal, run
   `sh /scripts/syncthing-direct.sh` — it adds `quic://0.0.0.0:443` to the
   listen addresses (idempotent; `default` and the relay fallback stay).
3. Dokploy UI → service → Ports: publish `443` → target `443`, protocol
   **UDP** (this lives in the Dokploy UI, not in `docker-compose.yml`).
4. On the host, `ufw allow 443/udp`.
5. Point a hostname at the server: A record `syncthing.<your-domain>` →
   VPS IP.

Then, on each device (desktop or mobile — QUIC is native everywhere), set
the server device's addresses to `quic://syncthing.<your-domain>:443,
dynamic`. Syncthing keeps the QUIC connection (there is no reachable TCP
path to upgrade to) and stays off the relay network.

If HTTP/3 must remain enabled on the Dokploy host, the fallback is QUIC on
UDP **22000** instead (same security, but a new firewall port): point the
script's `QUIC_ADDRESS` at `quic://0.0.0.0:22000` and publish `22000/udp`.

### Troubleshooting

```bash
# Is the daemon healthy? (the API key lives in the container's config.xml)
docker compose exec syncthing syncthing cli --gui-address 127.0.0.1:8384 \
  --gui-apikey "$(docker compose exec -T syncthing sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' /var/syncthing/config/config.xml)" show system
```

The helper scripts wrap this, so prefer them over raw `cli` calls.

## Todo agent (autonomous processing of todo notes)

The `todo-agent` service polls the notes volume every 5 seconds (`POLL_INTERVAL`)
and processes notes in the `todo/` folder whose `process` flag is ticked. A todo
note is a request written from any synced device; the agent interprets it via the
OpenCode Zen API, writes the result into the knowledge base and reports back
inside the todo note.

Variables (all optional except `LLM_API_KEY`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` | — (required) | OpenCode Zen API key (`https://opencode.ai/zen/v1`) |
| `LLM_BASE_URL` | `https://opencode.ai/zen/v1` | OpenAI-compatible base URL |
| `LLM_MODEL` | `deepseek-v4-flash` | Model name |
| `POLL_INTERVAL` | `5` | Seconds between cycles |
| `NOTES_DIR` | `/app/data/basic-memory` | Project root, where `todo/` lives (mounted `notes` volume); local dev overrides it to `/app/data/basic-memory/main` in `docker-compose.override.yml` |
| `STATE_DIR` | `/app/state` | State file location (`todo-agent-state` volume) |

A new note dropped in `todo/` is detected within a few seconds and annotated
with action and memory frontmatter (`title`, `type: todo`, `tags: [todo]`,
`process: false`, `deletable: false`, `memory_class: working`,
`lifecycle: raw`, `source: human`), which render as toggles in Obsidian.
Existing frontmatter keys are never overwritten. Result notes generated by the
agent use `lifecycle: candidate`, a folder-derived memory class, and
`source: external` when based on a URL or `source: agent` otherwise; they do
not use the obsolete `kind` field.

Lifecycle of a todo note:

1. Drop a note in `todo/` (e.g. a URL to evaluate). The agent adds the action
   frontmatter at the next cycle. Nothing is processed until you tick `process`.
2. Tick `process: true` in the frontmatter. At the next cycle the agent fetches
   the URLs for context, asks the LLM for a JSON result, writes the result note
   to the requested folder (tool evaluations land in `tools/`), appends a
   `## Résultat` section with `[[wikilinks]]`, and resets `process` to `false`.
   Re-ticking `process` reprocesses the note (result updated, not duplicated).
3. Tick `deletable: true` in the frontmatter and the agent deletes the todo
   note at the next cycle. The result note stays in the knowledge base. The
   legacy body checkbox `- [x] Traité — supprimable` is still honored.
4. Editing an already-processed todo note and re-ticking `process` reprocesses it.

Notes are processed top-level only; `*.sync-conflict-*` files and notes with
`type: hub` frontmatter (e.g. `TODO — General Hub`) are ignored. The agent's
bookkeeping lives in the `todo-agent-state` volume — never in the synced notes
folder, so it cannot cause sync conflicts.

Concurrent edits are handled merge-only: the agent writes atomically (temp file
+ rename), re-reads the note immediately before writing, and only touches a
file that has been stable for a few seconds — so it never clobbers an edit you
make while it works. If your editor later overwrites the injected frontmatter
with a stale buffer, the agent re-injects it at the next cycle.

Verify a single cycle manually:

```bash
docker compose exec todo-agent python /agent/main.py --once
```

## Rotating the token

Change `MCP_TOKEN` in Dokploy, redeploy, and update every client. There is a
single token: rotating it disconnects all of them at once.

## Two image quirks this compose works around

Both were found by running the image, and both fail silently if you copy
upstream's `docs/Docker.md` verbatim:

- **The image's default command is `--transport sse`**, the legacy transport.
  `docker-compose.yml` overrides `command:` with `--transport streamable-http`,
  which is what current MCP clients expect.
- **State does not live where upstream's compose example mounts it.** By
  default the server writes `config.json`, `memory.db` and the ~130MB fastembed
  model cache to `$HOME/.basic-memory` (`/home/appuser/.basic-memory`), not to
  `/app/.basic-memory`. Mounting only the latter means the entire index is
  rebuilt and the model re-downloaded on every redeploy. The fix is
  `BASIC_MEMORY_CONFIG_DIR=/app/.basic-memory`, pointing the server at the path
  the volume actually covers. Do not mount a volume straight onto
  `/home/appuser/.basic-memory` instead: that path does not exist in the image,
  so Docker creates it root-owned and the server crash-loops on a
  `PermissionError` at startup.

## Troubleshooting

`basic-memory mcp` logs to a file, not stdout — `docker compose logs
basic-memory` is empty during normal operation. The real log is inside the
config volume:

```bash
docker compose exec basic-memory tail -50 /app/.basic-memory/basic-memory.log
```

`GET /health` on the gateway answers `200` without a token, for liveness
probes. Everything else without a valid token gets `401` plus a
`WWW-Authenticate` header.

## Tuning

`BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED` (default `"true"` in
`docker-compose.yml`) controls semantic search. Leaving it on downloads a
~130MB ONNX embedding model on first index and keeps it resident. On a small
VPS, set it to `"false"` — search falls back to full-text, which is less
accurate but far cheaper.

## Known limits

These follow directly from using a proxy rather than in-process auth. They are
accepted trade-offs, not bugs:

1. **No authorization granularity.** Whoever holds the token gets everything:
   read, write, `delete_note`, `move_note`. There are no scopes and no per-tool
   restrictions. A read-only token would require wiring a FastMCP
   `TokenVerifier` into the server process.
2. **Comparison is not constant-time.** Caddy's header matcher does an ordinary
   string comparison. With a 32-byte random token a remote timing attack is not
   realistic — which is exactly why the token must come from `openssl rand` and
   never be chosen by hand.
3. **Single replica only.** The streamable-http transport is stateful
   (`Mcp-Session-Id`). Scaling `basic-memory` past one replica would break
   sessions without sticky routing.
4. **No claude.ai / Claude Desktop custom connector.** Those require OAuth 2.1
   with Dynamic Client Registration. This design targets clients that can send
   an arbitrary header.
