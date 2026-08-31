"""Todo agent: poll todo/ notes and process new/modified requests via LLM."""
import argparse
import fcntl
import os
import sys
import time
from pathlib import Path

import llm
import notes
import state as state_mod

SYSTEM_PROMPT = """You are the autonomous todo-processing agent for a Basic Memory knowledge base. The user drops request notes in the `todo/` folder; you must interpret each request and produce knowledge-base notes as the result.

Rules:
- Write note content in French.
- A todo note is one request. A list note (multiple items) is processed as a batch: produce one result note per item.
- For a tool or software evaluation, the result goes in the `tools/` folder and follows the existing conventions: frontmatter with `url` and `tags`, an `## Observations` section with category markers such as [usage], [status], [decision], [alternative], [lesson], and an `## Relations` section linking to [[Tools Catalog — Software & Services]] and related notes.
- `folder` must be one of: tools, devops-ia, guidelines, carriere, hardware, persona, todo. Default: tools for tool/software evaluations.
- `note_type` for a tool note is `tool`.
- Observations and relations are optional lists of strings; observations use the `[category] text #tag` format.

Return STRICT JSON (no markdown, no commentary) with this schema:
{
  "results": [
    {
      "folder": "tools",
      "title": "Human readable title",
      "note_type": "tool",
      "url": "https://... or null",
      "tags": ["tag1", "tag2"],
      "kind": "fact",
      "observations": ["[usage] ...", "[status] ..."],
      "relations": ["[[Tools Catalog — Software & Services]]"],
      "body": "Markdown body: what the tool is, how to integrate it in the user's workflow (French)."
    }
  ]
}"""


def _acquire_lock(state_dir: Path):
    """Take an exclusive, non-blocking flock on state_dir/lock.

    Returns the open file (the lock is held while it stays open) or None when
    another process currently runs a cycle. Serializes the poller against a
    manual `--once` so the same note is never processed twice concurrently.
    """
    lock_path = Path(state_dir) / "lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def process_note(path: Path, notes_dir: Path, client: llm.ChatClient) -> list[str]:
    front, body, frontmatter = notes.read_note(path)
    if notes.is_hub(front):
        return []
    context = ""
    urls = notes.extract_urls(body)
    if urls:
        fetched = []
        for u in urls[:3]:
            txt = notes.fetch_text(u)
            if txt:
                fetched.append(f"URL: {u}\n{txt}")
        if fetched:
            context = "\n\n".join(fetched)
    user = f"TODO NOTE:\n{path.name}\n\n{body}"
    if context:
        user += f"\n\nFETCHED CONTENT (for reference):\n{context[:30000]}"
    data = client.complete_json(SYSTEM_PROMPT, user)
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        raise llm.LLMError(f"LLM response missing 'results' list: {data!r}")
    titles = []
    for result in results:
        if not isinstance(result, dict) or not result.get("title"):
            raise llm.LLMError(f"Invalid result entry: {result!r}")
        notes.write_result_note(notes_dir, result.get("folder", "tools"), result)
        titles.append(result["title"])
    # Re-emit the original frontmatter block verbatim (list-valued fields
    # like tags only survive if untouched); the result section replaces any
    # previous one.
    new_body = frontmatter + notes.ensure_result_section(body, titles)
    path.write_text(new_body, encoding="utf-8")
    return titles


def run_once(notes_dir: Path, state_dir: Path, client: llm.ChatClient) -> tuple[int, int]:
    lock = _acquire_lock(state_dir)
    if lock is None:
        return 0, 0  # another instance holds the cycle lock
    try:
        return _run_once(notes_dir, state_dir, client)
    finally:
        lock.close()


def _run_once(notes_dir: Path, state_dir: Path, client: llm.ChatClient) -> tuple[int, int]:
    """One cycle: delete ticked notes, then process new/modified ones.

    Returns (handled, failed); failed notes are logged and retried next cycle.
    """
    store = state_mod.StateStore(state_dir)
    todo_files = notes.list_todo_files(notes_dir)

    deleted = 0
    for path in todo_files:
        try:
            _, body, _ = notes.read_note(path)
            if notes.has_ticked_checkbox(body):
                rel = str(path.relative_to(notes_dir))
                path.unlink()
                store.remove(rel)
                deleted += 1
        except Exception as e:
            print(f"[todo-agent] ERROR {path.name}: {e}", flush=True)

    processed = 0
    failed = 0
    for path in todo_files:
        if not path.exists():
            continue
        try:
            front, _, _ = notes.read_note(path)
            if notes.is_hub(front):
                continue
            rel = str(path.relative_to(notes_dir))
            current = state_mod.sha256_file(path)
            if current == store.hash_for(rel):
                continue
            process_note(path, notes_dir, client)
            store.set_processed(rel, state_mod.sha256_file(path))
            processed += 1
        except llm.LLMError as e:
            print(f"[todo-agent] ERROR {path.name}: {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"[todo-agent] ERROR {path.name}: {e}", flush=True)
            failed += 1
    return deleted + processed, failed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="todo-agent")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args(argv)

    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        print("LLM_API_KEY is required", file=sys.stderr)
        return 2
    notes_dir = Path(os.environ.get("NOTES_DIR", "/app/data/basic-memory/main"))
    state_dir = Path(os.environ.get("STATE_DIR", "/app/state"))
    base_url = os.environ.get("LLM_BASE_URL", "https://opencode.ai/zen/v1")
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    interval = int(os.environ.get("POLL_INTERVAL", "60"))
    client = llm.ChatClient(api_key=api_key, base_url=base_url, model=model)

    if args.once:
        handled, failed = run_once(notes_dir, state_dir, client)
        print(f"[todo-agent] cycle: {handled} handled, {failed} failed", flush=True)
        return 1 if failed else 0
    print(f"[todo-agent] watching {notes_dir}/todo every {interval}s", flush=True)
    while True:
        try:
            handled, failed = run_once(notes_dir, state_dir, client)
            if handled or failed:
                print(f"[todo-agent] cycle: {handled} handled, {failed} failed", flush=True)
        except Exception as e:
            print(f"[todo-agent] cycle error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())