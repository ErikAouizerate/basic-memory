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

    def test_preserves_frontmatter(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(
                d,
                name="fm.md",
                body="---\ntype: todo\npermalink: main/todo/zz\ntags:\n- veille\n---\n# Body",
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

    def test_llm_error_counts_failure(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d)
            client = _FakeClient({"results": []})  # empty results -> LLMError
            with mock.patch("notes.fetch_text", return_value=""):
                handled, failed = main.run_once(Path(d), Path(d) / "state", client)
            self.assertEqual(handled, 0)
            self.assertEqual(failed, 1)

    def test_lock_skips_when_held(self):
        with tempfile.TemporaryDirectory() as d:
            self._setup(d)
            client = _FakeClient({"results": [dict(RESULT)]})
            lock = main._acquire_lock(Path(d) / "state")
            try:
                handled, failed = main.run_once(Path(d), Path(d) / "state", client)
            finally:
                lock.close()
            self.assertEqual((handled, failed), (0, 0))
            self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()