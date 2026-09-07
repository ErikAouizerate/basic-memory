import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import notes
import main
import state as state_mod

PROCESSED = "---\nprocess: true\n---\n"


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

    def test_adds_memory_metadata_before_processing_ticked_note(self):
        with tempfile.TemporaryDirectory() as d:
            note = self._setup(
                d,
                name="fm.md",
                body="---\ntype: todo\nprocess: true\ndeletable: false\n---\n# Body",
            )
            past = time.time() - 100
            os.utime(note, (past, past))
            client = _FakeClient({"results": [dict(RESULT)]})
            with mock.patch("notes.fetch_text", return_value=""):
                main.run_once(Path(d), Path(d) / "state", client)
            text = note.read_text(encoding="utf-8")
            self.assertIn("memory_class: working", text)
            self.assertIn("lifecycle: raw", text)
            self.assertIn("source: human", text)

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


if __name__ == "__main__":
    unittest.main()
