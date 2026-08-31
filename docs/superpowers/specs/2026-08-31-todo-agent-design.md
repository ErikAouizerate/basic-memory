# Todo agent — autonomous processing of todo notes — design

Date: 2026-08-31
Status: approved by user (design review), pending spec review

## Goal

An autonomous Python service (`todo-agent`) that polls the Basic Memory notes
volume every minute and processes new or modified notes in the `todo/` folder.
A todo note is a request written by the user from any synced device (Syncthing).
The agent understands the request via an LLM (OpenCode Zen, OpenAI-compatible),
does research when the note contains URLs, produces a result note in the
knowledge base following existing conventions (e.g. `tools/`), and reports back
inside the todo note with a link to the result and a checkbox the user ticks to
approve deletion of the todo note.

## Constraints

- Dokploy shared host: no `ports:` mappings in `docker-compose.yml`.
- The agent runs in its own container on the server; it must not depend on the
  gateway (no token, no MCP client). It reads and writes markdown directly on
  the `notes` volume, exactly like the `syncthing` service already does; the
  basic-memory server's file watcher reindexes changes automatically.
- No LLM key is baked into the repository. All API configuration comes from
  environment variables (`LLM_API_KEY` is required at startup). Production
  (Dokploy) and local runs supply their own key. The user has an OpenCode Zen
  key locally at `~/.local/share/opencode/auth.json` (providers `opencode` /
  `opencode-go`, model `opencode-go/deepseek-v4-flash`); the same key must be
  set in Dokploy Environment variables for production.
- Communication with the user is French; note content is French. Code and
  commit messages are English (repo convention).
- No test framework (repo convention): verify with `docker compose config
  --quiet` and a manual `--once` run against a test note.

## User decisions (design review)

1. LLM backend: **OpenCode Zen**, OpenAI-compatible, model
   `opencode-go/deepseek-v4-flash`.
2. Read/write path: **direct files** on the `notes` volume (no gateway).
3. Processing unit: **one note = one request** (list notes are processed as a
   batch; the agent produces one result per item).
4. When the user ticks the checkbox `- [x] Traité — supprimable`, the agent
   **deletes the note** at the next cycle.
5. A modified (already processed) note is **reprocessed** (result updated, not
   duplicated).

## Architecture

```
┌─────────────┐  1 min   ┌──────────────────────────┐   OpenAI-compatible   ┌────────────┐
│ todo/ (notes)│◄────────│  todo-agent (Python)      │──────────────────────►│  OpenCode  │
│ volume notes │  scan    │  main.py llm.py notes.py │   POST /v1/chat/      │  Zen API   │
└─────────────┘          │  state.py                │   completions          └────────────┘
        ▲                └────────────┬─────────────┘
        │  writes markdown directly    │  state JSON (per-note hash)
        │  (result, link + checkbox)   │  → volume todo-agent-state
        └──────────────────────────────┘
```

### docker-compose.yml — new `todo-agent` service

```yaml
  todo-agent:
    image: python:3.12-slim
    working_dir: /app
    environment:
      LLM_API_KEY: ${LLM_API_KEY:?LLM_API_KEY is required}
      LLM_BASE_URL: ${LLM_BASE_URL:-https://opencode.ai/zen/v1}
      LLM_MODEL: ${LLM_MODEL:-opencode-go/deepseek-v4-flash}
      POLL_INTERVAL: ${POLL_INTERVAL:-60}
      NOTES_DIR: /app/data/basic-memory
      STATE_DIR: /app/state
    volumes:
      - ./agent:/app:ro
      - notes:/app/data
      - todo-agent-state:/app/state
    restart: unless-stopped
```

- No `depends_on`: the agent reads files directly and needs no other service.
- No `ports:` (Dokploy policy).
- `agent/` mounted read-only; the only writable paths are `notes` and the state
  volume.
- New volume `todo-agent-state` for the state file — deliberately **not**
  synced by Syncthing (it lives outside the `notes` volume), so the agent's
  bookkeeping never causes sync conflicts and the user never sees it.

### State file (state.py)

`STATE_DIR/state.json`:

```json
{
  "todo/to-test.md": {
    "hash": "sha256…",
    "processed_at": "2026-08-31T10:00:00Z",
    "result_permalink": "main/tools/super-simple-software-factory"
  }
}
```

Change detection: sha256 content hash of each `todo/*.md` (top-level, no
recursion). A note whose hash differs from the recorded one (or has no entry)
is processed. After the agent finishes its own writes, it re-records the hash
of the post-edit note so its own modifications never trigger a reprocess loop.

Ignored: `*.sync-conflict-*` files and notes with `type: hub` frontmatter
(e.g. `TODO — General Hub`).

## Lifecycle of a todo note

1. New or modified note detected (hash mismatch) → read frontmatter + body.
2. URL harvesting (regex over the body) + fetch via `httpx` (e.g. GitHub repo
   README through `raw.githubusercontent.com`, falling back to the HTML page).
   Fetched text is included in the LLM context as enrichment.
3. LLM call (OpenAI-compatible chat completions, `response_format:
   json_object`) with a system prompt describing:
   - the task (interpret the request, produce a result),
   - knowledge base conventions (structure of `tools/` notes: frontmatter
     `type: tool`, `url`, `tags`, `kind`, an Observations section with
     `[category]` markers, a Relations section with `[[wikilinks]]`),
   - the output JSON contract:
     `{title, folder, url, tags, kind, observations, relations, summary}`.
   Example input (the "To test" note): the URL of
   `disler/super-simple-software-factory` → output a French evaluation note in
   `folder: tools`.
4. Result written as a new note in the chosen folder (e.g. `tools/`), matching
   existing conventions. On a reprocess, the existing result note is updated
   (find/replace of the linked section) instead of duplicated.
5. Feedback appended to the todo note:
   ```markdown
   ## Résultat
   - [[Title of the result note]]
   - [ ] Traité — supprimable
   ```
6. State updated (hash of the post-edit todo note, `result_permalink`).

## Checkbox → deletion

Each cycle, before change detection: any `todo/*.md` containing the ticked
checkbox `- [x] Traité — supprimable` is deleted (file + state entry). The
result note stays in the knowledge base, so nothing is lost. Ticking the box is
a modification, but deletion has priority over reprocessing, so it never
triggers a reprocess.

## Robustness

- Errors are isolated per note (LLM timeout, network failure, invalid JSON):
  log the error, do not update the state → retried next cycle.
- LLM responses are validated locally against the JSON contract; on failure the
  note is retried next cycle.
- `--once` CLI flag runs a single cycle and exits (manual verification and
  debugging).
- Poll interval configurable via `POLL_INTERVAL` (seconds, default 60).
- All writes to the notes volume are append/create (no in-place edits to the
  todo note beyond appending the result section; reprocess edits only the
  result note and the result section).

## Files

New:
- `agent/main.py` — loop, cycle orchestration, `--once` flag.
- `agent/llm.py` — OpenAI-compatible client (`httpx`), JSON contract.
- `agent/notes.py` — markdown read/write, frontmatter, URL harvesting/fetch,
  result-section append/update.
- `agent/state.py` — state JSON load/save, hash tracking.
- `agent/requirements.txt` — `httpx`.

Modified:
- `docker-compose.yml` — `todo-agent` service + `todo-agent-state` volume.
- `.env.example` — `LLM_API_KEY=` (OpenCode Zen key, paste the value) +
  commented optional `LLM_BASE_URL`, `LLM_MODEL`, `POLL_INTERVAL`.
- `README.md` — "Agent todo" section: env vars, lifecycle, deletion by
  checkbox, `--once` verification.
- `AGENTS.md` — mention the service, the dedicated state volume, ignored
  `*.sync-conflict-*` / `type: hub`, and that `LLM_API_KEY` is required.

## Verification

No test framework (repo convention):
- `docker compose -f docker-compose.yml config --quiet`.
- Manual: create a test todo note, run
  `docker compose exec todo-agent python /app/main.py --once`, check the result
  note in `tools/` and the checkbox appended; tick the box, run `--once` again,
  check deletion.
- Existing `scripts/smoke-test.sh` unchanged.

## Out of scope

- Auto-updating the `TODO — General Hub` table with plan statuses.
- Subfolder recursion under `todo/`.
- Agent-driven LLM tool-calling loops (single-shot JSON contract instead).
- Deleting result notes when a todo note is deleted (results are kept as
  knowledge).