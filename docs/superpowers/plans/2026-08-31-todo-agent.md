# Todo Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `todo-agent` service that polls the Basic Memory notes volume every minute, processes new/modified notes in `todo/` via the OpenCode Zen API, writes result notes, and deletes todo notes whose checkbox the user has ticked.

**Architecture:** A stdlib-only Python loop (`agent/`) runs in a `python:3.12-slim` container that mounts the same `notes` volume as basic-memory and a dedicated `todo-agent-state` volume. Each cycle: (1) delete any `todo/*.md` whose checkbox `- [x] Traité — supprimable` is ticked, (2) sha256-compare each `todo/*.md` against a state JSON, (3) for new/modified notes: extract URLs, fetch enrichment, ask the LLM (OpenAI-compatible JSON contract) for result notes, write them, append a `## Résultat` section with `[[wikilinks]]` + a fresh checkbox to the todo note, and record the post-edit hash so the agent never reprocesses its own writes.

**Tech Stack:** Python 3.12, stdlib only (`urllib.request`, `json`, `hashlib`, `re`, `unittest`). No third-party dependencies — the `python:3.12-slim` image runs the code as-is.

## Global Constraints

- Dokploy policy: no `ports:` in `docker-compose.yml`.
- No LLM key in the repository; `LLM_API_KEY` is required at startup (`${LLM_API_KEY:?}`) and comes from `.env` locally / Dokploy env in production.
- Defaults: `LLM_BASE_URL=https://opencode.ai/zen/v1`, `LLM_MODEL=opencode-go/deepseek-v4-flash`, `POLL_INTERVAL=60`, `NOTES_DIR=/app/data/basic-memory`, `STATE_DIR=/app/state`.
- Note content is French; code, comments and commits are English (repo convention).
- Repo convention: no test framework beyond stdlib `unittest`. Tests run in the same image the service uses:
  `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
- Verification: `docker compose config --quiet` + a manual `--once` run; `scripts/smoke-test.sh` stays unchanged.
- Ignored by the agent: `*.sync-conflict-*` files and notes with frontmatter `type: hub`.
- Reprocess semantics: a modified, already-processed note is reprocessed; the existing `## Résultat` section is replaced, result notes are overwritten (never duplicated).
- Compose interpolates the shell environment over `.env` (repo gotcha): if `LLM_API_KEY` is exported in the shell it wins over `.env`.

---

### Task 1: State store (`agent/state.py`)

**Files:**
- Create: `agent/state.py`
- Test: `agent/tests/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `sha256_file(path: Path) -> str`
  - `class StateStore(state_dir: Path)` with methods `hash_for(rel_path: str) -> str | None`, `set_processed(rel_path: str, file_hash: str) -> None`, `remove(rel_path: str) -> None`. State file is `STATE_DIR/state.json` mapping `rel_path -> {"hash": str, "processed_at": str}`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_state.py`:

```python
import tempfile
import unittest
from pathlib import Path

from state import StateStore, sha256_file


class StateStoreTest(unittest.TestCase):
    def test_roundtrip_persists(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            store.set_processed("todo/a.md", "abc123")
            store2 = StateStore(Path(d))
            self.assertEqual(store2.hash_for("todo/a.md"), "abc123")

    def test_unknown_path_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            self.assertIsNone(store.hash_for("todo/nope.md"))

    def test_remove_deletes_entry(self):
        with tempfile.TemporaryDirectory() as d:
            store = StateStore(Path(d))
            store.set_processed("todo/a.md", "h")
            store.remove("todo/a.md")
            self.assertIsNone(store.hash_for("todo/a.md"))

    def test_corrupt_state_recovers_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "state.json").write_text("{not json", encoding="utf-8")
            store = StateStore(Path(d))
            self.assertIsNone(store.hash_for("todo/a.md"))


class Sha256Test(unittest.TestCase):
    def test_sha256_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.txt"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(
                sha256_file(p),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/state.py`:

```python
"""Persistent per-note processing state (sha256 hash bookkeeping)."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class StateStore:
    """JSON file mapping relative note paths to {hash, processed_at}."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "state.json"
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    def hash_for(self, rel_path: str) -> str | None:
        entry = self._data.get(rel_path)
        return entry["hash"] if entry else None

    def set_processed(self, rel_path: str, file_hash: str) -> None:
        self._data[rel_path] = {
            "hash": file_hash,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def remove(self, rel_path: str) -> None:
        if rel_path in self._data:
            del self._data[rel_path]
            self.save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/state.py agent/tests/test_state.py
git commit -m "feat: add todo-agent state store"
```

---

### Task 2: Notes helpers (`agent/notes.py`)

**Files:**
- Create: `agent/notes.py`
- Test: `agent/tests/test_notes.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `list_todo_files(notes_dir: Path) -> list[Path]` — sorted `todo/*.md`, skipping `*.sync-conflict-*`.
  - `read_note(path: Path) -> tuple[dict, str]` — `(frontmatter, body)`; frontmatter is a flat scalar dict (may be `{}`).
  - `is_hub(frontmatter: dict) -> bool`
  - `has_ticked_checkbox(body: str) -> bool`
  - `extract_urls(text: str) -> list[str]`
  - `fetch_text(url: str, urlopen=None, timeout: float = 20.0) -> str` — GitHub repos try the raw README first; strips HTML tags; returns `""` on failure; capped at 40000 chars.
  - `write_result_note(notes_dir: Path, folder: str, result: dict) -> Path` — writes `<notes_dir>/<folder>/<title>.md`, returns the path.
  - `ensure_result_section(body: str, result_titles: list[str]) -> str` — replaces an existing `## Résultat` section or appends a new one with one `[[title]]` line per title plus `- [ ] Traité — supprimable`.
  - Constants `TODO_DIR = "todo"`, `RESULT_MARKER = "Traité — supprimable"`, `RESULT_SECTION = "## Résultat"`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_notes.py`:

```python
import tempfile
import unittest
from pathlib import Path

from notes import (
    ensure_result_section,
    extract_urls,
    fetch_text,
    has_ticked_checkbox,
    is_hub,
    list_todo_files,
    read_note,
    write_result_note,
)


class _FakeResponse:
    headers = {"content-type": "text/plain; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=None):
        return b"<html>Agentic software factory summary</html>"


def _fake_urlopen(target, timeout=20.0):
    return _FakeResponse()


class ListTodoFilesTest(unittest.TestCase):
    def test_returns_only_md_and_skips_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            todo = Path(d) / "todo"
            todo.mkdir()
            (todo / "a.md").write_text("x", encoding="utf-8")
            (todo / "b.md.sync-conflict-20260831-123456-ABCDE-ABC").write_text("x", encoding="utf-8")
            (todo / "c.txt").write_text("x", encoding="utf-8")
            files = [p.name for p in list_todo_files(Path(d))]
            self.assertEqual(files, ["a.md"])

    def test_missing_todo_dir_returns_empty(self):
        self.assertEqual(list_todo_files(Path("/nonexistent")), [])


class ReadNoteTest(unittest.TestCase):
    def test_reads_frontmatter_and_body(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("---\ntitle: T\ntype: hub\n---\n# Body", encoding="utf-8")
            front, body = read_note(p)
            self.assertEqual(front.get("type"), "hub")
            self.assertIn("# Body", body)

    def test_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("just text", encoding="utf-8")
            front, body = read_note(p)
            self.assertEqual(front, {})
            self.assertEqual(body, "just text")


class HubAndCheckboxTest(unittest.TestCase):
    def test_is_hub(self):
        self.assertTrue(is_hub({"type": "hub"}))
        self.assertFalse(is_hub({"type": "todo"}))
        self.assertFalse(is_hub({}))

    def test_ticked_checkbox_detected(self):
        self.assertTrue(has_ticked_checkbox("- [x] Traité — supprimable"))
        self.assertFalse(has_ticked_checkbox("- [ ] Traité — supprimable"))
        self.assertFalse(has_ticked_checkbox("- [x] other"))


class ExtractUrlsTest(unittest.TestCase):
    def test_extracts_urls(self):
        urls = extract_urls("see https://github.com/a/b and https://x.y/z.")
        self.assertEqual(urls, ["https://github.com/a/b", "https://x.y/z"])


class FetchTextTest(unittest.TestCase):
    def test_strips_html(self):
        fetched = fetch_text("https://github.com/disler/super-simple-software-factory", urlopen=_fake_urlopen)
        self.assertIn("Agentic software factory", fetched)
        self.assertNotIn("<", fetched)

    def test_fetch_failure_returns_empty(self):
        def boom(target, timeout=20.0):
            raise OSError("nope")

        self.assertEqual(fetch_text("https://x.y/z", urlopen=boom), "")


class WriteResultNoteTest(unittest.TestCase):
    def test_writes_file_in_folder(self):
        with tempfile.TemporaryDirectory() as d:
            result = {
                "folder": "tools",
                "title": "Super Simple Software Factory",
                "note_type": "tool",
                "url": "https://github.com/disler/super-simple-software-factory",
                "tags": ["ai"],
                "kind": "fact",
                "observations": ["[usage] Agentic factory #ai"],
                "relations": ["[[Tools Catalog — Software & Services]]"],
                "body": "## Summary\nFrench text.",
            }
            path = write_result_note(Path(d), result["folder"], result)
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("title: Super Simple Software Factory", text)
            self.assertIn("## Observations", text)
            self.assertIn("[[Tools Catalog — Software & Services]]", text)


class EnsureResultSectionTest(unittest.TestCase):
    def test_appends_section(self):
        out = ensure_result_section("# Title\nline", ["Foo"])
        self.assertIn("## Résultat", out)
        self.assertIn("[[Foo]]", out)
        self.assertIn("- [ ] Traité — supprimable", out)

    def test_replaces_existing_section(self):
        body = "# Title\n## Résultat\n- [[Old]]\n- [ ] Traité — supprimable\n"
        out = ensure_result_section(body, ["New"])
        self.assertNotIn("[[Old]]", out)
        self.assertIn("[[New]]", out)
        self.assertEqual(out.count("## Résultat"), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'notes'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/notes.py`:

```python
"""Reading, scanning and writing markdown notes on the notes volume."""
import re
import urllib.request
from pathlib import Path

TODO_DIR = "todo"
RESULT_MARKER = "Traité — supprimable"
RESULT_SECTION = "## Résultat"

CONFLICT_RE = re.compile(r"\.sync-conflict-\d+-\d+.*\.md$", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")
GITHUB_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(/.*)?$")
FRONTMATTER_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*)$")

MAX_FETCH = 40_000


def list_todo_files(notes_dir: Path) -> list[Path]:
    todo = Path(notes_dir) / TODO_DIR
    if not todo.is_dir():
        return []
    return sorted(p for p in todo.glob("*.md") if not CONFLICT_RE.search(p.name))


def read_note(path: Path) -> tuple[dict, str]:
    """Return (frontmatter dict, body str). Frontmatter may be {}."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return _parse_frontmatter(parts[1]), parts[2].lstrip("\n")
    return {}, text


def _parse_frontmatter(block: str) -> dict:
    """Parse scalar `key: value` lines only (we only need `type`/`title`)."""
    front: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = FRONTMATTER_LINE_RE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if not value or value.startswith(("#", "{", "[", "&", "*", ">", "|", "!!")):
            continue
        front[m.group(1)] = value.strip().strip('"').strip("'")
    return front


def is_hub(frontmatter: dict) -> bool:
    return frontmatter.get("type") == "hub"


def has_ticked_checkbox(body: str) -> bool:
    return bool(re.search(r"- \[x\]\s*" + re.escape(RESULT_MARKER), body, re.IGNORECASE))


def extract_urls(text: str) -> list[str]:
    urls = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?)")
        if u and u not in urls:
            urls.append(u)
    return urls


def fetch_text(url: str, urlopen=None, timeout: float = 20.0) -> str:
    opener = urlopen or urllib.request.urlopen
    candidates = [url]
    m = GITHUB_RE.match(url)
    if m:
        owner, repo, _ = m.groups()
        candidates.insert(0, f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md")
    text = ""
    for target in candidates:
        try:
            with opener(target, timeout=timeout) as resp:
                raw = resp.read(MAX_FETCH)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            break
        except Exception:
            continue
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_FETCH]


def write_result_note(notes_dir: Path, folder: str, result: dict) -> Path:
    folder_dir = Path(notes_dir) / folder
    folder_dir.mkdir(parents=True, exist_ok=True)
    path = folder_dir / f"{result['title']}.md"
    path.write_text(_render_note(result), encoding="utf-8")
    return path


def _render_note(result: dict) -> str:
    lines = ["---", f"title: {result['title']}", f"type: {result.get('note_type', 'note')}"]
    if result.get("url"):
        lines.append(f"url: {result['url']}")
    tags = result.get("tags") or []
    if tags:
        lines.append("tags:")
        lines.extend(f"- {t}" for t in tags)
    if result.get("kind"):
        lines.append(f"kind: {result['kind']}")
    lines.append("---")
    if result.get("body"):
        lines.extend(["", result["body"].strip()])
    if result.get("observations"):
        lines.extend(["", "## Observations"])
        lines.extend(f"- {obs}" for obs in result["observations"])
    if result.get("relations"):
        lines.extend(["", "## Relations"])
        lines.extend(f"- {rel}" for rel in result["relations"])
    return "\n".join(lines) + "\n"


def ensure_result_section(body: str, result_titles: list[str]) -> str:
    section = RESULT_SECTION + "\n"
    section += "".join(f"- [[{t}]]\n" for t in result_titles)
    section += f"- [ ] {RESULT_MARKER}\n"
    prefix = re.split(rf"\n?{re.escape(RESULT_SECTION)}\n", body, maxsplit=1)[0]
    prefix = prefix.rstrip("\n")
    return prefix + "\n\n" + section.rstrip("\n") + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: all notes tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/notes.py agent/tests/test_notes.py
git commit -m "feat: add todo-agent notes helpers"
```

---

### Task 3: LLM client (`agent/llm.py`)

**Files:**
- Create: `agent/llm.py`
- Test: `agent/tests/test_llm.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `class LLMError(Exception)`
  - `class ChatClient(api_key: str, base_url: str, model: str, urlopen=None, timeout: float = 90.0)` with method `complete_json(system: str, user: str) -> dict`. Posts `POST {base_url}/chat/completions` with `response_format: {"type": "json_object"}`, `temperature: 0.2`; parses `choices[0].message.content` as JSON; raises `LLMError` on HTTP errors, network errors, or unparseable responses.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_llm.py`:

```python
import json
import unittest
import urllib.error

from llm import ChatClient, LLMError


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


def _make_opener(status=200, content=None):
    body = content if content is not None else json.dumps(
        {"choices": [{"message": {"content": json.dumps({"results": []})}}]}
    ).encode("utf-8")

    def opener(req, timeout=90.0):
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, None)
        return _FakeResponse(body)

    return opener


class ChatClientTest(unittest.TestCase):
    def test_complete_json_parses(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener())
        data = client.complete_json("sys", "user")
        self.assertEqual(data, {"results": []})

    def test_http_error_raises_llm_error(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener(status=401))
        with self.assertRaises(LLMError):
            client.complete_json("sys", "user")

    def test_invalid_json_raises_llm_error(self):
        client = ChatClient("key", "https://zen.example/v1", "model", urlopen=_make_opener(content=b"not json"))
        with self.assertRaises(LLMError):
            client.complete_json("sys", "user")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/llm.py`:

```python
"""Minimal OpenAI-compatible chat completions client (stdlib urllib)."""
import json
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


class ChatClient:
    def __init__(self, api_key: str, base_url: str, model: str, urlopen=None, timeout: float = 90.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.urlopen = urlopen or urllib.request.urlopen
        self.timeout = timeout

    def complete_json(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise LLMError(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from e
        except OSError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"LLM response unparseable: {raw[:300]!r}") from e
        if not isinstance(parsed, dict):
            raise LLMError("LLM response is not a JSON object")
        return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: all llm tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/llm.py agent/tests/test_llm.py
git commit -m "feat: add todo-agent llm client"
```

---

### Task 4: Main loop (`agent/main.py`)

**Files:**
- Create: `agent/main.py`
- Test: `agent/tests/test_main.py`

**Interfaces:**
- Consumes: `state.sha256_file`, `state.StateStore`; `notes.list_todo_files`, `notes.read_note`, `notes.is_hub`, `notes.has_ticked_checkbox`, `notes.extract_urls`, `notes.fetch_text`, `notes.write_result_note`, `notes.ensure_result_section`; `llm.ChatClient`, `llm.LLMError`.
- Produces:
  - `SYSTEM_PROMPT: str` — the task/conventions/JSON-schema prompt (below).
  - `process_note(path: Path, notes_dir: Path, client: ChatClient) -> list[str]` — raises `LLMError` on bad LLM output.
  - `run_once(notes_dir: Path, state_dir: Path, client: ChatClient) -> int` — deletes ticked notes, then processes new/modified ones; returns the number of notes handled (deleted + processed). Per-note errors are printed and swallowed (retried next cycle).
  - `main(argv=None) -> int` — reads env (`LLM_API_KEY` required), supports `--once`, otherwise loops with `time.sleep(POLL_INTERVAL)`.

- [ ] **Step 1: Write the failing test**

Create `agent/tests/test_main.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import main
import state as state_mod


class _FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append(user)
        return self.data


RESULT = {
    "folder": "tools",
    "title": "Result Note",
    "note_type": "tool",
    "url": "https://github.com/a/b",
    "tags": [],
    "kind": "fact",
    "observations": [],
    "relations": [],
    "body": "## Summary\nOk",
}


class RunOnceTest(unittest.TestCase):
    def _setup(self, d, name="to-test.md", body="https://github.com/a/b"):
        todo = Path(d) / "todo"
        todo.mkdir()
        note = todo / name
        note.write_text(body, encoding="utf-8")
        return note

    def test_processes_new_note_and_appends_result(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d)
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            text = note.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertIn("[[Result Note]]", text)
            self.assertTrue((Path(d) / "tools" / "Result Note.md").exists())
            store = state_mod.StateStore(Path(d) / "state")
            self.assertIsNotNone(store.hash_for("todo/to-test.md"))

    def test_skips_processed_note(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d)
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
                first_calls = len(client.calls)
                main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(len(client.calls), first_calls)

    def test_deletes_ticked_note(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body="- [x] Traité — supprimable")
            main.run_once(Path(d), Path(d) / "state", _FakeClient({"results": []}))
            self.assertFalse(note.exists())

    def test_skips_hub(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, name="hub.md", body="---\ntype: hub\n---\n# Hub")
            main.run_once(Path(d), Path(d) / "state", _FakeClient({"results": []}))
            self.assertTrue(note.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Write minimal implementation**

Create `agent/main.py`:

```python
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
    notes_dir = Path(os.environ.get("NOTES_DIR", "/app/data/basic-memory"))
    state_dir = Path(os.environ.get("STATE_DIR", "/app/state"))
    base_url = os.environ.get("LLM_BASE_URL", "https://opencode.ai/zen/v1")
    model = os.environ.get("LLM_MODEL", "opencode-go/deepseek-v4-flash")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`
Expected: all tests across the suite PASS (Tasks 1–4).

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main.py
git commit -m "feat: add todo-agent main loop"
```

---

### Task 5: Wire into compose, env, docs

**Files:**
- Modify: `docker-compose.yml` (add `todo-agent` service after `gateway`, add `todo-agent-state` volume)
- Modify: `.env.example` (add `LLM_API_KEY` + optional overrides)
- Modify: `README.md` (new "Agent todo" section)
- Modify: `AGENTS.md` (service description, gotchas)

**Interfaces:**
- Consumes: the `agent/` package from Tasks 1–4 (mounted read-only at `/agent`).
- Produces: a running `todo-agent` service on both local and Dokploy deployments.

- [ ] **Step 1: Add the service to `docker-compose.yml`**

After the `gateway:` block (before the top-level `volumes:`), add:

```yaml
  todo-agent:
    image: python:3.12-slim
    working_dir: /agent
    command: ["python", "/agent/main.py"]
    environment:
      LLM_API_KEY: ${LLM_API_KEY:?LLM_API_KEY is required}
      LLM_BASE_URL: ${LLM_BASE_URL:-https://opencode.ai/zen/v1}
      LLM_MODEL: ${LLM_MODEL:-opencode-go/deepseek-v4-flash}
      POLL_INTERVAL: ${POLL_INTERVAL:-60}
      NOTES_DIR: /app/data/basic-memory
      STATE_DIR: /app/state
    volumes:
      - ./agent:/agent:ro
      - notes:/app/data
      - todo-agent-state:/app/state
    restart: unless-stopped
```

And add `todo-agent-state:` to the top-level `volumes:` block:

```yaml
volumes:
  notes:
  config:
  syncthing-config:
  todo-agent-state:
```

No `ports:` (Dokploy policy), no `depends_on` (the agent reads files directly).

- [ ] **Step 2: Add the env var to `.env.example`**

Append (keeping the existing `MCP_TOKEN` line untouched):

```
# OpenCode Zen API key for the todo-agent (https://opencode.ai/zen/v1).
# Paste the value from ~/.local/share/opencode/auth.json (providers "opencode"/"opencode-go").
LLM_API_KEY=
# Optional overrides:
# LLM_BASE_URL=https://opencode.ai/zen/v1
# LLM_MODEL=opencode-go/deepseek-v4-flash
# POLL_INTERVAL=60
```

- [ ] **Step 3: Verify compose config**

Run: `set -a && source .env && set +a && docker compose -f docker-compose.yml config --quiet`
Expected: exits 0 (no output). If `.env` lacks `LLM_API_KEY`, this fails with `LLM_API_KEY is required` — add it first.

Also verify the local override still composes: `set -a && source .env && set +a && docker compose -f docker-compose.yml -f docker-compose.local.yml config --quiet`
Expected: exits 0.

- [ ] **Step 4: Add the "Agent todo" section to `README.md`**

Insert after the "Syncing the notes (Syncthing)" section:

```markdown
## Todo agent (autonomous processing of todo notes)

The `todo-agent` service polls the notes volume every minute (`POLL_INTERVAL`)
and processes new or modified notes in the `todo/` folder. A todo note is a
request written from any synced device; the agent interprets it via the
OpenCode Zen API, writes the result into the knowledge base and reports back
inside the todo note.

Variables (all optional except `LLM_API_KEY`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` | — (required) | OpenCode Zen API key (`https://opencode.ai/zen/v1`) |
| `LLM_BASE_URL` | `https://opencode.ai/zen/v1` | OpenAI-compatible base URL |
| `LLM_MODEL` | `opencode-go/deepseek-v4-flash` | Model name |
| `POLL_INTERVAL` | `60` | Seconds between cycles |
| `NOTES_DIR` | `/app/data/basic-memory` | Where the notes live (mounted `notes` volume) |
| `STATE_DIR` | `/app/state` | State file location (`todo-agent-state` volume) |

Lifecycle of a todo note:

1. Drop a note in `todo/` (e.g. a URL to evaluate). The agent detects it at the
   next cycle (sha256 of the file content), fetches the URLs for context, and
   asks the LLM for a JSON result.
2. The result note is written to the requested folder (tool evaluations land in
   `tools/`, following the existing note conventions) and the todo note gains a
   `## Résultat` section with `[[wikilinks]]` and a checkbox
   `- [ ] Traité — supprimable`.
3. When you tick the checkbox (`- [x] Traité — supprimable`), the agent deletes
   the todo note at the next cycle. The result note stays in the knowledge base.
4. Editing an already-processed todo note reprocesses it (the result is updated,
   not duplicated).

Notes are processed top-level only; `*.sync-conflict-*` files and notes with
`type: hub` frontmatter (e.g. `TODO — General Hub`) are ignored. The agent's
bookkeeping lives in the `todo-agent-state` volume — never in the synced notes
folder, so it cannot cause sync conflicts.

Verify a single cycle manually:

```bash
docker compose exec todo-agent python /agent/main.py --once
```
```

- [ ] **Step 5: Update `AGENTS.md`**

In the "What this is" section, add the todo-agent to the service list, and add a "Todo agent" bullet list (after the Syncthing block) with the load-bearing details:

```markdown
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
```

- [ ] **Step 6: Verify the running service locally**

```bash
set -a && source .env && set +a
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d todo-agent
docker compose logs todo-agent --tail=20
```

Expected: `[todo-agent] watching /app/data/basic-memory/todo every 60s`.

Then an end-to-end check with a real todo note: create `todo/To test.md`
containing `https://github.com/disler/super-simple-software-factory`, wait up to
one cycle (or run `--once`), and confirm:
- a `tools/*.md` result note exists,
- the todo note now has a `## Résultat` section with `[[...]]` and
  `- [ ] Traité — supprimable`,
- ticking the box (`- [x] Traité — supprimable`) then running `--once` deletes
  the todo note.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example README.md AGENTS.md
git commit -m "feat: wire todo-agent into compose and docs"
```
```

---

## Self-Review

**Spec coverage:**
- Poll every minute → `POLL_INTERVAL` default 60, loop in `main.py` (Task 4).
- Detect added/modified → sha256 vs `StateStore` (Tasks 1, 4).
- Process request via LLM → `SYSTEM_PROMPT` + `ChatClient.complete_json` (Tasks 3, 4).
- URL enrichment → `extract_urls` + `fetch_text` with GitHub README fallback (Task 2).
- Result note in `tools/` following conventions → `write_result_note` render (Task 2, Task 4).
- Link + checkbox appended → `ensure_result_section` (Task 2).
- Ticked checkbox → delete → `has_ticked_checkbox` + `path.unlink` priority before change detection (Tasks 2, 4).
- Reprocess on modification, no self-loop → post-edit hash recorded (Task 4), `ensure_result_section` replaces section (Task 2).
- Ignore `*.sync-conflict-*` and `type: hub` → `list_todo_files`, `is_hub` (Task 2).
- Env vars required/optional → compose + `.env.example` (Task 5).
- No `ports:` → compose (Task 5).
- French notes / English code → `SYSTEM_PROMPT`, doc strings (Tasks 3, 4, 5).
- Docs: README + AGENTS.md (Task 5).

**Placeholder scan:** No TBD/TODO; every step has concrete code, exact run commands and expected output.

**Type consistency:** `ChatClient.complete_json(system, user) -> dict` used in `main.process_note` matches Task 3. `StateStore.hash_for/set_processed/remove`, `notes.*` signatures used in Task 4 match Tasks 1–2. `ensure_result_section(body, result_titles: list[str])` matches its use with `titles: list[str]`. Fake `urlopen` signature `(req, timeout=...)` in tests matches the `self.urlopen(req, timeout=self.timeout)` call in `llm.py` and `fetch_text(url, urlopen=None, timeout=...)`.