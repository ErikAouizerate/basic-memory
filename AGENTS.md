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
  `POLL_INTERVAL` seconds (default 5) and processes notes via the OpenCode Zen
  API (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` env vars; image
  `python:3.12-slim`, stdlib only, no dependencies to install).
- New todo notes get action and memory frontmatter injected at the next cycle
  (`title`, `type: todo`, `tags: [todo]`, `process: false`, `deletable: false`,
  `memory_class: working`, `lifecycle: raw`, `source: human`) — merged in,
  existing keys never overwritten. Processing is gated on `process: true`; after
  processing the agent resets it to `false` (re-tick = reprocess). Generated
  result notes use `candidate` lifecycle metadata and omit the obsolete `kind`.
- A note with `deletable: true` in its frontmatter is deleted at the next
  cycle; the legacy body checkbox `- [x] Traité — supprimable` is still
  honored. `*.sync-conflict-*` files and `type: hub` notes (e.g.
  `TODO — General Hub`) are ignored.
- Writes are atomic (temp file + rename), the file is re-read immediately
  before writing, and only notes stable for 3 s are touched, so the agent
  never clobbers a concurrent user edit; if a stale editor buffer overwrites
  the injected frontmatter, it is re-injected at the next cycle.
- It writes markdown directly on the `notes` volume (like syncthing) — the
  basic-memory file watcher reindexes. Its state file lives in the
  `todo-agent-state` volume (`/app/state/state.json`), deliberately NOT in the
  notes volume, so it never syncs and never conflicts.
- The agent records the hash after its own writes, so its edits never trigger
  a reprocess loop.
- Same env gotcha as `MCP_TOKEN`: compose interpolates the shell environment
  over `.env`, so an exported `LLM_API_KEY` wins over `.env`.
- Manual verification: `docker compose exec todo-agent python /agent/main.py --once`;
  unit tests run with
  `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`.

## Shared Basic Memory notes

- Managed notes in the `notes` volume (project `main`) are shared agent memory.
  Modify them only through the Basic Memory MCP tools; never edit those
  Markdown files directly. The `todo-agent` is the deliberate exception
  because it owns its filesystem processing workflow.
- New and managed notes use `memory_class` (`episodic`, `semantic`,
  `procedural`, or `working`), `lifecycle` (`raw`, `candidate`, `canonical`,
  `superseded`, or `archived`), and `source` (`human`, `agent`, or `external`).
- `reviewed_at` is used only after review or promotion. `valid_until` is used
  only for knowledge that can expire. Both dates use `YYYY-MM-DD`.
- `kind` is no longer used for new notes. Agents may write raw or candidate
  captures, proposals, and working state, but canonical promotion requires
  explicit user or curator authorization. Canonical notes are not rewritten or
  deleted without that authorization.

### Retrieval by memory class

- `episodic` — search by date, event, participants, and context; include
  historical states when asked about what happened.
- `semantic` — prefer `lifecycle: canonical`; check `valid_until` and reject
  expired values; never silently merge conflicting candidates.
- `procedural` — load active canonical guides, policies, and skills directly
  when the task requires them; do not treat them as ordinary historical
  search results.
- `working` — restrict retrieval to the current task or project; do not carry
  stale working notes into unrelated tasks.

These retrieval and write rules are conventions, not ACLs: agents sharing the
same MCP credentials can technically write anywhere. Frontmatter is governance,
not a security boundary.

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
