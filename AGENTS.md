# AGENTS.md

## What this is

- No application code: a Dokploy deployment of the upstream `basic-memory`
  image (MCP server) + Caddy gateway + Syncthing notes sync. All behavior
  lives in `docker-compose.yml`, `Caddyfile`, `scripts/`.
- Notes live in the `notes` named volume (`/app/data/basic-memory` in the
  container); the index/`memory.db` live in the `config` volume. Don't
  search for source code — there is none.

## Local workflow

- Start: `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d`
  (the local override publishes the gateway on `127.0.0.1:8080` only).
- Validate: `docker compose -f docker-compose.yml config --quiet`
- Verify: `set -a && source .env && set +a && BASE_URL=http://localhost:8080 ./scripts/smoke-test.sh`
- The MCP server logs to a file, not stdout:
  `docker compose exec basic-memory tail -50 /app/.basic-memory/basic-memory.log`

## Environment gotchas

- Compose interpolates the **shell environment over `.env`**: if
  `MCP_TOKEN` is exported in the shell, `docker compose up` uses that value,
  not `.env`. To recreate a service with the `.env` token:
  `env -u MCP_TOKEN docker compose -f docker-compose.yml -f docker-compose.local.yml up -d <service>`.
- Rotating `MCP_TOKEN` requires recreating the gateway — Caddy bakes the
  token into the Caddyfile matcher at startup.
- `.env` is required (`${MCP_TOKEN:?}`) and gitignored; never commit it.

## Syncthing (load-bearing details)

- UI bound to `127.0.0.1:8384`, never published; all server-side config goes
  through `scripts/syncthing-{device-id,setup,pair}.sh`, which wrap
  `syncthing cli` with the API key read from the container's `config.xml`.
- `hostname: syncthing` must NOT become `basic-memory` again: the embedded
  DNS would register a duplicate A record and the gateway's
  `basic-memory:8000` dials become nondeterministic.
- The `entrypoint:` override is required: the official image entrypoint only
  chowns `$HOME`, so a fresh `syncthing-config` volume stays root-owned and
  the daemon crash-loops.
- `PUID`/`PGID` 1000 must match the basic-memory image's `appuser` — both
  containers share the `notes` volume.
- `.syncthing-folder-id` (gitignored) holds the pairing folder ID; it is
  not in git, so a Dokploy re-clone loses it and `setup.sh` mints a new ID.

## Dokploy policy

- Never add `ports:` to `docker-compose.yml` — the shared Dokploy host
  routes through Traefik internally. Ports to localhost belong in
  `docker-compose.local.yml` only.

## Conventions

- Communication with the user: **French**. Code, comments, docs, tests:
  **English**.
- Commits directly on `main`, conventional prefixes (`feat:`/`fix:`/`docs:`/`chore:`),
  push to `origin` (GitLab).
- No test framework: verify with `compose config` + `smoke-test.sh`;
  syncthing changes with the pairing scripts.
- `README.md` is the deployment doc; update it when compose/scripts behavior
  changes (docs are committed with the code).
