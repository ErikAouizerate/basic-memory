# Todo Agent Frontmatter Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the todo-agent on an explicit `process: true` frontmatter flag, move the deletion flag to `deletable: true` in the same frontmatter, drop the poll interval to 5 s, and make every agent write safe against simultaneous user edits.

**Architecture:** `agent/` is stdlib-only Python (`python:3.12-slim`). `notes.py` gains pure-text frontmatter helpers (merge-only injection, boolean parsing, atomic write with optimistic re-read) and `main.py` wires them into the cycle: delete approved notes, migrate/annotate the rest, process only ticked ones, reset `process` after processing. No new dependencies.

**Tech Stack:** Python 3.12 stdlib, `unittest`. Verification: `docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v`.

## Global Constraints

- Code, comments, tests in **English**; user-facing note content stays French (unchanged).
- Stdlib only in `agent/` — no new imports beyond `os`, `time` (already allowed).
- `type: hub` notes are always ignored (unchanged).
- The agent records the hash **after** its own writes (unchanged) so its edits never trigger a reprocess loop.
- Every write must go through `notes.atomic_write` (temp file + `os.replace`).
- Every write must re-read the file immediately before writing and abort (retry ≤ 3) if it changed — never clobber a concurrent user save.
- Frontmatter is merge-only: existing keys are never overwritten.
- Spec: `docs/superpowers/specs/2026-08-31-todo-agent-frontmatter-gating-design.md`.

---

### Task 1: notes.py — safe write primitives

**Files:**
- Modify: `agent/notes.py:1-22` (imports, constants)
- Modify: `agent/notes.py:25-38` (split `read_note` into a pure `split_note` + thin `read_note`)
- Test: `agent/tests/test_notes.py` (add imports + new test classes)

**Interfaces:**
- Produces:
  - `split_note(text: str) -> tuple[dict, str, str]` — `(frontmatter dict, body, raw block)`; raw is `""` when no frontmatter. The raw block ends with `---\n`.
  - `atomic_write(path: Path, text: str) -> None`
  - `is_stable(path: Path, min_age: float = STABLE_SECONDS) -> bool`
  - `_rewrite_if_unchanged(path: Path, transform) -> bool` — `transform: (text: str) -> str`; returns True when the file now has the transformed content (or transform was a no-op), False when it stayed busy.

- [ ] **Step 1: Add the failing tests**

Add to the top of `agent/tests/test_notes.py`:

```python
import os
import time
from unittest import mock

from notes import (
    _rewrite_if_unchanged,
    atomic_write,
    is_stable,
    split_note,
)
```

Add these test classes to `agent/tests/test_notes.py`:

```python
class SplitNoteTest(unittest.TestCase):
    def test_splits_frontmatter_and_body(self):
        front, body, raw = split_note("---\ntitle: T\n---\n# Body")
        self.assertEqual(front.get("title"), "T")
        self.assertEqual(body, "# Body")
        self.assertTrue(raw.startswith("---"))
        self.assertTrue(raw.endswith("---\n"))

    def test_no_frontmatter(self):
        front, body, raw = split_note("just text")
        self.assertEqual(front, {})
        self.assertEqual(body, "just text")
        self.assertEqual(raw, "")


class AtomicWriteTest(unittest.TestCase):
    def test_writes_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            atomic_write(p, "hello")
            self.assertEqual(p.read_text(encoding="utf-8"), "hello")
            self.assertFalse((Path(d) / "n.md.tmp").exists())


class IsStableTest(unittest.TestCase):
    def test_fresh_file_not_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("x", encoding="utf-8")
            self.assertFalse(is_stable(p, min_age=10))

    def test_old_file_stable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("x", encoding="utf-8")
            past = time.time() - 100
            os.utime(p, (past, past))
            self.assertTrue(is_stable(p, min_age=10))


class RewriteIfUnchangedTest(unittest.TestCase):
    def test_writes_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")
            ok = _rewrite_if_unchanged(p, lambda t: t + "!")
            self.assertTrue(ok)
            self.assertEqual(p.read_text(encoding="utf-8"), "old!")

    def test_noop_transform_returns_true(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")
            self.assertTrue(_rewrite_if_unchanged(p, lambda t: t))

    def test_never_clobbers_concurrent_save(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("old", encoding="utf-8")

            def merge(t):
                p.write_text("user edit", encoding="utf-8")  # user saves during our window
                return "---\nprocess: false\n---\n\n" + t  # built from what we read

            ok = _rewrite_if_unchanged(p, merge)
            self.assertTrue(ok)
            text = p.read_text(encoding="utf-8")
            self.assertIn("process: false", text)
            self.assertIn("user edit", text)  # the in-flight save landed before our write
```
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: `ImportError` — `split_note`, `atomic_write`, `is_stable`, `_rewrite_if_unchanged` not defined.

- [ ] **Step 3: Implement the primitives in `notes.py`**

Replace the imports block (`agent/notes.py:1-3`):

```python
"""Reading, scanning and writing markdown notes on the notes volume."""
import os
import re
import time
import urllib.request
from pathlib import Path
```

Replace the constants block (`agent/notes.py:6-8`):

```python
TODO_DIR = "todo"
RESULT_MARKER = "Traité — supprimable"
RESULT_SECTION = "## Résultat"
STABLE_SECONDS = 3
```

Replace `read_note` (`agent/notes.py:25-38`) with:

```python
def split_note(text: str) -> tuple[dict, str, str]:
    """Parse a note's text into (frontmatter dict, body str, raw block str).

    The raw block includes the leading/trailing ``---`` delimiters and a
    trailing newline, or ``""`` when the note has no frontmatter.
    """
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return _parse_frontmatter(parts[1]), parts[2].lstrip("\n"), "---" + parts[1] + "---\n"
    return {}, text, ""


def read_note(path: Path) -> tuple[dict, str, str]:
    """Return (frontmatter dict, body str, raw frontmatter block str).

    The raw block is preserved verbatim on write so list-valued fields
    (e.g. ``tags``) survive a rewrite.
    """
    return split_note(path.read_text(encoding="utf-8"))
```

Add these functions after `is_hub` (`agent/notes.py:58-60`):

```python
def is_stable(path: Path, min_age: float = STABLE_SECONDS) -> bool:
    """True when the file's mtime is at least ``min_age`` seconds old, so the
    agent does not touch a note mid-typing."""
    try:
        return time.time() - path.stat().st_mtime >= min_age
    except OSError:
        return False


def atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` so neither
    the basic-memory watcher nor Syncthing ever sees a torn write."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _rewrite_if_unchanged(path: Path, transform) -> bool:
    """Apply ``transform(text) -> new_text``, writing atomically only if the
    file did not change between our read and the write (optimistic
    concurrency). Retries a few times so an in-flight user save is picked up
    rather than clobbered. Returns True when the file now has the transformed
    content (or the transform was a no-op), False when it stayed busy.
    """
    for _ in range(3):
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return False
        new_text = transform(current)
        if new_text == current:
            return True
        try:
            if path.read_text(encoding="utf-8") == current:
                atomic_write(path, new_text)
                return True
        except OSError:
            return False
    return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: all pass, including the pre-existing `ReadNoteTest` (the refactor keeps its contract).

- [ ] **Step 5: Commit**

```bash
git add agent/notes.py agent/tests/test_notes.py
git commit -m "feat: add safe write primitives to todo-agent notes"
```

---

### Task 2: notes.py — frontmatter injection & migration

**Files:**
- Modify: `agent/notes.py` (constants + new functions after Task 1's additions)
- Test: `agent/tests/test_notes.py`

**Interfaces:**
- Consumes: `split_note`, `_rewrite_if_unchanged`, `is_hub`, `_yaml_scalar`, `RESULT_MARKER` (all exist after Task 1).
- Produces:
  - `frontmatter_is_complete(frontmatter: dict) -> bool`
  - `inject_frontmatter_text(stem: str, text: str) -> str` — merge-only; returns text unchanged when nothing to do; strips any stray legacy checkbox from the body.
  - `ensure_frontmatter(path: Path) -> bool` — inject/migrate atomically, never clobbering a concurrent save.
  - `set_frontmatter_value(raw: str, key: str, value: str) -> str`

- [ ] **Step 1: Add the failing tests**

Add to the import block in `agent/tests/test_notes.py`:

```python
from notes import (
    _rewrite_if_unchanged,
    atomic_write,
    ensure_frontmatter,
    frontmatter_bool,
    frontmatter_is_complete,
    inject_frontmatter_text,
    is_stable,
    set_frontmatter_value,
    split_note,
)
```

Add these test classes:

```python
class FrontmatterBoolTest(unittest.TestCase):
    def test_true_false_and_none(self):
        self.assertTrue(frontmatter_bool({"process": "true"}, "process"))
        self.assertTrue(frontmatter_bool({"process": "TRUE"}, "process"))
        self.assertFalse(frontmatter_bool({"process": "false"}, "process"))
        self.assertFalse(frontmatter_bool({"process": "no"}, "process"))
        self.assertIsNone(frontmatter_bool({}, "process"))


class FrontmatterCompleteTest(unittest.TestCase):
    def test_complete_when_all_present(self):
        front = {"title": "T", "type": "todo", "process": "false", "deletable": "false"}
        self.assertTrue(frontmatter_is_complete(front))

    def test_incomplete_when_missing(self):
        self.assertFalse(frontmatter_is_complete({"type": "todo"}))
        self.assertFalse(frontmatter_is_complete({}))


class InjectFrontmatterTest(unittest.TestCase):
    def test_adds_frontmatter_to_bare_note(self):
        out = inject_frontmatter_text("Ma demande", "Contenu de la demande")
        self.assertIn("title: Ma demande", out)
        self.assertIn("type: todo", out)
        self.assertIn("process: false", out)
        self.assertIn("deletable: false", out)
        self.assertIn("- todo", out)
        self.assertIn("Contenu de la demande", out)
        self.assertLess(out.index("---"), out.index("Contenu"))

    def test_merges_missing_keys_only(self):
        out = inject_frontmatter_text("foo", "---\ntype: todo\npermalink: main/todo/zz\n---\n# Body")
        self.assertIn("permalink: main/todo/zz", out)
        self.assertIn("process: false", out)
        self.assertEqual(out.count("type: todo"), 1)

    def test_keeps_existing_values(self):
        out = inject_frontmatter_text("foo", "---\ntitle: Mon titre\nprocess: true\n---\nB")
        self.assertIn("title: Mon titre", out)
        self.assertIn("process: true", out)
        self.assertNotIn("title: foo", out)

    def test_idempotent(self):
        text = "---\ntitle: T\ntype: todo\ntags:\n- todo\nprocess: false\ndeletable: false\n---\nBody"
        self.assertEqual(inject_frontmatter_text("T", text), text)

    def test_skips_hub(self):
        text = "---\ntype: hub\n---\n# Hub"
        self.assertEqual(inject_frontmatter_text("hub", text), text)

    def test_strips_stray_result_checkbox(self):
        text = "x\n\n## Résultat\n- [[Foo]]\n- [ ] Traité — supprimable\n"
        out = inject_frontmatter_text("x", text)
        self.assertNotIn("Traité — supprimable", out)
        self.assertIn("[[Foo]]", out)


class EnsureFrontmatterTest(unittest.TestCase):
    def test_injects_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("demande", encoding="utf-8")
            self.assertTrue(ensure_frontmatter(p))
            text = p.read_text(encoding="utf-8")
            self.assertIn("process: false", text)
            self.assertIn("demande", text)

    def test_noop_on_complete_note(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            text = "---\ntitle: T\ntype: todo\nprocess: false\ndeletable: false\n---\nB"
            p.write_text(text, encoding="utf-8")
            self.assertTrue(ensure_frontmatter(p))
            self.assertEqual(p.read_text(encoding="utf-8"), text)


class SetFrontmatterValueTest(unittest.TestCase):
    def test_replaces_value(self):
        raw = "---\nprocess: true\n---\n"
        self.assertEqual(set_frontmatter_value(raw, "process", "false"), "---\nprocess: false\n---\n")

    def test_inserts_missing_key_before_closing(self):
        raw = "---\ntype: todo\n---\n"
        out = set_frontmatter_value(raw, "process", "false")
        self.assertEqual(out.count("---"), 2)
        self.assertLess(out.index("process: false"), out.index("---\n", out.index("process: false")))

    def test_single_replacement(self):
        out = set_frontmatter_value("---\nprocess: true\n---\n", "process", "false")
        self.assertEqual(out.count("process: false"), 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: `ImportError` — new functions not defined.

- [ ] **Step 3: Implement in `notes.py`**

Add to the constants block:

```python
REQUIRED_FRONTMATTER = ("title", "type", "process", "deletable")
```

Add these functions in the helpers section:

```python
def frontmatter_bool(frontmatter: dict, key: str) -> bool | None:
    """Interpret a frontmatter key as a boolean: True/False, or None when the
    key is absent."""
    value = frontmatter.get(key)
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1", "yes")


def frontmatter_is_complete(frontmatter: dict) -> bool:
    """True when all action keys the agent manages are present."""
    return all(frontmatter.get(k) is not None for k in REQUIRED_FRONTMATTER)


def _strip_result_checkbox(body: str) -> str:
    """Remove the legacy ``- [ ] Traité — supprimable`` line; the flag now
    lives in the frontmatter as ``deletable``."""
    return re.sub(
        r"(?im)^- \[[ xX]\]\s*" + re.escape(RESULT_MARKER) + r"\s*$\n?",
        "",
        body,
    )


def set_frontmatter_value(raw: str, key: str, value: str) -> str:
    """Return the raw frontmatter block with ``key`` set to ``value``,
    inserting the key before the closing delimiter when absent. Only touches
    the block, never the body."""
    pattern = rf"(?im)^({re.escape(key)}):\s*.*$"
    if re.search(pattern, raw):
        return re.sub(pattern, rf"\1: {value}", raw, count=1)
    lines = raw.splitlines()
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    lines.insert(end - 1, f"{key}: {value}")
    return "\n".join(lines) + "\n"


def inject_frontmatter_text(stem: str, text: str) -> str:
    """Return ``text`` with the action frontmatter ensured (merge only:
    existing keys are preserved, missing ones are added). Hubs are left
    untouched. Any stray legacy checkbox is stripped from the body. Returns
    ``text`` unchanged when nothing to do."""
    front, body, raw = split_note(text)
    if is_hub(front):
        return text
    body = _strip_result_checkbox(body)
    if raw == "":
        block = (
            "---\n"
            f"title: {_yaml_scalar(stem)}\n"
            "type: todo\n"
            "tags:\n"
            "- todo\n"
            "process: false\n"
            "deletable: false\n"
            "---\n"
        )
        return block + ("\n" + body if body else "")
    lines = raw.splitlines()
    inserts = []
    for key, value in (
        ("title", _yaml_scalar(stem)),
        ("type", "todo"),
        ("process", "false"),
        ("deletable", "false"),
    ):
        if front.get(key) is None:
            inserts.append(f"{key}: {value}")
    if not re.search(r"(?im)^tags:", raw):
        inserts.append("tags:")
        inserts.append("- todo")
    if not inserts:
        return text
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    lines[end - 1 : end - 1] = inserts
    return "\n".join(lines) + "\n" + ("\n" + body if body else "")


def ensure_frontmatter(path: Path) -> bool:
    """Inject/migrate the action frontmatter atomically, without clobbering a
    concurrent user save. Returns True when the note now has it (or already
    did), False when the file stayed busy."""
    return _rewrite_if_unchanged(path, lambda text: inject_frontmatter_text(path.stem, text))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent/notes.py agent/tests/test_notes.py
git commit -m "feat: frontmatter injection and migration for todo notes"
```

---

### Task 3: notes.py — result section without checkbox + `append_result`

**Files:**
- Modify: `agent/notes.py:140-146` (`ensure_result_section`)
- Modify: `agent/notes.py` (add `append_result`)
- Test: `agent/tests/test_notes.py` (`EnsureResultSectionTest` update + new `AppendResultTest`)

**Interfaces:**
- Consumes: `split_note`, `ensure_result_section`, `set_frontmatter_value`, `_rewrite_if_unchanged`.
- Produces: `append_result(path: Path, result_titles: list[str]) -> bool` — replaces/keeps `## Résultat` and resets `process: false`, atomically on fresh content.

- [ ] **Step 1: Update/add the tests**

In `agent/tests/test_notes.py`, update `EnsureResultSectionTest`:

```python
class EnsureResultSectionTest(unittest.TestCase):
    def test_appends_section(self):
        out = ensure_result_section("# Title\nline", ["Foo"])
        self.assertIn("## Résultat", out)
        self.assertIn("[[Foo]]", out)
        self.assertNotIn("Traité — supprimable", out)

    def test_replaces_existing_section(self):
        body = "# Title\n## Résultat\n- [[Old]]\n- [ ] Traité — supprimable\n"
        out = ensure_result_section(body, ["New"])
        self.assertNotIn("[[Old]]", out)
        self.assertIn("[[New]]", out)
        self.assertEqual(out.count("## Résultat"), 1)
        self.assertNotIn("Traité — supprimable", out)
```

Add `append_result` to the import block:

```python
from notes import (
    _rewrite_if_unchanged,
    append_result,
    atomic_write,
    ensure_frontmatter,
    frontmatter_bool,
    frontmatter_is_complete,
    inject_frontmatter_text,
    is_stable,
    set_frontmatter_value,
    split_note,
)
```

Add this test class:

```python
class AppendResultTest(unittest.TestCase):
    def test_appends_section_and_resets_process(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("---\nprocess: true\n---\n# Body", encoding="utf-8")
            self.assertTrue(append_result(p, ["Foo"]))
            text = p.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertIn("[[Foo]]", text)
            self.assertIn("process: false", text)
            self.assertNotIn("process: true", text)

    def test_leaves_process_alone_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "n.md"
            p.write_text("# Body", encoding="utf-8")
            self.assertTrue(append_result(p, ["Foo"]))
            text = p.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertNotIn("process", text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: `EnsureResultSectionTest` assertions fail (checkbox still emitted) and `ImportError` for `append_result`.

- [ ] **Step 3: Implement in `notes.py`**

Replace `ensure_result_section` (`agent/notes.py:140-146`):

```python
def ensure_result_section(body: str, result_titles: list[str]) -> str:
    section = RESULT_SECTION + "\n"
    section += "".join(f"- [[{t}]]\n" for t in result_titles)
    prefix = re.split(rf"\n?{re.escape(RESULT_SECTION)}\n", body, maxsplit=1)[0]
    prefix = prefix.rstrip("\n")
    return prefix + "\n\n" + section.rstrip("\n") + "\n"


def append_result(path: Path, result_titles: list[str]) -> bool:
    """Append/replace the ``## Résultat`` section and reset ``process: false``
    on fresh content, never clobbering a concurrent user edit. Returns True on
    success, False when the file stayed busy."""

    def _apply(text: str) -> str:
        front, body, raw = split_note(text)
        new_body = ensure_result_section(body, result_titles)
        if front.get("process") is not None:
            raw = set_frontmatter_value(raw, "process", "false")
        return raw + new_body

    return _rewrite_if_unchanged(path, _apply)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent/notes.py agent/tests/test_notes.py
git commit -m "feat: result section without deletion checkbox, append_result reset"
```

---

### Task 4: main.py — gating, deletion, reset, poll interval

**Files:**
- Modify: `agent/main.py:59-91` (`process_note`)
- Modify: `agent/main.py:104-146` (`_run_once`)
- Modify: `agent/main.py:162` (`POLL_INTERVAL` default)
- Test: `agent/tests/test_main.py` (update existing tests + new ones)

**Interfaces:**
- Consumes: `notes.frontmatter_bool`, `notes.frontmatter_is_complete`, `notes.is_stable`, `notes.ensure_frontmatter`, `notes.has_ticked_checkbox`, `notes.append_result`.
- Produces: unchanged `run_once(notes_dir, state_dir, client) -> tuple[int, int]` and `process_note(path, notes_dir, client) -> list[str]`.

- [ ] **Step 1: Update and extend the tests**

In `agent/tests/test_main.py`, add imports and a constant:

```python
import os
import time

import notes
import main
import state as state_mod

PROCESSED = "---\nprocess: true\n---\n"
```

Replace the existing tests in `RunOnceTest` with:

```python
class RunOnceTest(unittest.TestCase):
    def _setup(self, d, name="to-test.md", body="https://github.com/a/b"):
        todo = Path(d) / "todo"
        todo.mkdir()
        note = todo / name
        note.write_text(body, encoding="utf-8")
        return note

    def test_processes_ticked_note_and_appends_result(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body=PROCESSED + "https://github.com/a/b")
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            text = note.read_text(encoding="utf-8")
            self.assertIn("## Résultat", text)
            self.assertIn("[[Result Note]]", text)
            self.assertIn("process: false", text)
            self.assertTrue((Path(d) / "tools" / "Result Note.md").exists())
            store = state_mod.StateStore(Path(d) / "state")
            self.assertIsNotNone(store.hash_for("todo/to-test.md"))

    def test_skips_processed_note(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d, body=PROCESSED + "https://github.com/a/b")
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
                first_calls = len(client.calls)
                main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(len(client.calls), first_calls)

    def test_deletes_ticked_legacy_checkbox(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body="- [x] Traité — supprimable")
            main.run_once(Path(d), Path(d) / "state", _FakeClient({"results": []}))
            self.assertFalse(note.exists())

    def test_deletes_via_deletable_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body="---\ndeletable: true\n---\n# done")
            main.run_once(Path(d), Path(d) / "state", _FakeClient({"results": []}))
            self.assertFalse(note.exists())

    def test_skips_hub(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, name="hub.md", body="---\ntype: hub\n---\n# Hub")
            main.run_once(Path(d), Path(d) / "state", _FakeClient({"results": []}))
            self.assertTrue(note.exists())

    def test_skips_unprocessed_note(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, name="new.md", body="Ma demande")
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(client.calls, [])
            self.assertEqual(note.read_text(encoding="utf-8"), "Ma demande")

    def test_injects_frontmatter_to_stable_unprocessed_note(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, name="new.md", body="Ma demande")
            past = time.time() - 100
            os.utime(note, (past, past))
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(client.calls, [])
            text = note.read_text(encoding="utf-8")
            self.assertIn("process: false", text)
            self.assertIn("deletable: false", text)
            self.assertIn("Ma demande", text)

    def test_preserves_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(
                d,
                name="fm.md",
                body="---\ntype: todo\nprocess: true\npermalink: main/todo/zz\ntags:\n- veille\n---\n# Body",
            )
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            text = note.read_text(encoding="utf-8")
            self.assertIn("type: todo", text)
            self.assertIn("permalink: main/todo/zz", text)
            self.assertIn("tags:\n- veille", text)
            self.assertIn("# Body", text)
            self.assertIn("## Résultat", text)
            self.assertIn("process: false", text)

    def test_llm_error_counts_failure(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d, body=PROCESSED + "https://github.com/a/b")
            client = _FakeClient({"results": []})  # empty results -> LLMError
            with mock.patch("notes.fetch_text", return_value=""):
                handled, failed = main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(handled, 0)
            self.assertEqual(failed, 1)

    def test_lock_skips_when_held(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d, body=PROCESSED + "https://github.com/a/b")
            client = _FakeClient({"results": [dict(RESULT)]})
            lock = main._acquire_lock(Path(d) / "state")
            try:
                handled, failed = main.run_once(Path(d), Path(d) / "state", client)
            finally:
                lock.close()
            self.assertEqual((handled, failed), (0, 0))
            self.assertEqual(client.calls, [])

    def test_retick_reprocesses(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body=PROCESSED + "https://github.com/a/b")
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
                first_calls = len(client.calls)
                text = note.read_text(encoding="utf-8").replace("process: false", "process: true")
                note.write_text(text, encoding="utf-8")
                main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(len(client.calls), first_calls + 1)
            self.assertIn("process: false", note.read_text(encoding="utf-8"))

    def test_does_not_lose_user_edit_during_processing(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(d, body=PROCESSED + "# Title\nword")
            client = _FakeClient({"results": [dict(RESULT)]})
            real_append = notes.append_result

            def racing_append(path, titles):
                path.write_text("---\nprocess: true\n---\n# Title\nword\nuser edit", encoding="utf-8")
                return real_append(path, titles)

            with mock.patch("notes.fetch_text", return_value=""), mock.patch(
                "notes.append_result", side_effect=racing_append
            ):
                main.run_once(Path(d), Path(d) / "state", client)
            text = note.read_text(encoding="utf-8")
            self.assertIn("user edit", text)
            self.assertIn("## Résultat", text)
            self.assertIn("process: false", text)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: several `test_main.py` failures (notes with `process: true` are not processed yet; `deletable` not honored) and `ImportError`/`AttributeError` for the new `notes.*` calls.

- [ ] **Step 3: Implement in `main.py`**

Replace `process_note` (`agent/main.py:59-91`):

```python
def process_note(path: Path, notes_dir: Path, client: llm.ChatClient) -> list[str]:
    front, body, _ = notes.read_note(path)
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
    if not notes.append_result(path, titles):
        raise llm.LLMError(f"could not write result section to {path.name} (file busy)")
    return titles
```

Replace `_run_once` (`agent/main.py:104-146`):

```python
def _run_once(notes_dir: Path, state_dir: Path, client: llm.ChatClient) -> tuple[int, int]:
    """One cycle: delete approved notes, migrate/annotate the rest, then
    process the notes whose `process` flag is ticked.

    Returns (handled, failed); failed notes are logged and retried next cycle.
    """
    store = state_mod.StateStore(state_dir)
    todo_files = notes.list_todo_files(notes_dir)

    deleted = 0
    for path in todo_files:
        try:
            front, body, _ = notes.read_note(path)
            if notes.frontmatter_bool(front, "deletable") or notes.has_ticked_checkbox(body):
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
            if notes.frontmatter_bool(front, "process") is not True:
                if not notes.frontmatter_is_complete(front) and notes.is_stable(path):
                    notes.ensure_frontmatter(path)
                continue
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
```

Change `agent/main.py:162`:

```python
    interval = int(os.environ.get("POLL_INTERVAL", "5"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent/main.py agent/tests/test_main.py
git commit -m "feat: gate todo-agent processing on frontmatter process flag"
```

---

### Task 5: Compose + env + docs

**Files:**
- Modify: `docker-compose.yml:92`
- Modify: `.env.example:12`
- Modify: `README.md:215-251`
- Modify: `AGENTS.md` ("Todo agent" bullet block)

- [ ] **Step 1: Apply the config changes**

`docker-compose.yml:92`:

```yaml
      POLL_INTERVAL: ${POLL_INTERVAL:-5}
```

`.env.example:12`:

```
# POLL_INTERVAL=5
```

- [ ] **Step 2: Update README.md**

Replace `README.md:215-251` with:

```markdown
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
with the action frontmatter (`title`, `type: todo`, `tags: [todo]`,
`process: false`, `deletable: false`), which render as toggles in Obsidian.
Existing frontmatter keys are never overwritten.

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
```

- [ ] **Step 3: Update AGENTS.md**

Replace the todo-agent bullets in `AGENTS.md` (currently lines 70-84: the bullets starting with "The `todo-agent` service polls..." down to "...so its edits never trigger a reprocess loop.") with:

```markdown
- The `todo-agent` service polls the `notes` volume (`todo/` folder) every
  `POLL_INTERVAL` seconds (default 5) and processes notes via the OpenCode Zen
  API (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` env vars; image
  `python:3.12-slim`, stdlib only, no dependencies to install).
- New notes get action frontmatter injected at the next cycle (`title`,
  `type: todo`, `tags: [todo]`, `process: false`, `deletable: false`) — merged
  in, existing keys never overwritten. Processing is gated on `process: true`;
  after processing the agent resets it to `false` (re-tick = reprocess).
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
```

- [ ] **Step 4: Verify compose config and commit**

Run:
```bash
docker compose -f docker-compose.yml config --quiet
```
Expected: exit 0.

```bash
git add docker-compose.yml .env.example README.md AGENTS.md
git commit -m "docs: poll interval 5s, frontmatter-gated todo lifecycle"
```

---

### Task 6: Full verification

- [ ] **Step 1: Run the whole unit suite**

```bash
docker run --rm -v "$PWD/agent:/app:ro" -w /app python:3.12-slim python -m unittest discover -s tests -v
```
Expected: every test in `test_notes.py`, `test_main.py`, `test_state.py`, `test_llm.py` passes.

- [ ] **Step 2: Validate compose**

```bash
docker compose -f docker-compose.yml config --quiet
```
Expected: exit 0.

- [ ] **Step 3: Push**

```bash
git push
```

---

## Self-Review Notes

- Spec coverage: frontmatter injection/merge (Task 2), `process` gating + reset (Tasks 3-4), `deletable` deletion + legacy compat (Task 4), migration/strip checkbox (Tasks 2-4), poll interval 5 s (Tasks 4-5), concurrency hardening (Tasks 1, 3-4), docs (Task 5).
- No placeholders: every step carries concrete code or an exact command.
- Type consistency: `append_result(path, titles)`, `ensure_frontmatter(path)`, `frontmatter_bool(front, key)`, `frontmatter_is_complete(front)` are defined once in the Tasks and consumed by name in later ones without signature drift.