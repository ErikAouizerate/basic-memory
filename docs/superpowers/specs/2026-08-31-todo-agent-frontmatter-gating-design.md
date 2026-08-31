# Todo agent — frontmatter gating & concurrent edits — design

Date: 2026-08-31
Status: approved in conversation, pending spec review

## Context

The `todo-agent` currently processes **every** new/modified note in `todo/`
the cycle after it appears, and embeds the deletion checkbox
`- [ ] Traité — supprimable` at the bottom of the note inside the
`## Résultat` section. This design gates processing on an explicit opt-in
flag living in the YAML frontmatter, moves the deletion flag next to it,
drops the poll interval to 5 s, and hardens writes against simultaneous
edits by the user.

Decisions from the brainstorming session:

1. The two flags live in the **YAML frontmatter** as booleans (`process`,
   `deletable`), rendered as toggles by Obsidian's properties UI.
2. After a note is processed, the agent resets `process` back to `false`
   (the flag is a "send" button; re-ticking re-processes because the content
   hash changes).
3. Existing notes are migrated automatically; the old body checkbox
   `- [x] Traité — supprimable` remains accepted for deletion (compat), but
   new notes use the frontmatter flag only.
4. Injected frontmatter: `title` (derived from the filename), `type: todo`,
   `tags: [todo]`, `process: false`, `deletable: false`.

## Target frontmatter

Injected by merge only — existing keys are never overwritten:

```yaml
---
title: <filename without .md>
type: todo
tags:
  - todo
process: false
deletable: false
---
```

## Lifecycle of a todo note

1. **New file** (no frontmatter): the agent injects the target frontmatter,
   then **waits** — notes with `process: false` (or absent) are skipped.
2. **`process: true`**: the agent runs the LLM, writes the result note(s),
   appends the `## Résultat` section (result links only, **no checkbox**),
   resets `process` to `false`, and records the post-write content hash.
3. **Re-tick after processing**: content hash differs from the stored one →
   reprocessed, `## Résultat` replaced in place.
4. **`deletable: true`**: the note is deleted (file + state entry). The old
   body marker `- [x] Traité — supprimable` is still honored (compat).
5. Deletion is prioritized over processing; `type: hub` notes are ignored.

## Concurrent-edit safety

The agent and the user edit the same files. Today `process_note` rewrites the
whole file, so a user save during a multi-second LLM call would be clobbered.
Hardening, all in `notes.py`:

- **Merge-only frontmatter**: the agent only sets missing keys. The full-file
  rewrite becomes: fresh re-read → rebuild frontmatter block with merged keys
  → fresh body + result section.
- **Re-read before write**: every write re-reads the file and rebuilds from
  the latest disk state, so a save between the agent's read and write is
  never lost. Frontmatter injection is idempotent; if the file changed since
  the initial read, it is rebuilt from the fresh content and retried next
  cycle.
- **Settling period**: a file is only touched when its mtime is at least
  `STABLE_SECONDS` (3 s) old, so frontmatter is not injected mid-typing. With
  a 5 s poll, an actively edited file is skipped until stable.
- **Atomic write**: temp file + `os.replace`, so neither the basic-memory
  watcher nor Syncthing sees a torn write.
- **Inherent limit** (documented): an editor holding a stale buffer can later
  overwrite the injected frontmatter (same class of problem as Syncthing
  conflicts). This is self-healing: the file reverts to no-frontmatter and
  the agent re-injects on the next stable cycle.

## Migration (one-time on upgrade)

For every `todo/*.md` (except `type: hub`): inject missing frontmatter keys
only, and when a migrated note still carries the old `- [ ] Traité —
supprimable` checkbox in `## Résultat`, remove that line (it moves to
`deletable: false` in the frontmatter). Migration obeys the same settling,
re-read-before-write and atomic-write rules.

## Poll interval

`POLL_INTERVAL` default drops from 60 s to 5 s in `docker-compose.yml` and in
`main.py`. `.env.example`, `README.md` and `AGENTS.md` are updated to match.

## Files touched

- `agent/notes.py` — frontmatter injection/merge, boolean parsing, settling
  period, atomic write, migration, checkbox removed from result section.
- `agent/main.py` — `process` gating in the cycle, reset to `false` after
  processing, deletion via `deletable` frontmatter + compat marker,
  `POLL_INTERVAL` default 5.
- `docker-compose.yml` — `POLL_INTERVAL` default 5.
- `.env.example`, `README.md`, `AGENTS.md` — doc updates.
- `agent/tests/` — see Testing.

## Testing

Stdlib `unittest` in a `python:3.12-slim` container. Coverage:

- `test_notes.py`: frontmatter injection (new file, partial-frontmatter
  merge, no overwrite), boolean parsing, settling-period skip, re-read race
  (file changed between read and write → not clobbered), migration removing
  the old body checkbox, `ensure_result_section` without the checkbox.
- `test_main.py`: `process: false` not processed / `process: true`
  processed and reset to `false`, deletion via `deletable: true`, compat old
  body marker, re-tick reprocess, hub still skipped.