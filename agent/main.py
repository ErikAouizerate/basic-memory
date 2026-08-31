"""Todo agent: poll todo/ notes and process new/modified requests via LLM."""
import argparse
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


def process_note(path: Path, notes_dir: Path, client: llm.ChatClient) -> list[str]:
    front, body = notes.read_note(path)
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
    new_body = notes.ensure_result_section(body, titles)
    path.write_text(new_body, encoding="utf-8")
    return titles


def run_once(notes_dir: Path, state_dir: Path, client: llm.ChatClient) -> int:
    store = state_mod.StateStore(state_dir)
    todo_files = notes.list_todo_files(notes_dir)

    deleted = 0
    for path in todo_files:
        _, body = notes.read_note(path)
        if notes.has_ticked_checkbox(body):
            rel = str(path.relative_to(notes_dir))
            path.unlink()
            store.remove(rel)
            deleted += 1

    processed = 0
    for path in todo_files:
        if not path.exists():
            continue
        front, _ = notes.read_note(path)
        if notes.is_hub(front):
            continue
        rel = str(path.relative_to(notes_dir))
        current = state_mod.sha256_file(path)
        if current == store.hash_for(rel):
            continue
        try:
            process_note(path, notes_dir, client)
            store.set_processed(rel, state_mod.sha256_file(path))
            processed += 1
        except llm.LLMError as e:
            print(f"[todo-agent] ERROR {rel}: {e}", flush=True)
    return deleted + processed


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
        run_once(notes_dir, state_dir, client)
        return 0
    print(f"[todo-agent] watching {notes_dir}/todo every {interval}s", flush=True)
    while True:
        try:
            n = run_once(notes_dir, state_dir, client)
            if n:
                print(f"[todo-agent] cycle: {n} note(s) handled", flush=True)
        except Exception as e:
            print(f"[todo-agent] cycle error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())