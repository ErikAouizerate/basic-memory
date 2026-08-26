# Basic Memory MCP — authenticated remote gateway

A deployment of the [Basic Memory](https://github.com/basicmachines-co/basic-memory)
MCP server over plain HTTP, protected by a static bearer token.

Any MCP-capable client (Claude Code, Codex, Cursor, a custom agent) can read,
write and search a Basic Memory knowledge base from any machine — no local
Python, no Markdown files on the client, no stdio pipe.

## There is no application code here, on purpose

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
| `docker-compose.local.yml` | Local-only override that publishes the gateway on `127.0.0.1:8080` |
| `.env.example` | The one secret you must set |
| `scripts/smoke-test.sh` | Proves auth works and the tools are reachable |
| `scripts/syncthing-*.sh` | Device ID, folder setup and pairing for Syncthing |

The `basic-memory` service publishes no ports. Only `gateway` is routable, and
it forwards nothing without a valid token. TLS and the public domain are
handled upstream by Traefik (via Dokploy).

## Run it locally

```bash
# Dokploy provides this network on the server; create it once for local runs.
docker network create dokploy-network

cp .env.example .env
echo "MCP_TOKEN=$(openssl rand -base64 32)" > .env

docker compose -f docker-compose.yml -f docker-compose.local.yml up -d

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

Dokploy uses `docker-compose.yml` alone — the `docker-compose.local.yml`
override is never applied there, so nothing is published on the host.

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
which run **inside the container** (the repo's `scripts/` directory is
mounted read-only at `/scripts`).

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

By default the server publishes **no port**: the laptop reaches it through
Syncthing's encrypted relay network (slower transfers, zero host exposure —
only devices paired by device ID can sync, and the relay only ever sees
encrypted data). The notes volume is never exposed in clear text; the
management UI is never published.

To switch to direct connections, publish the sync port and open the VPS
firewall — add `22000:22000/tcp` and `22000:22000/udp` to the `syncthing`
service in `docker-compose.yml`, then `ufw allow 22000/tcp` and
`ufw allow 22000/udp` on the host. Relays remain the fallback.

### Troubleshooting

```bash
# Is the daemon healthy? (the API key lives in the container's config.xml)
docker compose exec syncthing syncthing cli --gui-address 127.0.0.1:8384 \
  --gui-apikey "$(docker compose exec -T syncthing sed -n 's/.*<apikey>\([^<]*\)<\/apikey>.*/\1/p' /var/syncthing/config/config.xml)" show system
```

The helper scripts wrap this, so prefer them over raw `cli` calls.

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
