# AGENTS.md

## What this is

- No application code: a Dokploy deployment of the upstream `basic-memory`
  image (MCP server) + Caddy gateway + Syncthing notes sync + a small
  `todo-agent` service. All behavior lives in `docker-compose.yml`,
  `Caddyfile`, `agent/`, `scripts/`.
- Notes live in the `notes` named volume (`/app/data/basic-memory` in the
  container); the index/`memory.db` live in the `config` volume. Don't
  search for source code — there is none (except `agent/`, which is the
  todo-agent's stdlib-only Python code).

## Local workflow

- Start: `sh scripts/init-local-volumes.sh && docker compose up -d`
  (`docker-compose.override.yml` is auto-merged — no `-f` flag. It publishes
  the gateway on `127.0.0.1:8080` only and bind-mounts the named volumes to
  `./volumes/<volume-name>`, so notes/config are editable on the host).
- Devcontainer: open the repo in VS Code and "Reopen in Container". It attaches
  to the `dev` service of `docker-compose.override.yml` while the whole stack
  runs; inside it, smoke-test via `BASE_URL=http://gateway:8080`.
- Local volumes: `./volumes/` is gitignored and owned by UID 1000 for `notes`
  and `config` (see `scripts/init-local-volumes.sh`).
- Validate: `docker compose -f docker-compose.yml config --quiet`
- Verify: `set -a && source .env && set +a && BASE_URL=http://localhost:8080 ./scripts/smoke-test.sh`
- The MCP server logs to a file, not stdout:
  `docker compose exec basic-memory tail -50 /app/.basic-memory/basic-memory.log`

## Environment gotchas

- Compose interpolates the **shell environment over `.env`**: if
  `MCP_TOKEN` is exported in the shell, `docker compose up` uses that value,
  not `.env`. To recreate a service with the `.env` token:
  `env -u MCP_TOKEN docker compose up -d <service>`.
- Rotating `MCP_TOKEN` requires recreating the gateway — Caddy bakes the
  token into the Caddyfile matcher at startup.
- `.env` is required (`${MCP_TOKEN:?}`) and gitignored; never commit it.

## Syncthing (load-bearing details)

- UI bound to `127.0.0.1:8384`, never published; the helper scripts
  (`scripts/syncthing-{device-id,setup,pair}.sh`) run **inside the
  container** — invoke with `docker compose exec -T syncthing sh
  /scripts/...` locally, or `sh /scripts/...` in the Dokploy service
  terminal (`./scripts:/scripts:ro` is mounted by compose). They wrap
  `syncthing cli` with the API key read from the container's `config.xml`.
- The pairing folder ID is stored in the `syncthing-config` volume
  (`/var/syncthing/config/syncthing-folder-id`), not in git; if it is lost
  `setup.sh` reuses the sole existing folder before minting a new ID.
- `hostname: syncthing` must NOT become `basic-memory` again: the embedded
  DNS would register a duplicate A record and the gateway's
  `basic-memory:8000` dials become nondeterministic.
- The `entrypoint:` override is required: the official image entrypoint only
  chowns `$HOME`, so a fresh `syncthing-config` volume stays root-owned and
  the daemon crash-loops.
- `PUID`/`PGID` 1000 must match the basic-memory image's `appuser` — both
  containers share the `notes` volume.
- No `ports:` on the service by default (relay-only connectivity); the README
  documents how to publish `22000` for direct connections.

## Dokploy policy

- Never add `ports:` to `docker-compose.yml` — the shared Dokploy host
  routes through Traefik internally. Ports to localhost belong in
  `docker-compose.override.yml` only.

## Todo agent (load-bearing details)

- The `todo-agent` service polls the `notes` volume (`todo/` folder) every
  `POLL_INTERVAL` seconds (default 60) and processes new/modified notes via the
  OpenCode Zen API (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` env vars; image
  `python:3.12-slim`, stdlib only, no dependencies to install).
- It writes markdown directly on the `notes` volume (like syncthing) — the
  basic-memory file watcher reindexes. Its state file lives in the
  `todo-agent-state` volume (`/app/state/state.json`), deliberately NOT in the
  notes volume, so it never syncs and never conflicts.
- `*.sync-conflict-*` files and `type: hub` notes (e.g. `TODO — General Hub`)
  are ignored.
- A ticked checkbox `- [x] Traité — supprimable` in a todo note makes the agent
  delete that note at the next cycle; a modified processed note is reprocessed
  (result updated, not duplicated). The agent records the hash after its own
  writes, so its edits never trigger a reprocess loop.
- Same env gotcha as `MCP_TOKEN`: compose interpolates the shell environment
  over `.env`, so an exported `LLM_API_KEY` wins over `.env`.
- Manual verification: `docker compose exec todo-agent python /agent/main.py --once`;
  unit tests run with
  `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`.

## Conventions

- Communication with the user: **French**. Code, comments, docs, tests:
  **English**.
- Basic Memory is abbreviated **`bm`** (product, CLI, repo).
- Commits directly on `main`, conventional prefixes (`feat:`/`fix:`/`docs:`/`chore:`),
  push to `origin` (GitLab).
- No test framework: verify with `compose config` + `smoke-test.sh`;
  syncthing changes with the pairing scripts; `agent/` uses stdlib `unittest`
  run in a `python:3.12-slim` container (see Todo agent above).
- `README.md` is the deployment doc; update it when compose/scripts behavior
  changes (docs are committed with the code).
